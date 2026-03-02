#!/usr/bin/env python3
"""
Setup script for Autonomous Factory project with all FERs from forensic reports.
"""

import asyncio
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

# Project configuration
PROJECT_NAME = "Autonomous Factory"
PROJECT_DESCRIPTION = """Forensic analysis and remediation project for the Autonomous Factory codebase.
This project catalogs all Failure Event Records (FERs) from comprehensive security audits
to drive systematic improvements across the platform."""

def get_db_path():
    """Get the database path from config."""
    config_path = Path(__file__).parent / "config" / "factory_config.json"
    with open(config_path) as f:
        cfg = json.load(f)
    working_dir = Path(cfg["factory"]["working_dir"]).expanduser()
    return working_dir / "autonomous_factory" / "factory_state" / "factory.db"

def create_project(db_path):
    """Create the Autonomous Factory project."""
    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    # Create project
    conn.execute("""
        INSERT INTO projects (project_id, name, description, status, current_phase, project_path)
        VALUES (?, ?, ?, 'active', 0, ?)
    """, (project_id, PROJECT_NAME, PROJECT_DESCRIPTION, str(Path(db_path).parent / "projects" / PROJECT_NAME.lower().replace(" ", "_"))))
    
    conn.commit()
    print(f"✅ Created project: {project_id} - {PROJECT_NAME}")
    
    return project_id, conn

def add_fer(conn, project_id, fer_data):
    """Add a Failure Event Record as a DaC tag."""
    
    # Map FER severity to DaC tag type
    severity_map = {
        "CRITICAL": "TRAP",
        "HIGH": "HAL", 
        "MEDIUM": "DOM",
        "LOW": "SER"
    }
    
    tag_type = severity_map.get(fer_data["severity"], "ENV")
    
    conn.execute("""
        INSERT INTO dac_tags (task_id, tag_type, context, source_step, source_worker, 
                            project_id, project_type, phase, complexity, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
    """, (
        fer_data.get("fer_id", f"FER-{uuid.uuid4().hex[:6]}"),
        tag_type,
        fer_data["description"][:500],
        fer_data.get("file", "audit"),
        fer_data.get("source", "forensic_audit"),
        project_id,
        "web",
        1,  # Phase 1 - Blueprint
        "high" if fer_data["severity"] in ["CRITICAL", "HIGH"] else "low",
        datetime.now().isoformat()
    ))
    
    print(f"  Added {fer_data['severity']} FER: {fer_data.get('fer_id', 'UNKNOWN')}")

def add_learning_log(conn, project_id, fer_data):
    """Add FER to learning log for pattern recognition."""
    
    conn.execute("""
        INSERT INTO learning_log (project_id, bug_description, root_cause, fix_applied, 
                                 prevention_strategy, fixed_by, keywords, project_type, phase, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        project_id,
        fer_data["description"][:200],
        fer_data.get("root_cause", "Code audit finding"),
        "PENDING - See remediation plan",
        fer_data.get("prevention", "Implement input validation and security controls"),
        "forensic_audit",
        json.dumps([fer_data.get("fer_id", ""), fer_data["severity"], "security", "audit"]),
        "web",
        1,
        datetime.now().isoformat()
    ))

def main():
    """Main setup function."""
    print("=" * 60)
    print("AUTONOMOUS FACTORY PROJECT SETUP")
    print("=" * 60)
    
    db_path = get_db_path()
    print(f"Database: {db_path}")
    
    # Create project
    project_id, conn = create_project(db_path)
    
    # All FERs from forensic reports
    fers = [
        # CRITICAL FERs from BRUTAL audit
        {
            "fer_id": "FER-AF-003",
            "severity": "CRITICAL",
            "description": "Silent write request dropping on queue full - data loss possible",
            "file": "orchestration/database.py:837",
            "root_cause": "No backpressure mechanism implemented",
            "prevention": "Implement blocking put with timeout or backpressure signal"
        },
        {
            "fer_id": "FER-AF-005",
            "severity": "CRITICAL",
            "description": "Command injection via _build_command - user input passed to shell",
            "file": "workers/adapters.py:256",
            "root_cause": "User input passed directly to shell command",
            "prevention": "Use subprocess with shell=False and proper argument escaping"
        },
        {
            "fer_id": "FER-AF-006",
            "severity": "CRITICAL",
            "description": "SQL injection via f-string table name concatenation",
            "file": "orchestration/database.py:1063",
            "root_cause": "Table names concatenated directly into SQL without validation",
            "prevention": "Validate table/column names against whitelist"
        },
        {
            "fer_id": "CRIT-001",
            "severity": "CRITICAL",
            "description": "SQL Injection via f-string table names in INSERT/UPDATE/DELETE",
            "file": "orchestration/database.py:1063,1136,1144,1154",
            "root_cause": "Dynamic SQL generation without table name validation",
            "prevention": "Implement table name whitelist validation"
        },
        {
            "fer_id": "CRIT-002",
            "severity": "CRITICAL",
            "description": "Command injection in CLI worker adapter - user messages to subprocess",
            "file": "workers/adapters.py:155-162",
            "root_cause": "User chat messages passed directly to subprocess arguments",
            "prevention": "Sanitize inputs with shlex.quote()"
        },
        {
            "fer_id": "CRIT-003",
            "severity": "CRITICAL",
            "description": "Silent data loss when write queue is full",
            "file": "orchestration/database.py:837-841",
            "root_cause": "put_nowait with QueueFull exception silently drops data",
            "prevention": "Implement blocking queue with backpressure"
        },
        {
            "fer_id": "CRIT-004",
            "severity": "CRITICAL",
            "description": "CORS wildcard with credentials allows any origin",
            "file": "No_ai_detection/backend/main.py:13-19",
            "root_cause": "allow_origins=['*'] with allow_credentials=True",
            "prevention": "Restrict CORS to specific domains"
        },
        {
            "fer_id": "CRIT-005",
            "severity": "CRITICAL",
            "description": "Infinite loop without exit condition in broadcast",
            "file": "dashboard/dashboard_server.py:179",
            "root_cause": "while True with no shutdown condition",
            "prevention": "Add exit conditions and error recovery"
        },
        {
            "fer_id": "CRIT-006",
            "severity": "CRITICAL",
            "description": "Path traversal in project creation via name parameter",
            "file": "orchestration/master_orchestrator.py:953",
            "root_cause": "No validation on project_path construction",
            "prevention": "Validate and resolve paths with Path.resolve()"
        },
        {
            "fer_id": "CRIT-007",
            "severity": "CRITICAL",
            "description": "No authentication on dashboard API endpoints",
            "file": "dashboard/dashboard_server.py",
            "root_cause": "Missing auth middleware",
            "prevention": "Implement authentication and authorization"
        },
        
        # HIGH severity FERs
        {
            "fer_id": "HIGH-001",
            "severity": "HIGH",
            "description": "23 bare except blocks silently swallowing errors",
            "file": "Multiple files",
            "root_cause": "Use of bare 'except:' instead of specific exceptions",
            "prevention": "Replace with specific exception handling"
        },
        {
            "fer_id": "HIGH-002",
            "severity": "HIGH",
            "description": "SQL injection via dynamic WHERE clause building",
            "file": "orchestration/database.py:731,756,778,799,818",
            "root_cause": "Dynamic SQL clause construction from user filters",
            "prevention": "Validate filter parameters"
        },
        {
            "fer_id": "HIGH-003",
            "severity": "HIGH",
            "description": "Race condition in chat session switching",
            "file": "orchestration/master_orchestrator.py:225-261",
            "root_cause": "No locking on session state operations",
            "prevention": "Add asyncio.Lock() around session operations"
        },
        {
            "fer_id": "HIGH-004",
            "severity": "HIGH",
            "description": "Unbounded file growth in chat history (DoS vector)",
            "file": "orchestration/master_orchestrator.py:117-131",
            "root_cause": "No size limit on chat_history file writes",
            "prevention": "Implement size limits and rotation"
        },
        {
            "fer_id": "HIGH-005",
            "severity": "HIGH",
            "description": "Unsafe JSON deserialization without validation (28 instances)",
            "file": "Multiple files",
            "root_cause": "json.loads() without schema validation",
            "prevention": "Add JSON schema validation"
        },
        {
            "fer_id": "HIGH-006",
            "severity": "HIGH",
            "description": "Hardcoded security decisions per worker name",
            "file": "workers/adapters.py:131-142",
            "root_cause": "CLI-specific logic tied to worker names",
            "prevention": "Use configuration-driven approach"
        },
        {
            "fer_id": "HIGH-007",
            "severity": "HIGH",
            "description": "No rate limiting on LLM API calls",
            "file": "dashboard/dashboard_server.py",
            "root_cause": "Missing rate limiting middleware",
            "prevention": "Implement rate limiting"
        },
        {
            "fer_id": "HIGH-008",
            "severity": "HIGH",
            "description": "Process zombie creation under error conditions",
            "file": "workers/adapters.py:156-181",
            "root_cause": "Subprocess may not be properly cleaned up on exception",
            "prevention": "Use context managers for subprocess lifecycle"
        },
        {
            "fer_id": "HIGH-009",
            "severity": "HIGH",
            "description": "Missing input validation on WebSocket messages",
            "file": "dashboard/dashboard_server.py:214-299",
            "root_cause": "No schema validation on incoming JSON",
            "prevention": "Add JSON schema validation"
        },
        {
            "fer_id": "HIGH-010",
            "severity": "HIGH",
            "description": "TOCTOU race condition in file operations",
            "file": "orchestration/watchdog_state.py:78-81",
            "root_cause": "File write and rename not atomic",
            "prevention": "Use atomic file operations"
        },
        
        # MEDIUM severity FERs
        {
            "fer_id": "MED-001",
            "severity": "MEDIUM",
            "description": "Information disclosure in error messages",
            "file": "Multiple locations",
            "root_cause": "Error messages may leak sensitive info",
            "prevention": "Sanitize error messages for production"
        },
        {
            "fer_id": "MED-002",
            "severity": "MEDIUM",
            "description": "Weak randomness for session IDs (timestamp-based)",
            "file": "orchestration/master_orchestrator.py:76",
            "root_cause": "Predictable session ID generation",
            "prevention": "Use cryptographically secure random"
        },
        {
            "fer_id": "MED-003",
            "severity": "MEDIUM",
            "description": "No timeout on database read operations",
            "file": "orchestration/database.py",
            "root_cause": "Slow queries can block event loop",
            "prevention": "Add query timeouts"
        },
        {
            "fer_id": "MED-004",
            "severity": "MEDIUM",
            "description": "Memory leak in Phi3 Manager - unbounded queue",
            "file": "orchestration/phi3_manager.py:115-132",
            "root_cause": "No max size check on queue",
            "prevention": "Add maxsize to queue"
        },
        {
            "fer_id": "MED-005",
            "severity": "MEDIUM",
            "description": "Insecure deserialization pattern in process reaper",
            "file": "orchestration/process_reaper.py:96",
            "root_cause": "No validation of file content before parsing",
            "prevention": "Validate file content before json.loads"
        },
        
        # From gemini.md reports
        {
            "fer_id": "FER-CLI-001",
            "severity": "LOW",
            "description": "Unprotected json.loads in test file",
            "file": "tests/test_e2e_pipeline.py:972",
            "root_cause": "Test code doesn't handle corrupted rule files",
            "prevention": "Add try/except around json.loads in tests"
        },
        {
            "fer_id": "GOD-OBJECT-001",
            "severity": "HIGH",
            "description": "MasterOrchestrator is 1880 lines - God Object anti-pattern",
            "file": "orchestration/master_orchestrator.py",
            "root_cause": "Single class handles too many responsibilities",
            "prevention": "Refactor into smaller, focused classes"
        },
        {
            "fer_id": "DEPLOY-FRAGILE-001",
            "severity": "MEDIUM",
            "description": "Dashboard has 1200-line raw HTML string - deployment fragility",
            "file": "dashboard/dashboard_server.py",
            "root_cause": "UI embedded in Python code",
            "prevention": "Separate UI into template files"
        },
        {
            "fer_id": "ASYNC-GHOST-001",
            "severity": "MEDIUM",
            "description": "Unchecked asyncio.create_task calls bypass ProcessReaper",
            "file": "dashboard/dashboard_server.py:164,931",
            "root_cause": "Background tasks not tracked",
            "prevention": "Register all tasks with reaper"
        },
        {
            "fer_id": "ECU-SILENT-001",
            "severity": "HIGH",
            "description": "Silent error swallowing in ECU (Watchdog) - except: pass pattern",
            "file": "orchestration/master_watchdog.py:321",
            "root_cause": "Bare except hides structural decay",
            "prevention": "Use logger.exception instead of pass"
        }
    ]
    
    print("\n" + "=" * 60)
    print("ADDING FERs TO PROJECT")
    print("=" * 60)
    
    # Add all FERs
    for fer in fers:
        add_fer(conn, project_id, fer)
        add_learning_log(conn, project_id, fer)
    
    conn.commit()
    
    # Summary
    print("\n" + "=" * 60)
    print("SETUP COMPLETE")
    print("=" * 60)
    print(f"Project ID: {project_id}")
    print(f"Project Name: {PROJECT_NAME}")
    print(f"Total FERs added: {len(fers)}")
    
    # Count by severity
    critical = sum(1 for f in fers if f["severity"] == "CRITICAL")
    high = sum(1 for f in fers if f["severity"] == "HIGH")
    medium = sum(1 for f in fers if f["severity"] == "MEDIUM")
    low = sum(1 for f in fers if f["severity"] == "LOW")
    
    print(f"\nBreakdown:")
    print(f"  CRITICAL: {critical}")
    print(f"  HIGH: {high}")
    print(f"  MEDIUM: {medium}")
    print(f"  LOW: {low}")
    
    conn.close()
    print("\n✅ Project ready for remediation work!")

if __name__ == "__main__":
    main()
