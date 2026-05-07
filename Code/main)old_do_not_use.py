from fastapi import FastAPI, HTTPException, status
import asyncpg
import os
from pydantic import BaseModel
from typing import Optional

# ==========================================
# 1. App Initialization & Database Settings
# ==========================================

app = FastAPI(
    title="Mega Project ID Management System",
    description="Backend API for Site Access Control and Workflow Management",
    version="1.0.0"
)

# IMPORTANT: Change "your_password" to the password you created for PostgreSQL
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "Omar123") 
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "id_system")

# Formulate the connection DSN
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}"

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins for local testing
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods (GET, POST, PUT, etc.)
    allow_headers=["*"],  # Allows all headers
)

# ==========================================
# 2. Data Models (Pydantic)
# ==========================================

# Model for Submitting a New Application
class IDApplicationSubmit(BaseModel):
    full_name: str
    job_title: str
    employee_number: str
    organization: str
    subcontractor: Optional[str] = None
    gov_id_type: str
    gov_id_number: str
    blood_type: str
    mobile_number: str
    whatsapp_number: str
    is_third_party_sponsor: bool = False
    submitted_by_user_id: int 

# Model for Approving/Rejecting an Application
class WorkflowStatusUpdate(BaseModel):
    new_state: str  # e.g., 'Pending Security Clearance', 'Approved', 'Rejected'
    acted_by_user_id: int 
    comments: Optional[str] = None 


# ==========================================
# 3. API Endpoints: System Administration
# ==========================================

@app.get("/")
async def root():
    return {"message": "ID Management System API is running."}


@app.post("/api/v1/admin/init-db", status_code=status.HTTP_201_CREATED)
async def initialize_database():
    """Executes the SQL scripts sequentially to build the database foundation."""
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        
        # 1. Set Timezone for Saudi Arabia
        await conn.execute("SET TIME ZONE 'Asia/Riyadh';")

        # 2. Create the Users Table First
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS App_Users (
                user_id SERIAL PRIMARY KEY,
                full_name VARCHAR(100) NOT NULL,
                username VARCHAR(50) UNIQUE NOT NULL,
                hashed_password VARCHAR(255) NOT NULL, 
                force_password_change BOOLEAN DEFAULT TRUE,
                role_id INT DEFAULT 1, 
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 3. Insert the initial users
        await conn.execute("""
            INSERT INTO App_Users (full_name, username, hashed_password, role_id) VALUES
            ('Omar Abdulaziz Almaqhawi', 'omar.almaqhawi', 'temp_hash_123', 2), 
            ('Abdulkader Abdullatif', 'abdulkader.abdullatif', 'temp_hash_123', 3), 
            ('Hind Mana Alahmari', 'hind.alahmari', 'temp_hash_123', 4), 
            ('Badriah Rizq Allah', 'badriah.rizqallah', 'temp_hash_123', 1)
            ON CONFLICT (username) DO NOTHING;
        """)

        # 4. Create the Main Application Table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS Site_Access_ID (
                application_id SERIAL PRIMARY KEY,
                full_name VARCHAR(100) NOT NULL,
                job_title VARCHAR(100) NOT NULL,
                employee_number VARCHAR(50) NOT NULL,
                organization VARCHAR(100) NOT NULL,
                subcontractor VARCHAR(100),
                gov_id_type VARCHAR(20) NOT NULL,
                gov_id_number VARCHAR(50) NOT NULL,
                blood_type VARCHAR(5) NOT NULL,
                mobile_number VARCHAR(20) NOT NULL,
                whatsapp_number VARCHAR(20) NOT NULL,
                is_third_party_sponsor BOOLEAN DEFAULT FALSE,
                workflow_state VARCHAR(50) DEFAULT 'Pending HR Verification',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                submitted_by_user_id INT REFERENCES App_Users(user_id)
            );
        """)

        # 5. Create the Workflow History Table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS Workflow_History (
                history_id SERIAL PRIMARY KEY,
                application_id INT NOT NULL, 
                action_taken VARCHAR(50) NOT NULL, 
                previous_state VARCHAR(50), 
                new_state VARCHAR(50) NOT NULL,
                acted_by_user_id INT REFERENCES App_Users(user_id), 
                action_timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                comments TEXT 
            );
        """)

        # 6. Create the Index
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_application_history ON Workflow_History(application_id);")

        await conn.close()
        return {"status": "success", "message": "Database initialized successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Initialization failed: {str(e)}")


@app.get("/api/v1/users")
async def get_all_users():
    """Fetches all registered users from the database."""
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        records = await conn.fetch("SELECT user_id, full_name, username, role_id, is_active, created_at FROM App_Users")
        await conn.close()
        return {"users": [dict(record) for record in records]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# 4. API Endpoints: ID Application Management
# ==========================================

@app.post("/api/v1/applications/submit", status_code=status.HTTP_201_CREATED)
async def submit_id_application(app_data: IDApplicationSubmit):
    """Receives a new ID application and initializes the Workflow History."""
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        
        insert_query = """
            INSERT INTO Site_Access_ID 
            (full_name, job_title, employee_number, organization, subcontractor, gov_id_type, gov_id_number, blood_type, mobile_number, whatsapp_number, is_third_party_sponsor, submitted_by_user_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            RETURNING application_id;
        """
        
        new_app_id = await conn.fetchval(
            insert_query, 
            app_data.full_name, app_data.job_title, app_data.employee_number, 
            app_data.organization, app_data.subcontractor, app_data.gov_id_type, 
            app_data.gov_id_number, app_data.blood_type, app_data.mobile_number, 
            app_data.whatsapp_number, app_data.is_third_party_sponsor, 
            app_data.submitted_by_user_id
        )

        history_query = """
            INSERT INTO Workflow_History
            (application_id, action_taken, previous_state, new_state, acted_by_user_id, comments)
            VALUES ($1, $2, $3, $4, $5, $6);
        """
        await conn.execute(
            history_query,
            new_app_id, 
            "Initial Submission", 
            "Draft", 
            "Pending HR Verification", 
            app_data.submitted_by_user_id,
            "Application submitted successfully via web form."
        )

        await conn.close()
        return {"status": "success", "application_id": new_app_id}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Submission failed: {str(e)}")


@app.get("/api/v1/applications/pending")
async def get_pending_applications():
    """Fetches all applications awaiting HR or Security review."""
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        
        query = """
            SELECT application_id, full_name, job_title, organization, workflow_state, created_at 
            FROM Site_Access_ID
            WHERE workflow_state NOT IN ('Approved', 'Rejected', 'Cancelled')
            ORDER BY created_at DESC;
        """
        records = await conn.fetch(query)
        await conn.close()
        
        return {"pending_applications": [dict(record) for record in records]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/v1/applications/{application_id}/status")
async def update_application_status(application_id: int, update_data: WorkflowStatusUpdate):
    """Updates the state of an application and logs the action."""
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        
        current_state = await conn.fetchval(
            "SELECT workflow_state FROM Site_Access_ID WHERE application_id = $1", 
            application_id
        )
        
        if not current_state:
            await conn.close()
            raise HTTPException(status_code=404, detail="Application not found")

        await conn.execute(
            "UPDATE Site_Access_ID SET workflow_state = $1 WHERE application_id = $2",
            update_data.new_state, application_id
        )

        history_query = """
            INSERT INTO Workflow_History
            (application_id, action_taken, previous_state, new_state, acted_by_user_id, comments)
            VALUES ($1, $2, $3, $4, $5, $6);
        """
        await conn.execute(
            history_query,
            application_id, 
            "Status Update", 
            current_state, 
            update_data.new_state, 
            update_data.acted_by_user_id,
            update_data.comments
        )

        await conn.close()
        return {"status": "success", "message": f"Application {application_id} moved to {update_data.new_state}"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Update failed: {str(e)}")


# ==========================================
# 5. API Endpoints: Workflow History (Audit Log)
# ==========================================

@app.get("/api/v1/history")
async def get_workflow_history(limit: int = 100):
    """
    Fetches the workflow audit log, joined with App_Users to return the
    actor's full name as 'acted_by'. Ordered newest first.
    
    The frontend (index.html -> fetchHistory) expects:
      { "history": [ { history_id, application_id, action_taken,
                       previous_state, new_state, acted_by, comments,
                       action_timestamp }, ... ] }
    """
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        
        query = """
            SELECT 
                wh.history_id,
                wh.application_id,
                wh.action_taken,
                wh.previous_state,
                wh.new_state,
                COALESCE(au.full_name, 'Unknown User') AS acted_by,
                wh.action_timestamp,
                wh.comments
            FROM Workflow_History wh
            LEFT JOIN App_Users au ON wh.acted_by_user_id = au.user_id
            ORDER BY wh.action_timestamp DESC
            LIMIT $1;
        """
        records = await conn.fetch(query, limit)
        await conn.close()
        
        return {"history": [dict(record) for record in records]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"History fetch failed: {str(e)}")
