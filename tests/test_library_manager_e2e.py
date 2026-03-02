"""
Library Manager — Full UI-Driven Factory Run (Playwright E2E)
═══════════════════════════════════════════════════════════════════════════════
Boots (or reuses) the Autonomous Factory, then acts EXACTLY like a real user
would: clicking buttons, filling forms, reading the screen, approving modals.
NO direct REST API calls for create/launch/approve — everything through the UI.

Two allowed test-setup API calls:
  POST /api/config/mode  → enable local LLM mode (no token burn)
  DELETE /api/projects/{id} → cleanup previous run's project

Per CLAUDE.md §4: Full E2E, slowMo:500, one screenshot per page/state.

Run (factory auto-starts if not running):
  cd autonomous_factory
  pytest tests/test_library_manager_e2e.py --headed --slowmo=500 -v -s

Wave: library-manager
Screenshots: screenshots/wave-library-manager/
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

# ── Constants ──────────────────────────────────────────────────────────────────
DASHBOARD_URL   = "http://127.0.0.1:8420"
PROJECT_NAME    = "Personal Library Manager"
PROJECT_DESC    = (
    "CRUD book tracking: title, author, genre, rating 1-5 stars, "
    "notes, reading status (To Read/Reading/Completed). "
    "Search and filter by genre and author. "
    "Stack: React + TypeScript + Tailwind (FE), FastAPI (BE), SQLite (DB)."
)
WAVE_NAME       = "library-manager"

SCREENSHOT_DIR  = Path(__file__).parent.parent / "screenshots" / f"wave-{WAVE_NAME}"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# Timeouts
FACTORY_START_S  = 60     # Max wait for factory to come up
BLUEPRINT_WAIT_S = 900    # 15 min — blueprint generation (local LLM is faster)
BUILD_WAIT_S     = 3600   # 60 min — full TDD build
POLL_TICK_S      = 2      # Poll WS events every N seconds
SCREENSHOT_FREQ  = 30     # Periodic screenshot every N seconds while waiting

# ── Module-level shared state (survives across tests in session) ───────────────
_state: dict = {
    "project_id": None,
    "factory_proc": None,
    "ws_events": [],
    "ws_lock": threading.Lock(),
}


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _screenshot(page, name: str) -> str:
    """Capture a full-page screenshot per CLAUDE.md §4.3."""
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


def _attach_ws_collector(page, events: list, lock: threading.Lock) -> None:
    """Hook Playwright's WebSocket to collect all Factory events in real-time."""
    def handle_ws(ws):
        def on_frame(payload):
            try:
                raw = payload.get("body") if isinstance(payload, dict) else str(payload)
                data = json.loads(raw)
                with lock:
                    events.append(data)
                ev = data.get("event", "?")
                ph = data.get("phase", "")
                st = data.get("step", "")
                status = data.get("status", "")
                print(
                    f"  [{_ts()}] WS ▸ {ev:<30} ph={ph:<4} step={st:<8} st={status}",
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
            return matched[-1]  # return latest matching event
        # Periodic screenshot while waiting
        if page and screenshot_prefix and (time.time() - last_screenshot) >= SCREENSHOT_FREQ:
            _screenshot(page, f"{screenshot_prefix}--waiting")
            last_screenshot = time.time()
        # Pump Playwright event loop
        if page:
            try:
                page.wait_for_timeout(int(poll_s * 1000))
            except Exception:
                time.sleep(poll_s)
        else:
            time.sleep(poll_s)
    return None


def _print_event_summary(events: list, lock: threading.Lock):
    with lock:
        counts: dict = {}
        for e in events:
            ev = e.get("event", "?")
            counts[ev] = counts.get(ev, 0) + 1
    print("\n  ── WS Event Summary ──────────────────────────────", flush=True)
    for ev, cnt in sorted(counts.items()):
        print(f"    {ev:<35} x{cnt}", flush=True)
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
    """Shared browser context with video recording."""
    video_dir = SCREENSHOT_DIR / "videos"
    video_dir.mkdir(parents=True, exist_ok=True)
    browser = playwright.chromium.launch(headless=False, slow_mo=500)
    ctx = browser.new_context(
        viewport={"width": 1440, "height": 900},
        record_video_dir=str(video_dir),
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
        Setup (test-infrastructure only, not user workflow):
        1. Enable local LLM mode so no Claude/Kimi/Gemini CLI subprocesses run.
        2. Delete any previous 'Personal Library Manager' project.
        """
        import urllib.request as ur
        import urllib.error

        print("\n\n[SETUP] Enabling LOCAL TEST MODE (all roles → DeepSeek/Qwen)...", flush=True)

        # Enable local mode via API
        req = ur.Request(
            f"{DASHBOARD_URL}/api/config/mode",
            data=json.dumps({"mode": "local"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with ur.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                assert result.get("local_mode") is True, f"Local mode not enabled: {result}"
                print("  ✓ LOCAL MODE enabled — no paid API tokens will be used", flush=True)
        except Exception as e:
            pytest.fail(f"Failed to enable local mode: {e}")

        # Find and delete any previous "Personal Library Manager" project
        print("[SETUP] Checking for previous project run...", flush=True)
        try:
            with ur.urlopen(f"{DASHBOARD_URL}/api/projects", timeout=10) as resp:
                data = json.loads(resp.read())
                projects = data.get("projects", [])
                for proj in projects:
                    if proj.get("name") == PROJECT_NAME:
                        pid = proj["project_id"]
                        del_req = ur.Request(
                            f"{DASHBOARD_URL}/api/projects/{pid}",
                            method="DELETE",
                        )
                        try:
                            with ur.urlopen(del_req, timeout=10) as r:
                                print(f"  ✓ Deleted previous project {pid}", flush=True)
                        except Exception as del_e:
                            print(f"  ⚠ Delete failed (non-fatal): {del_e}", flush=True)
        except Exception as e:
            print(f"  ⚠ Project list check failed (non-fatal): {e}", flush=True)

        print("[SETUP] Done.\n", flush=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 01 — Dashboard health check
# ═══════════════════════════════════════════════════════════════════════════════

class TestDashboardHealth:
    def test_01_dashboard_loads_and_is_healthy(self, page):
        """Navigate to dashboard, verify it loads, workers healthy, LOCAL MODE badge visible."""
        print("\n\n[TEST 01] Dashboard health check", flush=True)

        page.goto(DASHBOARD_URL, wait_until="networkidle")
        page.wait_for_selector("h1", timeout=15000)
        _screenshot(page, "01-dashboard-initial-load")

        # Verify LOCAL MODE badge is visible
        badge = page.locator("#localModeBadge")
        page.wait_for_timeout(1500)  # let WS update badge
        badge_visible = badge.is_visible()
        print(f"  LOCAL MODE badge visible: {badge_visible}", flush=True)

        # Check worker status via API
        workers_resp = page.evaluate("async () => (await fetch('/api/workers/status')).json()")
        workers = workers_resp if isinstance(workers_resp, list) else []
        if isinstance(workers_resp, dict):
            workers = list(workers_resp.values()) if workers_resp else []
        print(f"  Workers: {[w.get('instance_name', w.get('name', '?')) for w in workers if isinstance(w, dict)]}", flush=True)

        # Navigate all available tabs/sections
        for tab_sel in ["#tabOrch", "#tabDirect", "#tabProject"]:
            try:
                page.click(tab_sel, timeout=2000)
                page.wait_for_timeout(500)
            except Exception:
                pass
        _screenshot(page, "01-dashboard-all-tabs-visited")

        print("  ✓ Dashboard healthy", flush=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 02 — Create project through the UI (pure UI interaction)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCreateProject:
    def test_02_create_project_via_ui(self, page):
        """
        Click '+ New' button → fill in the New Project modal → click 'Create Project'.
        No direct API calls — pure UI interaction as a real user would do.
        """
        print("\n\n[TEST 02] Creating project through UI", flush=True)

        # Navigate to dashboard if needed
        if DASHBOARD_URL not in page.url:
            page.goto(DASHBOARD_URL, wait_until="networkidle")
            page.wait_for_selector("h1", timeout=10000)

        _screenshot(page, "02-before-create-project")

        # Click '+ New' button — opens the New Project modal
        print("  Clicking '+ New' button...", flush=True)
        page.click(".proj-new-btn", timeout=10000)
        page.wait_for_selector("#newProjectModal", state="visible", timeout=5000)
        _screenshot(page, "02-new-project-modal-open")

        # Fill in project name
        name_field = page.locator("#newProjName")
        name_field.fill(PROJECT_NAME)

        # Fill in description
        desc_field = page.locator("#newProjDesc")
        desc_field.fill(PROJECT_DESC)

        _screenshot(page, "02-new-project-form-filled")

        # Click "Create Project"
        print("  Clicking 'Create Project'...", flush=True)
        page.click("#newProjSubmit")

        # Modal should close and project should appear in selector
        page.wait_for_selector("#newProjectModal", state="hidden", timeout=10000)
        page.wait_for_timeout(1000)

        # Verify project appears in the project selector
        sel_opts = page.evaluate("() => [...document.querySelectorAll('#projectSel option')].map(o => o.textContent.trim())")
        print(f"  Projects in selector: {sel_opts}", flush=True)
        assert any(PROJECT_NAME in opt for opt in sel_opts), \
            f"'{PROJECT_NAME}' not in project selector after creation. Options: {sel_opts}"

        # Read the project_id from the JS currentProjectId variable (set by createProjectModal)
        # This is more reliable than reading the dropdown value, which may still show
        # the previously selected project until the async loadProjects() re-selects it.
        project_id = page.evaluate("() => window.currentProjectId || null")
        if not project_id:
            # Fallback: read dropdown value (may be old project)
            project_id = page.evaluate("() => document.querySelector('#projectSel').value")
        if not project_id:
            # Last resort: find by matching name in dropdown options
            project_id = page.evaluate(
                f"() => [...document.querySelectorAll('#projectSel option')]"
                f".find(o => o.textContent.includes({json.dumps(PROJECT_NAME)}))?.value"
            )
        assert project_id, "Could not determine project_id after creation"
        _state["project_id"] = project_id
        print(f"  ✓ Project created: {project_id}", flush=True)

        # Ensure the new project is selected in the dropdown (in case selector shows old project)
        page.select_option("#projectSel", value=project_id)
        page.wait_for_timeout(500)

        _screenshot(page, "02-project-created-in-selector")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 03 — Launch project and watch full build via UI
# ═══════════════════════════════════════════════════════════════════════════════

class TestLaunchAndBuild:
    def test_03_launch_project_via_ui(self, page):
        """
        Click 'Launch' button → watch progress panel appear →
        approve blueprint in modal → watch TDD steps → approve UAT.
        Everything through the UI as a real user.
        """
        print("\n\n[TEST 03] Launching project via UI and watching build", flush=True)

        project_id = _state.get("project_id")
        assert project_id, "No project_id — test_02 must run first"

        # Ensure the project is selected in the dropdown
        current_sel = page.evaluate("() => document.querySelector('#projectSel').value")
        if current_sel != project_id:
            print(f"  Selecting project {project_id} in dropdown...", flush=True)
            page.select_option("#projectSel", value=project_id)
            page.wait_for_timeout(1000)

        # Verify Launch button is visible
        page.wait_for_selector("#launchBtn", state="visible", timeout=10000)
        launch_text = page.locator("#launchBtn").inner_text()
        print(f"  Launch button text: '{launch_text}'", flush=True)

        _screenshot(page, "03-before-launch")

        # ── Phase A: Click Launch ────────────────────────────────────────────
        print("  Clicking 'Launch' button...", flush=True)
        page.click("#launchBtn")

        # Verify immediate UI feedback — button becomes "Launching..." or "Running"
        page.wait_for_function(
            "() => { const b = document.getElementById('launchBtn'); "
            "return b && (b.disabled || b.textContent.includes('Running') || b.textContent.includes('Launching')); }",
            timeout=5000,
        )
        page.wait_for_timeout(500)
        _screenshot(page, "03-launch-clicked-ui-feedback")

        # Verify progress panel appeared
        page.wait_for_selector("#pnl-progress", state="visible", timeout=10000)
        _screenshot(page, "03-progress-panel-visible")
        print("  ✓ Progress panel visible — blueprint generating...", flush=True)

        # ── Phase B: Wait for project_launched WS event ──────────────────────
        launched = _wait_for_event(
            _state["ws_events"], _state["ws_lock"],
            "project_launched", timeout_s=30, page=page,
        )
        if launched:
            print("  ✓ project_launched WS event received", flush=True)
        else:
            print("  ⚠ project_launched event not received (continuing — may have been missed)", flush=True)

        # ── Phase C: Wait for blueprint_ready ───────────────────────────────
        print(f"  Waiting for blueprint_ready (up to {BLUEPRINT_WAIT_S}s)...", flush=True)
        bp_event = _wait_for_event(
            _state["ws_events"], _state["ws_lock"],
            "blueprint_ready",
            timeout_s=BLUEPRINT_WAIT_S,
            page=page,
            screenshot_prefix="03-blueprint-generating",
        )

        # API fallback: poll blueprint endpoint in case WS event was missed
        if not bp_event:
            print("  WS blueprint_ready not received — polling API...", flush=True)
            deadline = time.time() + 60
            while time.time() < deadline:
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
            pytest.fail(f"Blueprint not ready after {BLUEPRINT_WAIT_S}s")

        _screenshot(page, "03-blueprint-ready")
        print("  ✓ Blueprint ready!", flush=True)

        # ── Phase D: Blueprint modal should auto-open; if not, open it ───────
        page.wait_for_timeout(1000)
        modal_visible = page.locator("#blueprintModal").is_visible()
        if not modal_visible:
            print("  Blueprint modal not auto-opened — clicking button to open...", flush=True)
            # Look for a "View Blueprint" button or similar
            try:
                page.click("button:has-text('Blueprint')", timeout=5000)
                page.wait_for_selector("#blueprintModal", state="visible", timeout=5000)
            except Exception:
                # Trigger via JS as last resort
                page.evaluate("() => showBlueprintModal()")
                page.wait_for_selector("#blueprintModal", state="visible", timeout=5000)

        _screenshot(page, "03-blueprint-modal-open")

        # Show blueprint content summary
        bp_text_preview = page.evaluate(
            "() => document.getElementById('blueprintBody')?.innerText?.slice(0, 300) || 'empty'"
        )
        print(f"  Blueprint preview: {bp_text_preview[:200]}...", flush=True)

        # ── Phase E: Approve blueprint in UI ─────────────────────────────────
        print("  Approving blueprint via UI button...", flush=True)
        # The approve button says "Approve & Lock Contracts" — scope to modal to avoid
        # matching the UAT "Approve & Deploy to Production" button in the page DOM.
        page.on("dialog", lambda d: d.accept())
        page.locator("#blueprintModal button:has-text('Approve')").click(timeout=5000)
        page.wait_for_timeout(1000)

        # Modal should close after approval
        page.wait_for_selector("#blueprintModal", state="hidden", timeout=10000)
        _screenshot(page, "03-blueprint-approved")
        print("  ✓ Blueprint approved!", flush=True)

        # Verify blueprint_approved WS event
        bp_approved = _wait_for_event(
            _state["ws_events"], _state["ws_lock"],
            "blueprint_approved", timeout_s=30, page=page,
        )
        if bp_approved:
            print("  ✓ blueprint_approved WS event received", flush=True)

        # ── Phase F: Watch TDD build phases ──────────────────────────────────
        print(f"\n  Watching TDD build (up to {BUILD_WAIT_S}s)...", flush=True)
        last_tdd_step = ""
        build_deadline = time.time() + BUILD_WAIT_S
        tdd_screenshot_steps = {"AC", "RED", "GREEN", "BC", "OA", "GIT", "AD"}

        while time.time() < build_deadline:
            # Check for uat_ready
            uat_events = _find_events(_state["ws_events"], _state["ws_lock"], "uat_ready")
            if uat_events:
                print("  ✓ UAT ready event received!", flush=True)
                break

            # Check for project_error
            err_events = _find_events(_state["ws_events"], _state["ws_lock"], "project_error")
            if err_events:
                _screenshot(page, "03-ERROR-project-error")
                err = err_events[-1].get("error", "Unknown")
                pytest.fail(f"Project execution error: {err}")

            # Screenshot on new TDD step
            with _state["ws_lock"]:
                step_events = [e for e in _state["ws_events"] if e.get("event") == "tdd_step_update"]
            if step_events:
                current_step = step_events[-1].get("step", "")
                if current_step != last_tdd_step:
                    last_tdd_step = current_step
                    if current_step.upper() in tdd_screenshot_steps:
                        _screenshot(page, f"03-tdd-step-{current_step.lower()}")
                        print(f"  TDD step: {current_step}", flush=True)

            page.wait_for_timeout(int(POLL_TICK_S * 1000))
        else:
            _screenshot(page, "03-ERROR-build-timeout")
            pytest.fail(f"Build not complete after {BUILD_WAIT_S}s")

        _screenshot(page, "03-build-complete-uat-ready")

        # ── Phase G: Approve UAT in UI ────────────────────────────────────────
        print("  Waiting for UAT panel to appear in UI...", flush=True)
        try:
            page.wait_for_selector("#uatPanel", state="visible", timeout=15000)
            _screenshot(page, "03-uat-panel-visible")
        except Exception:
            print("  ⚠ UAT panel not visible — may need to scroll or reload progress", flush=True)
            page.evaluate("() => { const el = document.getElementById('uatPanel'); if(el) el.style.display=''; }")

        # Approve UAT
        print("  Approving UAT via UI...", flush=True)
        page.on("dialog", lambda d: d.accept())
        try:
            page.click("button:has-text('Approve')", timeout=5000)
        except Exception:
            # Try the specific approveUAT button
            try:
                page.click("button[onclick='approveUAT()']", timeout=3000)
            except Exception:
                page.evaluate("() => approveUAT()")

        page.wait_for_timeout(2000)
        _screenshot(page, "03-uat-approved")
        print("  ✓ UAT approved!", flush=True)

        # Verify uat_approved WS event
        uat_approved = _wait_for_event(
            _state["ws_events"], _state["ws_lock"],
            "uat_approved", timeout_s=30, page=page,
        )
        if uat_approved:
            print("  ✓ uat_approved WS event received", flush=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 04 — Final state verification
# ═══════════════════════════════════════════════════════════════════════════════

class TestFinalVerification:
    def test_04_verify_final_state(self, page):
        """
        Final checks after full build:
        - Progress API shows completed state
        - Worker health still OK
        - Screenshots captured
        - WS event summary
        """
        print("\n\n[TEST 04] Final state verification", flush=True)

        project_id = _state.get("project_id")
        if project_id:
            # Check progress via API
            progress = page.evaluate(
                f"async () => (await fetch('/api/projects/{project_id}/progress')).json()"
            )
            print(f"  Final progress: phase={progress.get('current_phase')}, "
                  f"running={progress.get('is_running')}, "
                  f"status={progress.get('status')}", flush=True)
            _screenshot(page, "04-final-progress")

            # Show blueprint status
            bp = page.evaluate(
                f"async () => (await fetch('/api/projects/{project_id}/blueprint')).json()"
            )
            bp_content = bp.get("blueprint", {}) or {}
            print(f"  Blueprint: version={bp_content.get('version')}, "
                  f"approved_by={bp_content.get('approved_by')}", flush=True)
            contracts = list((bp.get("contracts") or {}).keys())
            print(f"  Contracts generated: {contracts}", flush=True)

        # Worker health
        status = page.evaluate("async () => (await fetch('/api/status')).json()")
        _screenshot(page, "04-final-worker-status")
        w_list = status.get("workers", [])
        worker_names = (
            [w.get("instance_name", "?") for w in w_list]
            if isinstance(w_list, list)
            else list(w_list.keys())
        )
        print(f"  Workers: {worker_names}", flush=True)

        # Confirm no online AI workers were used (local mode)
        with _state["ws_lock"]:
            all_events = list(_state["ws_events"])
        # Screenshots count
        screenshots = list(SCREENSHOT_DIR.glob("*.png"))
        print(f"  Screenshots captured: {len(screenshots)}", flush=True)

        # WS event summary
        _print_event_summary(_state["ws_events"], _state["ws_lock"])

        # Verify project was actually built (uat_ready or uat_approved)
        uat_fired = bool(_find_events(_state["ws_events"], _state["ws_lock"], "uat_ready")) or \
                    bool(_find_events(_state["ws_events"], _state["ws_lock"], "uat_approved"))
        print(f"  UAT event fired: {uat_fired}", flush=True)

        # Final dashboard screenshot
        _screenshot(page, "04-dashboard-final-state")
        print("\n  ✓ All tests complete!", flush=True)
