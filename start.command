#!/bin/bash
# ═══════════════════════════════════════════════════════════
# Autonomous Factory — macOS Launcher
# Double-click this file in Finder to start the factory.
# ═══════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
echo -e "${BLUE}       AUTONOMOUS FACTORY v1.1 — Starting          ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}[FATAL] Python 3 not found. Install from python.org${NC}"
    read -p "Press Enter to exit..."
    exit 1
fi

PYTHON=$(command -v python3)
echo -e "${GREEN}[✓] Python: $($PYTHON --version)${NC}"

# Check Ollama
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo -e "${GREEN}[✓] Ollama: running${NC}"
else
    echo -e "${YELLOW}[!] Ollama not running. Attempting to start...${NC}"
    if command -v ollama &> /dev/null; then
        ollama serve &
        sleep 3
        if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
            echo -e "${GREEN}[✓] Ollama: started${NC}"
        else
            echo -e "${RED}[!] Ollama failed to start. Local models will be unavailable.${NC}"
        fi
    else
        echo -e "${RED}[!] Ollama not installed. Local models will be unavailable.${NC}"
    fi
fi

# Check required Ollama models
REQUIRED_MODELS=("deepseek-coder-v2:16b" "qwen2.5-coder:7b" "phi3:mini")
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    AVAILABLE=$(curl -s http://localhost:11434/api/tags | python3 -c "import sys,json; [print(m['name']) for m in json.load(sys.stdin).get('models',[])]" 2>/dev/null || echo "")
    for model in "${REQUIRED_MODELS[@]}"; do
        if echo "$AVAILABLE" | grep -q "$model"; then
            echo -e "${GREEN}[✓] Model: $model${NC}"
        else
            echo -e "${YELLOW}[!] Model $model not found. Pulling...${NC}"
            ollama pull "$model" || echo -e "${RED}[!] Failed to pull $model${NC}"
        fi
    done
fi

# Check CLI tools (non-fatal)
for tool in claude kimi gemini; do
    if command -v "$tool" &> /dev/null; then
        echo -e "${GREEN}[✓] CLI: $tool${NC}"
    else
        echo -e "${YELLOW}[!] CLI tool '$tool' not found (optional)${NC}"
    fi
done

# Check for stale PID / crash recovery
PID_FILE="factory_state/.factory.pid"
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo -e "${YELLOW}[!] Factory already running (PID $OLD_PID)${NC}"
        echo -e "${YELLOW}    Kill it first: kill $OLD_PID${NC}"
        read -p "Press Enter to exit..."
        exit 1
    else
        echo -e "${YELLOW}[!] Stale PID file found (previous crash). Cleaning up...${NC}"
        rm -f "$PID_FILE"
    fi
fi

# Create state directories
mkdir -p factory_state/logs
mkdir -p factory_state/checkpoints
mkdir -p factory_state/chat_archive

echo ""
echo -e "${BLUE}Starting factory...${NC}"
echo -e "${BLUE}Dashboard: http://127.0.0.1:8420${NC}"
echo ""

# Start the factory
$PYTHON main.py 2>&1 | tee factory_state/logs/factory.log &
FACTORY_PID=$!
echo "$FACTORY_PID" > "$PID_FILE"

# Wait for dashboard
for i in $(seq 1 30); do
    if curl -s http://127.0.0.1:8420/api/status > /dev/null 2>&1; then
        echo -e "${GREEN}[✓] Dashboard ready at http://127.0.0.1:8420${NC}"
        # Open in browser
        open "http://127.0.0.1:8420" 2>/dev/null || true
        break
    fi
    sleep 1
done

echo ""
echo -e "${GREEN}Factory running (PID $FACTORY_PID)${NC}"
echo -e "Press Ctrl+C to stop."
echo ""

# Trap SIGINT for graceful shutdown
trap "echo -e '${YELLOW}\nShutting down factory...${NC}'; kill $FACTORY_PID 2>/dev/null; rm -f $PID_FILE; exit 0" INT TERM

# Wait for the process
wait $FACTORY_PID
rm -f "$PID_FILE"
echo -e "${YELLOW}Factory stopped.${NC}"
read -p "Press Enter to close..."
