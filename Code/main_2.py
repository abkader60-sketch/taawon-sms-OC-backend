"""
Ta'awon P3 SMS - FastAPI Backend
================================
Single-file backend. Inline schema, endpoints, and DB connection logic.

Changes in this revision (29 Apr 2026, v0.4):
  + Administrator role with full permissions (manage_users + everything)
  + Workflow flipped to Security-only (HR stage removed as approval gate)
  + Existing 'Pending HR Verification' rows auto-migrated
  + Groups + User_Group_Members tables (organizational, not permission-bearing)
  + ~10 new /api/v1/admin/* endpoints for full CRUD over users, roles, groups
  + workflow_type column on Site_Access_ID (stored, not enforced - Decision 3 Option A)

Backwards-compatible additive migrations: existing tables/data are preserved
(except for the deliberate one-shot HR -> Security state remap above).
"""

import os
import smtplib
import ssl
import uuid
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List, Optional

import asyncpg
from dotenv import load_dotenv
from fastapi import (FastAPI, File, Form, Header, HTTPException, Path as FPath,
                     Request, UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# ============================================================
#  Configuration
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(ENV_FILE)

DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", "5432"))
DB_NAME     = os.getenv("DB_NAME", "id_system")
DB_USER     = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "Omar123")

SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM     = os.getenv("SMTP_FROM", SMTP_USERNAME)
SMTP_ENABLED  = bool(SMTP_USERNAME and SMTP_PASSWORD)

UPLOADS_DIR = PROJECT_ROOT / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_MIME_PREFIXES = ("image/",)
ALLOWED_MIME_EXACT = {"application/pdf"}

# ============================================================
#  App setup
# ============================================================
app = FastAPI(title="Ta'awon P3 SMS API", version="0.4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def get_conn() -> asyncpg.Connection:
    return await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, database=DB_NAME,
        user=DB_USER, password=DB_PASSWORD
    )


# ============================================================
#  Schema bootstrap (additive - safe to re-run)
# ============================================================
SCHEMA_SQL = """
-- ---------- Existing tables (kept) ----------
CREATE TABLE IF NOT EXISTS App_Users (
    user_id              SERIAL PRIMARY KEY,
    full_name            VARCHAR(100) NOT NULL,
    username             VARCHAR(50) UNIQUE NOT NULL,
    hashed_password      VARCHAR(255) NOT NULL,
    force_password_change BOOLEAN DEFAULT TRUE,
    role_id              INT DEFAULT 1,
    is_active            BOOLEAN DEFAULT TRUE,
    created_at           TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS Site_Access_ID (
    application_id          SERIAL PRIMARY KEY,
    full_name               VARCHAR(100) NOT NULL,
    job_title               VARCHAR(100) NOT NULL,
    employee_number         VARCHAR(50) NOT NULL,
    organization            VARCHAR(100) NOT NULL,
    subcontractor           VARCHAR(100),
    gov_id_type             VARCHAR(20) NOT NULL,
    gov_id_number           VARCHAR(50) NOT NULL,
    blood_type              VARCHAR(5)  NOT NULL,
    mobile_number           VARCHAR(20) NOT NULL,
    whatsapp_number         VARCHAR(20) NOT NULL,
    is_third_party_sponsor  BOOLEAN DEFAULT FALSE,
    workflow_state          VARCHAR(50) DEFAULT 'Pending Security Clearance',
    created_at              TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    submitted_by_user_id    INT REFERENCES App_Users(user_id)
);
ALTER TABLE Site_Access_ID ADD COLUMN IF NOT EXISTS email VARCHAR(150);
ALTER TABLE Site_Access_ID ADD COLUMN IF NOT EXISTS workflow_type VARCHAR(30) DEFAULT 'security_only';
-- Default for newly created applications - point them at Security-only:
ALTER TABLE Site_Access_ID ALTER COLUMN workflow_state SET DEFAULT 'Pending Security Clearance';

CREATE TABLE IF NOT EXISTS Workflow_History (
    history_id            SERIAL PRIMARY KEY,
    application_id        INT NOT NULL,
    action_taken          VARCHAR(50) NOT NULL,
    previous_state        VARCHAR(50),
    new_state             VARCHAR(50) NOT NULL,
    acted_by_user_id      INT REFERENCES App_Users(user_id),
    action_timestamp      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    comments              TEXT
);

CREATE TABLE IF NOT EXISTS Roles (
    role_id      SERIAL PRIMARY KEY,
    role_name    VARCHAR(50) UNIQUE NOT NULL,
    description  VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS Role_Permissions (
    role_id          INT NOT NULL REFERENCES Roles(role_id) ON DELETE CASCADE,
    permission_key   VARCHAR(50) NOT NULL,
    PRIMARY KEY (role_id, permission_key)
);

CREATE TABLE IF NOT EXISTS Attachments (
    attachment_id        SERIAL PRIMARY KEY,
    application_id       INT NOT NULL REFERENCES Site_Access_ID(application_id) ON DELETE CASCADE,
    attachment_type      VARCHAR(50) NOT NULL,
    file_path            VARCHAR(500) NOT NULL,
    original_filename    VARCHAR(255) NOT NULL,
    mime_type            VARCHAR(100),
    file_size_bytes      BIGINT,
    uploaded_at          TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    uploaded_by_user_id  INT REFERENCES App_Users(user_id)
);

CREATE TABLE IF NOT EXISTS Notifications (
    notification_id    SERIAL PRIMARY KEY,
    application_id     INT REFERENCES Site_Access_ID(application_id) ON DELETE CASCADE,
    channel            VARCHAR(20) NOT NULL,
    recipient          VARCHAR(150) NOT NULL,
    subject            VARCHAR(255),
    body               TEXT NOT NULL,
    delivery_status    VARCHAR(30) NOT NULL,
    error_message      TEXT,
    created_at         TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    sent_at            TIMESTAMP WITH TIME ZONE
);

-- ---------- NEW: Groups (organizational, not permission-bearing) ----------
CREATE TABLE IF NOT EXISTS Groups (
    group_id      SERIAL PRIMARY KEY,
    group_name    VARCHAR(80) UNIQUE NOT NULL,
    description   VARCHAR(255),
    created_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS User_Group_Members (
    user_id    INT NOT NULL REFERENCES App_Users(user_id)  ON DELETE CASCADE,
    group_id   INT NOT NULL REFERENCES Groups(group_id)    ON DELETE CASCADE,
    added_at   TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, group_id)
);
"""

# ----------------------------------------------------------------
# Permission catalog - used by the Admin panel.
# Adding a new permission? Add it here AND wire it up wherever it's enforced.
# ----------------------------------------------------------------
PERMISSION_CATALOG = [
    ("submit_application",   "Can submit a new ID application"),
    ("approve_security",     "Can approve/reject at Security stage (current single approval gate)"),
    ("approve_hr",           "Can approve/reject at HR stage (legacy - unused under security-only workflow)"),
    ("view_all_applications","Can see every application"),
    ("manage_users",         "Can administer users, roles, and groups"),
]


SEED_SQL = """
-- ---------- Seed Roles ----------
INSERT INTO Roles (role_id, role_name, description) VALUES
    (1, 'Submitter',          'Submits ID applications'),
    (2, 'HR Verification',    'Legacy HR approver (unused under security-only workflow)'),
    (3, 'Security Clearance', 'Approves applications at the Security stage'),
    (4, 'Reviewer',           'General reviewer (read-only)'),
    (5, 'Administrator',      'Full system administrator')
ON CONFLICT (role_id) DO NOTHING;

SELECT setval(pg_get_serial_sequence('roles','role_id'),
              GREATEST((SELECT MAX(role_id) FROM Roles), 1));

-- ---------- Seed Role_Permissions ----------
INSERT INTO Role_Permissions (role_id, permission_key) VALUES
    (1, 'submit_application'),
    (2, 'approve_hr'),                    -- legacy; harmless to keep
    (2, 'view_all_applications'),
    (3, 'approve_security'),
    (3, 'view_all_applications'),
    (4, 'view_all_applications'),
    -- Administrator gets EVERY permission key (kept in sync below in Python)
    (5, 'submit_application'),
    (5, 'approve_security'),
    (5, 'approve_hr'),
    (5, 'view_all_applications'),
    (5, 'manage_users')
ON CONFLICT (role_id, permission_key) DO NOTHING;

-- ---------- Seed Users ----------
INSERT INTO App_Users (user_id, full_name, username, hashed_password, role_id, force_password_change) VALUES
    (1, 'Badriah Rizq Allah',      'badriah.rizqallah',     'temp_hash_123', 1, TRUE),
    (2, 'Omar Abdulaziz Almaqhawi','omar.almaqhawi',        'temp_hash_123', 2, TRUE),
    (3, 'Abdulkader Abdullatif',   'abdulkader.abdullatif', 'temp_hash_123', 3, TRUE),
    (4, 'Hind Mana Alahmari',      'hind.alahmari',         'temp_hash_123', 4, TRUE)
ON CONFLICT (user_id) DO NOTHING;

SELECT setval(pg_get_serial_sequence('app_users','user_id'),
              GREATEST((SELECT MAX(user_id) FROM App_Users), 1));
"""

# One-shot data migrations (safe re-run: each filters by exactly the legacy state).
MIGRATION_SQL = """
-- 1. Promote Omar from HR Verification to Administrator (if still HR).
--    Re-running this after he's already moved is a no-op (his role_id is now 5,
--    so the WHERE clause matches zero rows).
UPDATE App_Users SET role_id = 5
 WHERE user_id = 2 AND role_id = 2;

-- 2. Migrate any application currently 'Pending HR Verification' (the legacy
--    workflow's first stage) into 'Pending Security Clearance'. Logged in
--    Workflow_History so the audit trail explains the jump.
WITH affected AS (
    SELECT application_id FROM Site_Access_ID
     WHERE workflow_state = 'Pending HR Verification'
)
INSERT INTO Workflow_History
    (application_id, action_taken, previous_state, new_state, acted_by_user_id, comments)
SELECT application_id, 'Workflow Migration', 'Pending HR Verification',
       'Pending Security Clearance', NULL,
       'Auto-migrated to security-only workflow (v0.4)'
  FROM affected;

UPDATE Site_Access_ID
   SET workflow_state = 'Pending Security Clearance'
 WHERE workflow_state = 'Pending HR Verification';
"""


@app.post("/api/v1/admin/init-db")
async def init_db():
    conn = await get_conn()
    try:
        await conn.execute(SCHEMA_SQL)
        await conn.execute(SEED_SQL)
        # Run the data migrations after the seeds, inside their own statement
        # so a re-init on a fresh DB still works.
        await conn.execute(MIGRATION_SQL)

        # Make sure Administrator (role_id=5) has every permission in the catalog -
        # if we add new permissions later, this keeps Administrator current.
        for key, _desc in PERMISSION_CATALOG:
            await conn.execute(
                """INSERT INTO Role_Permissions (role_id, permission_key)
                   VALUES (5, $1)
                   ON CONFLICT DO NOTHING""",
                key,
            )
    finally:
        await conn.close()
    return {"status": "ok", "message": "Schema, seeds, and migrations applied (idempotent)."}


# ============================================================
#  Models
# ============================================================
class StatusUpdate(BaseModel):
    new_state: str
    acted_by_user_id: int
    comments: Optional[str] = None


class UserCreate(BaseModel):
    full_name: str
    username: str
    role_id: int
    is_active: bool = True


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    role_id: Optional[int] = None
    is_active: Optional[bool] = None


class RoleCreate(BaseModel):
    role_name: str
    description: Optional[str] = None
    permissions: List[str] = []


class RolePermissionsUpdate(BaseModel):
    permissions: List[str]


class GroupCreate(BaseModel):
    group_name: str
    description: Optional[str] = None
    member_user_ids: List[int] = []


class GroupMembersUpdate(BaseModel):
    member_user_ids: List[int]


# ============================================================
#  Permission helpers
# ============================================================
async def fetch_user_permissions(conn: asyncpg.Connection, user_id: int) -> List[str]:
    rows = await conn.fetch(
        """SELECT rp.permission_key
             FROM Role_Permissions rp
             JOIN App_Users u ON u.role_id = rp.role_id
            WHERE u.user_id = $1""",
        user_id,
    )
    return [r["permission_key"] for r in rows]


def required_permission_for_state_change(current_state: str, new_state: str) -> Optional[str]:
    """Maps a state transition to the required permission key.
    Under the security-only workflow:
      Pending Security Clearance -> Approved/Rejected   needs approve_security
    The legacy HR transition is still recognised in case stale rows surface,
    but new applications never enter that state.
    """
    if current_state == "Pending Security Clearance" and new_state in ("Approved", "Rejected"):
        return "approve_security"
    if current_state == "Pending HR Verification" and new_state in (
        "Pending Security Clearance", "Rejected"
    ):
        return "approve_hr"  # legacy
    return None


async def require_permission(
    conn: asyncpg.Connection, acting_user_id: Optional[int], permission: str
) -> None:
    """Raises 403 if the acting user lacks the permission.
    NOTE: with no real auth yet, the acting user is identified via the
    'X-Acting-User-Id' header set by the frontend role-picker. This is a
    deliberately weak gate - real auth will replace it."""
    if not acting_user_id:
        raise HTTPException(401, "Missing X-Acting-User-Id header")
    perms = await fetch_user_permissions(conn, acting_user_id)
    if permission not in perms:
        raise HTTPException(403, f"User lacks permission '{permission}'")


def acting_user_id_from_header(x_acting_user_id: Optional[str]) -> Optional[int]:
    if not x_acting_user_id:
        return None
    try:
        return int(x_acting_user_id)
    except ValueError:
        return None


# ============================================================
#  Notification helpers
# ============================================================
def _build_rejection_email_body(applicant_name: str, app_id: int, reason: str) -> str:
    return (
        f"Dear {applicant_name},\n\n"
        f"Your Site Access ID application (#{app_id}) has been reviewed and "
        f"unfortunately could not be approved at this time.\n\n"
        f"Reason provided by the reviewer:\n{reason}\n\n"
        f"If you believe this was made in error or you would like to re-apply with "
        f"corrected details, please contact your site administrator.\n\n"
        f"Regards,\n"
        f"Ta'awon P3 Security Management System"
    )


def _build_rejection_whatsapp_body(applicant_name: str, app_id: int, reason: str) -> str:
    return (
        f"Hello {applicant_name}, your Site Access ID application "
        f"(#{app_id}) was not approved.\nReason: {reason}\n"
        f"Please contact your site administrator for next steps."
    )


async def _log_notification(conn, app_id, channel, recipient, subject, body,
                            status, error_message=None, sent_at=None):
    await conn.execute(
        """INSERT INTO Notifications
              (application_id, channel, recipient, subject, body,
               delivery_status, error_message, sent_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
        app_id, channel, recipient, subject, body,
        status, error_message, sent_at,
    )


def _send_email_smtp(to_addr: str, subject: str, body: str) -> tuple[bool, Optional[str]]:
    if not SMTP_ENABLED:
        return False, "SMTP not configured (SMTP_USERNAME/SMTP_PASSWORD missing in .env)"
    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_FROM
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.starttls(context=context)
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [to_addr], msg.as_string())
        return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def _handle_rejection_notifications(conn, app_record, reason: str) -> None:
    app_id   = app_record["application_id"]
    name     = app_record["full_name"]
    email    = app_record.get("email") if isinstance(app_record, dict) else app_record["email"]
    whatsapp = app_record["whatsapp_number"] or app_record["mobile_number"]

    if not email:
        await _log_notification(
            conn, app_id, "email", "(unknown)",
            None, "(skipped - no email on file)", "skipped_no_recipient"
        )
    else:
        subject = f"Site Access ID Application #{app_id} - Update"
        body = _build_rejection_email_body(name, app_id, reason)
        success, err = _send_email_smtp(email, subject, body)
        await _log_notification(
            conn, app_id, "email", email, subject, body,
            "sent" if success else "failed",
            err, datetime.utcnow() if success else None,
        )

    if not whatsapp:
        await _log_notification(
            conn, app_id, "whatsapp", "(unknown)",
            None, "(skipped - no WhatsApp number)", "skipped_no_recipient"
        )
    else:
        body = _build_rejection_whatsapp_body(name, app_id, reason)
        await _log_notification(
            conn, app_id, "whatsapp", whatsapp, None, body,
            "pending_whatsapp_api"
        )


# ============================================================
#  Endpoints - meta
# ============================================================
@app.get("/")
async def root():
    return {
        "service": "Ta'awon P3 SMS API",
        "version": app.version,
        "smtp_enabled": SMTP_ENABLED,
        "workflow": "security_only",
    }


# ============================================================
#  Endpoints - users (existing + new admin endpoints)
# ============================================================
@app.get("/api/v1/users")
async def list_users():
    conn = await get_conn()
    try:
        rows = await conn.fetch(
            """SELECT u.user_id, u.full_name, u.username, u.role_id,
                      r.role_name, u.is_active
                 FROM App_Users u
            LEFT JOIN Roles r ON r.role_id = u.role_id
                WHERE u.is_active = TRUE
             ORDER BY u.user_id"""
        )
        return {"users": [dict(r) for r in rows]}
    finally:
        await conn.close()


@app.get("/api/v1/users/{user_id}/permissions")
async def get_user_permissions(user_id: int):
    conn = await get_conn()
    try:
        user = await conn.fetchrow(
            """SELECT u.user_id, u.full_name, u.role_id, r.role_name
                 FROM App_Users u
            LEFT JOIN Roles r ON r.role_id = u.role_id
                WHERE u.user_id = $1""",
            user_id,
        )
        if not user:
            raise HTTPException(404, "User not found")
        perms = await fetch_user_permissions(conn, user_id)
        return {**dict(user), "permissions": perms}
    finally:
        await conn.close()


@app.get("/api/v1/permissions")
async def list_permission_catalog():
    """Static list of every permission key the system recognises."""
    return {"permissions": [{"key": k, "description": d} for k, d in PERMISSION_CATALOG]}


# ---------- Admin: Users ----------
@app.get("/api/v1/admin/users")
async def admin_list_users(x_acting_user_id: Optional[str] = Header(None)):
    conn = await get_conn()
    try:
        await require_permission(conn, acting_user_id_from_header(x_acting_user_id), "manage_users")
        rows = await conn.fetch(
            """SELECT u.user_id, u.full_name, u.username, u.role_id, r.role_name, u.is_active, u.created_at
                 FROM App_Users u
            LEFT JOIN Roles r ON r.role_id = u.role_id
             ORDER BY u.user_id"""
        )
        return {"users": [dict(r) for r in rows]}
    finally:
        await conn.close()


@app.post("/api/v1/admin/users")
async def admin_create_user(payload: UserCreate, x_acting_user_id: Optional[str] = Header(None)):
    conn = await get_conn()
    try:
        await require_permission(conn, acting_user_id_from_header(x_acting_user_id), "manage_users")
        try:
            row = await conn.fetchrow(
                """INSERT INTO App_Users (full_name, username, hashed_password, role_id, is_active)
                   VALUES ($1, $2, 'temp_hash_123', $3, $4)
                RETURNING user_id, full_name, username, role_id, is_active""",
                payload.full_name.strip(), payload.username.strip(),
                payload.role_id, payload.is_active,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, f"Username '{payload.username}' already exists")
        return {"status": "ok", "user": dict(row)}
    finally:
        await conn.close()


@app.put("/api/v1/admin/users/{user_id}")
async def admin_update_user(user_id: int, payload: UserUpdate,
                            x_acting_user_id: Optional[str] = Header(None)):
    conn = await get_conn()
    try:
        await require_permission(conn, acting_user_id_from_header(x_acting_user_id), "manage_users")
        # Build a dynamic SET clause so missing fields are ignored
        sets, vals = [], []
        if payload.full_name is not None:
            sets.append(f"full_name = ${len(vals) + 1}")
            vals.append(payload.full_name.strip())
        if payload.role_id is not None:
            sets.append(f"role_id = ${len(vals) + 1}")
            vals.append(payload.role_id)
        if payload.is_active is not None:
            sets.append(f"is_active = ${len(vals) + 1}")
            vals.append(payload.is_active)
        if not sets:
            raise HTTPException(400, "No fields to update")
        vals.append(user_id)
        q = f"UPDATE App_Users SET {', '.join(sets)} WHERE user_id = ${len(vals)} RETURNING user_id"
        row = await conn.fetchrow(q, *vals)
        if not row:
            raise HTTPException(404, "User not found")
        return {"status": "ok", "user_id": row["user_id"]}
    finally:
        await conn.close()


@app.delete("/api/v1/admin/users/{user_id}")
async def admin_deactivate_user(user_id: int, x_acting_user_id: Optional[str] = Header(None)):
    """Soft delete: just sets is_active = FALSE so audit trails stay intact."""
    conn = await get_conn()
    try:
        acting = acting_user_id_from_header(x_acting_user_id)
        await require_permission(conn, acting, "manage_users")
        if acting == user_id:
            raise HTTPException(400, "You cannot deactivate yourself")
        row = await conn.fetchrow(
            "UPDATE App_Users SET is_active = FALSE WHERE user_id = $1 RETURNING user_id",
            user_id,
        )
        if not row:
            raise HTTPException(404, "User not found")
        return {"status": "ok"}
    finally:
        await conn.close()


# ---------- Admin: Roles & Permissions ----------
@app.get("/api/v1/admin/roles")
async def admin_list_roles(x_acting_user_id: Optional[str] = Header(None)):
    conn = await get_conn()
    try:
        await require_permission(conn, acting_user_id_from_header(x_acting_user_id), "manage_users")
        roles = await conn.fetch("SELECT role_id, role_name, description FROM Roles ORDER BY role_id")
        out = []
        for r in roles:
            perms = await conn.fetch(
                "SELECT permission_key FROM Role_Permissions WHERE role_id = $1 ORDER BY permission_key",
                r["role_id"],
            )
            out.append({**dict(r), "permissions": [p["permission_key"] for p in perms]})
        return {"roles": out}
    finally:
        await conn.close()


@app.post("/api/v1/admin/roles")
async def admin_create_role(payload: RoleCreate, x_acting_user_id: Optional[str] = Header(None)):
    valid_keys = {k for k, _ in PERMISSION_CATALOG}
    bad = [p for p in payload.permissions if p not in valid_keys]
    if bad:
        raise HTTPException(400, f"Unknown permission keys: {bad}")
    conn = await get_conn()
    try:
        await require_permission(conn, acting_user_id_from_header(x_acting_user_id), "manage_users")
        async with conn.transaction():
            try:
                row = await conn.fetchrow(
                    """INSERT INTO Roles (role_name, description) VALUES ($1, $2)
                    RETURNING role_id, role_name, description""",
                    payload.role_name.strip(), payload.description,
                )
            except asyncpg.UniqueViolationError:
                raise HTTPException(409, f"Role '{payload.role_name}' already exists")
            for key in payload.permissions:
                await conn.execute(
                    "INSERT INTO Role_Permissions (role_id, permission_key) VALUES ($1, $2)",
                    row["role_id"], key,
                )
        return {"status": "ok", "role": dict(row), "permissions": payload.permissions}
    finally:
        await conn.close()


@app.put("/api/v1/admin/roles/{role_id}/permissions")
async def admin_update_role_permissions(role_id: int, payload: RolePermissionsUpdate,
                                        x_acting_user_id: Optional[str] = Header(None)):
    valid_keys = {k for k, _ in PERMISSION_CATALOG}
    bad = [p for p in payload.permissions if p not in valid_keys]
    if bad:
        raise HTTPException(400, f"Unknown permission keys: {bad}")
    conn = await get_conn()
    try:
        await require_permission(conn, acting_user_id_from_header(x_acting_user_id), "manage_users")
        role = await conn.fetchrow("SELECT role_id FROM Roles WHERE role_id = $1", role_id)
        if not role:
            raise HTTPException(404, "Role not found")
        async with conn.transaction():
            await conn.execute("DELETE FROM Role_Permissions WHERE role_id = $1", role_id)
            for key in payload.permissions:
                await conn.execute(
                    "INSERT INTO Role_Permissions (role_id, permission_key) VALUES ($1, $2)",
                    role_id, key,
                )
        return {"status": "ok"}
    finally:
        await conn.close()


# ---------- Admin: Groups ----------
@app.get("/api/v1/admin/groups")
async def admin_list_groups(x_acting_user_id: Optional[str] = Header(None)):
    conn = await get_conn()
    try:
        await require_permission(conn, acting_user_id_from_header(x_acting_user_id), "manage_users")
        groups = await conn.fetch(
            "SELECT group_id, group_name, description, created_at FROM Groups ORDER BY group_id"
        )
        out = []
        for g in groups:
            members = await conn.fetch(
                """SELECT u.user_id, u.full_name, u.username
                     FROM User_Group_Members m
                     JOIN App_Users u ON u.user_id = m.user_id
                    WHERE m.group_id = $1
                 ORDER BY u.full_name""",
                g["group_id"],
            )
            out.append({**dict(g), "members": [dict(m) for m in members]})
        return {"groups": out}
    finally:
        await conn.close()


@app.post("/api/v1/admin/groups")
async def admin_create_group(payload: GroupCreate, x_acting_user_id: Optional[str] = Header(None)):
    conn = await get_conn()
    try:
        await require_permission(conn, acting_user_id_from_header(x_acting_user_id), "manage_users")
        async with conn.transaction():
            try:
                row = await conn.fetchrow(
                    """INSERT INTO Groups (group_name, description) VALUES ($1, $2)
                    RETURNING group_id, group_name, description""",
                    payload.group_name.strip(), payload.description,
                )
            except asyncpg.UniqueViolationError:
                raise HTTPException(409, f"Group '{payload.group_name}' already exists")
            for uid in payload.member_user_ids:
                await conn.execute(
                    "INSERT INTO User_Group_Members (user_id, group_id) VALUES ($1, $2)",
                    uid, row["group_id"],
                )
        return {"status": "ok", "group": dict(row)}
    finally:
        await conn.close()


@app.put("/api/v1/admin/groups/{group_id}/members")
async def admin_update_group_members(group_id: int, payload: GroupMembersUpdate,
                                     x_acting_user_id: Optional[str] = Header(None)):
    conn = await get_conn()
    try:
        await require_permission(conn, acting_user_id_from_header(x_acting_user_id), "manage_users")
        g = await conn.fetchrow("SELECT group_id FROM Groups WHERE group_id = $1", group_id)
        if not g:
            raise HTTPException(404, "Group not found")
        async with conn.transaction():
            await conn.execute("DELETE FROM User_Group_Members WHERE group_id = $1", group_id)
            for uid in payload.member_user_ids:
                await conn.execute(
                    "INSERT INTO User_Group_Members (user_id, group_id) VALUES ($1, $2)",
                    uid, group_id,
                )
        return {"status": "ok"}
    finally:
        await conn.close()


@app.delete("/api/v1/admin/groups/{group_id}")
async def admin_delete_group(group_id: int, x_acting_user_id: Optional[str] = Header(None)):
    conn = await get_conn()
    try:
        await require_permission(conn, acting_user_id_from_header(x_acting_user_id), "manage_users")
        row = await conn.fetchrow("DELETE FROM Groups WHERE group_id = $1 RETURNING group_id", group_id)
        if not row:
            raise HTTPException(404, "Group not found")
        return {"status": "ok"}
    finally:
        await conn.close()


# ============================================================
#  Endpoints - applications (largely unchanged from v0.3)
# ============================================================
ATTACHMENT_FIELDS = ("id_copy", "insurance", "photo", "legal_agreement")


def _validate_upload(upload: UploadFile) -> None:
    if upload.content_type:
        ok = (upload.content_type in ALLOWED_MIME_EXACT or
              any(upload.content_type.startswith(p) for p in ALLOWED_MIME_PREFIXES))
        if not ok:
            raise HTTPException(415, f"Unsupported file type: {upload.content_type}")


async def _save_upload(upload: UploadFile, app_id: int, attachment_type: str) -> dict:
    _validate_upload(upload)
    contents = await upload.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File '{upload.filename}' exceeds {MAX_UPLOAD_BYTES // (1024*1024)}MB limit")
    app_dir = UPLOADS_DIR / f"app_{app_id}"
    app_dir.mkdir(exist_ok=True)
    original_name = Path(upload.filename or "file").name
    ext = Path(original_name).suffix.lower()
    stored_name = f"{attachment_type}_{uuid.uuid4().hex[:12]}{ext}"
    dest_path = app_dir / stored_name
    with open(dest_path, "wb") as f:
        f.write(contents)
    rel_path = dest_path.relative_to(UPLOADS_DIR).as_posix()
    return {
        "file_path": rel_path,
        "original_filename": original_name,
        "mime_type": upload.content_type,
        "file_size_bytes": len(contents),
    }


@app.post("/api/v1/applications/submit")
async def submit_application(
    full_name:               str  = Form(...),
    job_title:               str  = Form(...),
    employee_number:         str  = Form(...),
    organization:            str  = Form(...),
    subcontractor:           Optional[str] = Form(None),
    gov_id_type:             str  = Form(...),
    gov_id_number:           str  = Form(...),
    blood_type:              str  = Form(...),
    mobile_number:           str  = Form(...),
    whatsapp_number:         str  = Form(...),
    email:                   Optional[str] = Form(None),
    is_third_party_sponsor:  bool = Form(False),
    submitted_by_user_id:    int  = Form(...),
    id_copy:         Optional[UploadFile] = File(None),
    insurance:       Optional[UploadFile] = File(None),
    photo:           Optional[UploadFile] = File(None),
    legal_agreement: Optional[UploadFile] = File(None),
):
    conn = await get_conn()
    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                """INSERT INTO Site_Access_ID
                      (full_name, job_title, employee_number, organization,
                       subcontractor, gov_id_type, gov_id_number, blood_type,
                       mobile_number, whatsapp_number, email,
                       is_third_party_sponsor, submitted_by_user_id)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                RETURNING application_id, workflow_state""",
                full_name, job_title, employee_number, organization,
                subcontractor, gov_id_type, gov_id_number, blood_type,
                mobile_number, whatsapp_number, email,
                is_third_party_sponsor, submitted_by_user_id,
            )
            app_id = row["application_id"]
            uploads = {
                "id_copy":         id_copy,
                "insurance":       insurance,
                "photo":           photo,
                "legal_agreement": legal_agreement,
            }
            for kind, upload in uploads.items():
                if upload is None or upload.filename in (None, ""):
                    continue
                meta = await _save_upload(upload, app_id, kind)
                await conn.execute(
                    """INSERT INTO Attachments
                          (application_id, attachment_type, file_path, original_filename,
                           mime_type, file_size_bytes, uploaded_by_user_id)
                       VALUES ($1,$2,$3,$4,$5,$6,$7)""",
                    app_id, kind, meta["file_path"], meta["original_filename"],
                    meta["mime_type"], meta["file_size_bytes"], submitted_by_user_id,
                )
            await conn.execute(
                """INSERT INTO Workflow_History
                      (application_id, action_taken, previous_state, new_state,
                       acted_by_user_id, comments)
                   VALUES ($1, 'Submitted', 'Draft', $2, $3, $4)""",
                app_id, row["workflow_state"], submitted_by_user_id,
                "Application submitted",
            )
        return {"status": "ok", "application_id": app_id}
    finally:
        await conn.close()


@app.get("/api/v1/applications/pending")
async def list_pending():
    conn = await get_conn()
    try:
        rows = await conn.fetch(
            """SELECT application_id, full_name, organization, workflow_state, created_at
                 FROM Site_Access_ID
                WHERE workflow_state NOT IN ('Approved','Rejected','Cancelled')
             ORDER BY application_id DESC"""
        )
        return {"pending_applications": [dict(r) for r in rows]}
    finally:
        await conn.close()


@app.get("/api/v1/applications")
async def list_all_applications():
    """All applications regardless of state (newest first)."""
    conn = await get_conn()
    try:
        rows = await conn.fetch(
            """SELECT application_id, full_name, organization, workflow_state, created_at
                 FROM Site_Access_ID
             ORDER BY application_id DESC"""
        )
        return {"applications": [dict(r) for r in rows]}
    finally:
        await conn.close()


@app.get("/api/v1/applications/{application_id}")
async def get_application_full(application_id: int):
    conn = await get_conn()
    try:
        app_row = await conn.fetchrow(
            """SELECT s.*, u.full_name AS submitter_name
                 FROM Site_Access_ID s
            LEFT JOIN App_Users u ON u.user_id = s.submitted_by_user_id
                WHERE s.application_id = $1""",
            application_id,
        )
        if not app_row:
            raise HTTPException(404, "Application not found")
        history_rows = await conn.fetch(
            """SELECT h.*, u.full_name AS acted_by
                 FROM Workflow_History h
            LEFT JOIN App_Users u ON u.user_id = h.acted_by_user_id
                WHERE h.application_id = $1
             ORDER BY h.action_timestamp ASC""",
            application_id,
        )
        attach_rows = await conn.fetch(
            """SELECT attachment_id, attachment_type, original_filename,
                      mime_type, file_size_bytes, uploaded_at
                 FROM Attachments
                WHERE application_id = $1
             ORDER BY attachment_id ASC""",
            application_id,
        )
        notif_rows = await conn.fetch(
            """SELECT notification_id, channel, recipient, subject, body,
                      delivery_status, error_message, created_at, sent_at
                 FROM Notifications
                WHERE application_id = $1
             ORDER BY notification_id DESC""",
            application_id,
        )
        return {
            "application":   dict(app_row),
            "history":       [dict(r) for r in history_rows],
            "attachments":   [dict(r) for r in attach_rows],
            "notifications": [dict(r) for r in notif_rows],
        }
    finally:
        await conn.close()


@app.get("/api/v1/applications/{application_id}/attachment/{attachment_id}")
async def download_attachment(application_id: int, attachment_id: int):
    conn = await get_conn()
    try:
        row = await conn.fetchrow(
            """SELECT file_path, original_filename, mime_type
                 FROM Attachments
                WHERE attachment_id = $1 AND application_id = $2""",
            attachment_id, application_id,
        )
    finally:
        await conn.close()
    if not row:
        raise HTTPException(404, "Attachment not found")
    full_path = (UPLOADS_DIR / row["file_path"]).resolve()
    try:
        full_path.relative_to(UPLOADS_DIR.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid path")
    if not full_path.is_file():
        raise HTTPException(410, "File missing on disk")
    return FileResponse(
        full_path,
        media_type=row["mime_type"] or "application/octet-stream",
        filename=row["original_filename"],
    )


@app.put("/api/v1/applications/{application_id}/status")
async def update_status(application_id: int, payload: StatusUpdate):
    conn = await get_conn()
    try:
        async with conn.transaction():
            app_row = await conn.fetchrow(
                "SELECT * FROM Site_Access_ID WHERE application_id = $1",
                application_id,
            )
            if not app_row:
                raise HTTPException(404, "Application not found")
            current_state = app_row["workflow_state"]
            required = required_permission_for_state_change(current_state, payload.new_state)
            if required:
                perms = await fetch_user_permissions(conn, payload.acted_by_user_id)
                if required not in perms:
                    raise HTTPException(
                        403,
                        f"User lacks permission '{required}' to perform this action "
                        f"(transition: {current_state} -> {payload.new_state})",
                    )
            await conn.execute(
                "UPDATE Site_Access_ID SET workflow_state = $1 WHERE application_id = $2",
                payload.new_state, application_id,
            )
            await conn.execute(
                """INSERT INTO Workflow_History
                      (application_id, action_taken, previous_state, new_state,
                       acted_by_user_id, comments)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                application_id,
                "Reject" if payload.new_state == "Rejected" else "Approve",
                current_state, payload.new_state,
                payload.acted_by_user_id, payload.comments,
            )
            if payload.new_state == "Rejected":
                reason = payload.comments or "(no reason provided)"
                await _handle_rejection_notifications(conn, app_row, reason)
        return {"status": "ok", "application_id": application_id, "new_state": payload.new_state}
    finally:
        await conn.close()


@app.get("/api/v1/history")
async def list_history():
    conn = await get_conn()
    try:
        rows = await conn.fetch(
            """SELECT h.*, u.full_name AS acted_by
                 FROM Workflow_History h
            LEFT JOIN App_Users u ON u.user_id = h.acted_by_user_id
             ORDER BY h.action_timestamp DESC
                LIMIT 200"""
        )
        return {"history": [dict(r) for r in rows]}
    finally:
        await conn.close()


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
