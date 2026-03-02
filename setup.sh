#!/bin/bash
# ═══════════════════════════════════════════════════════════
# Autonomous Factory — First-Time Setup
# Run once to install dependencies and configure environment.
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
echo -e "${BLUE}       AUTONOMOUS FACTORY — Setup                  ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
echo ""

# 1. Check Python
echo -e "${YELLOW}[1/7] Checking Python...${NC}"
if command -v python3 &> /dev/null; then
    PY_VERSION=$( python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" )
    echo -e "${GREEN}  Python $PY_VERSION found${NC}"

    # Check minimum version (3.10+)
    MIN_OK=$(python3 -c "import sys; print('yes' if sys.version_info >= (3,10) else 'no')")
    if [ "$MIN_OK" != "yes" ]; then
        echo -e "${RED}  Python 3.10+ required. Current: $PY_VERSION${NC}"
        exit 1
    fi
else
    echo -e "${RED}  Python 3 not found. Install from python.org${NC}"
    exit 1
fi

# 2. Install Python dependencies
echo -e "${YELLOW}[2/7] Installing Python dependencies...${NC}"
pip3 install --quiet aiohttp asyncio-mqtt 2>/dev/null || {
    echo -e "${YELLOW}  pip install with --user flag${NC}"
    pip3 install --user --quiet aiohttp 2>/dev/null || echo -e "${YELLOW}  aiohttp may already be installed${NC}"
}
echo -e "${GREEN}  Done${NC}"

# 3. Check Ollama
echo -e "${YELLOW}[3/7] Checking Ollama...${NC}"
if command -v ollama &> /dev/null; then
    echo -e "${GREEN}  Ollama installed${NC}"
else
    echo -e "${YELLOW}  Ollama not found. Installing...${NC}"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo -e "${YELLOW}  Visit https://ollama.ai to download Ollama for macOS${NC}"
    else
        curl -fsSL https://ollama.ai/install.sh | sh || echo -e "${RED}  Ollama install failed${NC}"
    fi
fi

# 4. Pull required models
echo -e "${YELLOW}[4/7] Pulling Ollama models (this may take a while)...${NC}"
MODELS=("deepseek-coder-v2:16b" "qwen2.5-coder:7b" "phi3:mini")
for model in "${MODELS[@]}"; do
    echo -e "  Pulling $model..."
    ollama pull "$model" 2>/dev/null || echo -e "${YELLOW}  Failed to pull $model (Ollama may not be running)${NC}"
done
echo -e "${GREEN}  Done${NC}"

# 5. Check CLI tools
echo -e "${YELLOW}[5/7] Checking CLI tools...${NC}"
CLI_TOOLS=("claude" "kimi" "gemini")
for tool in "${CLI_TOOLS[@]}"; do
    if command -v "$tool" &> /dev/null; then
        echo -e "${GREEN}  [✓] $tool${NC}"
    else
        echo -e "${YELLOW}  [!] $tool not found (optional — install via npm or native installer)${NC}"
    fi
done

# 6. Create directory structure
echo -e "${YELLOW}[6/7] Creating directory structure...${NC}"
mkdir -p factory_state/logs
mkdir -p factory_state/checkpoints
mkdir -p factory_state/chat_archive
mkdir -p protocols
mkdir -p config
echo -e "${GREEN}  Done${NC}"

# 7. Make scripts executable
echo -e "${YELLOW}[7/7] Setting permissions...${NC}"
chmod +x start.command 2>/dev/null || true
chmod +x recover.sh 2>/dev/null || true
chmod +x setup.sh 2>/dev/null || true
chmod +x check_health.sh 2>/dev/null || true
echo -e "${GREEN}  Done${NC}"

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Setup complete!                                  ${NC}"
echo -e "${GREEN}  Run ./start.command to launch the factory.       ${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
