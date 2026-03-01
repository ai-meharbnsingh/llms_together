"""
TDD Tests — Discussion Mode (Multi-Model Panel Chat)
═══════════════════════════════════════════════════════
Tests the full flow: Discussion Chat → Sequential Model Responses →
Cancellation → History Persistence → Context Building → Dashboard WS/REST.

Uses real SQLite (temp DB), mocked worker adapters.
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Fix imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestration.database import ReadOnlyDB, WatchdogDB, get_write_queue, get_result_bus
from orchestration.master_orchestrator import MasterOrchestrator
from orchestration.role_router import RoleRouter


# ─── Fixtures ───


@pytest.fixture
def tmp_db(tmp_path):
    """Create a real SQLite DB with full schema + seed FK parents."""
    db_path = str(tmp_path / "test_factory.db")
    wdb = WatchdogDB(db_path)
    import sqlite3
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
    """ReadOnlyDB pointing at the test DB."""
    db_path, _ = tmp_db
    rdb = ReadOnlyDB(db_path)
    rdb.set_requester("test")
    return rdb


@pytest.fixture
def mock_worker_success():
    """Create a mock worker that returns a successful response."""
    def _make(name, response_text=None):
        worker = MagicMock()
        worker.name = name
        worker.config = {
            "type": "mock", "model": name,
            "max_context_tokens": 32000, "timeout": 30,
        }
        resp_text = response_text or f"Response from {name}"
        worker.send_message = AsyncMock(return_value={
            "success": True, "response": resp_text,
            "elapsed_ms": 150,
        })
        return worker
    return _make


@pytest.fixture
def mock_worker_failure():
    """Create a mock worker that raises an exception."""
    def _make(name):
        worker = MagicMock()
        worker.name = name
        worker.config = {
            "type": "mock", "model": name,
            "max_context_tokens": 32000, "timeout": 30,
        }
        worker.send_message = AsyncMock(
            side_effect=Exception(f"Timeout connecting to {name}"))
        return worker
    return _make


@pytest.fixture
def mock_worker_error_response():
    """Create a mock worker that returns success=False."""
    def _make(name):
        worker = MagicMock()
        worker.name = name
        worker.config = {
            "type": "mock", "model": name,
            "max_context_tokens": 32000, "timeout": 30,
        }
        worker.send_message = AsyncMock(return_value={
            "success": False, "error": "API rate limited",
        })
        return worker
    return _make


@pytest.fixture
def orchestrator(read_db, tmp_path, mock_worker_success):
    """Create a MasterOrchestrator with mock workers via RoleRouter."""
    config = {
        "factory": {
            "working_dir": str(tmp_path / "working"),
            "factory_state_dir": str(tmp_path / "state"),
        },
        "workers": {
            "claude": {"type": "mock", "model": "claude"},
            "gemini": {"type": "mock", "model": "gemini"},
            "deepseek": {"type": "mock", "model": "deepseek"},
            "qwen": {"type": "mock", "model": "qwen"},
        },
        "roles": {
            "code_generation_simple": {"primary": "qwen"},
        },
    }

    mock_workers = {
        "claude": mock_worker_success("claude"),
        "gemini": mock_worker_success("gemini"),
        "deepseek": mock_worker_success("deepseek"),
        "qwen": mock_worker_success("qwen"),
    }
    router = RoleRouter(config, mock_workers)

    orch = MasterOrchestrator(
        read_db=read_db, role_router=router,
        config=config, working_dir=str(tmp_path / "working"),
    )
    orch.phi3 = None  # Disable Phi3 for tests
    return orch


# ═══════════════════════════════════════════════════
# TEST 1: Basic Discussion Chat — Happy Path
# ═══════════════════════════════════════════════════


class TestDiscussionChatBasic:
    """Tests for basic discussion_chat() functionality."""

    async def test_discussion_returns_all_responses(self, orchestrator):
        """discussion_chat should return responses from all participants."""
        result = await orchestrator.discussion_chat(
            participants=["claude", "gemini"],
            message="What is Python?",
        )
        assert result["_handler"] == "discussion_chat"
        assert result["discussion_id"].startswith("disc_")
        assert len(result["responses"]) == 2
        assert result["responses"][0]["worker"] == "claude"
        assert result["responses"][1]["worker"] == "gemini"
        assert not result["cancelled"]

    async def test_discussion_single_participant(self, orchestrator):
        """discussion_chat works with a single participant."""
        result = await orchestrator.discussion_chat(
            participants=["deepseek"],
            message="Explain asyncio",
        )
        assert len(result["responses"]) == 1
        assert result["responses"][0]["worker"] == "deepseek"

    async def test_discussion_three_participants(self, orchestrator):
        """discussion_chat handles 3+ participants correctly."""
        result = await orchestrator.discussion_chat(
            participants=["claude", "gemini", "deepseek"],
            message="Compare Go vs Rust",
        )
        assert len(result["responses"]) == 3
        workers = [r["worker"] for r in result["responses"]]
        assert workers == ["claude", "gemini", "deepseek"]

    async def test_discussion_responses_contain_text(self, orchestrator):
        """Each response should contain actual text."""
        result = await orchestrator.discussion_chat(
            participants=["claude", "gemini"],
            message="Hello",
        )
        for resp in result["responses"]:
            assert resp["text"]
            assert len(resp["text"]) > 0


# ═══════════════════════════════════════════════════
# TEST 2: Discussion History Persistence
# ═══════════════════════════════════════════════════


class TestDiscussionHistory:
    """Tests for discussion messages being stored in chat history."""

    async def test_user_message_stored(self, orchestrator):
        """User message should appear in chat history with discussion mode."""
        await orchestrator.discussion_chat(
            participants=["claude"],
            message="Test history",
        )
        user_msgs = [
            h for h in orchestrator.chat_history
            if h["role"] == "user"
            and (h.get("metadata") or {}).get("mode") == "discussion"
        ]
        assert len(user_msgs) >= 1
        assert user_msgs[-1]["content"] == "Test history"

    async def test_assistant_messages_stored_per_worker(self, orchestrator):
        """Each participant's response is stored with worker metadata."""
        await orchestrator.discussion_chat(
            participants=["claude", "gemini"],
            message="Store test",
        )
        disc_msgs = [
            h for h in orchestrator.chat_history
            if h["role"] == "assistant"
            and (h.get("metadata") or {}).get("mode") == "discussion"
        ]
        assert len(disc_msgs) >= 2
        workers = [h["metadata"]["worker"] for h in disc_msgs]
        assert "claude" in workers
        assert "gemini" in workers

    async def test_discussion_id_consistent(self, orchestrator):
        """All messages in a round share the same discussion_id."""
        result = await orchestrator.discussion_chat(
            participants=["claude", "gemini"],
            message="ID test",
        )
        disc_id = result["discussion_id"]
        disc_msgs = [
            h for h in orchestrator.chat_history
            if (h.get("metadata") or {}).get("discussion_id") == disc_id
        ]
        # 1 user + 2 assistant = 3 messages
        assert len(disc_msgs) == 3

    async def test_history_persisted_to_disk(self, orchestrator):
        """Chat history should be written to disk after discussion."""
        await orchestrator.discussion_chat(
            participants=["claude"],
            message="Disk test",
        )
        assert orchestrator._history_file.exists()
        data = json.loads(orchestrator._history_file.read_text())
        disc_msgs = [
            m for m in data
            if (m.get("metadata") or {}).get("mode") == "discussion"
        ]
        assert len(disc_msgs) >= 2  # user + assistant


# ═══════════════════════════════════════════════════
# TEST 3: Sequential Context — Each Model Sees Prior Responses
# ═══════════════════════════════════════════════════


class TestDiscussionSequentialContext:
    """Tests that each model sees prior responses in the round."""

    async def test_second_model_sees_first_response(self, orchestrator):
        """The second participant's prompt should include the first model's response."""
        calls = []
        for name in ["claude", "gemini"]:
            worker = orchestrator.router.workers[name]
            original_send = worker.send_message

            async def capture_send(msg, system_prompt=None, files=None,
                                   _name=name, _orig=original_send):
                calls.append({"worker": _name, "prompt": msg})
                return await _orig(msg, system_prompt=system_prompt, files=files)

            worker.send_message = capture_send

        await orchestrator.discussion_chat(
            participants=["claude", "gemini"],
            message="What is TDD?",
        )

        assert len(calls) == 2
        # First model (claude) should NOT see prior responses
        assert "gemini:" not in calls[0]["prompt"].lower()
        # Second model (gemini) SHOULD see claude's response
        assert "claude:" in calls[1]["prompt"] or "Response from claude" in calls[1]["prompt"]

    async def test_third_model_sees_both_prior(self, orchestrator):
        """Third participant should see both prior responses."""
        calls = []
        for name in ["claude", "gemini", "deepseek"]:
            worker = orchestrator.router.workers[name]

            async def capture_send(msg, system_prompt=None, files=None,
                                   _name=name):
                calls.append({"worker": _name, "prompt": msg})
                return {"success": True, "response": f"Reply from {_name}",
                         "elapsed_ms": 100}

            worker.send_message = capture_send

        await orchestrator.discussion_chat(
            participants=["claude", "gemini", "deepseek"],
            message="Debate microservices",
        )

        assert len(calls) == 3
        # deepseek (3rd) should see both claude and gemini responses
        assert "claude:" in calls[2]["prompt"] or "Reply from claude" in calls[2]["prompt"]
        assert "gemini:" in calls[2]["prompt"] or "Reply from gemini" in calls[2]["prompt"]


# ═══════════════════════════════════════════════════
# TEST 4: Discussion System Prompt
# ═══════════════════════════════════════════════════


class TestDiscussionSystemPrompt:
    """Tests that the system prompt correctly identifies each model."""

    async def test_system_prompt_contains_worker_name(self, orchestrator):
        """System prompt should tell the model its identity."""
        captured_system = []
        worker = orchestrator.router.workers["claude"]

        async def capture_send(msg, system_prompt=None, files=None):
            captured_system.append(system_prompt)
            return {"success": True, "response": "OK", "elapsed_ms": 50}

        worker.send_message = capture_send

        await orchestrator.discussion_chat(
            participants=["claude"],
            message="Test",
        )

        assert len(captured_system) == 1
        assert "claude" in captured_system[0]
        assert "multi-AI discussion" in captured_system[0]


# ═══════════════════════════════════════════════════
# TEST 5: Error Handling — Skip on Failure
# ═══════════════════════════════════════════════════


class TestDiscussionErrorHandling:
    """Tests that discussion handles worker failures gracefully."""

    async def test_missing_worker_skipped(self, orchestrator):
        """If a participant isn't in the router, skip with message."""
        result = await orchestrator.discussion_chat(
            participants=["claude", "nonexistent_model", "gemini"],
            message="Skip test",
        )
        # Should still get responses from claude and gemini
        workers = [r["worker"] for r in result["responses"]]
        assert "claude" in workers
        assert "gemini" in workers
        assert "nonexistent_model" not in workers

    async def test_missing_worker_error_in_history(self, orchestrator):
        """Missing worker should produce an error message in history."""
        await orchestrator.discussion_chat(
            participants=["nonexistent_model"],
            message="Error test",
        )
        error_msgs = [
            h for h in orchestrator.chat_history
            if h["role"] == "assistant"
            and (h.get("metadata") or {}).get("error") is True
        ]
        assert len(error_msgs) >= 1
        assert "unavailable" in error_msgs[0]["content"] or "skipping" in error_msgs[0]["content"]

    async def test_exception_worker_skipped(self, orchestrator, mock_worker_failure):
        """Worker that raises exception should be skipped."""
        orchestrator.router.workers["claude"] = mock_worker_failure("claude")

        result = await orchestrator.discussion_chat(
            participants=["claude", "gemini"],
            message="Exception test",
        )
        workers = [r["worker"] for r in result["responses"]]
        assert "claude" not in workers
        assert "gemini" in workers

    async def test_error_response_worker_skipped(self, orchestrator, mock_worker_error_response):
        """Worker that returns success=False should be skipped."""
        orchestrator.router.workers["claude"] = mock_worker_error_response("claude")

        result = await orchestrator.discussion_chat(
            participants=["claude", "gemini"],
            message="API error test",
        )
        workers = [r["worker"] for r in result["responses"]]
        assert "claude" not in workers
        assert "gemini" in workers

    async def test_all_workers_fail_returns_empty(self, orchestrator, mock_worker_failure):
        """If all workers fail, return empty responses list."""
        orchestrator.router.workers["claude"] = mock_worker_failure("claude")
        orchestrator.router.workers["gemini"] = mock_worker_failure("gemini")

        result = await orchestrator.discussion_chat(
            participants=["claude", "gemini"],
            message="All fail test",
        )
        assert result["responses"] == []


# ═══════════════════════════════════════════════════
# TEST 6: Cancellation — cancel_discussion_round()
# ═══════════════════════════════════════════════════


class TestDiscussionCancellation:
    """Tests for discussion round cancellation."""

    async def test_cancel_stops_after_current(self, orchestrator):
        """cancel_discussion_round should stop loop after current model finishes."""
        call_order = []

        async def slow_claude(msg, system_prompt=None, files=None):
            call_order.append("claude")
            # Cancel after first model responds
            orchestrator.cancel_discussion_round()
            return {"success": True, "response": "Claude reply", "elapsed_ms": 100}

        orchestrator.router.workers["claude"].send_message = slow_claude

        result = await orchestrator.discussion_chat(
            participants=["claude", "gemini", "deepseek"],
            message="Cancel test",
        )

        assert result["cancelled"] is True
        assert len(result["responses"]) == 1
        assert result["responses"][0]["worker"] == "claude"
        assert "gemini" not in [r["worker"] for r in result["responses"]]

    async def test_cancel_resets_between_rounds(self, orchestrator):
        """After cancellation, next discussion_chat should work normally."""
        orchestrator.cancel_discussion_round()

        result = await orchestrator.discussion_chat(
            participants=["claude", "gemini"],
            message="After cancel",
        )
        # cancel event is cleared at start of discussion_chat
        assert not result["cancelled"]
        assert len(result["responses"]) == 2


# ═══════════════════════════════════════════════════
# TEST 7: on_response Callback
# ═══════════════════════════════════════════════════


class TestDiscussionCallback:
    """Tests that on_response callback fires correctly."""

    async def test_callback_called_per_response(self, orchestrator):
        """on_response should be called once per participant."""
        callbacks = []

        async def on_resp(worker, text, elapsed):
            callbacks.append({"worker": worker, "text": text, "elapsed": elapsed})

        await orchestrator.discussion_chat(
            participants=["claude", "gemini"],
            message="Callback test",
            on_response=on_resp,
        )

        assert len(callbacks) == 2
        assert callbacks[0]["worker"] == "claude"
        assert callbacks[1]["worker"] == "gemini"
        assert callbacks[0]["elapsed"] == 150  # from mock

    async def test_callback_called_for_errors(self, orchestrator):
        """on_response should fire even for error/skip messages."""
        callbacks = []

        async def on_resp(worker, text, elapsed):
            callbacks.append({"worker": worker, "text": text})

        await orchestrator.discussion_chat(
            participants=["nonexistent_model", "claude"],
            message="Error callback test",
            on_response=on_resp,
        )

        assert len(callbacks) == 2
        assert "unavailable" in callbacks[0]["text"] or "skipping" in callbacks[0]["text"]


# ═══════════════════════════════════════════════════
# TEST 8: Conversation Context for Discussion Mode
# ═══════════════════════════════════════════════════


class TestDiscussionContext:
    """Tests for _build_conversation_context with discussion mode."""

    async def test_context_labels_with_worker_names(self, orchestrator):
        """Discussion history should label assistant lines with worker name."""
        await orchestrator.discussion_chat(
            participants=["claude"],
            message="Context label test",
        )
        context = orchestrator._build_conversation_context("discussion", "")
        # Should contain "claude:" not "Assistant:"
        assert "claude:" in context
        assert "User:" in context

    async def test_context_shows_all_participants(self, orchestrator):
        """Discussion context should not filter by single worker."""
        await orchestrator.discussion_chat(
            participants=["claude", "gemini"],
            message="Multi-participant context test",
        )
        context = orchestrator._build_conversation_context("discussion", "")
        assert "claude:" in context
        assert "gemini:" in context

    async def test_context_does_not_mix_modes(self, orchestrator):
        """Discussion context should not include direct/project messages."""
        # Add a direct chat message
        orchestrator._append_history("user", "Direct msg", metadata={
            "mode": "direct", "worker": "claude",
        })
        orchestrator._append_history("assistant", "Direct resp", metadata={
            "mode": "direct", "worker": "claude",
        })
        # Add discussion message
        await orchestrator.discussion_chat(
            participants=["claude"],
            message="Discussion msg",
        )

        context = orchestrator._build_conversation_context("discussion", "")
        assert "Direct msg" not in context
        assert "Discussion msg" in context


# ═══════════════════════════════════════════════════
# TEST 9: Phi3 Summary Integration
# ═══════════════════════════════════════════════════


class TestDiscussionPhi3:
    """Tests for Phi3 summary queuing after discussion round."""

    async def test_phi3_queued_after_round(self, orchestrator):
        """Phi3.queue_summary should be called with combined responses."""
        mock_phi3 = MagicMock()
        mock_phi3.queue_summary = AsyncMock()
        orchestrator.phi3 = mock_phi3

        await orchestrator.discussion_chat(
            participants=["claude", "gemini"],
            message="Summary test",
        )

        mock_phi3.queue_summary.assert_called_once()
        call_kwargs = mock_phi3.queue_summary.call_args
        assert call_kwargs.kwargs["user_query"] == "Summary test"
        assert "[claude]" in call_kwargs.kwargs["llm_response"]
        assert "[gemini]" in call_kwargs.kwargs["llm_response"]

    async def test_phi3_not_queued_when_all_fail(self, orchestrator, mock_worker_failure):
        """Phi3 should not be called if no successful responses."""
        mock_phi3 = MagicMock()
        mock_phi3.queue_summary = AsyncMock()
        orchestrator.phi3 = mock_phi3
        orchestrator.router.workers["claude"] = mock_worker_failure("claude")

        await orchestrator.discussion_chat(
            participants=["claude"],
            message="Fail test",
        )

        mock_phi3.queue_summary.assert_not_called()


# ═══════════════════════════════════════════════════
# TEST 10: Dashboard HTML — Discussion Tab Exists
# ═══════════════════════════════════════════════════


class TestDashboardHTML:
    """Tests that the dashboard HTML contains discussion mode elements."""

    def test_discussion_tab_in_html(self):
        """Dashboard HTML should contain Discussion tab button."""
        from dashboard.dashboard_server import DASHBOARD_HTML
        assert 'id="tab-discussion"' in DASHBOARD_HTML
        assert "Discussion" in DASHBOARD_HTML

    def test_participant_panel_in_html(self):
        """Dashboard HTML should contain participant checkboxes panel."""
        from dashboard.dashboard_server import DASHBOARD_HTML
        assert 'id="participantPanel"' in DASHBOARD_HTML
        assert 'id="participantList"' in DASHBOARD_HTML

    def test_discussion_css_in_html(self):
        """Dashboard HTML should contain discussion-specific CSS."""
        from dashboard.dashboard_server import DASHBOARD_HTML
        assert "discussion-active" in DASHBOARD_HTML
        assert "participant-chk" in DASHBOARD_HTML
        assert "data-worker" in DASHBOARD_HTML

    def test_discussion_js_mode_handler(self):
        """JS setMode should handle discussion case."""
        from dashboard.dashboard_server import DASHBOARD_HTML
        assert "mode==='discussion'" in DASHBOARD_HTML
        assert "discussion_chat" in DASHBOARD_HTML
        assert "discussion_cancel" in DASHBOARD_HTML
        assert "discussionInProgress" in DASHBOARD_HTML

    def test_discussion_ws_events_handled(self):
        """JS should handle discussion_start and discussion_end events."""
        from dashboard.dashboard_server import DASHBOARD_HTML
        assert "discussion_start" in DASHBOARD_HTML
        assert "discussion_end" in DASHBOARD_HTML

    def test_participant_checkbox_functions(self):
        """JS should have toggleParticipant and getSelectedParticipants."""
        from dashboard.dashboard_server import DASHBOARD_HTML
        assert "toggleParticipant" in DASHBOARD_HTML
        assert "getSelectedParticipants" in DASHBOARD_HTML

    def test_worker_colored_bubbles_css(self):
        """CSS should define worker-specific border colors."""
        from dashboard.dashboard_server import DASHBOARD_HTML
        for worker in ["claude", "gemini", "kimi", "deepseek", "qwen"]:
            assert f'data-worker="{worker}"' in DASHBOARD_HTML

    def test_mode_badge_discussion(self):
        """Mode badge for discussion should exist."""
        from dashboard.dashboard_server import DASHBOARD_HTML
        assert "mode-badge discussion" in DASHBOARD_HTML


# ═══════════════════════════════════════════════════
# TEST 11: Dashboard Server Routes
# ═══════════════════════════════════════════════════


class TestDashboardRoutes:
    """Tests that the dashboard server registers discussion routes."""

    def test_discussion_rest_route_registered(self):
        """POST /api/chat/discussion route should be registered."""
        from dashboard.dashboard_server import DashboardServer
        import inspect
        source = inspect.getsource(DashboardServer.start)
        assert "/api/chat/discussion" in source

    def test_discussion_ws_action_handled(self):
        """WS action 'discussion_chat' should be handled."""
        from dashboard.dashboard_server import DashboardServer
        import inspect
        source = inspect.getsource(DashboardServer._websocket)
        assert "discussion_chat" in source
        assert "discussion_cancel" in source

    def test_handle_ws_discussion_method_exists(self):
        """_handle_ws_discussion method should exist."""
        from dashboard.dashboard_server import DashboardServer
        assert hasattr(DashboardServer, "_handle_ws_discussion")
        import inspect
        assert inspect.iscoroutinefunction(DashboardServer._handle_ws_discussion)

    def test_api_chat_discussion_method_exists(self):
        """_api_chat_discussion method should exist."""
        from dashboard.dashboard_server import DashboardServer
        assert hasattr(DashboardServer, "_api_chat_discussion")
        import inspect
        assert inspect.iscoroutinefunction(DashboardServer._api_chat_discussion)


# ═══════════════════════════════════════════════════
# TEST 12: Auto-Loop Discussion
# ═══════════════════════════════════════════════════


class TestDiscussionAutoLoop:
    """Tests for auto_loop continuous discussion."""

    async def test_auto_loop_runs_multiple_rounds(self, orchestrator):
        """auto_loop=True should run multiple rounds until cancelled."""
        call_count = 0

        async def counting_send(msg, system_prompt=None, files=None):
            nonlocal call_count
            call_count += 1
            # Cancel after round 2 starts (4 calls = 2 rounds x 2 participants)
            if call_count >= 4:
                orchestrator.cancel_discussion_round()
            return {"success": True, "response": f"Reply {call_count}", "elapsed_ms": 50}

        orchestrator.router.workers["claude"].send_message = counting_send
        orchestrator.router.workers["gemini"].send_message = counting_send

        result = await orchestrator.discussion_chat(
            participants=["claude", "gemini"],
            message="Loop test",
            auto_loop=True,
        )
        assert result["rounds"] >= 2
        assert result["cancelled"] is True
        assert len(result["responses"]) >= 4

    async def test_auto_loop_false_single_round(self, orchestrator):
        """auto_loop=False (default) should do exactly one round."""
        result = await orchestrator.discussion_chat(
            participants=["claude", "gemini"],
            message="No loop test",
            auto_loop=False,
        )
        assert result["rounds"] == 1
        assert len(result["responses"]) == 2

    async def test_auto_loop_stops_if_all_fail(self, orchestrator, mock_worker_failure):
        """Auto-loop should stop if all workers fail in a round."""
        orchestrator.router.workers["claude"] = mock_worker_failure("claude")
        orchestrator.router.workers["gemini"] = mock_worker_failure("gemini")

        result = await orchestrator.discussion_chat(
            participants=["claude", "gemini"],
            message="All fail loop",
            auto_loop=True,
        )
        assert result["rounds"] == 1
        assert result["responses"] == []

    def test_you_checkbox_in_html(self):
        """Dashboard should have a You checkbox."""
        from dashboard.dashboard_server import DASHBOARD_HTML
        assert 'id="youToggle"' in DASHBOARD_HTML
        assert "isAutoLoop" in DASHBOARD_HTML
        assert "auto_loop" in DASHBOARD_HTML


# ═══════════════════════════════════════════════════
# TEST: Kimi Parser — TextPart extraction (Bug 1)
# ═══════════════════════════════════════════════════


class TestKimiParser:
    """Tests for _parse_response() handling Kimi's raw protocol output."""

    def _make_adapter(self):
        from workers.adapters import CLIWorkerAdapter
        return CLIWorkerAdapter("kimi", {"cli_command": "kimi", "timeout": 30})

    def test_clean_text_passes_through(self):
        """Non-Kimi output should pass through unchanged."""
        adapter = self._make_adapter()
        assert adapter._parse_response("Hello world") == "Hello world"

    def test_simple_textpart_extraction(self):
        """Simple TextPart with no special characters should be extracted."""
        adapter = self._make_adapter()
        raw = "TurnBegin()\nTextPart( type='text', text='Hello from Kimi')\nTurnEnd()"
        assert adapter._parse_response(raw) == "Hello from Kimi"

    def test_textpart_with_escaped_quotes(self):
        """TextPart containing escaped single quotes must not truncate."""
        adapter = self._make_adapter()
        raw = r"TextPart( type='text', text='It\'s a beautiful day, isn\'t it?')"
        result = adapter._parse_response(raw)
        assert "It's a beautiful day" in result
        assert "isn't it?" in result

    def test_textpart_multiline_with_escaped_quotes(self):
        """Multiline TextPart with escaped quotes and newlines."""
        adapter = self._make_adapter()
        raw = (
            "TurnBegin()\n"
            "ThinkPart( type='think', text='thinking...')\n"
            r"TextPart( type='text', text='Line one.\nLine two.\nDon\'t stop.')" "\n"
            "TurnEnd()"
        )
        result = adapter._parse_response(raw)
        assert "Line one." in result
        assert "Line two." in result
        assert "Don't stop." in result

    def test_multiple_textparts_joined(self):
        """Multiple TextPart blocks should be joined with double newline."""
        adapter = self._make_adapter()
        raw = (
            "TextPart( type='text', text='First part')\n"
            "StatusUpdate()\n"
            "TextPart( type='text', text='Second part')"
        )
        result = adapter._parse_response(raw)
        assert "First part" in result
        assert "Second part" in result

    def test_textpart_escaped_quote_before_comma_in_content(self):
        """
        Escaped quote followed by comma in content must NOT cause early
        truncation. This is the actual Kimi failure mode: \\', in the text
        matches the regex terminator pattern '(?:,|...).
        """
        adapter = self._make_adapter()
        # Content: "Choose option a', then proceed to step b"
        # In Kimi protocol: the ' is escaped as \'
        raw = r"TextPart( type='text', text='Choose option a\', then proceed to step b')"
        result = adapter._parse_response(raw)
        assert "then proceed to step b" in result, (
            f"Regex truncated at escaped quote. Got: {result!r}"
        )

    def test_textpart_escaped_quote_before_paren_in_content(self):
        """
        Escaped quote followed by ) in content must NOT cause early truncation.
        """
        adapter = self._make_adapter()
        # Content: "func('x') returns y"
        raw = r"TextPart( type='text', text='func(\'x\') returns y')"
        result = adapter._parse_response(raw)
        assert "returns y" in result, (
            f"Regex truncated at escaped quote. Got: {result!r}"
        )


# ═══════════════════════════════════════════════════
# TEST: Mid-round participant uncheck (Bug 2)
# ═══════════════════════════════════════════════════


class TestMidRoundUncheck:
    """Tests for removing a participant mid-round."""

    async def test_uncheck_mid_round_skips_worker(self, orchestrator):
        """
        If a participant is removed from _discussion_participants while the
        loop is iterating, they should be skipped (not called).
        """
        call_order = []
        claude_result = {"success": True, "response": "Claude here", "elapsed_ms": 100}
        gemini_result = {"success": True, "response": "Gemini here", "elapsed_ms": 100}

        async def claude_responds_and_unchecks(*args, **kwargs):
            call_order.append("claude")
            # Simulate user unchecking gemini mid-round
            orchestrator.update_discussion_participants(["claude"])
            return claude_result

        async def gemini_responds(*args, **kwargs):
            call_order.append("gemini")
            return gemini_result

        orchestrator.router.workers["claude"].send_message = AsyncMock(
            side_effect=claude_responds_and_unchecks
        )
        orchestrator.router.workers["gemini"].send_message = AsyncMock(
            side_effect=gemini_responds
        )

        result = await orchestrator.discussion_chat(
            participants=["claude", "gemini"],
            message="Test mid-round uncheck",
        )

        # Gemini should NOT have been called
        assert "claude" in call_order
        assert "gemini" not in call_order
        assert len(result["responses"]) == 1

    async def test_uncheck_preserves_already_responded(self, orchestrator):
        """
        If a participant already responded and is then unchecked, their response
        should still be in the results.
        """
        call_order = []
        gemini_result = {"success": True, "response": "Gemini here", "elapsed_ms": 100}

        async def gemini_responds_and_unchecks(*args, **kwargs):
            call_order.append("gemini")
            # Uncheck deepseek after gemini responds
            orchestrator.update_discussion_participants(["claude", "gemini"])
            return gemini_result

        orchestrator.router.workers["gemini"].send_message = AsyncMock(
            side_effect=gemini_responds_and_unchecks
        )

        result = await orchestrator.discussion_chat(
            participants=["claude", "gemini", "deepseek"],
            message="Test partial uncheck",
        )

        # claude and gemini responded, deepseek skipped
        assert len(result["responses"]) == 2


# ═══════════════════════════════════════════════════
# TEST: Discussion prompt trimming (Bug 3)
# ═══════════════════════════════════════════════════


class TestDiscussionPromptTrimming:
    """Tests that discussion prompts are trimmed per-worker context budget."""

    async def test_prompt_respects_worker_context_budget(
        self, orchestrator, mock_worker_success
    ):
        """
        A worker with small max_context_tokens should receive a prompt
        that fits within its token budget (max_context_tokens / 2 * 4 chars).
        The history must be built using the TARGET worker's budget, not the
        default 32000.
        """
        # Create a worker with a tiny context window
        small_worker = mock_worker_success("kimi")
        small_worker.config["max_context_tokens"] = 1000  # very small = ~2000 char budget

        orchestrator.router.workers["kimi"] = small_worker

        # Stuff the history with lots of discussion messages.
        # Each message ~400 chars, 60 entries = ~24000 chars total.
        # Default budget (32000 tokens) would allow ~42000 chars of history.
        # Kimi budget (1000 tokens) should allow only ~1300 chars of history.
        for i in range(30):
            orchestrator._append_history("user", f"Message number {i} " * 20, metadata={
                "mode": "discussion", "discussion_id": "disc_old",
            })
            orchestrator._append_history("assistant", f"Response number {i} " * 20, metadata={
                "mode": "discussion", "worker": "claude",
                "discussion_id": "disc_old",
            })

        result = await orchestrator.discussion_chat(
            participants=["kimi"],
            message="Short question",
        )

        assert result["responses"][0]["worker"] == "kimi"
        # Verify the prompt sent to kimi was trimmed to its budget
        call_args = small_worker.send_message.call_args
        prompt_sent = call_args[0][0]  # first positional arg = prompt
        # Kimi budget: 1000 tokens → use 1/2 for prompt → 500 tokens → ~2000 chars
        # Allow overhead for the instruction text appended after history
        max_prompt_chars = 1000 * 4  # generous: full token budget in chars
        assert len(prompt_sent) < max_prompt_chars, (
            f"Prompt ({len(prompt_sent)} chars) exceeds kimi's budget "
            f"({max_prompt_chars} chars). History not trimmed per-worker."
        )

    async def test_large_context_worker_gets_more_history(
        self, orchestrator, mock_worker_success
    ):
        """
        A worker with a large context window should receive more history
        than a worker with a small one, given the same chat history.
        """
        small_worker = mock_worker_success("kimi")
        small_worker.config["max_context_tokens"] = 1000
        large_worker = mock_worker_success("claude")
        large_worker.config["max_context_tokens"] = 200000

        orchestrator.router.workers["kimi"] = small_worker
        orchestrator.router.workers["claude"] = large_worker

        for i in range(30):
            orchestrator._append_history("user", f"Message number {i} " * 20, metadata={
                "mode": "discussion", "discussion_id": "disc_old",
            })
            orchestrator._append_history("assistant", f"Response number {i} " * 20, metadata={
                "mode": "discussion", "worker": "deepseek",
                "discussion_id": "disc_old",
            })

        result = await orchestrator.discussion_chat(
            participants=["kimi", "claude"],
            message="Compare prompts",
        )

        kimi_prompt = small_worker.send_message.call_args[0][0]
        claude_prompt = large_worker.send_message.call_args[0][0]

        # Claude should get a substantially longer prompt than kimi
        assert len(claude_prompt) > len(kimi_prompt) * 2, (
            f"Claude ({len(claude_prompt)} chars) should get much more history "
            f"than kimi ({len(kimi_prompt)} chars)"
        )
