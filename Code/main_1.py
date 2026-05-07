"""
Ta'awon P3 SMS - FastAPI Backend
================================
Single-file backend. Inline schema, endpoints, and DB connection logic.

Changes in this revision (28 Apr 2026):
  + Roles + Role_Permissions tables (privilege model)
  + Attachments table + multipart file upload support
  + Notifications table (email + WhatsApp logging)
  + Email-on-rejection via Gmail SMTP (env-driven)
  + WhatsApp-on-rejection logged to DB (real send pending Meta API)
  + GET /api/v1/applications/{id}  -- full record incl. history & attachments
  + GET /api/v1/applications/{id}/attachment/{attachment_id}  -- file download
  + GET /api/v1/users/{id}/permissions
  + DB password + SMTP credentials moved to .env (was hardcoded)

Backwards-compatible additive migrations: existing tables/data are preserved.
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
from fastapi import (FastAPI, File, Form, HTTPException, Path as FPath,
                     Request, UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# ============================================================
#  Configuration
# ============================================================
# Load .env from the project root (one level up from /Code)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(ENV_FILE)

# Database
DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", "5432"))
DB_NAME     = os.getenv("DB_NAME", "id_system")
DB_USER     = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "Omar123")  # fallback to legacy hardcode

# SMTP (Gmail by default)
SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")  # Gmail App Password (16 chars)
SMTP_FROM     = os.getenv("SMTP_FROM", SMTP_USERNAME)
SMTP_ENABLED  = bool(SMTP_USERNAME and SMTP_PASSWORD)

# File uploads
UPLOADS_DIR = PROJECT_ROOT / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB per file
ALLOWED_MIME_PREFIXES = ("image/",)
ALLOWED_MIME_EXACT = {"application/pdf"}

# ============================================================
#  App setup
# ============================================================
app = FastAPI(title="Ta'awon P3 SMS API", version="0.3.0")

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
-- ---------- App_Users (existing) ----------
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

-- ---------- Site_Access_ID (existing + new email column) ----------
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
    workflow_state          VARCHAR(50) DEFAULT 'Pending HR Verification',
    created_at              TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    submitted_by_user_id    INT REFERENCES App_Users(user_id)
);
ALTER TABLE Site_Access_ID ADD COLUMN IF NOT EXISTS email VARCHAR(150);

-- ---------- Workflow_History (existing) ----------
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

-- ---------- Roles (NEW) ----------
CREATE TABLE IF NOT EXISTS Roles (
    role_id      SERIAL PRIMARY KEY,
    role_name    VARCHAR(50) UNIQUE NOT NULL,
    description  VARCHAR(255)
);

-- ---------- Role_Permissions (NEW) ----------
-- Permission keys are simple strings. Current set:
--   submit_application       -- can submit a new ID application
--   approve_hr               -- can approve / reject at 'Pending HR Verification'
--   approve_security         -- can approve / reject at 'Pending Security Clearance'
--   view_all_applications    -- can see every application (not just own)
CREATE TABLE IF NOT EXISTS Role_Permissions (
    role_id          INT NOT NULL REFERENCES Roles(role_id) ON DELETE CASCADE,
    permission_key   VARCHAR(50) NOT NULL,
    PRIMARY KEY (role_id, permission_key)
);

-- ---------- Attachments (NEW) ----------
CREATE TABLE IF NOT EXISTS Attachments (
    attachment_id        SERIAL PRIMARY KEY,
    application_id       INT NOT NULL REFERENCES Site_Access_ID(application_id) ON DELETE CASCADE,
    attachment_type      VARCHAR(50) NOT NULL,    -- 'id_copy' | 'insurance' | 'photo' | 'legal_agreement'
    file_path            VARCHAR(500) NOT NULL,   -- relative to PROJECT_ROOT/uploads
    original_filename    VARCHAR(255) NOT NULL,
    mime_type            VARCHAR(100),
    file_size_bytes      BIGINT,
    uploaded_at          TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    uploaded_by_user_id  INT REFERENCES App_Users(user_id)
);

-- ---------- Notifications (NEW) ----------
CREATE TABLE IF NOT EXISTS Notifications (
    notification_id    SERIAL PRIMARY KEY,
    application_id     INT REFERENCES Site_Access_ID(application_id) ON DELETE CASCADE,
    channel            VARCHAR(20) NOT NULL,    -- 'email' | 'whatsapp'
    recipient          VARCHAR(150) NOT NULL,
    subject            VARCHAR(255),
    body               TEXT NOT NULL,
    delivery_status    VARCHAR(30) NOT NULL,    -- 'sent' | 'failed' | 'pending_whatsapp_api' | 'skipped_no_recipient'
    error_message      TEXT,
    created_at         TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    sent_at            TIMESTAMP WITH TIME ZONE
);
"""

# Seed data is idempotent: ON CONFLICT DO NOTHING for everything.
SEED_SQL = """
-- ---------- Seed Roles ----------
INSERT INTO Roles (role_id, role_name, description) VALUES
    (1, 'Submitter',         'Submits ID applications'),
    (2, 'HR Verification',   'Verifies HR details and forwards to Security'),
    (3, 'Security Clearance','Final approver after HR verification'),
    (4, 'Reviewer',          'General reviewer (role TBD)')
ON CONFLICT (role_id) DO NOTHING;

-- Make sure the SERIAL counter doesn't collide with the explicit IDs above
SELECT setval(pg_get_serial_sequence('roles','role_id'),
              GREATEST((SELECT MAX(role_id) FROM Roles), 1));

-- ---------- Seed Role_Permissions ----------
INSERT INTO Role_Permissions (role_id, permission_key) VALUES
    (1, 'submit_application'),
    (2, 'approve_hr'),
    (2, 'view_all_applications'),
    (3, 'approve_security'),
    (3, 'view_all_applications'),
    (4, 'view_all_applications')
ON CONFLICT (role_id, permission_key) DO NOTHING;

-- ---------- Seed App_Users ----------
INSERT INTO App_Users (user_id, full_name, username, hashed_password, role_id, force_password_change) VALUES
    (1, 'Badriah Rizq Allah',      'badriah.rizqallah',     'temp_hash_123', 1, TRUE),
    (2, 'Omar Abdulaziz Almaqhawi','omar.almaqhawi',        'temp_hash_123', 2, TRUE),
    (3, 'Abdulkader Abdullatif',   'abdulkader.abdullatif', 'temp_hash_123', 3, TRUE),
    (4, 'Hind Mana Alahmari',      'hind.alahmari',         'temp_hash_123', 4, TRUE)
ON CONFLICT (user_id) DO NOTHING;

SELECT setval(pg_get_serial_sequence('app_users','user_id'),
              GREATEST((SELECT MAX(user_id) FROM App_Users), 1));
"""


@app.post("/api/v1/admin/init-db")
async def init_db():
    conn = await get_conn()
    try:
        await conn.execute(SCHEMA_SQL)
        await conn.execute(SEED_SQL)
    finally:
        await conn.close()
    return {"status": "ok", "message": "Schema ensured and seeds applied (idempotent)."}


# ============================================================
#  Models
# ============================================================
class StatusUpdate(BaseModel):
    new_state: str
    acted_by_user_id: int
    comments: Optional[str] = None


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
    """Maps a state transition to the permission key required to perform it."""
    if current_state == "Pending HR Verification" and new_state in (
        "Pending Security Clearance", "Rejected"
    ):
        return "approve_hr"
    if current_state == "Pending Security Clearance" and new_state in (
        "Approved", "Rejected"
    ):
        return "approve_security"
    return None  # unknown / unrestricted transition


# ============================================================
#  Notification helpers (email + whatsapp)
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
    """Returns (success, error_message_or_none). Does NOT raise."""
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
    """Logs (and where possible sends) email + whatsapp messages for a rejected application.
    Never raises - notification failures must not break the workflow."""
    app_id   = app_record["application_id"]
    name     = app_record["full_name"]
    email    = app_record.get("email")
    whatsapp = app_record.get("whatsapp_number") or app_record.get("mobile_number")

    # ----- Email -----
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

    # ----- WhatsApp -----
    # Always logged; never sent (no Meta API yet). When the Meta API is wired
    # up, change 'pending_whatsapp_api' to 'sent' on actual delivery and stamp sent_at.
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
#  Endpoints
# ============================================================
@app.get("/")
async def root():
    return {
        "service": "Ta'awon P3 SMS API",
        "version": app.version,
        "smtp_enabled": SMTP_ENABLED,
    }


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


# ----------------------------------------------------------------
# Submit application (multipart - now accepts files)
# ----------------------------------------------------------------
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

    # Per-application subdirectory keeps things tidy
    app_dir = UPLOADS_DIR / f"app_{app_id}"
    app_dir.mkdir(exist_ok=True)

    # Build a safe unique name. We keep the original extension only.
    original_name = Path(upload.filename or "file").name
    ext = Path(original_name).suffix.lower()
    stored_name = f"{attachment_type}_{uuid.uuid4().hex[:12]}{ext}"
    dest_path = app_dir / stored_name

    with open(dest_path, "wb") as f:
        f.write(contents)

    # Store path RELATIVE to UPLOADS_DIR so the install can move freely.
    rel_path = dest_path.relative_to(UPLOADS_DIR).as_posix()
    return {
        "file_path": rel_path,
        "original_filename": original_name,
        "mime_type": upload.content_type,
        "file_size_bytes": len(contents),
    }


@app.post("/api/v1/applications/submit")
async def submit_application(
    # ---- form fields (mirroring xlsx spec where backend supports it) ----
    full_name:               str  = Form(...),
    job_title:               str  = Form(...),                 # Trade in Project
    employee_number:         str  = Form(...),
    organization:            str  = Form(...),                 # Company / Employer
    subcontractor:           Optional[str] = Form(None),
    gov_id_type:             str  = Form(...),
    gov_id_number:           str  = Form(...),
    blood_type:              str  = Form(...),
    mobile_number:           str  = Form(...),
    whatsapp_number:         str  = Form(...),
    email:                   Optional[str] = Form(None),
    is_third_party_sponsor:  bool = Form(False),
    submitted_by_user_id:    int  = Form(...),
    # ---- attachments (all optional at HTTP level; UI enforces requireds) ----
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

            # Save attachments
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

            # Initial history row
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


# ----------------------------------------------------------------
# List pending applications
# ----------------------------------------------------------------
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


# ----------------------------------------------------------------
# Get single application (full record + history + attachments)
# ----------------------------------------------------------------
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


# ----------------------------------------------------------------
# Download an attachment file
# ----------------------------------------------------------------
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
    # Path-traversal hardening: confirm we're still inside UPLOADS_DIR
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


# ----------------------------------------------------------------
# Update workflow status (with permission enforcement + notifications)
# ----------------------------------------------------------------
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

            # ---- Permission check ----
            required = required_permission_for_state_change(current_state, payload.new_state)
            if required:
                perms = await fetch_user_permissions(conn, payload.acted_by_user_id)
                if required not in perms:
                    raise HTTPException(
                        403,
                        f"User lacks permission '{required}' to perform this action "
                        f"(transition: {current_state} -> {payload.new_state})",
                    )

            # ---- Apply ----
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

            # ---- On rejection: send email + log whatsapp ----
            if payload.new_state == "Rejected":
                reason = payload.comments or "(no reason provided)"
                await _handle_rejection_notifications(conn, app_row, reason)

        return {"status": "ok", "application_id": application_id, "new_state": payload.new_state}
    finally:
        await conn.close()


# ----------------------------------------------------------------
# Workflow history (global)
# ----------------------------------------------------------------
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


# ----------------------------------------------------------------
# Catch-all error formatter (so the frontend always gets JSON)
# ----------------------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
