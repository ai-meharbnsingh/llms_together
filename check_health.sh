#!/bin/bash
# ═══════════════════════════════════════════════════════════
# Autonomous Factory — Health Check
# Quick diagnostic of factory components.
# ═══════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
echo -e "${BLUE}       AUTONOMOUS FACTORY — Health Check           ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
echo ""

ISSUES=0

# 1. Factory process
echo -e "${YELLOW}[Factory Process]${NC}"
PID_FILE="factory_state/.factory.pid"
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo -e "  ${GREEN}Running (PID $PID)${NC}"
    else
        echo -e "  ${RED}NOT running (stale PID $PID)${NC}"
        ISSUES=$((ISSUES + 1))
    fi
else
    echo -e "  ${YELLOW}No PID file (may not be started)${NC}"
fi

# 2. Dashboard
echo -e "${YELLOW}[Dashboard]${NC}"
if curl -s http://127.0.0.1:8420/api/status > /dev/null 2>&1; then
    STATUS=$(curl -s http://127.0.0.1:8420/api/status | python3 -c "
import sys,json
d = json.load(sys.stdin)
workers = d.get('workers', {})
healthy = sum(1 for w in workers.values() if w.get('status') == 'healthy')
total = len(workers)
print(f'{healthy}/{total} workers healthy')
" 2>/dev/null || echo "connected but parse failed")
    echo -e "  ${GREEN}Online — $STATUS${NC}"
else
    echo -e "  ${RED}NOT responding at http://127.0.0.1:8420${NC}"
    ISSUES=$((ISSUES + 1))
fi

# 3. Ollama
echo -e "${YELLOW}[Ollama]${NC}"
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    MODEL_COUNT=$(curl -s http://localhost:11434/api/tags | python3 -c "
import sys,json
models = json.load(sys.stdin).get('models', [])
print(len(models))
" 2>/dev/null || echo "?")
    echo -e "  ${GREEN}Running — $MODEL_COUNT models loaded${NC}"
else
    echo -e "  ${RED}NOT running${NC}"
    ISSUES=$((ISSUES + 1))
fi

# 4. Database
echo -e "${YELLOW}[Database]${NC}"
DB_PATH="factory_state/factory.db"
if [ -f "$DB_PATH" ]; then
    DB_SIZE=$(du -h "$DB_PATH" | cut -f1)
    INTEGRITY=$(python3 -c "
import sqlite3
conn = sqlite3.connect('file:$DB_PATH?mode=ro', uri=True)
print(conn.execute('PRAGMA integrity_check').fetchone()[0])
conn.close()
" 2>/dev/null || echo "error")

    if [ "$INTEGRITY" = "ok" ]; then
        # Get schema version
        VERSION=$(python3 -c "
import sqlite3
conn = sqlite3.connect('file:$DB_PATH?mode=ro', uri=True)
print(conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0])
conn.close()
" 2>/dev/null || echo "?")
        echo -e "  ${GREEN}OK — ${DB_SIZE}, schema v${VERSION}${NC}"
    else
        echo -e "  ${RED}CORRUPTED — $INTEGRITY${NC}"
        ISSUES=$((ISSUES + 1))
    fi
else
    echo -e "  ${YELLOW}Not created yet${NC}"
fi

# 5. CLI Tools
echo -e "${YELLOW}[CLI Tools]${NC}"
for tool in claude kimi gemini; do
    if command -v "$tool" &> /dev/null; then
        echo -e "  ${GREEN}[✓] $tool${NC}"
    else
        echo -e "  ${YELLOW}[!] $tool (not installed)${NC}"
    fi
done

# 6. Task summary (if DB exists)
if [ -f "$DB_PATH" ]; then
    echo -e "${YELLOW}[Tasks]${NC}"
    python3 -c "
import sqlite3
conn = sqlite3.connect('file:$DB_PATH?mode=ro', uri=True)
conn.row_factory = sqlite3.Row
rows = conn.execute('SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status').fetchall()
if rows:
    for r in rows:
        print(f'  {r[\"status\"]}: {r[\"cnt\"]}')
else:
    print('  No tasks')
# Pending escalations
esc = conn.execute(\"SELECT COUNT(*) FROM escalations WHERE status='pending'\").fetchone()[0]
if esc > 0:
    print(f'  ⚠ {esc} pending escalation(s)')
conn.close()
" 2>/dev/null || echo "  Could not read tasks"
fi

# 7. Disk space
echo -e "${YELLOW}[Disk]${NC}"
STATE_SIZE=$(du -sh factory_state 2>/dev/null | cut -f1 || echo "?")
echo -e "  factory_state: $STATE_SIZE"

echo ""
if [ $ISSUES -eq 0 ]; then
    echo -e "${GREEN}All checks passed. Factory is healthy.${NC}"
else
    echo -e "${RED}$ISSUES issue(s) detected. Run ./recover.sh if needed.${NC}"
fi
