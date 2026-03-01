#!/usr/bin/env python3
"""Playwright visual test for chat sessions + project selector."""
import time
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8420"
SHOTS = "screenshots/session-test"


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=500)
        page = browser.new_page(viewport={"width": 1400, "height": 900})

        # 1. Load dashboard
        page.goto(URL)
        page.wait_for_load_state("networkidle")
        time.sleep(2)
        page.screenshot(path=f"{SHOTS}/01-initial-load.png", full_page=True)
        print("[1] Dashboard loaded")

        # 2. Check session bar exists
        session_bar = page.locator("#sessionBar")
        print(f"[2] Session bar visible: {session_bar.is_visible()}")
        page.screenshot(path=f"{SHOTS}/02-session-bar.png", full_page=True)

        # 3. Click "+ New" and handle prompt dialog
        page.on("dialog", lambda d: (time.sleep(0.3), d.accept("My Test Session")))
        new_btn = page.locator(".session-new")
        print(f"[3] '+ New' button visible: {new_btn.is_visible()}")
        new_btn.click()
        time.sleep(2)
        page.screenshot(path=f"{SHOTS}/03-after-new-session.png", full_page=True)
        print("[3] Clicked '+ New', accepted prompt with 'My Test Session'")

        # 4. Check sessions updated
        tabs = page.locator(".session-tab")
        count = tabs.count()
        print(f"[4] Session tabs count: {count}")
        for i in range(count):
            txt = tabs.nth(i).text_content()
            cls = tabs.nth(i).get_attribute("class")
            print(f"    Tab {i}: '{txt}' class='{cls}'")
        page.screenshot(path=f"{SHOTS}/04-sessions-after-new.png", full_page=True)

        # 5. Click first tab (switch to old session)
        if count > 1:
            tabs.first.click()
            time.sleep(2)
            page.screenshot(path=f"{SHOTS}/05-switched-to-first.png", full_page=True)
            print("[5] Switched to first session tab")

        # 6. Check project bar
        proj_bar = page.locator("#projectBar")
        print(f"[6] Project bar visible: {proj_bar.is_visible()}")

        # 7. Click project "+ New" and handle dialogs
        def handle_project_dialogs(dialog):
            if "name" in dialog.message.lower():
                dialog.accept("Playwright Project")
            elif "description" in dialog.message.lower() or "optional" in dialog.message.lower():
                dialog.accept("Created by Playwright test")
            else:
                dialog.accept("test input")

        # Remove old handler, add project handler
        page.remove_listener("dialog", page.listeners("dialog")[0] if page.listeners("dialog") else None)
        page.on("dialog", handle_project_dialogs)

        proj_new = page.locator(".proj-new-btn")
        print(f"[7] Project '+ New' visible: {proj_new.is_visible()}")
        proj_new.click()
        time.sleep(2)
        page.screenshot(path=f"{SHOTS}/07-after-project-create.png", full_page=True)
        print("[7] Created project via dashboard")

        # 8. Check project dropdown
        proj_sel = page.locator("#projectSel")
        options = proj_sel.locator("option")
        print(f"[8] Project dropdown options: {options.count()}")
        for i in range(options.count()):
            print(f"    Option {i}: '{options.nth(i).text_content()}'")

        # 9. Switch to Direct Chat mode - project bar should dim
        page.locator("#tab-direct").click()
        time.sleep(1)
        page.screenshot(path=f"{SHOTS}/09-direct-mode-dimmed.png", full_page=True)
        print("[9] Direct mode - project bar should be dimmed")

        # 10. Switch back to Orchestrator mode
        page.locator("#tab-orchestrator").click()
        time.sleep(1)
        page.screenshot(path=f"{SHOTS}/10-orchestrator-mode.png", full_page=True)
        print("[10] Back to Orchestrator mode")

        # 11. Send a chat message
        chat_input = page.locator("#chatIn")
        chat_input.fill("Hello from Playwright test!")
        page.locator("#chatBtn").click()
        time.sleep(3)
        page.screenshot(path=f"{SHOTS}/11-after-chat-message.png", full_page=True)
        print("[11] Sent chat message")

        # 12. Console errors check
        errors = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        time.sleep(1)

        print("\n=== SUMMARY ===")
        print(f"Screenshots saved to {SHOTS}/")
        if errors:
            print(f"Console errors: {errors}")
        else:
            print("No console errors detected")

        # Keep browser open for user to inspect
        print("\nBrowser will stay open for 60 seconds for inspection...")
        time.sleep(60)
        browser.close()


if __name__ == "__main__":
    run()
