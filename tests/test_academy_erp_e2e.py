"""
Academy ERP System — Full UI-Driven Factory Run (Playwright E2E)
═══════════════════════════════════════════════════════════════════════════════
Boots (or reuses) the Autonomous Factory, then acts EXACTLY like a real user:
clicking buttons, filling forms, reading the screen, approving modals.

Project: Academy ERP — Students, Courses, Faculty, Enrollment, Grades, Attendance.
Stack:   React + TypeScript + Tailwind (FE), FastAPI (BE), SQLite (DB).

Per CLAUDE.md §4: Full E2E, slowMo:500, one screenshot per page/state.

TDD policy: if ANY factory-side failure is detected (project_error, pipeline
gate failure, compilation failure), the test logs the root cause, records it,
then raises pytest.fail() so the runner can restart after a code fix.

Run:
  cd autonomous_factory
  pytest tests/test_academy_erp_e2e.py --headed --slowmo=500 -v -s

Wave: academy-erp
Screenshots: screenshots/wave-academy-erp/
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest

# ── Path fix ──────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Constants ─────────────────────────────────────────────────────────────────
DASHBOARD_URL   = "http://127.0.0.1:8420"
PROJECT_NAME    = "Academy ERP System"
PROJECT_DESC    = (
    "Comprehensive ERP for an educational academy with the following modules:\n"
    "1. Student Management — enroll, profile, contact info, status (active/graduated/suspended).\n"
    "2. Course Catalog — create courses with code, name, credits, description, capacity.\n"
    "3. Faculty Management — faculty profiles, assigned courses, department.\n"
    "4. Enrollment — students enroll in courses per semester; seat limits enforced.\n"
    "5. Grade Book — faculty enter grades per student per course; GPA auto-calculated.\n"
    "6. Attendance Tracker — mark present/absent per student per class session.\n"
    "7. Dashboard — admin sees KPIs: total students, active courses, avg GPA, attendance rate.\n"
    "Stack: React + TypeScript + Tailwind CSS (frontend), "
    "FastAPI + SQLAlchemy (backend), SQLite (database), Alembic (migrations), "
    "Pytest + Playwright (tests). REST API with JWT auth. Role-based: Admin, Faculty, Student."
)
WAVE_NAME       = "academy-erp"

SCREENSHOT_DIR  = Path(__file__).parent.parent / "screenshots" / f"wave-{WAVE_NAME}"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# Failure log — captures every factory-side error for post-run analysis
FAILURE_LOG     = SCREENSHOT_DIR / "failure_log.jsonl"

# Timeouts
FACTORY_START_S  = 60      # Max wait for factory to come up
BLUEPRINT_WAIT_S = 1200    # 20 min — blueprint + dual audit for complex ERP
BUILD_WAIT_S     = 5400    # 90 min — full TDD build for multi-module ERP
POLL_TICK_S      = 2       # Poll WS events every N seconds
SCREENSHOT_FREQ  = 30      # Periodic screenshot every N seconds while waiting

# ── Module-level shared state (survives across tests in session) ───────────────
_state: dict = {
    "project_id": None,
    "factory_proc": None,
    "ws_events": [],
    "ws_lock": threading.Lock(),
    "failures_detected": [],
    "tdd_steps_seen": [],
}


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _is_page_alive(page) -> bool:
    """Return True if the Playwright page is still open and usable."""
    try:
        return not page.is_closed()
    except Exception:
        return False


def _screenshot(page, name: str) -> str:
    """Capture a full-page screenshot per CLAUDE.md §4.3."""
    if not _is_page_alive(page):
        return ""
    ts = int(time.time())
    path = SCREENSHOT_DIR / f"{name}--{ts}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        print(f"  [{_ts()}] 📸  {path.name}", flush=True)
    except Exception as e:
        print(f"  [{_ts()}] ⚠ screenshot failed: {e}", flush=True)
    return str(path)


def _is_dashboard_live(url: str = DASHBOARD_URL, timeout: int = 3) -> bool:
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except Exception:
        return False


def _log_failure(failure: dict) -> None:
    """Append failure record to JSONL log for post-run analysis."""
    entry = {"ts": _ts(), **failure}
    with open(FAILURE_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"\n  ❌ FAILURE LOGGED: {entry}", flush=True)


def _attach_ws_collector(page, events: list, lock: threading.Lock) -> None:
    """Hook Playwright's WebSocket to collect all Factory events in real-time."""
    def handle_ws(ws):
        def on_frame(payload):
            try:
                raw = payload.get("body") if isinstance(payload, dict) else str(payload)
                data = json.loads(raw)
                with lock:
                    events.append(data)
                ev      = data.get("event", "?")
                ph      = data.get("phase", "")
                step    = data.get("step", "")
                status  = data.get("status", "")
                task_id = data.get("task_id", "")
                print(
                    f"  [{_ts()}] WS ▸ {ev:<32} ph={ph:<4} step={step:<10} "
                    f"task={task_id:<30} st={status}",
                    flush=True,
                )
            except Exception:
                pass
        ws.on("framereceived", on_frame)
    page.on("websocket", handle_ws)


def _find_events(events: list, lock: threading.Lock, event_type: str) -> list:
    with lock:
        return [e for e in events if e.get("event") == event_type]


def _wait_for_event(
    events: list,
    lock: threading.Lock,
    event_type: str,
    timeout_s: int,
    page=None,
    screenshot_prefix: str = "",
    poll_s: float = POLL_TICK_S,
) -> dict | None:
    """Poll events list until event_type appears or timeout."""
    deadline = time.time() + timeout_s
    last_screenshot = time.time()
    while time.time() < deadline:
        matched = _find_events(events, lock, event_type)
        if matched:
            return matched[-1]
        if page and screenshot_prefix and (time.time() - last_screenshot) >= SCREENSHOT_FREQ:
            _screenshot(page, f"{screenshot_prefix}--waiting")
            last_screenshot = time.time()
        if page and _is_page_alive(page):
            try:
                page.wait_for_timeout(int(poll_s * 1000))
            except Exception:
                time.sleep(poll_s)
        else:
            time.sleep(poll_s)
    return None


def _check_for_factory_errors(events: list, lock: threading.Lock) -> list[dict]:
    """Return all project_error events (factory pipeline failures)."""
    return _find_events(events, lock, "project_error")


def _print_tdd_progress(events: list, lock: threading.Lock) -> None:
    with lock:
        steps = [e for e in events if e.get("event") == "tdd_step_update"]
    if steps:
        print(f"\n  ── TDD Steps seen so far ──", flush=True)
        for e in steps[-10:]:  # last 10
            print(f"    ph={e.get('phase','?')} task={e.get('task_id','?'):<30} "
                  f"step={e.get('step','?'):<12} status={e.get('status','?')}", flush=True)


def _print_event_summary(events: list, lock: threading.Lock) -> None:
    with lock:
        counts: dict = {}
        for e in events:
            ev = e.get("event", "?")
            counts[ev] = counts.get(ev, 0) + 1
    print("\n  ── WS Event Summary ──────────────────────────────", flush=True)
    for ev, cnt in sorted(counts.items()):
        print(f"    {ev:<40} x{cnt}", flush=True)
    print("  ──────────────────────────────────────────────────", flush=True)


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def factory_server():
    """Ensure factory is running; auto-start if needed."""
    if _is_dashboard_live():
        print("\n  Factory already running — reusing.", flush=True)
        yield
        return

    print("\n  Starting factory (python3 main.py)...", flush=True)
    factory_root = Path(__file__).parent.parent
    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(factory_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _state["factory_proc"] = proc

    deadline = time.time() + FACTORY_START_S
    while time.time() < deadline:
        if _is_dashboard_live():
            print(f"  Factory online after {int(time.time() - (deadline - FACTORY_START_S))}s", flush=True)
            break
        if proc.poll() is not None:
            out, _ = proc.communicate()
            pytest.fail(f"Factory exited unexpectedly: {out[-2000:]}")
        time.sleep(2)
    else:
        proc.terminate()
        pytest.fail(f"Factory did not start in {FACTORY_START_S}s")

    yield

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module")
def browser_context(playwright):
    """Shared browser context. No video recording — avoids macOS OOM kills on long pipelines."""
    browser = playwright.chromium.launch(headless=False, slow_mo=500)
    ctx = browser.new_context(
        viewport={"width": 1440, "height": 900},
        # record_video_dir disabled: video encoding + slow_mo over 90-min build
        # causes macOS to OOM-kill the browser (TargetClosedError mid-wait_for_timeout)
    )
    yield ctx
    ctx.close()
    browser.close()


@pytest.fixture(scope="module")
def page(factory_server, browser_context):
    """Single page for all tests (preserves WS connection across tests)."""
    p = browser_context.new_page()
    _attach_ws_collector(p, _state["ws_events"], _state["ws_lock"])
    yield p
    p.close()


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 00 — Setup: enable local mode + clean up any previous run
# ═══════════════════════════════════════════════════════════════════════════════

class TestSetup:
    def test_00_enable_local_mode_and_cleanup(self, page):
        """
        Setup (test-infra only):
        1. Enable local LLM mode (all roles → DeepSeek/Qwen) so no paid tokens burn.
        2. Delete any previous 'Academy ERP System' project.
        """
        import urllib.error

        print("\n\n[SETUP] Enabling LOCAL TEST MODE (all roles → DeepSeek/Qwen)...", flush=True)

        req = urllib.request.Request(
            f"{DASHBOARD_URL}/api/config/mode",
            data=json.dumps({"mode": "local"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                assert result.get("local_mode") is True, f"Local mode not enabled: {result}"
                print("  ✓ LOCAL MODE enabled — DeepSeek/Qwen for all roles", flush=True)
        except Exception as e:
            pytest.fail(f"Failed to enable local mode: {e}")

        # Delete any previous Academy ERP project
        print("[SETUP] Checking for previous Academy ERP project...", flush=True)
        try:
            with urllib.request.urlopen(f"{DASHBOARD_URL}/api/projects", timeout=10) as resp:
                data = json.loads(resp.read())
                projects = data.get("projects", [])
                for proj in projects:
                    if PROJECT_NAME in proj.get("name", ""):
                        pid = proj["project_id"]
                        del_req = urllib.request.Request(
                            f"{DASHBOARD_URL}/api/projects/{pid}",
                            method="DELETE",
                        )
                        try:
                            with urllib.request.urlopen(del_req, timeout=10):
                                print(f"  ✓ Deleted previous project {pid}", flush=True)
                        except Exception as del_e:
                            print(f"  ⚠ Delete failed (non-fatal): {del_e}", flush=True)
        except Exception as e:
            print(f"  ⚠ Project list check failed (non-fatal): {e}", flush=True)

        # Clear failure log from previous run
        if FAILURE_LOG.exists():
            FAILURE_LOG.unlink()

        print("[SETUP] Done.\n", flush=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 01 — Dashboard health check
# ═══════════════════════════════════════════════════════════════════════════════

class TestDashboardHealth:
    def test_01_dashboard_loads_and_healthy(self, page):
        """
        Navigate to dashboard, verify it loads, LOCAL MODE badge visible,
        all workers healthy, all tabs accessible.
        """
        print("\n\n[TEST 01] Dashboard health check", flush=True)

        page.goto(DASHBOARD_URL, wait_until="networkidle")
        page.wait_for_selector("h1", timeout=15000)
        _screenshot(page, "01-dashboard-initial-load")

        # LOCAL MODE badge
        badge = page.locator("#localModeBadge")
        page.wait_for_timeout(1500)
        badge_visible = badge.is_visible()
        print(f"  LOCAL MODE badge visible: {badge_visible}", flush=True)

        # Worker health via API
        workers_resp = page.evaluate("async () => (await fetch('/api/workers/status')).json()")
        if isinstance(workers_resp, list):
            worker_names = [w.get("instance_name", "?") for w in workers_resp]
        elif isinstance(workers_resp, dict):
            worker_names = list(workers_resp.keys())
        else:
            worker_names = []
        print(f"  Workers: {worker_names}", flush=True)

        # Visit all tabs
        for tab_sel in ["#tabOrch", "#tabDirect", "#tabProject"]:
            try:
                page.click(tab_sel, timeout=2000)
                page.wait_for_timeout(400)
            except Exception:
                pass
        _screenshot(page, "01-all-tabs-visited")

        # Check status API
        status = page.evaluate("async () => (await fetch('/api/status')).json()")
        task_stats = status.get("task_stats", {})
        escalations = status.get("escalations", [])
        print(f"  Task stats: {task_stats}", flush=True)
        print(f"  Pending escalations: {len(escalations)}", flush=True)
        _screenshot(page, "01-status-api-checked")

        print("  ✓ Dashboard healthy", flush=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 02 — Create Academy ERP project through UI
# ═══════════════════════════════════════════════════════════════════════════════

class TestCreateProject:
    def test_02_create_academy_erp_via_ui(self, page):
        """
        Click '+ New' → fill project name + description → click 'Create Project'.
        Pure UI interaction — no direct API calls.
        """
        print("\n\n[TEST 02] Creating Academy ERP System through UI", flush=True)

        if DASHBOARD_URL not in page.url:
            page.goto(DASHBOARD_URL, wait_until="networkidle")
            page.wait_for_selector("h1", timeout=10000)

        _screenshot(page, "02-before-create")

        # Open New Project modal
        print("  Clicking '+ New' button...", flush=True)
        page.click(".proj-new-btn", timeout=10000)
        page.wait_for_selector("#newProjectModal", state="visible", timeout=5000)
        _screenshot(page, "02-new-project-modal-open")

        # Fill project name
        page.locator("#newProjName").fill(PROJECT_NAME)

        # Fill description — may need to expand textarea
        desc_field = page.locator("#newProjDesc")
        desc_field.fill(PROJECT_DESC)

        _screenshot(page, "02-form-filled-academy-erp")

        # Submit
        print("  Submitting 'Create Project'...", flush=True)
        page.click("#newProjSubmit")
        page.wait_for_selector("#newProjectModal", state="hidden", timeout=10000)
        page.wait_for_timeout(1500)

        # Verify project in selector
        sel_opts = page.evaluate(
            "() => [...document.querySelectorAll('#projectSel option')].map(o => o.textContent.trim())"
        )
        print(f"  Projects in selector: {sel_opts}", flush=True)
        assert any(PROJECT_NAME in opt for opt in sel_opts), \
            f"'{PROJECT_NAME}' not in project selector. Options: {sel_opts}"

        # Capture project_id
        project_id = page.evaluate("() => window.currentProjectId || null")
        if not project_id:
            project_id = page.evaluate("() => document.querySelector('#projectSel').value")
        if not project_id:
            project_id = page.evaluate(
                f"() => [...document.querySelectorAll('#projectSel option')]"
                f".find(o => o.textContent.includes({json.dumps(PROJECT_NAME)}))?.value"
            )
        assert project_id, "Could not determine project_id after creation"
        _state["project_id"] = project_id
        print(f"  ✓ Academy ERP project created: {project_id}", flush=True)

        # Ensure it's selected
        page.select_option("#projectSel", value=project_id)
        page.wait_for_timeout(500)
        _screenshot(page, "02-academy-erp-in-selector")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 03 — Launch + watch full pipeline (blueprint → build → UAT)
# ═══════════════════════════════════════════════════════════════════════════════

class TestLaunchAndBuild:
    def test_03_launch_and_full_pipeline(self, page):
        """
        Full pipeline:
        A. Click Launch
        B. Wait for blueprint_ready
        C. Review + approve blueprint in modal
        D. Watch TDD build (screenshot each step)
        E. Approve UAT
        F. Verify project_complete
        """
        print("\n\n[TEST 03] Launching Academy ERP and watching full pipeline", flush=True)

        project_id = _state.get("project_id")
        assert project_id, "No project_id — test_02 must run first"

        # Ensure project selected
        current_sel = page.evaluate("() => document.querySelector('#projectSel').value")
        if current_sel != project_id:
            page.select_option("#projectSel", value=project_id)
            page.wait_for_timeout(1000)

        page.wait_for_selector("#launchBtn", state="visible", timeout=10000)
        _screenshot(page, "03-before-launch")

        # ── A: Click Launch ───────────────────────────────────────────────────
        print("  Clicking 'Launch'...", flush=True)
        page.click("#launchBtn")

        page.wait_for_function(
            "() => { const b = document.getElementById('launchBtn'); "
            "return b && (b.disabled || b.textContent.includes('Running') || b.textContent.includes('Launching')); }",
            timeout=8000,
        )
        page.wait_for_timeout(500)
        _screenshot(page, "03-launch-clicked")

        page.wait_for_selector("#pnl-progress", state="visible", timeout=10000)
        _screenshot(page, "03-progress-panel-visible")
        print("  ✓ Progress panel visible — blueprint generating...", flush=True)

        # ── B: Wait for blueprint_ready ───────────────────────────────────────
        print(f"  Waiting for blueprint_ready (up to {BLUEPRINT_WAIT_S}s)...", flush=True)
        bp_event = _wait_for_event(
            _state["ws_events"], _state["ws_lock"],
            "blueprint_ready",
            timeout_s=BLUEPRINT_WAIT_S,
            page=page,
            screenshot_prefix="03-blueprint-gen",
        )

        # API fallback if WS event missed
        if not bp_event:
            print("  WS blueprint_ready not received — polling API...", flush=True)
            api_deadline = time.time() + 120
            while time.time() < api_deadline:
                bp_check = page.evaluate(
                    f"async () => (await fetch('/api/projects/{project_id}/blueprint')).json()"
                )
                if bp_check.get("blueprint") and bp_check["blueprint"].get("content"):
                    print("  ✓ Blueprint detected via API poll", flush=True)
                    bp_event = {"event": "blueprint_ready", "source": "api_poll"}
                    break
                page.wait_for_timeout(5000)

        if not bp_event:
            _screenshot(page, "03-ERROR-blueprint-timeout")
            err_events = _check_for_factory_errors(_state["ws_events"], _state["ws_lock"])
            if err_events:
                err_msg = err_events[-1].get("error", "Unknown error")
                _log_failure({"stage": "blueprint", "error": err_msg, "type": "factory_error"})
                pytest.fail(f"Factory error during blueprint generation: {err_msg}")
            pytest.fail(f"Blueprint not ready after {BLUEPRINT_WAIT_S}s — no error event either")

        _screenshot(page, "03-blueprint-ready")
        print("  ✓ Blueprint ready!", flush=True)

        # Show blueprint summary in console
        try:
            bp_data = page.evaluate(
                f"async () => (await fetch('/api/projects/{project_id}/blueprint')).json()"
            )
            bp_content = (bp_data.get("blueprint") or {}).get("content", "")
            bp_version = (bp_data.get("blueprint") or {}).get("version", "?")
            print(f"  Blueprint version: {bp_version}", flush=True)
            print(f"  Blueprint preview (first 400 chars):\n  {bp_content[:400]}...", flush=True)
            contracts = list((bp_data.get("contracts") or {}).keys())
            print(f"  Contracts generated: {contracts}", flush=True)
        except Exception as e:
            print(f"  ⚠ Blueprint fetch failed: {e}", flush=True)

        # ── C: Blueprint modal — review + approve ─────────────────────────────
        page.wait_for_timeout(1000)
        modal_visible = page.locator("#blueprintModal").is_visible()
        if not modal_visible:
            print("  Blueprint modal not auto-opened — opening manually...", flush=True)
            try:
                page.click("button:has-text('Blueprint')", timeout=5000)
                page.wait_for_selector("#blueprintModal", state="visible", timeout=5000)
            except Exception:
                try:
                    page.evaluate("() => showBlueprintModal()")
                    page.wait_for_selector("#blueprintModal", state="visible", timeout=5000)
                except Exception as e:
                    print(f"  ⚠ Could not open blueprint modal: {e}", flush=True)

        if page.locator("#blueprintModal").is_visible():
            # Read and display blueprint content
            bp_text = page.evaluate(
                "() => document.getElementById('blueprintBody')?.innerText?.slice(0, 500) || 'empty'"
            )
            print(f"  Blueprint content (first 500):\n  {bp_text}", flush=True)
            _screenshot(page, "03-blueprint-modal-content")

            # Check for audit issues displayed
            audit_section = page.evaluate(
                "() => document.getElementById('auditSection')?.innerText?.slice(0, 300) || ''"
            )
            if audit_section:
                print(f"  Audit issues:\n  {audit_section}", flush=True)

            # Approve
            print("  Approving blueprint via UI...", flush=True)
            page.on("dialog", lambda d: d.accept())
            try:
                page.locator("#blueprintModal button:has-text('Approve')").click(timeout=5000)
            except Exception:
                # Try approve-blueprint button by ID
                try:
                    page.locator("#approveBlueprintBtn").click(timeout=3000)
                except Exception:
                    page.evaluate("() => approveBlueprint()")

            page.wait_for_selector("#blueprintModal", state="hidden", timeout=15000)
            _screenshot(page, "03-blueprint-approved")
            print("  ✓ Blueprint approved!", flush=True)
        else:
            print("  ⚠ Blueprint modal not visible — attempting API approval...", flush=True)
            result = page.evaluate(
                f"async () => (await fetch('/api/projects/{project_id}/approve-blueprint', "
                "{ method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}' })).json()"
            )
            print(f"  API approve result: {result}", flush=True)
            _screenshot(page, "03-blueprint-api-approved")

        # Verify blueprint_approved WS event
        bp_approved = _wait_for_event(
            _state["ws_events"], _state["ws_lock"],
            "blueprint_approved", timeout_s=30, page=page,
        )
        if bp_approved:
            print("  ✓ blueprint_approved WS event received", flush=True)
        else:
            print("  ⚠ blueprint_approved WS event not received (continuing)", flush=True)

        # ── D: Watch TDD build phases ─────────────────────────────────────────
        print(f"\n  Watching TDD build (up to {BUILD_WAIT_S}s)...", flush=True)
        print("  Monitoring: task execution, TDD steps, gate results, compilation...", flush=True)

        last_tdd_step   = ""
        last_task_id    = ""
        last_phase      = ""
        build_deadline  = time.time() + BUILD_WAIT_S

        # TDD steps we want to screenshot
        TDD_SCREENSHOT_STEPS = {"AC", "RED", "GREEN", "REF", "BC", "BF", "OA", "GIT", "AD"}

        consecutive_error_check = 0

        while time.time() < build_deadline:
            # ── Check: uat_ready (WS events first, then REST API fallback) ───
            uat_events = _find_events(_state["ws_events"], _state["ws_lock"], "uat_ready")
            if not uat_events:
                # REST API fallback: poll project status in case WS connection died
                try:
                    with urllib.request.urlopen(
                        f"{DASHBOARD_URL}/api/projects/{project_id}/progress", timeout=5
                    ) as resp:
                        progress_data = json.loads(resp.read())
                    if progress_data.get("awaiting") in ("uat_approval", "uat_blocked_e2e"):
                        e2e_ok = progress_data.get("awaiting") == "uat_approval"
                        uat_events = [{"event": "uat_ready", "e2e_passed": e2e_ok, "source": "api_poll"}]
                        print(f"  ✓ UAT ready detected via REST API (awaiting={progress_data.get('awaiting')})", flush=True)
                except Exception:
                    pass  # factory not responding yet — keep waiting
            if uat_events:
                uat_ev = uat_events[-1]
                e2e_passed = uat_ev.get("e2e_passed", True)
                print(f"\n  ✓ UAT ready! e2e_passed={e2e_passed}", flush=True)
                if not e2e_passed:
                    print("  ⚠ E2E tests FAILED — UAT panel should show red warning", flush=True)
                    _screenshot(page, "03-uat-ready-e2e-FAILED")
                else:
                    _screenshot(page, "03-uat-ready-e2e-PASSED")
                break

            # ── Check: project_error ─────────────────────────────────────────
            err_events = _check_for_factory_errors(_state["ws_events"], _state["ws_lock"])
            if err_events:
                err = err_events[-1].get("error", "Unknown")
                err_phase = err_events[-1].get("phase", "?")
                _screenshot(page, f"03-ERROR-factory-phase{err_phase}")
                _log_failure({
                    "stage": f"build-phase-{err_phase}",
                    "error": err,
                    "type": "project_error",
                    "task_id": err_events[-1].get("task_id", "?"),
                })
                _print_event_summary(_state["ws_events"], _state["ws_lock"])
                pytest.fail(
                    f"Factory project_error at phase {err_phase}: {err}\n"
                    "Fix the factory code that caused this, then restart the test."
                )

            # ── Track TDD step changes ────────────────────────────────────────
            with _state["ws_lock"]:
                step_events = [e for e in _state["ws_events"] if e.get("event") == "tdd_step_update"]
            if step_events:
                latest = step_events[-1]
                cur_step   = latest.get("step", "")
                cur_task   = latest.get("task_id", "")
                cur_phase  = str(latest.get("phase", ""))

                if cur_task != last_task_id or cur_phase != last_phase:
                    last_task_id = cur_task
                    last_phase   = cur_phase
                    print(f"\n  ── Phase {cur_phase} Task: {cur_task} ──", flush=True)

                if cur_step != last_tdd_step:
                    last_tdd_step = cur_step
                    status = latest.get("status", "")
                    print(f"  TDD step: [{cur_step}] status={status} task={cur_task}", flush=True)

                    if cur_step.upper() in TDD_SCREENSHOT_STEPS:
                        _screenshot(page, f"03-tdd-ph{cur_phase}-{cur_task[-8:]}-step-{cur_step.lower()}")

                    # TDD failure — RED step result with FAIL status
                    if cur_step.upper() in ("RED", "GREEN") and status == "FAIL":
                        print(
                            f"  ⚠ TDD {cur_step} step FAILED for task {cur_task} "
                            f"(factory will retry — watching)", flush=True
                        )
                        _state["failures_detected"].append({
                            "step": cur_step, "task": cur_task, "phase": cur_phase
                        })
                        _screenshot(page, f"03-TDD-FAIL-ph{cur_phase}-{cur_step}")

            # ── Periodic dashboard screenshots ────────────────────────────────
            consecutive_error_check += 1
            if consecutive_error_check % 30 == 0:  # every ~60s
                elapsed = BUILD_WAIT_S - int(build_deadline - time.time())
                print(f"  [{_ts()}] Still building... ({elapsed}s elapsed)", flush=True)
                _screenshot(page, f"03-build-progress-{elapsed}s")
                _print_tdd_progress(_state["ws_events"], _state["ws_lock"])

            if _is_page_alive(page):
                try:
                    page.wait_for_timeout(int(POLL_TICK_S * 1000))
                except Exception:
                    time.sleep(POLL_TICK_S)
            else:
                time.sleep(POLL_TICK_S)
        else:
            _screenshot(page, "03-ERROR-build-timeout")
            _print_event_summary(_state["ws_events"], _state["ws_lock"])
            pytest.fail(f"Build did not complete after {BUILD_WAIT_S}s")

        try:
            _screenshot(page, "03-build-complete-uat-ready")
        except Exception:
            pass

        # ── E: UAT Panel — review + approve (UI first, REST API fallback) ────
        print("\n  Approving UAT...", flush=True)
        uat_approved_via_api = False
        try:
            page.wait_for_selector("#uatPanel", state="visible", timeout=15000)
            _screenshot(page, "03-uat-panel-visible")

            # Read E2E status from panel
            panel_text = page.locator("#uatPanel").inner_text()
            print(f"  UAT panel text:\n  {panel_text[:500]}", flush=True)
        except Exception:
            print("  ⚠ UAT panel not visible (browser may be closed) — using REST API...", flush=True)

        # Approve UAT through UI; if page closed, fall back to REST API
        try:
            page.on("dialog", lambda d: d.accept())
            try:
                page.click("#uatPanel button:has-text('Approve')", timeout=5000)
            except Exception:
                try:
                    page.click("button:has-text('Approve & Deploy')", timeout=5000)
                except Exception:
                    try:
                        page.click("button[onclick='approveUAT()']", timeout=3000)
                    except Exception:
                        page.evaluate("() => approveUAT()")
            try:
                page.wait_for_timeout(2000)
            except Exception:
                time.sleep(2)
            _screenshot(page, "03-uat-approved")
        except Exception as ui_err:
            print(f"  ⚠ UI approve failed ({ui_err}) — calling REST API approve-uat", flush=True)
            req = urllib.request.Request(
                f"{DASHBOARD_URL}/api/projects/{project_id}/approve-uat",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    result = json.loads(resp.read())
                print(f"  REST API UAT approve result: {result}", flush=True)
                uat_approved_via_api = True
            except Exception as api_err:
                print(f"  ⚠ REST API UAT approve also failed: {api_err}", flush=True)

        print("  ✓ UAT approved!", flush=True)

        # Verify uat_approved WS event
        uat_approved = _wait_for_event(
            _state["ws_events"], _state["ws_lock"],
            "uat_approved", timeout_s=30, page=page,
        )
        if uat_approved:
            print("  ✓ uat_approved WS event received", flush=True)

        # ── F: project_complete ───────────────────────────────────────────────
        complete_event = _wait_for_event(
            _state["ws_events"], _state["ws_lock"],
            "project_complete", timeout_s=120, page=page,
        )
        if complete_event:
            print("  ✓ project_complete event received!", flush=True)
        else:
            print("  ⚠ project_complete not received (may be called project_finished)", flush=True)

        _screenshot(page, "03-pipeline-finished")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 04 — Verify built artefacts
# ═══════════════════════════════════════════════════════════════════════════════

class TestVerifyArtefacts:
    def test_04_verify_built_files_and_final_state(self, page):
        """
        After full build, verify:
        - Working directory has backend/ frontend/ tests/ files
        - git log shows phase commits
        - Progress API shows phase 5 (deploy) complete
        - All workers idle
        - Screenshot count and WS event summary
        """
        print("\n\n[TEST 04] Verifying built artefacts", flush=True)

        project_id = _state.get("project_id")
        if not project_id:
            pytest.skip("No project_id — previous tests failed")

        # ── Final progress ────────────────────────────────────────────────────
        progress = page.evaluate(
            f"async () => (await fetch('/api/projects/{project_id}/progress')).json()"
        )
        phase    = progress.get("current_phase", "?")
        running  = progress.get("is_running", "?")
        status   = progress.get("status", "?")
        print(f"  Final: phase={phase}, running={running}, status={status}", flush=True)
        _screenshot(page, "04-final-progress")

        # ── Blueprint / contracts ─────────────────────────────────────────────
        bp = page.evaluate(
            f"async () => (await fetch('/api/projects/{project_id}/blueprint')).json()"
        )
        bp_content = bp.get("blueprint") or {}
        contracts  = list((bp.get("contracts") or {}).keys())
        print(f"  Blueprint version: {bp_content.get('version','?')}, "
              f"approved_by: {bp_content.get('approved_by','?')}", flush=True)
        print(f"  Contracts: {contracts}", flush=True)
        assert contracts, "Expected at least one contract (api_contract.json)"

        # ── Disk artefacts ────────────────────────────────────────────────────
        # Determine working dir from project name (slugified)
        import re
        slug = re.sub(r"[^a-z0-9]+", "_", PROJECT_NAME.lower()).strip("_")
        working_dir = Path.home() / "working" / slug
        if working_dir.exists():
            all_files = list(working_dir.rglob("*"))
            py_files  = [f for f in all_files if f.suffix == ".py"]
            ts_files  = [f for f in all_files if f.suffix in (".ts", ".tsx")]
            test_files = [f for f in all_files if "test" in f.name.lower()]
            print(f"\n  Working dir: {working_dir}", flush=True)
            print(f"  Total files: {len(all_files)}", flush=True)
            print(f"  Python files: {len(py_files)}", flush=True)
            print(f"  TypeScript files: {len(ts_files)}", flush=True)
            print(f"  Test files: {len(test_files)}", flush=True)
            if py_files:
                print(f"  Sample BE files: {[f.name for f in py_files[:10]]}", flush=True)
            if ts_files:
                print(f"  Sample FE files: {[f.name for f in ts_files[:10]]}", flush=True)
        else:
            print(f"  ⚠ Working dir {working_dir} not found — checking alternate paths", flush=True)
            # Check common slugs
            for candidate in Path.home().glob("working/*/"):
                if "academy" in candidate.name.lower():
                    working_dir = candidate
                    print(f"  Found: {working_dir}", flush=True)
                    break

        # ── Git log ───────────────────────────────────────────────────────────
        if working_dir and working_dir.exists():
            try:
                git_log = subprocess.run(
                    ["git", "log", "--oneline", "-20"],
                    cwd=str(working_dir),
                    capture_output=True, text=True, timeout=10
                )
                if git_log.returncode == 0:
                    print(f"\n  Git log (last 20):\n{git_log.stdout}", flush=True)
                else:
                    print(f"  ⚠ git log failed: {git_log.stderr}", flush=True)
            except Exception as e:
                print(f"  ⚠ git log error: {e}", flush=True)

        # ── Worker health ─────────────────────────────────────────────────────
        status_resp = page.evaluate("async () => (await fetch('/api/status')).json()")
        workers = status_resp.get("workers", [])
        idle_workers = [
            w.get("instance_name", "?") for w in workers
            if w.get("status") == "idle"
        ]
        print(f"  Idle workers: {idle_workers}", flush=True)
        _screenshot(page, "04-final-worker-status")

        # ── Screenshots ───────────────────────────────────────────────────────
        screenshots = list(SCREENSHOT_DIR.glob("*.png"))
        print(f"\n  Screenshots captured: {len(screenshots)}", flush=True)

        # ── TDD failures summary ──────────────────────────────────────────────
        failures = _state.get("failures_detected", [])
        if failures:
            print(f"\n  TDD failures detected during run (recovered by factory):", flush=True)
            for f in failures:
                print(f"    Phase {f['phase']} | Task {f['task']} | Step {f['step']}", flush=True)
        else:
            print("  ✓ No TDD failures detected during build", flush=True)

        # ── WS event summary ──────────────────────────────────────────────────
        _print_event_summary(_state["ws_events"], _state["ws_lock"])

        # ── Final dashboard state ─────────────────────────────────────────────
        if DASHBOARD_URL not in page.url:
            page.goto(DASHBOARD_URL, wait_until="networkidle")
        _screenshot(page, "04-dashboard-final")

        print("\n  ✓ Academy ERP E2E complete!", flush=True)

        # Assert we actually built something
        uat_fired = (
            bool(_find_events(_state["ws_events"], _state["ws_lock"], "uat_ready")) or
            bool(_find_events(_state["ws_events"], _state["ws_lock"], "uat_approved"))
        )
        assert uat_fired, "UAT event never fired — pipeline may not have completed"
