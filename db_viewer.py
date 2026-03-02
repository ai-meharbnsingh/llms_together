#!/usr/bin/env python3
"""
Lightweight zero-dependency web server for viewing the SQLite database.
Serves the db_viewer.html file and provides simple JSON API endpoints.
"""

import http.server
import json
import sqlite3
import socketserver
from pathlib import Path
from urllib.parse import urlparse, parse_qs

PORT = 8080
FACTORY_ROOT = Path(__file__).parent
try:
    with open(FACTORY_ROOT / "config" / "factory_config.json") as f:
        cfg = json.load(f)
    working_dir = Path(cfg.get("factory", {}).get("working_dir", "~/working")).expanduser()
except Exception as e:
    print(f"Warning: Could not load factory config: {e}")
    working_dir = Path("~/working").expanduser()

DB_PATH = working_dir / "autonomous_factory" / "factory_state" / "factory.db"

class DBViewerHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()

    def do_GET(self):
        parsed_path = urlparse(self.path)
        
        if parsed_path.path == "/":
            self.path = "/db_viewer.html"
            return http.server.SimpleHTTPRequestHandler.do_GET(self)
        
        elif parsed_path.path == "/api/tables":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            
            try:
                conn = sqlite3.connect(str(DB_PATH))
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                tables = [row['name'] for row in cursor.fetchall()]
                conn.close()
                self.wfile.write(json.dumps({"success": True, "tables": tables}).encode())
            except Exception as e:
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())
            return
            
        elif parsed_path.path == "/api/data":
            query_components = parse_qs(parsed_path.query)
            table_name = query_components.get("table", [None])[0]
            
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            
            if not table_name:
                self.wfile.write(json.dumps({"success": False, "error": "Table name not provided"}).encode())
                return
                
            try:
                conn = sqlite3.connect(str(DB_PATH))
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # Validate table name securely against the database dictionary
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                valid_tables = {row['name'] for row in cursor.fetchall()}
                if table_name not in valid_tables:
                    conn.close()
                    self.wfile.write(json.dumps({"success": False, "error": f"Invalid table name: {table_name}"}).encode())
                    return
                
                # Get columns
                cursor.execute(f"PRAGMA table_info('{table_name}')")  # nosemgrep
                columns = [row['name'] for row in cursor.fetchall()]
                
                # Get data
                cursor.execute(f"SELECT * FROM '{table_name}' LIMIT 1000")  # nosemgrep
                rows = [dict(row) for row in cursor.fetchall()]
                
                conn.close()
                self.wfile.write(json.dumps({
                    "success": True, 
                    "columns": columns,
                    "rows": rows
                }).encode())
            except Exception as e:
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())
            return
            
        return http.server.SimpleHTTPRequestHandler.do_GET(self)

if __name__ == "__main__":
    if not DB_PATH.exists():
        print(f"Warning: Database file not found at {DB_PATH}")
        
    with socketserver.TCPServer(("", PORT), DBViewerHandler) as httpd:
        print(f"Serving Database Viewer at http://localhost:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")
