#!/bin/bash
# ═══════════════════════════════════════════════════════════
# Autonomous Factory — Crash Recovery
# Run after unexpected shutdown to recover state.
# ═══════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
echo -e "${BLUE}       AUTONOMOUS FACTORY — Crash Recovery         ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
echo ""

DB_PATH="factory_state/factory.db"

# 1. Kill any orphan processes
echo -e "${YELLOW}[1/6] Killing orphan processes...${NC}"
PID_FILE="factory_state/.factory.pid"
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "  Killing stale factory process: $OLD_PID"
        kill "$OLD_PID" 2>/dev/null || true
        sleep 2
        kill -9 "$OLD_PID" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
fi

# Kill any stray python processes running main.py
pkill -f "python.*main.py" 2>/dev/null || true
echo -e "${GREEN}  Done${NC}"

# 2. Check DB integrity
echo -e "${YELLOW}[2/6] Checking database integrity...${NC}"
if [ -f "$DB_PATH" ]; then
    INTEGRITY=$(python3 -c "
import sqlite3
conn = sqlite3.connect('$DB_PATH')
result = conn.execute('PRAGMA integrity_check').fetchone()
print(result[0])
conn.close()
" 2>/dev/null || echo "error")

    if [ "$INTEGRITY" = "ok" ]; then
        echo -e "${GREEN}  Database: OK${NC}"
    else
        echo -e "${RED}  Database: CORRUPTED ($INTEGRITY)${NC}"
        echo -e "${YELLOW}  Creating backup and rebuilding...${NC}"
        cp "$DB_PATH" "${DB_PATH}.corrupted.$(date +%s)"
        echo -e "${YELLOW}  Backup saved. DB will be rebuilt on next start.${NC}"
    fi
else
    echo -e "${YELLOW}  No database found (will be created on start)${NC}"
fi

# 3. Clean WAL/SHM files (safe after crash)
echo -e "${YELLOW}[3/6] Cleaning WAL files...${NC}"
if [ -f "${DB_PATH}-wal" ]; then
    python3 -c "
import sqlite3
conn = sqlite3.connect('$DB_PATH')
conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
conn.close()
print('  WAL checkpoint completed')
" 2>/dev/null || echo "  WAL cleanup skipped (DB may need rebuild)"
fi
echo -e "${GREEN}  Done${NC}"

# 4. Check for stuck tasks
echo -e "${YELLOW}[4/6] Checking for stuck tasks...${NC}"
if [ -f "$DB_PATH" ]; then
    STUCK=$(python3 -c "
import sqlite3
conn = sqlite3.connect('file:$DB_PATH?mode=ro', uri=True)
conn.row_factory = sqlite3.Row
rows = conn.execute(\"SELECT task_id, assigned_to, status FROM tasks WHERE status='in_progress'\").fetchall()
for r in rows:
    print(f'  {r[\"task_id\"]} assigned_to={r[\"assigned_to\"]} status={r[\"status\"]}')
if not rows:
    print('  No stuck tasks')
conn.close()
" 2>/dev/null || echo "  Could not check tasks")
    echo "$STUCK"
fi
echo -e "${GREEN}  Done${NC}"

# 5. Clean temporary files
echo -e "${YELLOW}[5/6] Cleaning temporary files...${NC}"
rm -f factory_state/.factory.pid
rm -f factory_state/logs/*.tmp
find factory_state/checkpoints -name "*.tmp" -delete 2>/dev/null || true
echo -e "${GREEN}  Done${NC}"

# 6. Verify state files
echo -e "${YELLOW}[6/6] Verifying state files...${NC}"
for f in factory_state/chat_history.json factory_state/chat_sessions.json; do
    if [ -f "$f" ]; then
        if python3 -c "import json; json.load(open('$f'))" 2>/dev/null; then
            echo -e "${GREEN}  $f: valid JSON${NC}"
        else
            echo -e "${RED}  $f: INVALID JSON — backing up and resetting${NC}"
            cp "$f" "${f}.corrupted.$(date +%s)"
            echo "[]" > "$f"
        fi
    fi
done

echo ""
echo -e "${GREEN}Recovery complete. Run ./start.command to restart.${NC}"
