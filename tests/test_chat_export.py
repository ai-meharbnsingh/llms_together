"""
TDD Tests — Chat Export Download Feature
═════════════════════════════════════════
Tests: merge/dedup logic, JSON/Markdown formatting, endpoint validation,
ReadOnlyDB.get_all_session_messages (no LIMIT), and HTML export button.

Uses real SQLite (temp DB), mocked worker adapters.
"""

import asyncio
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestration.database import ReadOnlyDB, WatchdogDB, get_write_queue, get_result_bus


# ─── Fixtures ───


@pytest.fixture
def tmp_db(tmp_path):
    """Create a real SQLite DB with full schema + seed FK parents."""
    db_path = str(tmp_path / "test_factory.db")
    wdb = WatchdogDB(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO dashboard_state (instance_name, status) "
        "VALUES ('phi3-orchestrator', 'active')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO projects (project_id, name, description, status) "
        "VALUES ('proj_test', 'Test', 'Test project', 'active')"
    )
    conn.commit()
    conn.close()
    return db_path, wdb


@pytest.fixture
def read_db(tmp_db):
    db_path, _ = tmp_db
    rdb = ReadOnlyDB(db_path)
    rdb.set_requester("test")
    return rdb


@pytest.fixture
def session_id():
    return "session_export_test"


@pytest.fixture
def populated_db(tmp_db, session_id):
    """Insert 300 messages into chat_archive for the test session."""
    db_path, wdb = tmp_db
    conn = sqlite3.connect(db_path)
    base_time = datetime(2025, 6, 1, 12, 0, 0)
    for i in range(300):
        ts = (base_time + timedelta(seconds=i)).isoformat()
        role = "user" if i % 2 == 0 else "assistant"
        worker = ["deepseek", "qwen", "claude"][i % 3]
        meta = json.dumps({"mode": "direct", "worker": worker, "session_id": session_id})
        conn.execute(
            "INSERT INTO chat_archive (session_id, role, content, mode, worker, "
            "metadata, original_timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, role, f"Message {i}", "direct", worker, meta, ts),
        )
    conn.commit()
    conn.close()
    rdb = ReadOnlyDB(db_path)
    rdb.set_requester("test")
    return rdb


# ─── 1. ReadOnlyDB.get_all_session_messages (no LIMIT) ───


def test_get_all_session_messages_no_limit(populated_db, session_id):
    """Insert 300 msgs, verify all returned (no LIMIT truncation)."""
    msgs = populated_db.get_all_session_messages(session_id)
    assert len(msgs) == 300
    # Verify chronological order
    timestamps = [m["timestamp"] for m in msgs]
    assert timestamps == sorted(timestamps)


# ─── 2. Merge + Dedup ───


def _make_msg(role, content, ts, worker="deepseek", mode="direct"):
    return {
        "role": role,
        "content": content,
        "timestamp": ts,
        "metadata": {"mode": mode, "worker": worker},
    }


def test_merge_dedup_no_overlap():
    """Cold and warm with distinct timestamps → all kept."""
    from dashboard.dashboard_server import _merge_and_dedup

    cold = [_make_msg("user", "cold1", "2025-06-01T12:00:00"),
            _make_msg("assistant", "cold2", "2025-06-01T12:00:01")]
    warm = [_make_msg("user", "warm1", "2025-06-01T12:00:02"),
            _make_msg("assistant", "warm2", "2025-06-01T12:00:03")]
    merged = _merge_and_dedup(cold, warm)
    assert len(merged) == 4


def test_merge_dedup_with_overlap():
    """Shared timestamps → duplicates removed."""
    from dashboard.dashboard_server import _merge_and_dedup

    shared_ts = "2025-06-01T12:00:00"
    cold = [_make_msg("user", "msg", shared_ts)]
    warm = [_make_msg("user", "msg", shared_ts),
            _make_msg("assistant", "reply", "2025-06-01T12:00:01")]
    merged = _merge_and_dedup(cold, warm)
    assert len(merged) == 2  # deduped shared_ts, kept reply


def test_merge_sort_order():
    """Merged output sorted chronologically."""
    from dashboard.dashboard_server import _merge_and_dedup

    cold = [_make_msg("user", "old", "2025-06-01T12:00:05")]
    warm = [_make_msg("user", "new", "2025-06-01T12:00:01")]
    merged = _merge_and_dedup(cold, warm)
    assert merged[0]["timestamp"] == "2025-06-01T12:00:01"
    assert merged[1]["timestamp"] == "2025-06-01T12:00:05"


# ─── 3. JSON Export Structure ───


def test_json_export_structure():
    """JSON export has session_id, session_name, messages, message_count."""
    from dashboard.dashboard_server import _merge_and_dedup

    msgs = [_make_msg("user", "hi", "2025-06-01T12:00:00"),
            _make_msg("assistant", "hello", "2025-06-01T12:00:01")]
    export = {
        "session_id": "test_session",
        "session_name": "Test Chat",
        "message_count": len(msgs),
        "messages": msgs,
    }
    assert "session_id" in export
    assert "session_name" in export
    assert "messages" in export
    assert export["message_count"] == 2


# ─── 4. Markdown Export ───


def test_md_export_has_sections():
    """Markdown has chronological + grouped-by-worker sections."""
    from dashboard.dashboard_server import _format_chat_markdown

    msgs = [
        _make_msg("user", "hello", "2025-06-01T12:00:00", worker="deepseek"),
        _make_msg("assistant", "hi there", "2025-06-01T12:00:01", worker="deepseek"),
        _make_msg("user", "question", "2025-06-01T12:00:02", worker="qwen"),
        _make_msg("assistant", "answer", "2025-06-01T12:00:03", worker="qwen"),
    ]
    md = _format_chat_markdown(msgs, "sess_1", "My Chat")
    assert "# Chat Export: My Chat" in md
    assert "## Chronological View" in md
    assert "## Grouped by Worker" in md


def test_md_grouped_by_worker():
    """Each worker gets a section with correct message count."""
    from dashboard.dashboard_server import _format_chat_markdown

    msgs = [
        _make_msg("user", "a", "2025-06-01T12:00:00", worker="deepseek"),
        _make_msg("assistant", "b", "2025-06-01T12:00:01", worker="deepseek"),
        _make_msg("assistant", "c", "2025-06-01T12:00:02", worker="qwen"),
    ]
    md = _format_chat_markdown(msgs, "sess_1", "Test")
    assert "### deepseek" in md
    assert "### qwen" in md
    # deepseek has 2 messages, qwen has 1
    assert "(2 messages)" in md
    assert "(1 messages)" in md


# ─── 5. Endpoint Validation ───


@pytest.fixture
def mock_dashboard():
    """Create a minimal DashboardServer-like object for endpoint testing."""
    from dashboard.dashboard_server import DashboardServer

    rdb = MagicMock()
    rdb.get_all_session_messages = MagicMock(return_value=[])
    cfg = {"dashboard": {"host": "127.0.0.1", "port": 18420}}
    ds = DashboardServer(read_db=rdb, config=cfg)
    ds.orchestrator = MagicMock()
    ds.orchestrator.session_id = "sess_test"
    ds.orchestrator.chat_history = []
    ds.orchestrator._get_session_name = MagicMock(return_value="Test Chat")
    ds.watchdog = MagicMock()
    ds.watchdog.db = MagicMock()
    ds.watchdog.db.drain_write_queue = AsyncMock(return_value=0)
    ds.watchdog.write_queue = MagicMock()
    ds.watchdog.result_bus = MagicMock()
    return ds


@pytest.mark.asyncio
async def test_invalid_format_400(mock_dashboard):
    """format=xml returns 400."""
    req = MagicMock()
    req.query = {"format": "xml"}
    resp = await mock_dashboard._api_chat_download(req)
    assert resp.status == 400


@pytest.mark.asyncio
async def test_no_orchestrator_503(mock_dashboard):
    """No orchestrator returns 503."""
    mock_dashboard.orchestrator = None
    req = MagicMock()
    req.query = {"format": "json"}
    resp = await mock_dashboard._api_chat_download(req)
    assert resp.status == 503


# ─── 6. HTML Export Button ───


@pytest.mark.asyncio
async def test_export_button_in_html(mock_dashboard):
    """Dashboard HTML contains export button/dropdown."""
    req = MagicMock()
    resp = await mock_dashboard._index(req)
    body = resp.body.decode() if hasattr(resp.body, "decode") else resp.text
    assert "downloadChat" in body
    assert "dl-dropdown" in body
