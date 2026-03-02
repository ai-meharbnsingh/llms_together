"""
Wave 7B Tests — Infrastructure hardening fixes.

FER-AF-015: ReadOnlyDB._read_conn() wraps sqlite3.connect with RuntimeError on bad DB.
FER-AF-020: WriteResultBus.create_waiter() uses asyncio.get_running_loop() (not deprecated get_event_loop).
FER-AF-023: MasterWatchdog._ollama_running() reads Ollama URL from config, not hardcoded.
FER-AF-028: DashboardServer.start() raises RuntimeError (not raw OSError) on port conflict.
"""

import asyncio
import os
import sqlite3
import sys
import warnings
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_mock_watchdog(config: dict):
    """Create a MasterWatchdog with a fully mocked config (no file I/O)."""
    from orchestration.master_watchdog import MasterWatchdog

    wdg = object.__new__(MasterWatchdog)
    wdg.config = config
    return wdg


def _make_mock_tcp_site_class(side_effect=None):
    """
    Build a mock TCPSite class whose instances have an async start() method.
    We replace the entire TCPSite class because TCPSite.__init__ requires a
    fully set-up runner (runner.server must not be None).  Mocking only
    AppRunner.setup() leaves runner.server=None and the real __init__ still
    raises before our patched start() is ever reached.
    """
    mock_site_instance = MagicMock()
    if side_effect is not None:
        mock_site_instance.start = AsyncMock(side_effect=side_effect)
    else:
        mock_site_instance.start = AsyncMock(return_value=None)
    mock_tcp_site_class = MagicMock(return_value=mock_site_instance)
    return mock_tcp_site_class


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-015: ReadOnlyDB._read_conn raises RuntimeError for bad DB path
# ─────────────────────────────────────────────────────────────────────────────


def test_read_conn_raises_runtime_error_on_bad_db():
    """
    Passing a non-existent path to ReadOnlyDB and attempting any read
    must raise RuntimeError with a message containing 'Cannot open factory DB'.
    Previously an unhandled sqlite3.OperationalError would escape.
    """
    from orchestration.database import ReadOnlyDB

    bad_path = "/tmp/this_file_does_not_exist_af_7b.db"
    # Ensure the file really does not exist
    if os.path.exists(bad_path):
        os.remove(bad_path)

    ro = ReadOnlyDB(bad_path)
    with pytest.raises(RuntimeError, match="Cannot open factory DB"):
        ro.get_project("any-id")


def test_read_conn_runtime_error_chains_original():
    """RuntimeError.__cause__ is the original sqlite3.OperationalError."""
    from orchestration.database import ReadOnlyDB

    ro = ReadOnlyDB("/tmp/nonexistent_chained_af_7b.db")
    with pytest.raises(RuntimeError) as exc_info:
        ro.get_project("x")

    assert isinstance(exc_info.value.__cause__, sqlite3.OperationalError)


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-020: WriteResultBus.create_waiter uses get_running_loop
# ─────────────────────────────────────────────────────────────────────────────


async def test_create_waiter_uses_running_loop():
    """
    Inside a running event loop, create_waiter() must return an asyncio.Future
    without raising DeprecationWarning or RuntimeError.
    """
    from orchestration.database import WriteResultBus

    bus = WriteResultBus()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fut = bus.create_waiter("test-waiter-id")

    assert isinstance(fut, asyncio.Future)

    # No DeprecationWarning about get_event_loop should have been emitted
    deprecation_warnings = [
        w for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "get_event_loop" in str(w.message).lower()
    ]
    assert deprecation_warnings == [], (
        f"Unexpected DeprecationWarning about get_event_loop: {deprecation_warnings}"
    )

    # Clean up — cancel the future so no warnings about unresolved futures
    fut.cancel()


async def test_create_waiter_future_is_resolvable():
    """Future returned by create_waiter() can be resolved normally."""
    from orchestration.database import WriteResultBus

    bus = WriteResultBus()
    fut = bus.create_waiter("resolve-me")
    bus.resolve("resolve-me", {"ok": True})
    result = await fut
    assert result == {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-023: _ollama_running uses config URL, not hardcoded localhost
# ─────────────────────────────────────────────────────────────────────────────


async def test_ollama_running_uses_config_url():
    """
    When config has workers.phi3.api_base = 'http://custom-host:9999',
    the HTTP call must target 'http://custom-host:9999/api/tags'.
    """
    config = {
        "workers": {
            "phi3": {
                "api_base": "http://custom-host:9999",
            }
        }
    }
    wdg = _make_mock_watchdog(config)

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    captured_urls = []

    def fake_get(url, **kwargs):
        captured_urls.append(url)
        return mock_response

    mock_session.get = fake_get

    import aiohttp
    with patch.object(aiohttp, "ClientSession", return_value=mock_session):
        result = await wdg._ollama_running()

    assert result is True
    assert len(captured_urls) == 1
    assert captured_urls[0] == "http://custom-host:9999/api/tags"


async def test_ollama_running_fallback_to_default():
    """
    When workers.phi3 key is missing from config, the URL must fall back
    to 'http://localhost:11434/api/tags'.
    """
    config = {
        "workers": {
            # phi3 key intentionally absent
            "deepseek": {"type": "local_ollama"}
        }
    }
    wdg = _make_mock_watchdog(config)

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    captured_urls = []

    def fake_get(url, **kwargs):
        captured_urls.append(url)
        return mock_response

    mock_session.get = fake_get

    import aiohttp
    with patch.object(aiohttp, "ClientSession", return_value=mock_session):
        result = await wdg._ollama_running()

    assert result is True
    assert len(captured_urls) == 1
    assert captured_urls[0] == "http://localhost:11434/api/tags"


async def test_ollama_running_strips_trailing_slash():
    """api_base with trailing slash must produce a clean URL without double slash."""
    config = {
        "workers": {
            "phi3": {
                "api_base": "http://custom-host:9999/",
            }
        }
    }
    wdg = _make_mock_watchdog(config)

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    captured_urls = []

    def fake_get(url, **kwargs):
        captured_urls.append(url)
        return mock_response

    mock_session.get = fake_get

    import aiohttp
    with patch.object(aiohttp, "ClientSession", return_value=mock_session):
        result = await wdg._ollama_running()

    assert "//api/tags" not in captured_urls[0]
    assert captured_urls[0].endswith("/api/tags")


# ─────────────────────────────────────────────────────────────────────────────
# FER-AF-028: DashboardServer.start() raises RuntimeError on port conflict
# ─────────────────────────────────────────────────────────────────────────────


async def test_dashboard_port_conflict_raises_runtime_error():
    """
    When aiohttp.web.TCPSite.start raises OSError('address in use'),
    DashboardServer.start() must catch it and raise RuntimeError
    with 'already in use' in the message.
    """
    from dashboard.dashboard_server import DashboardServer
    from orchestration.database import ReadOnlyDB

    rdb = MagicMock(spec=ReadOnlyDB)
    rdb.set_requester = MagicMock()
    cfg = {"dashboard": {"host": "127.0.0.1", "port": 18421}}
    ds = DashboardServer(read_db=rdb, config=cfg)

    import aiohttp.web as web

    mock_tcp_site_class = _make_mock_tcp_site_class(
        side_effect=OSError("address already in use")
    )
    with patch.object(web.AppRunner, "setup", new_callable=AsyncMock):
        with patch.object(web, "TCPSite", mock_tcp_site_class):
            with pytest.raises(RuntimeError, match="already in use"):
                await ds.start()


async def test_dashboard_port_conflict_error_chains_oserror():
    """RuntimeError.__cause__ is the original OSError."""
    from dashboard.dashboard_server import DashboardServer
    from orchestration.database import ReadOnlyDB

    rdb = MagicMock(spec=ReadOnlyDB)
    rdb.set_requester = MagicMock()
    cfg = {"dashboard": {"host": "127.0.0.1", "port": 18422}}
    ds = DashboardServer(read_db=rdb, config=cfg)

    import aiohttp.web as web

    mock_tcp_site_class = _make_mock_tcp_site_class(
        side_effect=OSError("address already in use")
    )
    with patch.object(web.AppRunner, "setup", new_callable=AsyncMock):
        with patch.object(web, "TCPSite", mock_tcp_site_class):
            with pytest.raises(RuntimeError) as exc_info:
                await ds.start()

    assert isinstance(exc_info.value.__cause__, OSError)


async def test_dashboard_start_success_no_error():
    """
    When site.start() succeeds, DashboardServer.start() must not raise.
    Validates the happy path is unaffected by the try/except wrapper.
    """
    from dashboard.dashboard_server import DashboardServer
    from orchestration.database import ReadOnlyDB

    rdb = MagicMock(spec=ReadOnlyDB)
    rdb.set_requester = MagicMock()
    cfg = {"dashboard": {"host": "127.0.0.1", "port": 18423}}
    ds = DashboardServer(read_db=rdb, config=cfg)

    import aiohttp.web as web

    mock_tcp_site_class = _make_mock_tcp_site_class(side_effect=None)
    with patch.object(web.AppRunner, "setup", new_callable=AsyncMock):
        with patch.object(web, "TCPSite", mock_tcp_site_class):
            # Should complete without raising
            await ds.start()

    # Broadcast task was created
    assert hasattr(ds, "_broadcast_task")
    ds._broadcast_task.cancel()
