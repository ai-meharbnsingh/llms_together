import asyncio
from playwright.async_api import async_playwright
import time
import os
import json

async def run_factory_demo():
    print("🚀 Starting Autonomous Factory Live Demo: Personal Library Manager")
    
    # We assume the factory is already running. If not, the user needs to start it first.
    # python main.py
    
    async with async_playwright() as p:
        # Launch browser (visible for the demo)
        browser = await p.chromium.launch(headless=False, slow_mo=500)
        
        # Create a new browser context with a larger viewport
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 800}
        )
        
        page = await context.new_page()
        
        print("🌐 Connecting to Autonomous Factory Dashboard...")
        try:
            # Navigate to the dashboard
            await page.goto("http://127.0.0.1:8420")
            print("✅ Dashboard loaded successfully.")
        except Exception as e:
            print(f"❌ Failed to load dashboard: {e}")
            print("❗ Is the Autonomous Factory running? (python main.py)")
            await browser.close()
            return

        # Wait a moment to let the user see the dashboard UI
        await asyncio.sleep(2)
        
        # Define the prompt based on the user's specification
        prompt = """
Build a Personal Library Manager web application.

Description:
A simple book tracking application where users can:
- Create: Add books with title, author, genre, rating, notes
- Read: View book list, search/filter by genre/author
- Update: Edit book details, update reading status (To Read/Reading/Completed)
- Delete: Remove books from library

Tech Stack:
Frontend: React + TypeScript + Tailwind CSS
Backend: FastAPI (Python)
Database: SQLite
API: RESTful endpoints

Ensure it uses a clean, modern design.
        """.strip()

        print("📝 Creating Personal Library Manager project...")
        # Create project via API call
        create_result = await page.evaluate(f"""async () => {{
            const resp = await fetch('/api/projects/create', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{
                    name: 'Personal Library Manager',
                    description: {json.dumps(prompt)}
                }})
            }});
            return await resp.json();
        }}""")
        
        project_id = create_result.get("project_id") if create_result else None
        if not project_id:
            print("❌ Failed to create project")
            await browser.close()
            return
            
        print(f"✅ Project created with ID: {project_id}")
        print("⏳ Waiting 6 seconds for the DB Watchdog to flush the project to disk...")
        await asyncio.sleep(6)

        print(f"🚀 Launching project {project_id}...")
        launch_result = await page.evaluate(f"""async () => {{
            const resp = await fetch('/api/projects/launch', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{ project_id: '{project_id}' }})
            }});
            return await resp.json();
        }}""")
        
        if launch_result.get("started"):
            print("✅ Project launched successfully!")
        else:
            print(f"⚠️ Failed to launch project: {launch_result}")

        print("🔄 Selecting the project in the dashboard...")
        # Step 3: Select Project
        await page.evaluate(f"""() => {{
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
        await asyncio.sleep(1)
        print("✅ Project selected.")

        print("📝 Submitting the project prompt...")
        
        # Define the prompt based on the user's specification
        prompt = """
Build a Personal Library Manager web application.

Description:
A simple book tracking application where users can:
- Create: Add books with title, author, genre, rating, notes
- Read: View book list, search/filter by genre/author
- Update: Edit book details, update reading status (To Read/Reading/Completed)
- Delete: Remove books from library

Tech Stack:
Frontend: React + TypeScript + Tailwind CSS
Backend: FastAPI (Python)
Database: SQLite
API: RESTful endpoints

Ensure it uses a clean, modern design.
        """.strip()

        # Locate the chat input and submit the prompt
        try:
            # The dashboard uses id="chatIn" for the text area and id="chatBtn" for the send button
            chat_input = page.locator("#chatIn")
            await chat_input.wait_for(state="visible", timeout=5000)
            
            # Fill the input
            await chat_input.fill(prompt)
            print("✅ Prompt filled.")
            
            await asyncio.sleep(1)
            
            # Click the send button
        except Exception as e:
            print(f"❌ Failed to find or interact with chat input: {e}")
            # Optional: take a screenshot to debug
            await page.screenshot(path="failed_input_screenshot.png")
            print("Saved debug screenshot to failed_input_screenshot.png")
        
        print("⏳ Waiting for factory processing to begin. This will take some time...")
        print("👀 Watch the dashboard in the browser to monitor progress!")
        
        # Keep the browser open for a significant amount of time so the user can watch the factory work
        # The project generation can take minutes.
        try:
            # Wait for 15 minutes (or until the user closes the browser manualy)
            # We'll ping the status occasionally
            for i in range(15 * 6):  # 15 minutes in 10-second intervals
                await asyncio.sleep(10)
                if i % 6 == 0:  # Print a marker every minute
                    print(f"Monitoring... ({i//6} minutes elapsed)")
                    
                # Check if browser was closed by user
                if not context.pages:
                    print("Browser was closed by the user.")
                    break
        except asyncio.CancelledError:
            print("Interrupted.")
            
        finally:
            print("🛑 Closing browser...")
            await browser.close()

if __name__ == "__main__":
    # Ensure playwright browsers are installed
    print("Ensuring Playwright browsers are installed...")
    os.system("playwright install chromium")
    
    # Run the demo
    asyncio.run(run_factory_demo())
