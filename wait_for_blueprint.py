import sqlite3
import time
import sys

db_path = "/Users/meharban/working/autonomous_factory/factory_state/factory.db"
project_id = "proj_27cd6b73"

print(f"Waiting for blueprint or tasks for project {project_id}...")
for i in range(60): # wait up to 10 minutes (60 * 10s)
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check blueprints
        cursor.execute("SELECT version FROM blueprint_revisions WHERE project_id=?", (project_id,))
        bps = cursor.fetchall()
        
        # Check tasks
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE project_id=?", (project_id,))
        tasks = cursor.fetchone()[0]
        
        if bps or tasks > 0:
            print(f"Found! Blueprints: {len(bps)}, Tasks: {tasks}")
            sys.exit(0)
            
        conn.close()
    except Exception as e:
        print(f"Error checking DB: {e}")
        
    time.sleep(10)
    print(f"Still waiting... ({i*10}s elapsed)")

print("Timeout waiting for blueprint.")
sys.exit(1)
