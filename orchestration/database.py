"""
Database Module - Autonomous Factory
═══════════════════════════════════════════════════════════════
CRITICAL DESIGN RULE: ONLY THE WATCHDOG WRITES TO THE DATABASE.
All other components (Orchestrator, Workers, Dashboard, Phi3)
send write requests via the message bus (asyncio.Queue).
The Watchdog drains the queue and executes writes in batches.
═══════════════════════════════════════════════════════════════
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("factory.database")

SCHEMA_VERSION = 2

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA cache_size = -64000;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    blueprint_version INTEGER DEFAULT 1,
    blueprint_approved_by TEXT,
    blueprint_approved_at DATETIME,
    current_phase INTEGER DEFAULT 0,
    status TEXT CHECK(status IN ('active','paused','completed','failed')),
    git_repo TEXT,
    project_path TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    phase INTEGER NOT NULL,
    module TEXT NOT NULL,
    description TEXT NOT NULL,
    complexity TEXT CHECK(complexity IN ('low','high')),
    task_file_path TEXT,
    assigned_to TEXT,
    assigned_at DATETIME,
    status TEXT CHECK(status IN ('pending','in_progress','testing','review','approved','blocked','failed')),
    current_step TEXT,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 2,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    worker TEXT NOT NULL,
    step TEXT NOT NULL,
    state_data TEXT,
    files_modified TEXT,
    tests_status TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS worker_health (
    worker_id TEXT PRIMARY KEY,
    worker_type TEXT NOT NULL,
    status TEXT CHECK(status IN ('healthy','degraded','crashed','offline')),
    last_heartbeat DATETIME,
    last_task_id TEXT,
    failure_count INTEGER DEFAULT 0,
    total_tasks_completed INTEGER DEFAULT 0,
    avg_response_time_ms INTEGER,
    pid INTEGER,
    FOREIGN KEY (last_task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS escalations (
    escalation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    escalation_type TEXT NOT NULL,
    escalated_by TEXT NOT NULL,
    escalation_reason TEXT NOT NULL,
    context_data TEXT,
    status TEXT CHECK(status IN ('pending','resolved','dismissed')),
    human_decision TEXT,
    resolved_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS quality_gates (
    gate_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    gate_type TEXT NOT NULL,
    passed BOOLEAN NOT NULL,
    confidence_score REAL,
    findings TEXT,
    executed_by TEXT NOT NULL,
    executed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS commits (
    commit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    git_commit_hash TEXT,
    branch TEXT NOT NULL,
    files_changed TEXT,
    conflict_detected BOOLEAN DEFAULT FALSE,
    human_reviewed BOOLEAN DEFAULT FALSE,
    merged BOOLEAN DEFAULT FALSE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS blueprint_revisions (
    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    changes_summary TEXT NOT NULL,
    blueprint_content TEXT NOT NULL,
    reason TEXT,
    approved_by TEXT,
    approved_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    UNIQUE(project_id, version)
);

CREATE TABLE IF NOT EXISTS training_data (
    training_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    bug_description TEXT NOT NULL,
    bug_context TEXT,
    solution TEXT NOT NULL,
    fixed_by TEXT NOT NULL,
    validated BOOLEAN DEFAULT FALSE,
    phase TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS phase_completions (
    completion_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    phase INTEGER NOT NULL,
    e2e_tests_passed BOOLEAN NOT NULL,
    test_results TEXT,
    human_uat_completed BOOLEAN DEFAULT FALSE,
    human_approved BOOLEAN DEFAULT FALSE,
    completed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    UNIQUE(project_id, phase)
);

CREATE TABLE IF NOT EXISTS decision_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    decision_type TEXT NOT NULL,
    decision_maker TEXT NOT NULL,
    decision TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    context TEXT,
    cost_estimate REAL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS dashboard_state (
    instance_name TEXT PRIMARY KEY,
    status TEXT CHECK(status IN ('active','idle','working','respawning','crashed')),
    current_task_id TEXT,
    context_usage_percent REAL,
    context_token_count INTEGER,
    max_context_tokens INTEGER,
    last_activity DATETIME,
    tasks_completed_today INTEGER DEFAULT 0,
    avg_task_duration_seconds REAL,
    FOREIGN KEY (current_task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS context_summaries (
    summary_id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_name TEXT NOT NULL,
    original_chat_ids TEXT NOT NULL,
    summary_text TEXT NOT NULL,
    keywords TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    token_count INTEGER,
    compression_ratio REAL,
    FOREIGN KEY (instance_name) REFERENCES dashboard_state(instance_name)
);

CREATE TABLE IF NOT EXISTS chat_summaries (
    chat_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    project_id TEXT,
    phase INTEGER,
    task_id TEXT,
    instance_name TEXT NOT NULL,
    parent_worker TEXT NOT NULL,
    user_query TEXT NOT NULL,
    llm_response_summary TEXT NOT NULL,
    full_llm_response TEXT,
    keywords TEXT,
    decisions_made TEXT,
    context_metadata TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_task ON chat_summaries(task_id);
CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_summaries(session_id);
CREATE INDEX IF NOT EXISTS idx_chat_timestamp ON chat_summaries(timestamp);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_escalations_status ON escalations(status);
CREATE INDEX IF NOT EXISTS idx_checkpoints_task ON checkpoints(task_id);
CREATE INDEX IF NOT EXISTS idx_context_instance ON context_summaries(instance_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_instance ON chat_summaries(instance_name);
CREATE INDEX IF NOT EXISTS idx_chat_keywords ON chat_summaries(keywords);
CREATE INDEX IF NOT EXISTS idx_chat_worker ON chat_summaries(parent_worker);

CREATE TABLE IF NOT EXISTS chat_archive (
    archive_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    mode TEXT,
    worker TEXT,
    project_id TEXT,
    metadata TEXT,
    original_timestamp TEXT NOT NULL,
    archived_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_archive_session ON chat_archive(session_id);
CREATE INDEX IF NOT EXISTS idx_archive_worker ON chat_archive(worker);
CREATE INDEX IF NOT EXISTS idx_archive_mode ON chat_archive(mode);
CREATE INDEX IF NOT EXISTS idx_archive_timestamp ON chat_archive(original_timestamp);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


# ═══════════════════════════════════════════════════════════════
# MESSAGE BUS: Write requests queued here by all components,
# drained ONLY by Watchdog.
# ═══════════════════════════════════════════════════════════════

class DBWriteRequest:
    """A single database write request."""
    __slots__ = ("operation", "table", "params", "callback_id", "timestamp", "requester")

    def __init__(self, operation: str, table: str, params: dict,
                 requester: str, callback_id: str = None):
        self.operation = operation      # 'insert', 'update', 'upsert', 'delete'
        self.table = table              # table name
        self.params = params            # column->value dict
        self.requester = requester      # who requested this write
        self.callback_id = callback_id  # optional: for awaiting result
        self.timestamp = time.time()

    def __repr__(self):
        return f"<DBWrite {self.operation} {self.table} by={self.requester}>"


class WriteResultBus:
    """Optional bus for components that need to await write confirmation."""
    def __init__(self):
        self._futures: Dict[str, asyncio.Future] = {}

    def create_waiter(self, callback_id: str) -> asyncio.Future:
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self._futures[callback_id] = fut
        return fut

    def resolve(self, callback_id: str, result: Any):
        fut = self._futures.pop(callback_id, None)
        if fut and not fut.done():
            fut.set_result(result)

    def reject(self, callback_id: str, error: Exception):
        fut = self._futures.pop(callback_id, None)
        if fut and not fut.done():
            fut.set_exception(error)


# Global write queue — shared across all components
_write_queue: asyncio.Queue = None
_result_bus: WriteResultBus = None


def get_write_queue() -> asyncio.Queue:
    global _write_queue
    if _write_queue is None:
        _write_queue = asyncio.Queue(maxsize=10000)
    return _write_queue


def get_result_bus() -> WriteResultBus:
    global _result_bus
    if _result_bus is None:
        _result_bus = WriteResultBus()
    return _result_bus


# ═══════════════════════════════════════════════════════════════
# READ-ONLY DATABASE HANDLE: Used by all non-Watchdog components.
# Can ONLY read. Write attempts raise an error.
# ═══════════════════════════════════════════════════════════════

class ReadOnlyDB:
    """
    Read-only database access for Orchestrator, Workers, Dashboard, Phi3.
    ALL writes go through the message bus -> Watchdog.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._requester = "unknown"

    def set_requester(self, name: str):
        """Set identity for write request attribution."""
        self._requester = name

    @contextmanager
    def _read_conn(self):
        """Read-only connection."""
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # --- Read Operations ---

    def get_project(self, project_id: str) -> Optional[dict]:
        with self._read_conn() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE project_id=?", (project_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_active_project(self) -> Optional[dict]:
        with self._read_conn() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE status='active' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def list_projects(self, include_completed: bool = False) -> list:
        """List projects. Default: active/paused only. With include_completed: all."""
        with self._read_conn() as conn:
            if include_completed:
                rows = conn.execute(
                    "SELECT project_id, name, status, current_phase, created_at "
                    "FROM projects ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT project_id, name, status, current_phase, created_at "
                    "FROM projects WHERE status IN ('active', 'paused') "
                    "ORDER BY created_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def get_session_messages(self, session_id: str, limit: int = 200) -> list:
        """Load archived messages for a session from chat_archive."""
        with self._read_conn() as conn:
            rows = conn.execute(
                "SELECT role, content, mode, worker, project_id, metadata, "
                "original_timestamp FROM chat_archive "
                "WHERE session_id=? ORDER BY original_timestamp ASC LIMIT ?",
                (session_id, limit)
            ).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                # Reconstruct chat_history format
                meta = {}
                if d.get("metadata"):
                    try:
                        meta = json.loads(d["metadata"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                results.append({
                    "role": d["role"],
                    "content": d["content"],
                    "timestamp": d["original_timestamp"],
                    "metadata": meta,
                })
            return results

    def get_all_session_messages(self, session_id: str) -> list:
        """Load ALL archived messages for a session (no LIMIT). Used for export."""
        with self._read_conn() as conn:
            rows = conn.execute(
                "SELECT role, content, mode, worker, project_id, metadata, "
                "original_timestamp FROM chat_archive "
                "WHERE session_id=? ORDER BY original_timestamp ASC",
                (session_id,)
            ).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                meta = {}
                if d.get("metadata"):
                    try:
                        meta = json.loads(d["metadata"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                results.append({
                    "role": d["role"],
                    "content": d["content"],
                    "timestamp": d["original_timestamp"],
                    "metadata": meta,
                })
            return results

    def get_task(self, task_id: str) -> Optional[dict]:
        with self._read_conn() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_tasks_by_status(self, status: str, project_id: str = None) -> list:
        with self._read_conn() as conn:
            if project_id:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status=? AND project_id=?",
                    (status, project_id)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status=?", (status,)
                ).fetchall()
            return [dict(r) for r in rows]

    def get_tasks_by_phase(self, project_id: str, phase: int) -> list:
        with self._read_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE project_id=? AND phase=?",
                (project_id, phase)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_last_checkpoint(self, task_id: str) -> Optional[dict]:
        with self._read_conn() as conn:
            row = conn.execute(
                "SELECT * FROM checkpoints WHERE task_id=? ORDER BY timestamp DESC LIMIT 1",
                (task_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_worker_health(self, worker_id: str) -> Optional[dict]:
        with self._read_conn() as conn:
            row = conn.execute(
                "SELECT * FROM worker_health WHERE worker_id=?", (worker_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_pending_escalations(self, limit: int = 10) -> list:
        with self._read_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM escalations WHERE status='pending' ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_dashboard_states(self) -> list:
        with self._read_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM dashboard_state ORDER BY instance_name"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_task_stats(self, project_id: str = None) -> dict:
        with self._read_conn() as conn:
            q = "SELECT status, COUNT(*) as count FROM tasks"
            p = ()
            if project_id:
                q += " WHERE project_id=?"
                p = (project_id,)
            q += " GROUP BY status"
            rows = conn.execute(q, p).fetchall()
            return {r["status"]: r["count"] for r in rows}

    def get_recent_activity(self, limit: int = 10) -> list:
        with self._read_conn() as conn:
            rows = conn.execute("""
                SELECT 'checkpoint' as type, task_id, worker as actor,
                       step as detail, timestamp FROM checkpoints
                UNION ALL
                SELECT 'escalation' as type, task_id, escalated_by as actor,
                       escalation_type as detail, created_at as timestamp FROM escalations
                UNION ALL
                SELECT 'quality_gate' as type, task_id, executed_by as actor,
                       gate_type as detail, executed_at as timestamp FROM quality_gates
                ORDER BY timestamp DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def get_stuck_tasks(self, timeout_minutes: int = 10) -> list:
        with self._read_conn() as conn:
            rows = conn.execute("""
                SELECT task_id, assigned_to, updated_at FROM tasks
                WHERE status='in_progress'
                AND updated_at < datetime('now', ?)
            """, (f"-{timeout_minutes} minutes",)).fetchall()
            return [dict(r) for r in rows]

    def get_latest_blueprint(self, project_id: str) -> Optional[dict]:
        with self._read_conn() as conn:
            row = conn.execute(
                "SELECT * FROM blueprint_revisions WHERE project_id=? ORDER BY version DESC LIMIT 1",
                (project_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_context_summary(self, instance_name: str) -> Optional[dict]:
        with self._read_conn() as conn:
            row = conn.execute("""
                SELECT summary_text, keywords, token_count, original_chat_ids
                FROM context_summaries WHERE instance_name=?
                ORDER BY summary_id DESC LIMIT 1
            """, (instance_name,)).fetchone()
            return dict(row) if row else None

    # --- Recall API ---

    def get_chat(self, chat_id: str) -> Optional[dict]:
        """Retrieve a single chat record by ID, including full_llm_response."""
        with self._read_conn() as conn:
            row = conn.execute(
                "SELECT * FROM chat_summaries WHERE chat_id=?", (chat_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_chats_by_session(self, session_id: str, limit: int = 100) -> list:
        """Retrieve all chats for a session, ordered by timestamp descending."""
        with self._read_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_summaries WHERE session_id=? "
                "ORDER BY timestamp DESC LIMIT ?",
                (session_id, limit)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_chats_by_ids(self, chat_ids: list) -> list:
        """Retrieve specific chats by a list of IDs. Preserves input order."""
        if not chat_ids:
            return []
        placeholders = ",".join("?" for _ in chat_ids)
        with self._read_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM chat_summaries WHERE chat_id IN ({placeholders})",
                chat_ids
            ).fetchall()
            result_map = {dict(r)["chat_id"]: dict(r) for r in rows}
            return [result_map[cid] for cid in chat_ids if cid in result_map]

    def get_doc(self, instance_name: str) -> Optional[dict]:
        """Get the latest Document of Context for an instance.
        Returns all columns with parsed JSON fields."""
        with self._read_conn() as conn:
            row = conn.execute(
                "SELECT * FROM context_summaries WHERE instance_name=? "
                "ORDER BY summary_id DESC LIMIT 1",
                (instance_name,)
            ).fetchone()
            if row:
                result = dict(row)
                if result.get("original_chat_ids"):
                    try:
                        result["original_chat_ids_parsed"] = json.loads(result["original_chat_ids"])
                    except (json.JSONDecodeError, TypeError):
                        result["original_chat_ids_parsed"] = []
                if result.get("keywords"):
                    try:
                        result["keywords_parsed"] = json.loads(result["keywords"])
                    except (json.JSONDecodeError, TypeError):
                        result["keywords_parsed"] = []
                return result
            return None

    def get_doc_history(self, instance_name: str, limit: int = 10) -> list:
        """Get historical DoC versions for an instance (audit/debugging)."""
        with self._read_conn() as conn:
            rows = conn.execute(
                "SELECT summary_id, instance_name, created_at, token_count, compression_ratio "
                "FROM context_summaries WHERE instance_name=? "
                "ORDER BY summary_id DESC LIMIT ?",
                (instance_name, limit)
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Cold Memory Search ---

    def search_chats_by_keyword(self, keyword: str, worker: str = None,
                                mode: str = None, limit: int = 50) -> list:
        """Search Phi3 chat_summaries by keyword in keywords/user_query/summary fields."""
        with self._read_conn() as conn:
            clauses = ["(keywords LIKE ? OR user_query LIKE ? OR llm_response_summary LIKE ?)"]
            params = [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]
            if worker:
                clauses.append("parent_worker = ?")
                params.append(worker)
            sql = (f"SELECT * FROM chat_summaries WHERE {' AND '.join(clauses)} "
                   f"ORDER BY timestamp DESC LIMIT ?")
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def search_archive(self, keyword: str = None, worker: str = None,
                       mode: str = None, session_id: str = None,
                       offset: int = 0, limit: int = 50) -> list:
        """Search raw chat_archive messages with optional filters."""
        with self._read_conn() as conn:
            clauses = []
            params = []
            if keyword:
                clauses.append("content LIKE ?")
                params.append(f"%{keyword}%")
            if worker:
                clauses.append("worker = ?")
                params.append(worker)
            if mode:
                clauses.append("mode = ?")
                params.append(mode)
            if session_id:
                clauses.append("session_id = ?")
                params.append(session_id)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            sql = (f"SELECT * FROM chat_archive {where} "
                   f"ORDER BY original_timestamp DESC LIMIT ? OFFSET ?")
            params.extend([limit, offset])
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def get_archive_count(self, keyword: str = None, worker: str = None,
                          mode: str = None) -> int:
        """Get total count of archived messages matching filters (for pagination)."""
        with self._read_conn() as conn:
            clauses = []
            params = []
            if keyword:
                clauses.append("content LIKE ?")
                params.append(f"%{keyword}%")
            if worker:
                clauses.append("worker = ?")
                params.append(worker)
            if mode:
                clauses.append("mode = ?")
                params.append(mode)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            row = conn.execute(f"SELECT COUNT(*) as cnt FROM chat_archive {where}", params).fetchone()
            return row["cnt"] if row else 0

    # --- Write Requests (queued to Watchdog) ---

    def request_write(self, operation: str, table: str, params: dict,
                      callback_id: str = None):
        """
        Submit a write request to the Watchdog's queue.
        Non-blocking. Returns immediately.
        """
        req = DBWriteRequest(
            operation=operation,
            table=table,
            params=params,
            requester=self._requester,
            callback_id=callback_id,
        )
        q = get_write_queue()
        try:
            q.put_nowait(req)
        except asyncio.QueueFull:
            logger.error(f"Write queue FULL! Dropping request: {req}")

    async def request_write_and_wait(self, operation: str, table: str,
                                      params: dict, timeout: float = 10.0) -> Any:
        """
        Submit write request and wait for Watchdog to confirm.
        Used when caller needs the result (e.g., auto-incremented ID).
        """
        import uuid
        cb_id = f"cb_{uuid.uuid4().hex[:8]}"
        bus = get_result_bus()
        fut = bus.create_waiter(cb_id)
        self.request_write(operation, table, params, callback_id=cb_id)
        return await asyncio.wait_for(fut, timeout=timeout)


# ═══════════════════════════════════════════════════════════════
# WRITE-CAPABLE DATABASE: ONLY instantiated by Watchdog.
# Contains all actual SQL write methods.
# ═══════════════════════════════════════════════════════════════

class WatchdogDB:
    """
    WRITE-capable database handle. ONLY the Watchdog may instantiate this.
    Also has read access for its own monitoring logic.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_schema()

    def _init_schema(self):
        with self._write_conn() as conn:
            conn.executescript(SCHEMA_SQL)
            cur = conn.execute("SELECT MAX(version) FROM schema_version")
            row = cur.fetchone()
            current_version = row[0] if row[0] is not None else 0
            if current_version == 0:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            elif current_version < 2:
                self._migrate_v1_to_v2(conn)
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (2,))
            conn.commit()
        logger.info(f"WatchdogDB initialized at {self.db_path}")

    def _migrate_v1_to_v2(self, conn):
        """Migrate v1 schema to v2: add chat_archive table + new indexes."""
        logger.info("Migrating schema v1 → v2: adding chat_archive table")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_archive (
                archive_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                mode TEXT,
                worker TEXT,
                project_id TEXT,
                metadata TEXT,
                original_timestamp TEXT NOT NULL,
                archived_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_archive_session ON chat_archive(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_archive_worker ON chat_archive(worker)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_archive_mode ON chat_archive(mode)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_archive_timestamp ON chat_archive(original_timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_keywords ON chat_summaries(keywords)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_worker ON chat_summaries(parent_worker)")
        logger.info("Schema migration v1 → v2 complete")

    @contextmanager
    def _write_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        except Exception as e:
            conn.rollback()
            logger.error(f"DB write error: {e}")
            raise
        finally:
            conn.close()

    @contextmanager
    def _read_conn(self):
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # --- Queue Drain: Called by Watchdog's write loop ---

    async def drain_write_queue(self, queue: asyncio.Queue,
                                 result_bus: WriteResultBus,
                                 batch_size: int = 50):
        """
        Drain pending write requests from the queue and execute them.
        Called periodically by Watchdog (every 5 seconds or on-demand).
        """
        writes: List[DBWriteRequest] = []
        while not queue.empty() and len(writes) < batch_size:
            try:
                req = queue.get_nowait()
                writes.append(req)
            except asyncio.QueueEmpty:
                break

        if not writes:
            return 0

        executed = 0
        with self._write_conn() as conn:
            for req in writes:
                try:
                    result = self._execute_write(conn, req)
                    executed += 1
                    if req.callback_id:
                        result_bus.resolve(req.callback_id, result)
                except Exception as e:
                    logger.error(f"Write failed: {req} -> {e}")
                    if req.callback_id:
                        result_bus.reject(req.callback_id, e)
            conn.commit()

        if executed > 0:
            logger.debug(f"Drained {executed} write(s) from queue")
        return executed

    def _execute_write(self, conn: sqlite3.Connection, req: DBWriteRequest) -> Any:
        """Execute a single write request. Returns lastrowid or rowcount."""
        table = req.table
        params = req.params
        op = req.operation

        if op == "insert":
            cols = ", ".join(params.keys())
            placeholders = ", ".join("?" for _ in params)
            sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
            cur = conn.execute(sql, list(params.values()))
            return cur.lastrowid

        elif op == "upsert":
            pk = params.pop("_pk", None)
            conflict_col = params.pop("_conflict", pk)
            cols = ", ".join(params.keys())
            placeholders = ", ".join("?" for _ in params)
            updates = ", ".join(f"{k}=excluded.{k}" for k in params if k != conflict_col)
            sql = (f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
                   f"ON CONFLICT({conflict_col}) DO UPDATE SET {updates}")
            cur = conn.execute(sql, list(params.values()))
            return cur.lastrowid

        elif op == "update":
            where = params.pop("_where", {})
            if not where:
                raise ValueError("UPDATE requires _where clause")
            sets = ", ".join(f"{k}=?" for k in params)
            where_clause = " AND ".join(f"{k}=?" for k in where)
            sql = f"UPDATE {table} SET {sets} WHERE {where_clause}"
            values = list(params.values()) + list(where.values())
            cur = conn.execute(sql, values)
            return cur.rowcount

        elif op == "delete":
            where = params.get("_where", {})
            if not where:
                raise ValueError("DELETE requires _where clause")
            where_clause = " AND ".join(f"{k}=?" for k in where)
            sql = f"DELETE FROM {table} WHERE {where_clause}"
            cur = conn.execute(sql, list(where.values()))
            return cur.rowcount

        elif op == "raw":
            sql = params.get("sql", "")
            args = params.get("args", [])
            cur = conn.execute(sql, args)
            return cur.lastrowid or cur.rowcount

        else:
            raise ValueError(f"Unknown operation: {op}")

    # --- Direct Write Methods (Watchdog internal use ONLY) ---

    def create_project(self, project_id, name, description="", git_repo=None, project_path=None):
        with self._write_conn() as conn:
            conn.execute(
                "INSERT INTO projects (project_id,name,description,status,git_repo,project_path) VALUES (?,?,?,'active',?,?)",
                (project_id, name, description, git_repo, project_path))
            conn.commit()

    def update_project(self, project_id, **kw):
        with self._write_conn() as conn:
            sets = ",".join(f"{k}=?" for k in kw)
            conn.execute(f"UPDATE projects SET {sets},updated_at=CURRENT_TIMESTAMP WHERE project_id=?",
                         list(kw.values()) + [project_id])
            conn.commit()

    def create_task(self, task_id, project_id, phase, module, description):
        with self._write_conn() as conn:
            conn.execute(
                "INSERT INTO tasks (task_id,project_id,phase,module,description,status) VALUES (?,?,?,?,?,'pending')",
                (task_id, project_id, phase, module, description))
            conn.commit()

    def update_task(self, task_id, **kw):
        with self._write_conn() as conn:
            sets = ",".join(f"{k}=?" for k in kw)
            conn.execute(f"UPDATE tasks SET {sets},updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
                         list(kw.values()) + [task_id])
            conn.commit()

    def save_checkpoint(self, task_id, worker, step, state_data=None, files_modified=None, tests_status=None):
        with self._write_conn() as conn:
            conn.execute(
                "INSERT INTO checkpoints (task_id,worker,step,state_data,files_modified,tests_status) VALUES (?,?,?,?,?,?)",
                (task_id, worker, step,
                 json.dumps(state_data) if state_data else None,
                 json.dumps(files_modified) if files_modified else None,
                 json.dumps(tests_status) if tests_status else None))
            conn.commit()

    def update_worker_health(self, worker_id, worker_type, status, pid=None, **kw):
        with self._write_conn() as conn:
            conn.execute("""
                INSERT INTO worker_health (worker_id,worker_type,status,last_heartbeat,pid)
                VALUES (?,?,?,CURRENT_TIMESTAMP,?)
                ON CONFLICT(worker_id) DO UPDATE SET
                status=excluded.status, last_heartbeat=CURRENT_TIMESTAMP,
                pid=COALESCE(excluded.pid, worker_health.pid)
            """, (worker_id, worker_type, status, pid))
            if kw:
                sets = ",".join(f"{k}=?" for k in kw)
                conn.execute(f"UPDATE worker_health SET {sets} WHERE worker_id=?",
                             list(kw.values()) + [worker_id])
            conn.commit()

    def create_escalation(self, task_id, escalation_type, escalated_by, reason, context_data=None):
        with self._write_conn() as conn:
            conn.execute(
                "INSERT INTO escalations (task_id,escalation_type,escalated_by,escalation_reason,context_data,status) VALUES (?,?,?,?,?,'pending')",
                (task_id, escalation_type, escalated_by, reason,
                 json.dumps(context_data) if context_data else None))
            conn.commit()

    def resolve_escalation(self, escalation_id, human_decision):
        with self._write_conn() as conn:
            conn.execute(
                "UPDATE escalations SET human_decision=?,status='resolved',resolved_at=CURRENT_TIMESTAMP WHERE escalation_id=?",
                (human_decision, escalation_id))
            conn.commit()

    def log_quality_gate(self, task_id, gate_type, passed, executed_by, confidence_score=None, findings=None):
        with self._write_conn() as conn:
            conn.execute(
                "INSERT INTO quality_gates (task_id,gate_type,passed,confidence_score,findings,executed_by) VALUES (?,?,?,?,?,?)",
                (task_id, gate_type, passed, confidence_score,
                 json.dumps(findings) if findings else None, executed_by))
            conn.commit()

    def save_blueprint(self, project_id, version, content, changes_summary, reason=None):
        with self._write_conn() as conn:
            conn.execute(
                "INSERT INTO blueprint_revisions (project_id,version,blueprint_content,changes_summary,reason) VALUES (?,?,?,?,?)",
                (project_id, version, content, changes_summary, reason))
            conn.commit()

    def approve_blueprint(self, project_id, version):
        with self._write_conn() as conn:
            conn.execute(
                "UPDATE blueprint_revisions SET approved_by='HUMAN',approved_at=CURRENT_TIMESTAMP WHERE project_id=? AND version=?",
                (project_id, version))
            conn.commit()

    def log_decision(self, decision_type, decision_maker, decision, reasoning,
                     task_id=None, context=None, cost_estimate=None):
        with self._write_conn() as conn:
            conn.execute(
                "INSERT INTO decision_logs (task_id,decision_type,decision_maker,decision,reasoning,context,cost_estimate) VALUES (?,?,?,?,?,?,?)",
                (task_id, decision_type, decision_maker, decision, reasoning,
                 json.dumps(context) if context else None, cost_estimate))
            conn.commit()

    def update_dashboard_state(self, instance_name, **kw):
        with self._write_conn() as conn:
            conn.execute("""
                INSERT INTO dashboard_state (instance_name,status,last_activity)
                VALUES (?,'idle',CURRENT_TIMESTAMP)
                ON CONFLICT(instance_name) DO NOTHING
            """, (instance_name,))
            if kw:
                sets = ",".join(f"{k}=?" for k in kw)
                conn.execute(
                    f"UPDATE dashboard_state SET {sets},last_activity=CURRENT_TIMESTAMP WHERE instance_name=?",
                    list(kw.values()) + [instance_name])
            conn.commit()

    def save_context_summary(self, instance_name, chat_ids, summary_text,
                              keywords=None, token_count=None, compression_ratio=None):
        with self._write_conn() as conn:
            conn.execute(
                "INSERT INTO context_summaries (instance_name,original_chat_ids,summary_text,keywords,token_count,compression_ratio) VALUES (?,?,?,?,?,?)",
                (instance_name, json.dumps(chat_ids), summary_text,
                 json.dumps(keywords) if keywords else None, token_count, compression_ratio))
            conn.commit()

    def save_chat_summary(self, chat_id, session_id, instance_name, parent_worker,
                           user_query, llm_response_summary, **kw):
        with self._write_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO chat_summaries
                (chat_id,session_id,instance_name,parent_worker,user_query,llm_response_summary,
                 project_id,phase,task_id,keywords,decisions_made,context_metadata)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (chat_id, session_id, instance_name, parent_worker, user_query,
                  llm_response_summary,
                  kw.get("project_id"), kw.get("phase"), kw.get("task_id"),
                  json.dumps(kw.get("keywords")) if kw.get("keywords") else None,
                  json.dumps(kw.get("decisions_made")) if kw.get("decisions_made") else None,
                  json.dumps(kw.get("context_metadata")) if kw.get("context_metadata") else None))
            conn.commit()

    def archive_chat_messages(self, messages: list):
        """Bulk insert raw chat messages into cold storage (chat_archive)."""
        if not messages:
            return
        with self._write_conn() as conn:
            for msg in messages:
                meta = msg.get("metadata") or {}
                conn.execute("""
                    INSERT INTO chat_archive
                    (session_id, role, content, mode, worker, project_id, metadata, original_timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    meta.get("session_id", "unknown"),
                    msg.get("role", "unknown"),
                    msg.get("content", ""),
                    meta.get("mode"),
                    meta.get("worker"),
                    meta.get("project_id"),
                    json.dumps(meta) if meta else None,
                    msg.get("timestamp", datetime.now().isoformat()),
                ))
            conn.commit()
        logger.info(f"Archived {len(messages)} messages to cold storage")

    def create_commit(self, task_id, branch, files_changed=None, git_commit_hash=None):
        with self._write_conn() as conn:
            conn.execute(
                "INSERT INTO commits (task_id,branch,files_changed,git_commit_hash) VALUES (?,?,?,?)",
                (task_id, branch,
                 json.dumps(files_changed) if files_changed else None, git_commit_hash))
            conn.commit()

    def complete_phase(self, project_id, phase, e2e_passed, test_results=None):
        with self._write_conn() as conn:
            conn.execute(
                "INSERT INTO phase_completions (project_id,phase,e2e_tests_passed,test_results) VALUES (?,?,?,?)",
                (project_id, phase, e2e_passed,
                 json.dumps(test_results) if test_results else None))
            conn.commit()

    def save_training_data(self, project_id, bug_description, solution, fixed_by,
                            bug_context=None, phase=None):
        with self._write_conn() as conn:
            conn.execute(
                "INSERT INTO training_data (project_id,bug_description,bug_context,solution,fixed_by,phase) VALUES (?,?,?,?,?,?)",
                (project_id, bug_description, bug_context, solution, fixed_by, phase))
            conn.commit()

    def integrity_check(self) -> bool:
        with self._read_conn() as conn:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            return result[0] == "ok"

    # --- Read methods (Watchdog also needs to read) ---

    def get_project(self, project_id):
        with self._read_conn() as conn:
            row = conn.execute("SELECT * FROM projects WHERE project_id=?", (project_id,)).fetchone()
            return dict(row) if row else None

    def get_active_project(self):
        with self._read_conn() as conn:
            row = conn.execute("SELECT * FROM projects WHERE status='active' ORDER BY created_at DESC LIMIT 1").fetchone()
            return dict(row) if row else None

    def list_projects(self, include_completed=False):
        with self._read_conn() as conn:
            if include_completed:
                rows = conn.execute(
                    "SELECT project_id, name, status, current_phase, created_at "
                    "FROM projects ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT project_id, name, status, current_phase, created_at "
                    "FROM projects WHERE status IN ('active', 'paused') "
                    "ORDER BY created_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def get_task(self, task_id):
        with self._read_conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def get_last_checkpoint(self, task_id):
        with self._read_conn() as conn:
            row = conn.execute(
                "SELECT * FROM checkpoints WHERE task_id=? ORDER BY timestamp DESC LIMIT 1",
                (task_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_stuck_tasks(self, timeout_minutes=10):
        with self._read_conn() as conn:
            rows = conn.execute("""
                SELECT task_id,assigned_to,updated_at FROM tasks
                WHERE status='in_progress' AND updated_at < datetime('now',?)
            """, (f"-{timeout_minutes} minutes",)).fetchall()
            return [dict(r) for r in rows]

    def get_pending_escalations(self, limit=10):
        with self._read_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM escalations WHERE status='pending' ORDER BY created_at DESC LIMIT ?",
                (limit,)).fetchall()
            return [dict(r) for r in rows]

    def get_all_dashboard_states(self):
        with self._read_conn() as conn:
            rows = conn.execute("SELECT * FROM dashboard_state ORDER BY instance_name").fetchall()
            return [dict(r) for r in rows]

    def get_task_stats(self, project_id=None):
        with self._read_conn() as conn:
            q = "SELECT status, COUNT(*) as count FROM tasks"
            p = ()
            if project_id:
                q += " WHERE project_id=?"
                p = (project_id,)
            q += " GROUP BY status"
            rows = conn.execute(q, p).fetchall()
            return {r["status"]: r["count"] for r in rows}

    def get_recent_activity(self, limit=10):
        with self._read_conn() as conn:
            rows = conn.execute("""
                SELECT 'checkpoint' as type, task_id, worker as actor,
                       step as detail, timestamp FROM checkpoints
                UNION ALL
                SELECT 'escalation', task_id, escalated_by,
                       escalation_type, created_at FROM escalations
                UNION ALL
                SELECT 'quality_gate', task_id, executed_by,
                       gate_type, executed_at FROM quality_gates
                ORDER BY timestamp DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
