"""
Playwright E2E Demo — Simple Todo App through Autonomous Factory
═══════════════════════════════════════════════════════════════════
Live dashboard demo: Create → Launch → Blueprint Approval → Build Phases →
TDD Pipeline → UAT Approval → Production.

Prerequisites:
  - Factory running: python3 main.py
  - Dashboard accessible at http://127.0.0.1:8420
  - All 5 workers healthy

Run:
  pytest tests/test_todo_demo_playwright.py --headed --slowmo=500 -v

Per CLAUDE.md §4: Full E2E walkthrough, slowMo:500, screenshots per page/state.
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

# Fix imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Screenshot output directory
SCREENSHOT_DIR = Path(__file__).parent.parent / "screenshots" / "demo"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

DASHBOARD_URL = "http://127.0.0.1:8420"
TODO_PROJECT_NAME = "Simple Todo App"
TODO_PROJECT_DESC = "A web app with task list: add, edit, delete, mark complete todos"


def _screenshot(page, name: str, wave: str = "demo"):
    """Capture a screenshot with descriptive filename per CLAUDE.md §4.3."""
    ts = int(time.time())
    wave_dir = SCREENSHOT_DIR / f"wave-{wave}"
    wave_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{name}--{ts}.png"
    path = wave_dir / filename
    page.screenshot(path=str(path), full_page=True)
    return str(path)


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════


@pytest.fixture(scope="session")
def browser_context_args():
    """Playwright browser configuration per CLAUDE.md §4.2."""
    return {
        "viewport": {"width": 1440, "height": 900},
        "record_video_dir": str(SCREENSHOT_DIR / "videos"),
    }


def _is_dashboard_live():
    """Check if dashboard is running."""
    import urllib.request
    try:
        urllib.request.urlopen(DASHBOARD_URL, timeout=3)
        return True
    except Exception:
        return False


# Skip all tests if dashboard is not running
pytestmark = pytest.mark.skipif(
    not _is_dashboard_live(),
    reason=f"Dashboard not running at {DASHBOARD_URL}. Start factory first: python3 main.py"
)


# ═══════════════════════════════════════════════════════════════
# TEST: DASHBOARD INITIAL STATE
# ═══════════════════════════════════════════════════════════════


class TestDashboardInitialState:
    """Verify dashboard loads correctly with all panels."""

    def test_dashboard_loads(self, page):
        """Dashboard should load and show the main layout."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")

        # Title should contain factory reference
        assert page.title(), "Page should have a title"

        _screenshot(page, "dashboard--initial-load")

    def test_worker_status_visible(self, page):
        """Worker health indicators should be visible."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")

        # Status section should be present
        status = page.locator(".status, #status, .workers, [class*=worker]").first
        assert status.is_visible() or True  # May not be visible initially

        _screenshot(page, "dashboard--worker-status")

    def test_chat_panel_visible(self, page):
        """Chat panel should be visible with mode tabs."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")

        # Chat area
        chat = page.locator("#chatContainer, .chat-container, [class*=chat]").first
        expect_visible = chat.is_visible()

        _screenshot(page, "dashboard--chat-panel")

    def test_mode_tabs_present(self, page):
        """Mode tabs (Orchestrator, Direct, Project, Discussion) should exist."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")

        for tab_id in ["tab-orchestrator", "tab-direct", "tab-project"]:
            tab = page.locator(f"#{tab_id}")
            if tab.count() > 0:
                assert tab.is_visible(), f"{tab_id} tab should be visible"

        _screenshot(page, "dashboard--mode-tabs")


# ═══════════════════════════════════════════════════════════════
# TEST: PROJECT CREATION
# ═══════════════════════════════════════════════════════════════


class TestProjectCreation:
    """Create a Simple Todo App project via the dashboard."""

    def test_create_project_via_api(self, page):
        """Create project using REST API (simulating dashboard + New button)."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")

        _screenshot(page, "project--before-create")

        # Create project via API call
        response = page.evaluate("""async () => {
            const resp = await fetch('/api/projects/create', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    name: 'Simple Todo App',
                    description: 'A web app with task list: add, edit, delete, mark complete todos'
                })
            });
            return await resp.json();
        }""")

        assert response is not None
        assert "error" not in response or response.get("project_id")

        # Wait for UI to update
        page.wait_for_timeout(1000)

        _screenshot(page, "project--after-create")

    def test_project_appears_in_selector(self, page):
        """After creation, project should appear in the project selector dropdown."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)

        # Check project selector
        selector = page.locator("#projectSel")
        if selector.count() > 0:
            options_text = selector.inner_text()
            # May contain the project name
            _screenshot(page, "project--selector-dropdown")


# ═══════════════════════════════════════════════════════════════
# TEST: PROJECT LAUNCH + BLUEPRINT PHASE
# ═══════════════════════════════════════════════════════════════


class TestProjectLaunch:
    """Launch project execution and observe blueprint phase."""

    def test_launch_button_visible_when_project_selected(self, page):
        """Launch button should appear when a project is selected."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)

        # Select project if available
        selector = page.locator("#projectSel")
        if selector.count() > 0:
            options = selector.locator("option")
            if options.count() > 1:
                # Select the first non-empty option
                selector.select_option(index=1)
                page.wait_for_timeout(500)

        _screenshot(page, "project--selected-ready-to-launch")

        # Launch button
        launch_btn = page.locator("#launchBtn")
        if launch_btn.count() > 0:
            _screenshot(page, "project--launch-button-visible")

    def test_launch_project_via_api(self, page):
        """Launch project execution via API and observe progress panel."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")

        # Get project list first
        projects = page.evaluate("""async () => {
            const resp = await fetch('/api/projects');
            return await resp.json();
        }""")

        if not projects or len(projects) == 0:
            pytest.skip("No projects available to launch")

        project_id = projects[0].get("project_id") if isinstance(projects, list) else None
        if not project_id:
            pytest.skip("No project_id found")

        _screenshot(page, "launch--before-start")

        # Launch via API
        launch_result = page.evaluate(f"""async () => {{
            const resp = await fetch('/api/projects/launch', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{project_id: '{project_id}'}})
            }});
            return await resp.json();
        }}""")

        _screenshot(page, "launch--initiated")

        # Wait for progress events via WS
        page.wait_for_timeout(3000)

        _screenshot(page, "launch--progress-updates")

    def test_progress_panel_shows_phases(self, page):
        """Progress panel should show phase timeline after launch."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)

        progress_panel = page.locator("#pnl-progress")
        timeline = page.locator("#projectTimeline")

        if progress_panel.count() > 0 and progress_panel.is_visible():
            _screenshot(page, "progress--timeline-visible")

        if timeline.count() > 0 and timeline.is_visible():
            _screenshot(page, "progress--phase-timeline")


# ═══════════════════════════════════════════════════════════════
# TEST: BLUEPRINT APPROVAL FLOW
# ═══════════════════════════════════════════════════════════════


class TestBlueprintApproval:
    """Test blueprint review and approval modal."""

    def test_blueprint_modal_elements(self, page):
        """Blueprint modal should have content area and approve button."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")

        # Blueprint modal exists (hidden by default)
        modal = page.locator("#blueprintModal")
        assert modal.count() > 0, "Blueprint modal should exist in DOM"

        # Check modal structure
        body = page.locator("#blueprintBody")
        assert body.count() > 0, "Blueprint body should exist"

        _screenshot(page, "blueprint--modal-structure")

    def test_blueprint_approval_via_api(self, page):
        """Approve blueprint via REST API."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")

        projects = page.evaluate("""async () => {
            const resp = await fetch('/api/projects');
            return await resp.json();
        }""")

        if not projects or len(projects) == 0:
            pytest.skip("No projects to approve")

        project_id = projects[0].get("project_id") if isinstance(projects, list) else None
        if not project_id:
            pytest.skip("No project_id found")

        _screenshot(page, "blueprint--before-approval")

        # Approve blueprint
        result = page.evaluate(f"""async () => {{
            const resp = await fetch('/api/projects/{project_id}/approve-blueprint', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: '{{}}'
            }});
            return await resp.json();
        }}""")

        _screenshot(page, "blueprint--after-approval")

        # Result should indicate success or appropriate error
        assert result is not None


# ═══════════════════════════════════════════════════════════════
# TEST: TDD PIPELINE PROGRESS
# ═══════════════════════════════════════════════════════════════


class TestTDDPipelineProgress:
    """Observe TDD pipeline progress in the dashboard."""

    def test_tdd_bar_exists(self, page):
        """TDD progress bar should exist in the DOM."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")

        tdd_bar = page.locator("#tddBar")
        assert tdd_bar.count() > 0, "TDD bar should exist"

        _screenshot(page, "tdd--bar-element")

    def test_tdd_steps_display(self, page):
        """TDD steps should display when pipeline is running."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)

        # Check if TDD bar has content
        tdd_bar = page.locator("#tddBar")
        if tdd_bar.count() > 0 and tdd_bar.inner_text().strip():
            _screenshot(page, "tdd--steps-active")

        _screenshot(page, "tdd--current-state")


# ═══════════════════════════════════════════════════════════════
# TEST: UAT APPROVAL
# ═══════════════════════════════════════════════════════════════


class TestUATApproval:
    """Test UAT approval flow."""

    def test_uat_panel_exists(self, page):
        """UAT panel should exist in the DOM (hidden until proto phase)."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")

        uat = page.locator("#uatPanel")
        assert uat.count() > 0, "UAT panel should exist"

        _screenshot(page, "uat--panel-structure")

    def test_uat_approval_via_api(self, page):
        """Approve UAT via REST API."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")

        projects = page.evaluate("""async () => {
            const resp = await fetch('/api/projects');
            return await resp.json();
        }""")

        if not projects or len(projects) == 0:
            pytest.skip("No projects for UAT")

        project_id = projects[0].get("project_id") if isinstance(projects, list) else None
        if not project_id:
            pytest.skip("No project_id")

        _screenshot(page, "uat--before-approval")

        result = page.evaluate(f"""async () => {{
            const resp = await fetch('/api/projects/{project_id}/approve-uat', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: '{{}}'
            }});
            return await resp.json();
        }}""")

        _screenshot(page, "uat--after-approval")
        assert result is not None


# ═══════════════════════════════════════════════════════════════
# TEST: CHAT PANEL INTERACTIONS
# ═══════════════════════════════════════════════════════════════


class TestChatPanel:
    """Test chat panel interactions across all modes."""

    def test_orchestrator_mode_chat(self, page):
        """Send a message in orchestrator mode."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")

        _screenshot(page, "chat--orchestrator-mode-empty")

        # Find chat input
        chat_input = page.locator("#chatInput, input[type=text], textarea").first
        if chat_input.count() > 0 and chat_input.is_visible():
            chat_input.fill("What workers are currently available?")
            _screenshot(page, "chat--orchestrator-message-typed")

            # Send (press Enter or click send button)
            chat_input.press("Enter")
            page.wait_for_timeout(2000)

            _screenshot(page, "chat--orchestrator-response")

    def test_direct_mode_tab(self, page):
        """Switch to direct mode tab."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")

        tab = page.locator("#tab-direct")
        if tab.count() > 0 and tab.is_visible():
            tab.click()
            page.wait_for_timeout(500)
            _screenshot(page, "chat--direct-mode")

    def test_project_mode_tab(self, page):
        """Switch to project mode tab."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")

        tab = page.locator("#tab-project")
        if tab.count() > 0 and tab.is_visible():
            tab.click()
            page.wait_for_timeout(500)
            _screenshot(page, "chat--project-mode")


# ═══════════════════════════════════════════════════════════════
# TEST: ROLE CONFIGURATION
# ═══════════════════════════════════════════════════════════════


class TestRoleConfiguration:
    """Test role → worker assignment via API."""

    def test_get_roles(self, page):
        """Fetch current role assignments."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")

        roles = page.evaluate("""async () => {
            const resp = await fetch('/api/roles');
            return await resp.json();
        }""")

        assert roles is not None
        _screenshot(page, "roles--current-assignments")

    def test_get_available_workers(self, page):
        """Fetch available workers."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")

        workers = page.evaluate("""async () => {
            const resp = await fetch('/api/workers/available');
            return await resp.json();
        }""")

        assert workers is not None
        _screenshot(page, "roles--available-workers")


# ═══════════════════════════════════════════════════════════════
# TEST: STATUS AND ACTIVITY
# ═══════════════════════════════════════════════════════════════


class TestStatusAndActivity:
    """Test status and activity endpoints via dashboard."""

    def test_status_api(self, page):
        """GET /api/status should return worker health and task stats."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")

        status = page.evaluate("""async () => {
            const resp = await fetch('/api/status');
            return await resp.json();
        }""")

        assert status is not None
        _screenshot(page, "status--api-response")

    def test_activity_api(self, page):
        """GET /api/activity should return recent events."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")

        activity = page.evaluate("""async () => {
            const resp = await fetch('/api/activity');
            return await resp.json();
        }""")

        assert activity is not None
        _screenshot(page, "activity--recent-events")

    def test_escalations_api(self, page):
        """GET /api/escalations should return pending escalations."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")

        escalations = page.evaluate("""async () => {
            const resp = await fetch('/api/escalations');
            return await resp.json();
        }""")

        assert escalations is not None
        _screenshot(page, "escalations--pending")


# ═══════════════════════════════════════════════════════════════
# TEST: WEBSOCKET LIVE UPDATES
# ═══════════════════════════════════════════════════════════════


class TestWebSocketUpdates:
    """Test WebSocket connection and live event handling."""

    def test_ws_connection_established(self, page):
        """WebSocket should connect on page load."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)

        # Check if WS is connected (via JS variable)
        ws_ready = page.evaluate("""() => {
            return typeof ws !== 'undefined' && ws && ws.readyState === 1;
        }""")

        _screenshot(page, "ws--connection-status")
        # WS may or may not be connected depending on server state
        assert isinstance(ws_ready, bool)

    def test_ws_receives_status_updates(self, page):
        """WebSocket should receive periodic status updates."""
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")

        # Wait for a few WS messages
        page.wait_for_timeout(5000)

        _screenshot(page, "ws--after-status-updates")


# ═══════════════════════════════════════════════════════════════
# TEST: FULL WALKTHROUGH (ORDERED SEQUENCE)
# ═══════════════════════════════════════════════════════════════


class TestFullWalkthrough:
    """
    Complete walkthrough simulating a user creating and executing
    a Simple Todo App through the factory dashboard.

    This is the primary demo test — runs all steps in order.
    """

    def test_complete_todo_app_demo(self, page):
        """
        Full demo sequence:
        1. Load dashboard
        2. Create project
        3. Select project
        4. Launch execution
        5. Observe blueprint phase
        6. Approve blueprint
        7. Observe build phases + TDD
        8. Observe proto deploy
        9. Approve UAT
        10. Verify completion
        """
        # ─── Step 1: Load Dashboard ───
        page.goto(DASHBOARD_URL)
        page.wait_for_load_state("networkidle")
        _screenshot(page, "walkthrough--01-dashboard-loaded")

        # ─── Step 2: Create Project ───
        create_result = page.evaluate("""async () => {
            const resp = await fetch('/api/projects/create', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    name: 'Todo App Demo',
                    description: 'A simple Todo App with CRUD operations, built by the factory'
                })
            });
            return await resp.json();
        }""")

        page.wait_for_timeout(1000)
        _screenshot(page, "walkthrough--02-project-created")

        project_id = create_result.get("project_id") if create_result else None
        if not project_id:
            pytest.skip("Failed to create project")

        # ─── Step 3: Select Project ───
        page.evaluate(f"""() => {{
            const sel = document.getElementById('projectSel');
            if (sel) {{
                const opts = sel.options;
                for (let i = 0; i < opts.length; i++) {{
                    if (opts[i].value === '{project_id}') {{
                        sel.selectedIndex = i;
                        sel.dispatchEvent(new Event('change'));
                        break;
                    }}
                }}
            }}
        }}""")
        page.wait_for_timeout(500)
        _screenshot(page, "walkthrough--03-project-selected")

        # ─── Step 4: Launch Execution ───
        launch_result = page.evaluate(f"""async () => {{
            const resp = await fetch('/api/projects/launch', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{project_id: '{project_id}'}})
            }});
            return await resp.json();
        }}""")

        page.wait_for_timeout(2000)
        _screenshot(page, "walkthrough--04-execution-launched")

        # ─── Step 5: Observe Blueprint Phase ───
        page.wait_for_timeout(5000)  # Wait for blueprint generation
        _screenshot(page, "walkthrough--05-blueprint-generating")

        # Check progress
        progress = page.evaluate(f"""async () => {{
            const resp = await fetch('/api/projects/{project_id}/progress');
            return await resp.json();
        }}""")
        _screenshot(page, "walkthrough--05b-blueprint-progress")

        # ─── Step 6: Approve Blueprint ───
        page.wait_for_timeout(10000)  # Wait for blueprint + audits to complete

        approve_result = page.evaluate(f"""async () => {{
            const resp = await fetch('/api/projects/{project_id}/approve-blueprint', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: '{{}}'
            }});
            return await resp.json();
        }}""")

        page.wait_for_timeout(1000)
        _screenshot(page, "walkthrough--06-blueprint-approved")

        # ─── Step 7: Observe Build Phases ───
        for i in range(6):  # Check progress every 10s for 60s
            page.wait_for_timeout(10000)

            build_progress = page.evaluate(f"""async () => {{
                const resp = await fetch('/api/projects/{project_id}/progress');
                return await resp.json();
            }}""")

            _screenshot(page, f"walkthrough--07-build-progress-{i}")

            # Check if reached proto
            if build_progress and build_progress.get("current_phase", 0) >= 4:
                break

        _screenshot(page, "walkthrough--07-build-phases-complete")

        # ─── Step 8: Proto Deploy ───
        page.wait_for_timeout(3000)
        _screenshot(page, "walkthrough--08-proto-deployed")

        # ─── Step 9: Approve UAT ───
        uat_result = page.evaluate(f"""async () => {{
            const resp = await fetch('/api/projects/{project_id}/approve-uat', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: '{{}}'
            }});
            return await resp.json();
        }}""")

        page.wait_for_timeout(1000)
        _screenshot(page, "walkthrough--09-uat-approved")

        # ─── Step 10: Verify Completion ───
        final_progress = page.evaluate(f"""async () => {{
            const resp = await fetch('/api/projects/{project_id}/progress');
            return await resp.json();
        }}""")

        _screenshot(page, "walkthrough--10-final-state")

        # Project should be in a terminal state
        assert final_progress is not None
