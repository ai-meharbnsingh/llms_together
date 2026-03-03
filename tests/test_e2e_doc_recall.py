"""
E2E TDD Tests — DoC + Full Chat Persistence + Recall API
═══════════════════════════════════════════════════════════
Tests the full flow: DB Recall API → Full Chat Persistence →
DoC Builder → Orchestrator Recovery → Watchdog Recall.

Uses real SQLite (temp DB), mocked Ollama API.
"""

import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Fix imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestration.database import (
    ReadOnlyDB,
    WatchdogDB,
    get_write_queue,
    get_result_bus,
)
from orchestration.phi3_manager import (
    Phi3Instance,
    Phi3Manager,
    DOC_INITIAL_TEMPLATE,
    DOC_UPDATE_PROMPT,
)
from orchestration.master_orchestrator import MasterOrchestrator
from orchestration.role_router import RoleRouter


# ─── Fixtures ───


@pytest.fixture
def tmp_db(tmp_path):
    """Create a real SQLite DB with full schema + seed FK parents."""
    db_path = str(tmp_path / "test_factory.db")
    wdb = WatchdogDB(db_path)
    # Drain any stale writes from other test modules (global queue pollution)
    q = get_write_queue()
    while not q.empty():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            break
    # Seed FK parents: dashboard_state for context_summaries FK
    import sqlite3
    conn = sqlite3.connect(db_path)
    for name in ("orchestrator", "phi3-orchestrator", "phi3-claude",
                 "deepseek", "qwen", "claude", "kimi", "gemini"):
        conn.execute(
            "INSERT OR IGNORE INTO dashboard_state (instance_name, status) "
            "VALUES (?, 'active')", (name,))
    # Seed project + tasks for chat_summaries FK
    conn.execute("INSERT OR IGNORE INTO projects (project_id, name, description, status) VALUES ('proj_test', 'Test', 'Test project', 'active')")
    for i in range(5):
        conn.execute(f"INSERT OR IGNORE INTO tasks (task_id, project_id, phase, module, description, status) VALUES ('task_{i:03d}', 'proj_test', 1, 'backend', 'task {i}', 'pending')")
    conn.commit()
    conn.close()
    return db_path, wdb


@pytest.fixture
def read_db(tmp_db):
    """ReadOnlyDB pointing at the test DB."""
    db_path, _ = tmp_db
    rdb = ReadOnlyDB(db_path)
    rdb.set_requester("test")
    return rdb


@pytest.fixture
def watchdog_db(tmp_db):
    """WatchdogDB with write access."""
    _, wdb = tmp_db
    return wdb


@pytest.fixture
def sample_chat_rows(watchdog_db):
    """Insert 5 sample chat rows and return their IDs."""
    chat_ids = []
    for i in range(5):
        cid = f"chat_test_{i:04d}"
        watchdog_db.save_chat_summary(
            chat_id=cid,
            session_id="session_test_001",
            instance_name="phi3-orchestrator",
            parent_worker="orchestrator",
            user_query=f"User question number {i} with full detail and context",
            llm_response_summary=f"Summary of response {i}",
            project_id="proj_test",
            phase=1,
            task_id=f"task_{i:03d}",
            keywords=["python", "test", f"keyword_{i}"],
            decisions_made=[f"decision_{i}"],
        )
        chat_ids.append(cid)
    return chat_ids


class MockAiohttpResponse:
    """Proper async context manager mock for aiohttp response."""
    def __init__(self, text):
        self.status = 200
        self._text = text

    async def json(self):
        return {"response": self._text}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _make_ollama_response(text):
    """Helper: build a mock aiohttp response for Ollama."""
    return MockAiohttpResponse(text)


def _make_mock_session(responses):
    """Helper: build a mock session that returns responses in order.
    responses: list of text strings for Ollama responses.
    """
    call_count = 0

    def mock_post(url, **kwargs):
        nonlocal call_count
        idx = min(call_count, len(responses) - 1)
        call_count += 1
        return MockAiohttpResponse(responses[idx])

    session = MagicMock()
    session.closed = False
    session.post = mock_post
    return session


# ═══════════════════════════════════════════════════
# TEST 1: ReadOnlyDB Recall API
# ═══════════════════════════════════════════════════


class TestRecallAPI:
    """Tests for the 5 new ReadOnlyDB recall methods."""

    def test_get_chat_found(self, read_db, sample_chat_rows):
        """get_chat returns full record for existing chat_id."""
        result = read_db.get_chat("chat_test_0002")
        assert result is not None
        assert result["chat_id"] == "chat_test_0002"
        assert result["session_id"] == "session_test_001"
        assert result["parent_worker"] == "orchestrator"
        assert "question number 2" in result["user_query"]

    def test_get_chat_not_found(self, read_db, sample_chat_rows):
        """get_chat returns None for missing chat_id."""
        result = read_db.get_chat("chat_nonexistent")
        assert result is None

    def test_get_chats_by_session(self, read_db, sample_chat_rows):
        """get_chats_by_session returns all chats for a session."""
        results = read_db.get_chats_by_session("session_test_001")
        assert len(results) == 5
        # All 5 returned (ordering may vary within same second)
        ids = {r["chat_id"] for r in results}
        assert ids == set(sample_chat_rows)

    def test_get_chats_by_session_with_limit(self, read_db, sample_chat_rows):
        """get_chats_by_session respects limit parameter."""
        results = read_db.get_chats_by_session("session_test_001", limit=2)
        assert len(results) == 2

    def test_get_chats_by_session_empty(self, read_db, sample_chat_rows):
        """get_chats_by_session returns empty list for unknown session."""
        results = read_db.get_chats_by_session("session_nonexistent")
        assert results == []

    def test_get_chats_by_ids(self, read_db, sample_chat_rows):
        """get_chats_by_ids returns specific chats, preserves order."""
        ids = ["chat_test_0003", "chat_test_0001", "chat_test_0004"]
        results = read_db.get_chats_by_ids(ids)
        assert len(results) == 3
        assert results[0]["chat_id"] == "chat_test_0003"
        assert results[1]["chat_id"] == "chat_test_0001"
        assert results[2]["chat_id"] == "chat_test_0004"

    def test_get_chats_by_ids_partial(self, read_db, sample_chat_rows):
        """get_chats_by_ids skips missing IDs gracefully."""
        ids = ["chat_test_0000", "chat_missing", "chat_test_0002"]
        results = read_db.get_chats_by_ids(ids)
        assert len(results) == 2
        assert results[0]["chat_id"] == "chat_test_0000"
        assert results[1]["chat_id"] == "chat_test_0002"

    def test_get_chats_by_ids_empty(self, read_db):
        """get_chats_by_ids with empty list returns empty."""
        assert read_db.get_chats_by_ids([]) == []

    def test_get_doc_none(self, read_db):
        """get_doc returns None when no DoC exists."""
        result = read_db.get_doc("phi3-orchestrator")
        assert result is None

    def test_get_doc_found(self, read_db, watchdog_db):
        """get_doc returns latest DoC with parsed JSON fields."""
        watchdog_db.save_context_summary(
            instance_name="phi3-orchestrator",
            chat_ids=["chat_001", "chat_002"],
            summary_text="## DECISIONS MADE\n- Use PostgreSQL",
            keywords=["postgres", "db"],
            token_count=150,
            compression_ratio=0.3,
        )
        result = read_db.get_doc("phi3-orchestrator")
        assert result is not None
        assert "DECISIONS MADE" in result["summary_text"]
        assert result["token_count"] == 150
        assert result["original_chat_ids_parsed"] == ["chat_001", "chat_002"]
        assert result["keywords_parsed"] == ["postgres", "db"]

    def test_get_doc_returns_latest(self, read_db, watchdog_db):
        """get_doc returns the most recent DoC version (highest summary_id)."""
        watchdog_db.save_context_summary(
            instance_name="phi3-orchestrator",
            chat_ids=["chat_001"],
            summary_text="old doc",
            token_count=50,
        )
        watchdog_db.save_context_summary(
            instance_name="phi3-orchestrator",
            chat_ids=["chat_001", "chat_002"],
            summary_text="new doc",
            token_count=100,
        )
        result = read_db.get_doc("phi3-orchestrator")
        # Latest by summary_id (autoincrement) — "new doc" was inserted second
        assert result["summary_text"] == "new doc"
        assert result["token_count"] == 100

    def test_get_doc_history(self, read_db, watchdog_db):
        """get_doc_history returns historical DoC versions."""
        for i in range(3):
            watchdog_db.save_context_summary(
                instance_name="phi3-orchestrator",
                chat_ids=[f"chat_{i}"],
                summary_text=f"doc version {i}",
                token_count=50 + i * 10,
            )
        results = read_db.get_doc_history("phi3-orchestrator")
        assert len(results) == 3
        # Most recent first (highest token_count was last insert)
        assert results[0]["token_count"] == 70

    def test_get_context_summary_includes_chat_ids(self, read_db, watchdog_db):
        """get_context_summary now returns original_chat_ids."""
        watchdog_db.save_context_summary(
            instance_name="phi3-orchestrator",
            chat_ids=["c1", "c2", "c3"],
            summary_text="test summary",
            token_count=100,
        )
        result = read_db.get_context_summary("phi3-orchestrator")
        assert result is not None
        assert "original_chat_ids" in result
        parsed = json.loads(result["original_chat_ids"])
        assert parsed == ["c1", "c2", "c3"]


# ═══════════════════════════════════════════════════
# TEST 2: Full Chat Persistence (persist_full flag)
# ═══════════════════════════════════════════════════


class TestFullChatPersistence:
    """Tests that persist_full=True stores full query + full response."""

    @pytest.fixture
    def phi3_instance(self, tmp_db):
        db_path, _ = tmp_db
        rdb = ReadOnlyDB(db_path)
        inst = Phi3Instance("orchestrator", rdb, "http://localhost:11434", "phi3:mini")
        return inst

    @pytest.mark.asyncio
    async def test_persist_full_stores_complete_data(self, phi3_instance, watchdog_db, tmp_db):
        """When persist_full=True, full user_query and full_llm_response are stored."""
        db_path, _ = tmp_db

        long_query = "A" * 1000  # longer than 500 char truncation
        long_response = "B" * 2000

        summary_json = json.dumps({
            "summary": "Test summary",
            "decisions": ["decision_1"],
            "keywords": ["test"],
        })
        doc_text = "## DECISIONS MADE\n- decision_1\n\n## REQUIREMENTS CAPTURED\n(none)\n\n## CURRENT STATE\n- Phase: 0\n\n## KEY CONTEXT\n(none)\n\n## ACTION ITEMS\n(none)\n\n## SESSION HISTORY\n- Test exchange"

        phi3_instance._session = _make_mock_session([summary_json, doc_text])

        req = {
            "chat_id": "chat_full_test",
            "session_id": "session_full",
            "user_query": long_query,
            "llm_response": long_response,
            "persist_full": True,
            "project_id": "proj_test",
        }

        await phi3_instance._summarize(req)

        # Drain the write queue through WatchdogDB
        queue = get_write_queue()
        result_bus = get_result_bus()
        count = await watchdog_db.drain_write_queue(queue, result_bus)
        assert count >= 1  # at least the chat_summary write

        # Verify full data was stored
        rdb = ReadOnlyDB(db_path)
        chat = rdb.get_chat("chat_full_test")
        assert chat is not None
        assert len(chat["user_query"]) == 1000  # NOT truncated to 500
        assert chat["full_llm_response"] == long_response
        assert chat["llm_response_summary"] == "Test summary"

    @pytest.mark.asyncio
    async def test_persist_false_truncates(self, phi3_instance, watchdog_db, tmp_db):
        """When persist_full=False (default), user_query is truncated, no full_llm_response."""
        db_path, _ = tmp_db

        long_query = "C" * 1000
        summary_json = json.dumps({
            "summary": "Truncated summary",
            "decisions": [],
            "keywords": [],
        })

        phi3_instance._session = _make_mock_session([summary_json])

        req = {
            "chat_id": "chat_trunc_test",
            "session_id": "session_trunc",
            "user_query": long_query,
            "llm_response": "short response",
            "persist_full": False,
        }

        await phi3_instance._summarize(req)

        queue = get_write_queue()
        result_bus = get_result_bus()
        await watchdog_db.drain_write_queue(queue, result_bus)

        rdb = ReadOnlyDB(db_path)
        chat = rdb.get_chat("chat_trunc_test")
        assert chat is not None
        assert len(chat["user_query"]) == 500  # truncated
        assert chat["full_llm_response"] is None  # not stored


# ═══════════════════════════════════════════════════
# TEST 3: DoC Builder (_update_doc)
# ═══════════════════════════════════════════════════


class TestDocBuilder:
    """Tests the Document of Context rolling update system."""

    @pytest.fixture
    def phi3_instance(self, tmp_db):
        db_path, _ = tmp_db
        rdb = ReadOnlyDB(db_path)
        inst = Phi3Instance("orchestrator", rdb, "http://localhost:11434", "phi3:mini")
        return inst

    def test_doc_initial_template_structure(self):
        """DOC_INITIAL_TEMPLATE has all required sections."""
        for section in ["DECISIONS MADE", "REQUIREMENTS CAPTURED",
                        "CURRENT STATE", "KEY CONTEXT", "ACTION ITEMS",
                        "SESSION HISTORY"]:
            assert section in DOC_INITIAL_TEMPLATE

    def test_doc_update_prompt_has_placeholders(self):
        """DOC_UPDATE_PROMPT has all required format placeholders."""
        for placeholder in ["{current_doc}", "{user_query}", "{summary}",
                            "{decisions}", "{keywords}", "{chat_id}"]:
            assert placeholder in DOC_UPDATE_PROMPT

    @pytest.mark.asyncio
    async def test_update_doc_creates_new_doc(self, phi3_instance, watchdog_db, tmp_db):
        """_update_doc creates a DoC when none exists (initial template used)."""
        db_path, _ = tmp_db

        doc_output = (
            "## DECISIONS MADE\n- Use PostgreSQL (Chat: chat_doc_001)\n\n"
            "## REQUIREMENTS CAPTURED\n- Must support auth\n\n"
            "## CURRENT STATE\n- Project: active\n- Phase: 1\n- Active Tasks: 1\n\n"
            "## KEY CONTEXT\n- Backend uses Python\n\n"
            "## ACTION ITEMS\n- [ ] Set up DB\n\n"
            "## SESSION HISTORY\n- User asked about database choice"
        )

        phi3_instance._session = _make_mock_session([doc_output])

        req = {
            "chat_id": "chat_doc_001",
            "user_query": "Should we use PostgreSQL?",
            "llm_response": "Yes, PostgreSQL is best for this use case.",
        }
        parsed = {"summary": "Discussed DB choice", "decisions": ["Use PostgreSQL"], "keywords": ["postgres"]}

        await phi3_instance._update_doc(req, parsed)

        # Drain writes
        queue = get_write_queue()
        result_bus = get_result_bus()
        await watchdog_db.drain_write_queue(queue, result_bus)

        # Verify DoC was created
        rdb = ReadOnlyDB(db_path)
        doc = rdb.get_doc("phi3-orchestrator")
        assert doc is not None
        assert "DECISIONS MADE" in doc["summary_text"]
        assert doc["original_chat_ids_parsed"] == ["chat_doc_001"]
        assert doc["token_count"] > 0

    @pytest.mark.asyncio
    async def test_update_doc_appends_chat_ids(self, phi3_instance, watchdog_db, tmp_db):
        """_update_doc accumulates chat_ids across updates."""
        db_path, _ = tmp_db

        # Seed an existing DoC
        watchdog_db.save_context_summary(
            instance_name="phi3-orchestrator",
            chat_ids=["chat_prev_001", "chat_prev_002"],
            summary_text="## DECISIONS MADE\n- old decision",
            keywords=["old"],
            token_count=50,
        )

        doc_output = "## DECISIONS MADE\n- old decision\n- new decision\n\n## REQUIREMENTS CAPTURED\n(none)\n\n## CURRENT STATE\n- Phase: 1\n\n## KEY CONTEXT\n(none)\n\n## ACTION ITEMS\n(none)\n\n## SESSION HISTORY\n- New exchange"

        phi3_instance._session = _make_mock_session([doc_output])

        req = {"chat_id": "chat_new_003", "user_query": "q", "llm_response": "r"}
        parsed = {"summary": "s", "decisions": [], "keywords": []}

        await phi3_instance._update_doc(req, parsed)

        queue = get_write_queue()
        result_bus = get_result_bus()
        await watchdog_db.drain_write_queue(queue, result_bus)

        rdb = ReadOnlyDB(db_path)
        doc = rdb.get_doc("phi3-orchestrator")
        assert "chat_prev_001" in doc["original_chat_ids_parsed"]
        assert "chat_prev_002" in doc["original_chat_ids_parsed"]
        assert "chat_new_003" in doc["original_chat_ids_parsed"]
        assert len(doc["original_chat_ids_parsed"]) == 3

    @pytest.mark.asyncio
    async def test_update_doc_skips_short_output(self, phi3_instance, watchdog_db, tmp_db):
        """_update_doc skips if Phi3 returns too-short output (<50 chars)."""
        db_path, _ = tmp_db

        phi3_instance._session = _make_mock_session(["too short"])

        req = {"chat_id": "chat_skip", "user_query": "q", "llm_response": "r"}
        parsed = {"summary": "s", "decisions": [], "keywords": []}

        await phi3_instance._update_doc(req, parsed)

        queue = get_write_queue()
        result_bus = get_result_bus()
        await watchdog_db.drain_write_queue(queue, result_bus)

        rdb = ReadOnlyDB(db_path)
        doc = rdb.get_doc("phi3-orchestrator")
        assert doc is None  # nothing written


# ═══════════════════════════════════════════════════
# TEST 4: Orchestrator DoC Recovery
# ═══════════════════════════════════════════════════


class TestOrchestratorRecovery:
    """Tests that MasterOrchestrator loads DoC on init."""

    @pytest.fixture
    def mock_router(self):
        router = MagicMock(spec=RoleRouter)
        router.get_worker.return_value = None
        router.get_worker_name.return_value = "mock_worker"
        return router

    def test_fresh_session_no_doc(self, tmp_db, mock_router):
        """Orchestrator starts clean with no existing DoC."""
        db_path, _ = tmp_db
        rdb = ReadOnlyDB(db_path)
        orch = MasterOrchestrator(rdb, mock_router, {}, "/tmp/test_working")
        assert orch._doc_context is None
        assert len(orch.chat_history) == 0

    def test_recovery_loads_doc(self, tmp_db, mock_router):
        """Orchestrator loads existing DoC into chat_history on init."""
        db_path, wdb = tmp_db
        wdb.save_context_summary(
            instance_name="phi3-orchestrator",
            chat_ids=["c1", "c2", "c3"],
            summary_text="## DECISIONS MADE\n- Use JWT auth\n\n## CURRENT STATE\n- Phase: 2",
            keywords=["jwt", "auth"],
            token_count=200,
            compression_ratio=0.25,
        )

        rdb = ReadOnlyDB(db_path)
        orch = MasterOrchestrator(rdb, mock_router, {}, "/tmp/test_working")

        assert orch._doc_context is not None
        assert "JWT auth" in orch._doc_context
        assert len(orch.chat_history) == 1
        assert orch.chat_history[0]["role"] == "system"
        assert "[RECOVERED CONTEXT]" in orch.chat_history[0]["content"]
        assert orch.chat_history[0]["metadata"]["type"] == "doc_recovery"
        assert orch.chat_history[0]["metadata"]["chats_covered"] == 3

    def test_recovery_handles_corruption(self, tmp_db, mock_router):
        """Orchestrator handles DoC recovery failure gracefully."""
        db_path, _ = tmp_db
        rdb = ReadOnlyDB(db_path)
        # Monkey-patch get_doc to raise
        rdb.get_doc = MagicMock(side_effect=Exception("DB corruption"))
        orch = MasterOrchestrator(rdb, mock_router, {}, "/tmp/test_working")
        # Should not crash
        assert orch._doc_context is None


# ═══════════════════════════════════════════════════
# TEST 5: Orchestrator handle_message wires persist_full
# ═══════════════════════════════════════════════════


class TestHandleMessagePersistFull:
    """Tests that handle_message passes persist_full=True to Phi3."""

    @pytest.fixture
    def mock_router(self):
        router = MagicMock(spec=RoleRouter)
        router.get_worker.return_value = None
        router.get_worker_name.return_value = "mock"
        return router

    @pytest.mark.asyncio
    async def test_handle_message_calls_phi3_with_persist_full(self, tmp_db, mock_router):
        """handle_message passes persist_full=True to phi3.queue_summary."""
        db_path, _ = tmp_db
        rdb = ReadOnlyDB(db_path)
        orch = MasterOrchestrator(rdb, mock_router, {}, "/tmp/test_working")

        mock_phi3 = AsyncMock()
        mock_phi3.queue_summary = AsyncMock(return_value="chat_test_123")
        orch.phi3 = mock_phi3

        await orch.handle_message("hello world")

        mock_phi3.queue_summary.assert_called_once()
        call_kwargs = mock_phi3.queue_summary.call_args
        assert call_kwargs.kwargs.get("persist_full") is True


# ═══════════════════════════════════════════════════
# TEST 6: Phi3Manager lifecycle
# ═══════════════════════════════════════════════════


class TestPhi3ManagerLifecycle:
    """Tests Phi3Manager start/stop/get."""

    @pytest.mark.asyncio
    async def test_start_creates_instances(self, tmp_db):
        """start_all creates Phi3Instance per worker name."""
        db_path, _ = tmp_db
        rdb = ReadOnlyDB(db_path)
        config = {"workers": {"phi3": {"api_base": "http://localhost:11434", "model": "phi3:mini"}}}
        mgr = Phi3Manager(config, rdb)

        await mgr.start_all(["orchestrator", "claude"])
        assert "orchestrator" in mgr.instances
        assert "claude" in mgr.instances
        assert mgr.instances["orchestrator"].name == "phi3-orchestrator"
        assert mgr.instances["claude"].name == "phi3-claude"

        await mgr.stop_all()
        assert len(mgr.instances) == 0

    @pytest.mark.asyncio
    async def test_get_returns_instance(self, tmp_db):
        """get() returns the correct Phi3Instance by parent name."""
        db_path, _ = tmp_db
        rdb = ReadOnlyDB(db_path)
        config = {"workers": {"phi3": {}}}
        mgr = Phi3Manager(config, rdb)

        await mgr.start_all(["orchestrator"])
        inst = mgr.get("orchestrator")
        assert inst is not None
        assert inst.name == "phi3-orchestrator"
        assert mgr.get("nonexistent") is None

        await mgr.stop_all()

    @pytest.mark.asyncio
    async def test_queue_summary_persist_full_param(self, tmp_db):
        """queue_summary accepts persist_full parameter."""
        db_path, _ = tmp_db
        rdb = ReadOnlyDB(db_path)
        inst = Phi3Instance("orchestrator", rdb)

        chat_id = await inst.queue_summary(
            user_query="test",
            llm_response="response",
            session_id="sess",
            persist_full=True,
        )
        assert chat_id.startswith("chat_")

        # Verify the queued request has persist_full
        req = inst._queue.get_nowait()
        assert req["persist_full"] is True


# ═══════════════════════════════════════════════════
# TEST 7: Full E2E Flow
# ═══════════════════════════════════════════════════


class TestFullE2EFlow:
    """Integration test: message → Phi3 summarize → full chat stored → DoC updated → recovery works."""

    @pytest.mark.asyncio
    async def test_full_flow(self, tmp_db):
        """Complete flow: handle_message → phi3 → DB → recovery."""
        db_path, wdb = tmp_db

        # 1. Set up orchestrator with mock router
        rdb = ReadOnlyDB(db_path)
        router = MagicMock(spec=RoleRouter)
        router.get_worker.return_value = None
        router.get_worker_name.return_value = "mock"

        orch = MasterOrchestrator(rdb, router, {}, "/tmp/test_working")

        # 2. Set up Phi3 with mocked Ollama
        phi3_rdb = ReadOnlyDB(db_path)
        phi3 = Phi3Instance("orchestrator", phi3_rdb)

        summary_json = json.dumps({
            "summary": "User asked about database choice, decided PostgreSQL",
            "decisions": ["Use PostgreSQL"],
            "keywords": ["postgresql", "database"],
        })
        doc_output = (
            "## DECISIONS MADE\n- Use PostgreSQL: Best for relational data (Chat: {chat_id})\n\n"
            "## REQUIREMENTS CAPTURED\n- Need relational database\n\n"
            "## CURRENT STATE\n- Project: active\n- Phase: 1\n- Active Tasks: planning\n\n"
            "## KEY CONTEXT\n- Backend is Python\n\n"
            "## ACTION ITEMS\n- [ ] Set up PostgreSQL\n\n"
            "## SESSION HISTORY\n- Discussed and decided on PostgreSQL"
        )

        phi3._session = _make_mock_session([summary_json, doc_output])
        orch.phi3 = phi3

        # 3. Send a message through orchestrator
        response = await orch.handle_message("Should we use PostgreSQL for the database?")
        assert response is not None

        # 4. Manually run the Phi3 summarize (normally async loop does this)
        req = phi3._queue.get_nowait()
        assert req["persist_full"] is True
        await phi3._summarize(req)

        # 5. Drain writes to DB
        queue = get_write_queue()
        result_bus = get_result_bus()
        count = await wdb.drain_write_queue(queue, result_bus)
        assert count >= 2  # chat_summary + context_summary

        # 6. Verify full chat was stored
        verify_rdb = ReadOnlyDB(db_path)
        chats = verify_rdb.get_chats_by_session(orch.session_id)
        assert len(chats) >= 1
        chat = chats[0]
        assert "PostgreSQL" in chat["user_query"]
        assert chat["full_llm_response"] is not None  # full response stored

        # 7. Verify DoC was created
        doc = verify_rdb.get_doc("phi3-orchestrator")
        assert doc is not None
        assert "DECISIONS MADE" in doc["summary_text"]
        assert len(doc["original_chat_ids_parsed"]) == 1

        # 8. Simulate crash recovery: new orchestrator loads DoC
        recovery_rdb = ReadOnlyDB(db_path)
        recovered_orch = MasterOrchestrator(recovery_rdb, router, {}, "/tmp/test_working")
        assert recovered_orch._doc_context is not None
        assert "DECISIONS MADE" in recovered_orch._doc_context
        assert recovered_orch.chat_history[0]["role"] == "system"
        assert "[RECOVERED CONTEXT]" in recovered_orch.chat_history[0]["content"]

        # 9. Verify recall API works for the stored chat
        chat_id = chat["chat_id"]
        recalled = verify_rdb.get_chat(chat_id)
        assert recalled is not None
        assert recalled["full_llm_response"] is not None


# ═══════════════════════════════════════════════════
# TEST 8: Cold Storage Flush
# ═══════════════════════════════════════════════════


class TestColdStorageFlush:
    """Tests that overflow messages are flushed to chat_archive before trim."""

    @pytest.fixture
    def mock_router(self):
        router = MagicMock(spec=RoleRouter)
        router.get_worker.return_value = None
        router.get_worker_name.return_value = "mock"
        router.workers = {}
        return router

    def test_overflow_flushes_to_cold_storage(self, tmp_db, mock_router):
        """When history exceeds 200, overflow is queued as writes to chat_archive."""
        db_path, wdb = tmp_db
        rdb = ReadOnlyDB(db_path)
        config = {
            "factory": {"factory_state_dir": str(Path(db_path).parent)},
        }
        orch = MasterOrchestrator(rdb, mock_router, config, "/tmp/test_working")

        # Fill with 210 messages
        for i in range(210):
            orch.chat_history.append({
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"Message number {i}",
                "timestamp": f"2025-01-01T{i:05d}",
                "metadata": {"mode": "direct", "worker": "qwen", "session_id": "sess_overflow"},
            })
        orch._save_chat_history()

        # Verify trim: only 200 in warm memory
        assert len(orch.chat_history) == 200

        # Verify overflow was queued: drain the write queue
        queue = get_write_queue()
        result_bus = get_result_bus()
        # Drain all pending writes
        count = 0
        with wdb._write_conn() as conn:
            while not queue.empty():
                req = queue.get_nowait()
                if req.table == "chat_archive":
                    count += 1
                    wdb._execute_write(conn, req)
            conn.commit()

        assert count == 10  # 210 - 200 = 10 overflow messages

        # Verify cold storage has the messages
        verify_rdb = ReadOnlyDB(db_path)
        archived = verify_rdb.search_archive(worker="qwen")
        assert len(archived) == 10

    def test_no_flush_under_200(self, tmp_db, mock_router):
        """No cold flush when history is under 200."""
        db_path, _ = tmp_db
        rdb = ReadOnlyDB(db_path)
        config = {
            "factory": {"factory_state_dir": str(Path(db_path).parent)},
        }
        orch = MasterOrchestrator(rdb, mock_router, config, "/tmp/test_working")

        for i in range(50):
            orch.chat_history.append({
                "role": "user", "content": f"msg {i}",
                "timestamp": datetime.now().isoformat(),
                "metadata": {"session_id": "sess"},
            })
        orch._save_chat_history()

        assert len(orch.chat_history) == 50  # no trim

    def test_append_history_includes_session_id(self, tmp_db, mock_router):
        """_append_history always includes session_id in metadata."""
        db_path, _ = tmp_db
        rdb = ReadOnlyDB(db_path)
        orch = MasterOrchestrator(rdb, mock_router, {}, "/tmp/test_working")

        orch._append_history("user", "hello")
        entry = orch.chat_history[-1]
        assert "session_id" in entry["metadata"]
        assert entry["metadata"]["session_id"] == orch.session_id

        # With existing metadata
        orch._append_history("user", "test", metadata={"mode": "direct"})
        entry2 = orch.chat_history[-1]
        assert entry2["metadata"]["session_id"] == orch.session_id
        assert entry2["metadata"]["mode"] == "direct"


# ═══════════════════════════════════════════════════
# TEST 9: Keyword Search
# ═══════════════════════════════════════════════════


class TestKeywordSearch:
    """Tests for search_chats_by_keyword on Phi3 summaries."""

    def test_keyword_found(self, read_db, sample_chat_rows):
        """search_chats_by_keyword returns matches when keyword exists."""
        results = read_db.search_chats_by_keyword("keyword_2")
        assert len(results) == 1
        assert results[0]["chat_id"] == "chat_test_0002"

    def test_keyword_not_found(self, read_db, sample_chat_rows):
        """search_chats_by_keyword returns empty for non-existent keyword."""
        results = read_db.search_chats_by_keyword("nonexistent_xyz_42")
        assert len(results) == 0

    def test_keyword_in_query(self, read_db, sample_chat_rows):
        """search_chats_by_keyword matches on user_query field too."""
        results = read_db.search_chats_by_keyword("question number 3")
        assert len(results) == 1
        assert results[0]["chat_id"] == "chat_test_0003"

    def test_keyword_broad_match(self, read_db, sample_chat_rows):
        """search_chats_by_keyword returns multiple matches for broad keyword."""
        results = read_db.search_chats_by_keyword("python")
        assert len(results) == 5  # all 5 have "python" in keywords

    def test_keyword_with_worker_filter(self, read_db, watchdog_db):
        """search_chats_by_keyword filters by worker when specified."""
        # Insert chats for different workers
        for worker in ["qwen", "deepseek"]:
            watchdog_db.save_chat_summary(
                chat_id=f"chat_{worker}_auth",
                session_id="sess_filter",
                instance_name="phi3-orchestrator",
                parent_worker=worker,
                user_query="How to implement auth?",
                llm_response_summary="Use JWT tokens",
                keywords=["auth", "jwt"],
            )
        results = read_db.search_chats_by_keyword("auth", worker="qwen")
        assert len(results) == 1
        assert results[0]["parent_worker"] == "qwen"

    def test_keyword_respects_limit(self, read_db, sample_chat_rows):
        """search_chats_by_keyword respects the limit parameter."""
        results = read_db.search_chats_by_keyword("python", limit=2)
        assert len(results) == 2


# ═══════════════════════════════════════════════════
# TEST 10: Archive Search
# ═══════════════════════════════════════════════════


class TestArchiveSearch:
    """Tests for search_archive and get_archive_count on cold storage."""

    @pytest.fixture
    def seeded_archive(self, watchdog_db, tmp_db):
        """Seed 20 messages in chat_archive for search tests."""
        db_path, _ = tmp_db
        messages = []
        for i in range(20):
            worker = "qwen" if i % 2 == 0 else "deepseek"
            mode = "direct" if i < 10 else "project"
            messages.append({
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"{'Authentication' if i < 5 else 'Database'} discussion message {i}",
                "timestamp": f"2025-01-01T{i:05d}",
                "metadata": {
                    "mode": mode, "worker": worker,
                    "session_id": f"sess_{i // 10}",
                },
            })
        watchdog_db.archive_chat_messages(messages)
        return ReadOnlyDB(db_path)

    def test_search_by_keyword(self, seeded_archive):
        """search_archive finds messages containing keyword."""
        results = seeded_archive.search_archive(keyword="Authentication")
        assert len(results) == 5

    def test_search_by_worker(self, seeded_archive):
        """search_archive filters by worker."""
        results = seeded_archive.search_archive(worker="qwen")
        assert len(results) == 10
        assert all(r["worker"] == "qwen" for r in results)

    def test_search_by_mode(self, seeded_archive):
        """search_archive filters by mode."""
        results = seeded_archive.search_archive(mode="project")
        assert len(results) == 10

    def test_search_combined_filters(self, seeded_archive):
        """search_archive combines multiple filters."""
        results = seeded_archive.search_archive(
            keyword="Database", worker="qwen", mode="direct")
        assert all("Database" in r["content"] for r in results)
        assert all(r["worker"] == "qwen" for r in results)

    def test_search_pagination(self, seeded_archive):
        """search_archive supports offset/limit pagination."""
        page1 = seeded_archive.search_archive(limit=5, offset=0)
        page2 = seeded_archive.search_archive(limit=5, offset=5)
        assert len(page1) == 5
        assert len(page2) == 5
        # No overlap
        ids1 = {r["archive_id"] for r in page1}
        ids2 = {r["archive_id"] for r in page2}
        assert ids1.isdisjoint(ids2)

    def test_get_archive_count(self, seeded_archive):
        """get_archive_count returns correct totals."""
        total = seeded_archive.get_archive_count()
        assert total == 20

        qwen_count = seeded_archive.get_archive_count(worker="qwen")
        assert qwen_count == 10

        auth_count = seeded_archive.get_archive_count(keyword="Authentication")
        assert auth_count == 5

    def test_search_by_session(self, seeded_archive):
        """search_archive filters by session_id."""
        results = seeded_archive.search_archive(session_id="sess_0")
        assert len(results) == 10


# ═══════════════════════════════════════════════════
# TEST 11: Warm Memory Context Builder
# ═══════════════════════════════════════════════════


class TestWarmMemoryContextBuilder:
    """Tests that _build_conversation_context fuses DoC + history correctly."""

    @pytest.fixture
    def mock_router(self):
        router = MagicMock(spec=RoleRouter)
        router.get_worker.return_value = None
        router.get_worker_name.return_value = "mock"
        router.workers = {}
        return router

    def test_doc_prefix_when_sparse(self, tmp_db, mock_router):
        """When < 5 relevant messages in orchestrator mode, DoC is prepended as LONG-TERM CONTEXT."""
        db_path, wdb = tmp_db
        wdb.save_context_summary(
            instance_name="phi3-orchestrator",
            chat_ids=["c1"],
            summary_text="## DECISIONS\n- Use React with TypeScript",
            keywords=["react"],
            token_count=50,
        )
        rdb = ReadOnlyDB(db_path)
        orch = MasterOrchestrator(rdb, mock_router, {}, "/tmp/test_working")

        # Add 3 relevant messages in orchestrator mode (< 5 threshold)
        for i in range(3):
            orch.chat_history.append({
                "role": "user", "content": f"orch msg {i}",
                "timestamp": datetime.now().isoformat(),
                "metadata": {"mode": "orchestrator"},
            })

        ctx = orch._build_conversation_context("orchestrator", "qwen")
        assert "LONG-TERM CONTEXT" in ctx
        assert "Use React with TypeScript" in ctx
        assert "orch msg" in ctx

    def test_no_doc_when_rich(self, tmp_db, mock_router):
        """When >= 5 relevant messages, DoC is NOT prepended."""
        db_path, wdb = tmp_db
        wdb.save_context_summary(
            instance_name="phi3-orchestrator",
            chat_ids=["c1"],
            summary_text="## DECISIONS\n- Use React",
            keywords=["react"],
            token_count=50,
        )
        rdb = ReadOnlyDB(db_path)
        orch = MasterOrchestrator(rdb, mock_router, {}, "/tmp/test_working")

        # Add 6 relevant messages (>= 5 threshold)
        for i in range(6):
            orch.chat_history.append({
                "role": "user", "content": f"direct msg {i}",
                "timestamp": datetime.now().isoformat(),
                "metadata": {"mode": "direct", "worker": "qwen"},
            })

        ctx = orch._build_conversation_context("direct", "qwen")
        assert "LONG-TERM CONTEXT" not in ctx
        assert "direct msg" in ctx

    def test_doc_recovery_entries_skipped(self, tmp_db, mock_router):
        """doc_recovery system entries are excluded from conversation context."""
        db_path, wdb = tmp_db
        wdb.save_context_summary(
            instance_name="phi3-orchestrator",
            chat_ids=["c1"],
            summary_text="## DECISIONS\n- Use React",
            keywords=["react"],
            token_count=50,
        )
        rdb = ReadOnlyDB(db_path)
        orch = MasterOrchestrator(rdb, mock_router, {}, "/tmp/test_working")

        # The __init__ added a doc_recovery entry, check it's skipped
        assert len(orch.chat_history) == 1
        assert orch.chat_history[0]["metadata"]["type"] == "doc_recovery"

        # Add 1 real message
        orch.chat_history.append({
            "role": "user", "content": "hello from direct",
            "timestamp": datetime.now().isoformat(),
            "metadata": {"mode": "direct", "worker": "qwen"},
        })

        ctx = orch._build_conversation_context("direct", "qwen")
        assert "RECOVERED CONTEXT" not in ctx
        assert "hello from direct" in ctx

    def test_no_doc_no_prefix(self, tmp_db, mock_router):
        """When no DoC exists, no prefix added even with sparse history."""
        db_path, _ = tmp_db
        rdb = ReadOnlyDB(db_path)
        orch = MasterOrchestrator(rdb, mock_router, {}, "/tmp/test_working")

        orch.chat_history.append({
            "role": "user", "content": "sparse msg",
            "timestamp": datetime.now().isoformat(),
            "metadata": {"mode": "direct", "worker": "qwen"},
        })

        ctx = orch._build_conversation_context("direct", "qwen")
        assert "LONG-TERM CONTEXT" not in ctx
        assert "sparse msg" in ctx


# ═══════════════════════════════════════════════════
# TEST 12: Schema Migration
# ═══════════════════════════════════════════════════


class TestSchemaMigration:
    """Tests that schema migration from v1 to v2 works."""

    def test_fresh_db_is_v3(self, tmp_path):
        """A fresh database gets schema version 3 (v3 added dac_tags, learning_log, cost_tracking)."""
        db_path = str(tmp_path / "fresh.db")
        wdb = WatchdogDB(db_path)
        import sqlite3
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        assert row[0] == 3
        # chat_archive table exists
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chat_archive'"
        ).fetchone()
        assert row is not None
        conn.close()

    def test_archive_chat_messages(self, tmp_db):
        """WatchdogDB.archive_chat_messages bulk inserts correctly."""
        db_path, wdb = tmp_db
        messages = [
            {
                "role": "user", "content": "test message 1",
                "timestamp": "2025-01-01T00:00:00",
                "metadata": {"session_id": "sess_1", "mode": "direct", "worker": "qwen"},
            },
            {
                "role": "assistant", "content": "test response 1",
                "timestamp": "2025-01-01T00:00:01",
                "metadata": {"session_id": "sess_1", "mode": "direct", "worker": "qwen"},
            },
        ]
        wdb.archive_chat_messages(messages)

        rdb = ReadOnlyDB(db_path)
        results = rdb.search_archive()
        assert len(results) == 2
        assert results[0]["role"] in ("user", "assistant")
        assert results[0]["worker"] == "qwen"


# ═══════════════════════════════════════════════════
# TEST 13: Project Selection
# ═══════════════════════════════════════════════════


class TestProjectSelection:
    """Tests for project listing, selection, filtering, and context scoping."""

    @pytest.fixture
    def mock_router(self):
        router = MagicMock(spec=RoleRouter)
        router.get_worker.return_value = None
        router.get_worker_name.return_value = "mock"
        router.workers = {}
        return router

    @pytest.fixture
    def multi_project_db(self, tmp_path):
        """Create DB with 3 projects: 2 active, 1 completed."""
        db_path = str(tmp_path / "proj_test.db")
        wdb = WatchdogDB(db_path)
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO dashboard_state (instance_name, status) VALUES ('phi3-orchestrator', 'active')")
        conn.execute(
            "INSERT INTO projects (project_id, name, description, status, current_phase, created_at) "
            "VALUES ('proj_a', 'Alpha', 'First project', 'active', 1, '2025-01-01T00:00:00')"
        )
        conn.execute(
            "INSERT INTO projects (project_id, name, description, status, current_phase, created_at) "
            "VALUES ('proj_b', 'Beta', 'Second project', 'active', 2, '2025-01-02T00:00:00')"
        )
        conn.execute(
            "INSERT INTO projects (project_id, name, description, status, current_phase, created_at) "
            "VALUES ('proj_c', 'Gamma', 'Done project', 'completed', 3, '2025-01-03T00:00:00')"
        )
        conn.commit()
        conn.close()
        return db_path, wdb

    # --- list_projects ---

    def test_list_projects_active_only(self, multi_project_db):
        """list_projects returns only active/paused projects by default."""
        db_path, _ = multi_project_db
        rdb = ReadOnlyDB(db_path)
        projects = rdb.list_projects()
        assert len(projects) == 2
        ids = {p["project_id"] for p in projects}
        assert ids == {"proj_a", "proj_b"}
        assert all(p["status"] in ("active", "paused") for p in projects)

    def test_list_projects_include_completed(self, multi_project_db):
        """list_projects with include_completed returns all projects."""
        db_path, _ = multi_project_db
        rdb = ReadOnlyDB(db_path)
        projects = rdb.list_projects(include_completed=True)
        assert len(projects) == 3
        ids = {p["project_id"] for p in projects}
        assert "proj_c" in ids

    # --- select_project ---

    def test_select_valid_project(self, multi_project_db, mock_router):
        """select_project sets current_project for a valid ID."""
        db_path, _ = multi_project_db
        rdb = ReadOnlyDB(db_path)
        orch = MasterOrchestrator(rdb, mock_router, {}, "/tmp/test_working")
        result = orch.select_project("proj_a")
        assert result["selected"] == "proj_a"
        assert result["name"] == "Alpha"
        assert orch.current_project == "proj_a"

    def test_select_nonexistent_project(self, multi_project_db, mock_router):
        """select_project returns error for non-existent project."""
        db_path, _ = multi_project_db
        rdb = ReadOnlyDB(db_path)
        orch = MasterOrchestrator(rdb, mock_router, {}, "/tmp/test_working")
        result = orch.select_project("proj_nonexistent")
        assert "error" in result
        assert orch.current_project is None

    def test_select_none_deselects(self, multi_project_db, mock_router):
        """select_project(None) deselects current project."""
        db_path, _ = multi_project_db
        rdb = ReadOnlyDB(db_path)
        orch = MasterOrchestrator(rdb, mock_router, {}, "/tmp/test_working")
        orch.select_project("proj_a")
        assert orch.current_project == "proj_a"
        result = orch.select_project(None)
        assert result["selected"] is None
        assert orch.current_project is None

    # --- get_chat_history_filtered ---

    def test_history_filtered_by_project(self, multi_project_db, mock_router):
        """get_chat_history_filtered returns only matching project messages + orchestrator."""
        db_path, _ = multi_project_db
        rdb = ReadOnlyDB(db_path)
        orch = MasterOrchestrator(rdb, mock_router, {}, "/tmp/test_working")

        # Add messages for different projects and modes
        orch.chat_history = [
            {"role": "user", "content": "orch msg", "timestamp": "t1",
             "metadata": {"mode": "orchestrator"}},
            {"role": "user", "content": "proj_a msg", "timestamp": "t2",
             "metadata": {"mode": "project", "project_id": "proj_a", "worker": "qwen"}},
            {"role": "user", "content": "proj_b msg", "timestamp": "t3",
             "metadata": {"mode": "project", "project_id": "proj_b", "worker": "qwen"}},
            {"role": "user", "content": "direct msg", "timestamp": "t4",
             "metadata": {"mode": "direct", "worker": "qwen"}},
        ]

        filtered = orch.get_chat_history_filtered("proj_a")
        contents = [m["content"] for m in filtered]
        assert "orch msg" in contents       # orchestrator mode always included
        assert "proj_a msg" in contents     # matching project
        assert "proj_b msg" not in contents # different project
        assert "direct msg" not in contents # direct mode excluded when filtering

    def test_history_unfiltered_when_no_project(self, multi_project_db, mock_router):
        """get_chat_history_filtered(None) returns all messages."""
        db_path, _ = multi_project_db
        rdb = ReadOnlyDB(db_path)
        orch = MasterOrchestrator(rdb, mock_router, {}, "/tmp/test_working")

        orch.chat_history = [
            {"role": "user", "content": "msg1", "timestamp": "t1", "metadata": {}},
            {"role": "user", "content": "msg2", "timestamp": "t2", "metadata": {}},
        ]

        filtered = orch.get_chat_history_filtered(None)
        assert len(filtered) == 2
        assert filtered is orch.chat_history  # same list returned

    # --- context builder project filter ---

    def test_context_builder_filters_by_project(self, multi_project_db, mock_router):
        """_build_conversation_context excludes other project's messages in project mode."""
        db_path, _ = multi_project_db
        rdb = ReadOnlyDB(db_path)
        orch = MasterOrchestrator(rdb, mock_router, {}, "/tmp/test_working")
        orch.select_project("proj_a")

        orch.chat_history = [
            {"role": "user", "content": "proj_a context", "timestamp": "t1",
             "metadata": {"mode": "project", "worker": "qwen", "project_id": "proj_a"}},
            {"role": "user", "content": "proj_b context", "timestamp": "t2",
             "metadata": {"mode": "project", "worker": "qwen", "project_id": "proj_b"}},
            {"role": "user", "content": "proj_a second", "timestamp": "t3",
             "metadata": {"mode": "project", "worker": "qwen", "project_id": "proj_a"}},
        ]

        ctx = orch._build_conversation_context("project", "qwen")
        assert "proj_a context" in ctx
        assert "proj_a second" in ctx
        assert "proj_b context" not in ctx  # filtered out


# ═══════════════════════════════════════════════════
# TEST 14: Chat Sessions (Tabs)
# ═══════════════════════════════════════════════════


class TestChatSessions:
    """Tests for chat session tabs: create, switch, list, rename."""

    @pytest.fixture
    def mock_router(self):
        router = MagicMock(spec=RoleRouter)
        router.get_worker.return_value = None
        router.get_worker_name.return_value = "mock"
        router.workers = {}
        return router

    @pytest.fixture
    def orch_with_state(self, tmp_path, mock_router):
        """Orchestrator with state dir for file persistence."""
        db_path = str(tmp_path / "sessions_test.db")
        wdb = WatchdogDB(db_path)
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO dashboard_state (instance_name, status) VALUES ('phi3-orchestrator', 'active')")
        conn.commit()
        conn.close()
        rdb = ReadOnlyDB(db_path)
        config = {"factory": {"factory_state_dir": str(tmp_path)}}
        orch = MasterOrchestrator(rdb, mock_router, config, "/tmp/test_working")
        return orch, wdb, db_path

    def test_new_session_creates_id(self, orch_with_state):
        """new_chat_session creates a new unique session_id."""
        orch, _, _ = orch_with_state
        old_id = orch.session_id
        result = orch.new_chat_session("Test Session")
        assert result["session_id"] != old_id
        assert result["name"] == "Test Session"
        assert orch.session_id == result["session_id"]

    def test_new_session_flushes_to_cold(self, orch_with_state):
        """new_chat_session flushes current messages to cold storage."""
        orch, wdb, db_path = orch_with_state
        # Add messages to current session
        for i in range(5):
            orch._append_history("user", f"msg {i}", metadata={"mode": "orchestrator"})
        assert len(orch.chat_history) == 5
        old_session = orch.session_id

        # Create new session
        orch.new_chat_session()

        # Drain writes to DB
        queue = get_write_queue()
        result_bus = get_result_bus()
        with wdb._write_conn() as conn:
            while not queue.empty():
                req = queue.get_nowait()
                wdb._execute_write(conn, req)
            conn.commit()

        # Verify old messages are in cold archive
        rdb = ReadOnlyDB(db_path)
        archived = rdb.get_session_messages(old_session)
        assert len(archived) == 5
        assert archived[0]["content"] == "msg 0"

    def test_new_session_clears_history(self, orch_with_state):
        """new_chat_session clears in-memory chat_history."""
        orch, _, _ = orch_with_state
        orch._append_history("user", "old message")
        assert len(orch.chat_history) >= 1

        orch.new_chat_session()
        assert len(orch.chat_history) == 0

    def test_switch_session_loads_from_archive(self, orch_with_state):
        """switch_chat_session loads messages from cold archive."""
        orch, wdb, db_path = orch_with_state
        # Add messages to session 1
        orch._append_history("user", "session 1 message", metadata={"mode": "orchestrator"})
        session_1 = orch.session_id

        # Create session 2
        orch.new_chat_session("Session 2")
        orch._append_history("user", "session 2 message", metadata={"mode": "orchestrator"})
        session_2 = orch.session_id

        # Drain writes
        queue = get_write_queue()
        result_bus = get_result_bus()
        with wdb._write_conn() as conn:
            while not queue.empty():
                req = queue.get_nowait()
                wdb._execute_write(conn, req)
            conn.commit()

        # Switch back to session 1
        result = orch.switch_chat_session(session_1)

        # Drain the flush writes from switch
        with wdb._write_conn() as conn:
            while not queue.empty():
                req = queue.get_nowait()
                wdb._execute_write(conn, req)
            conn.commit()

        assert result["session_id"] == session_1
        assert orch.session_id == session_1
        # Should have loaded session 1's archived messages
        contents = [m["content"] for m in orch.chat_history]
        assert "session 1 message" in contents

    def test_switch_same_session_noop(self, orch_with_state):
        """switch_chat_session to current session is a no-op."""
        orch, _, _ = orch_with_state
        current = orch.session_id
        result = orch.switch_chat_session(current)
        assert result["status"] == "already_active"
        assert orch.session_id == current

    def test_list_sessions_shows_active(self, orch_with_state):
        """list_chat_sessions marks current session as active."""
        orch, _, _ = orch_with_state
        orch.new_chat_session("Second")
        sessions = orch.list_chat_sessions()
        assert len(sessions) == 2
        active = [s for s in sessions if s["is_active"]]
        assert len(active) == 1
        assert active[0]["name"] == "Second"

    def test_rename_session(self, orch_with_state):
        """rename_chat_session updates session name."""
        orch, _, _ = orch_with_state
        sid = orch.session_id
        result = orch.rename_chat_session(sid, "Renamed Session")
        assert result["name"] == "Renamed Session"
        sessions = orch.list_chat_sessions()
        names = {s["session_id"]: s["name"] for s in sessions}
        assert names[sid] == "Renamed Session"

    def test_get_session_messages(self, orch_with_state):
        """get_session_messages returns archived messages for a session."""
        orch, wdb, db_path = orch_with_state
        # Archive some messages directly
        wdb.archive_chat_messages([
            {"role": "user", "content": "archived msg 1", "timestamp": "2025-01-01T00:00:01",
             "metadata": {"session_id": "sess_archive_test", "mode": "direct", "worker": "qwen"}},
            {"role": "assistant", "content": "archived msg 2", "timestamp": "2025-01-01T00:00:02",
             "metadata": {"session_id": "sess_archive_test", "mode": "direct", "worker": "qwen"}},
        ])
        rdb = ReadOnlyDB(db_path)
        messages = rdb.get_session_messages("sess_archive_test")
        assert len(messages) == 2
        assert messages[0]["content"] == "archived msg 1"
        assert messages[1]["content"] == "archived msg 2"
        assert messages[0]["role"] == "user"
