#!/bin/bash
set -euo pipefail

# MultiLLM Gateway — One-line installer for Claude Code
# Usage: curl -sSL https://raw.githubusercontent.com/adibirzu/multillm/main/install.sh | bash

REPO_URL="https://github.com/adibirzu/multillm.git"
INSTALL_DIR="${MULTILLM_INSTALL_DIR:-$HOME/.local/share/multillm}"
DATA_DIR="$HOME/.multillm"
CLAUDE_DIR="$HOME/.claude"

C_GREEN='\033[0;32m'
C_CYAN='\033[0;36m'
C_YELLOW='\033[0;33m'
C_RED='\033[0;31m'
C_BOLD='\033[1m'
C_RESET='\033[0m'

info()  { echo -e "${C_CYAN}▸${C_RESET} $*"; }
ok()    { echo -e "${C_GREEN}✓${C_RESET} $*"; }
warn()  { echo -e "${C_YELLOW}!${C_RESET} $*"; }
fail()  { echo -e "${C_RED}✗${C_RESET} $*" >&2; exit 1; }

echo -e "\n${C_BOLD}MultiLLM Gateway — Installer${C_RESET}\n"

# ── Prerequisites ──────────────────────────────────────────────────────────
command -v python3 >/dev/null 2>&1 || fail "python3 is required. Install Python 3.11+."
command -v pip3 >/dev/null 2>&1 || command -v pip >/dev/null 2>&1 || fail "pip is required."
command -v git >/dev/null 2>&1 || fail "git is required."

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [[ "$PY_MAJOR" -lt 3 ]] || [[ "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 11 ]]; then
    fail "Python 3.11+ required (found $PY_VERSION)"
fi
ok "Python $PY_VERSION"

# ── Detect if running from inside the repo ─────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/multillm/gateway.py" ]]; then
    INSTALL_DIR="$SCRIPT_DIR"
    info "Running from repo directory: $INSTALL_DIR"
else
    # Clone or update
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        info "Updating existing installation..."
        cd "$INSTALL_DIR" && git pull --ff-only 2>/dev/null || true
    else
        info "Cloning MultiLLM to $INSTALL_DIR..."
        mkdir -p "$(dirname "$INSTALL_DIR")"
        git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
    fi
fi
ok "Source ready at $INSTALL_DIR"

# ── Install Python package ─────────────────────────────────────────────────
info "Installing Python package..."
PIP_CMD="pip3"
command -v pip3 >/dev/null 2>&1 || PIP_CMD="pip"
cd "$INSTALL_DIR"
$PIP_CMD install -e "." --quiet 2>/dev/null || $PIP_CMD install -e "." 2>&1 | tail -3
ok "Python package installed"

# ── Create data directory ──────────────────────────────────────────────────
mkdir -p "$DATA_DIR"

# ── Create .env if missing ─────────────────────────────────────────────────
if [[ ! -f "$INSTALL_DIR/.env" && -f "$INSTALL_DIR/.env.example" ]]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    ok "Created .env from template (edit $INSTALL_DIR/.env to add API keys)"
fi

# ── Register Claude Code hooks ─────────────────────────────────────────────
HOOKS_FILE="$CLAUDE_DIR/hooks.json"
GATEWAY_HOOK_CMD="$INSTALL_DIR/hooks/start-gateway.sh"

info "Registering Claude Code hooks..."
mkdir -p "$CLAUDE_DIR"

if [[ -f "$HOOKS_FILE" ]]; then
    # Check if hook already registered
    if grep -q "start-gateway.sh" "$HOOKS_FILE" 2>/dev/null; then
        ok "Hooks already registered"
    else
        # Merge into existing hooks.json
        python3 -c "
import json, sys
with open('$HOOKS_FILE') as f:
    hooks = json.load(f)
entry = {
    'hooks': [{
        'type': 'command',
        'command': '$GATEWAY_HOOK_CMD',
        'timeout': 15,
        'statusMessage': 'Starting MultiLLM gateway...'
    }]
}
hooks.setdefault('SessionStart', []).append(entry)
with open('$HOOKS_FILE', 'w') as f:
    json.dump(hooks, f, indent=2)
print('Merged hook into existing hooks.json')
" && ok "Hook added to $HOOKS_FILE"
    fi
else
    cat > "$HOOKS_FILE" <<HOOKEOF
{
  "SessionStart": [
    {
      "hooks": [
        {
          "type": "command",
          "command": "$GATEWAY_HOOK_CMD",
          "timeout": 15,
          "statusMessage": "Starting MultiLLM gateway..."
        }
      ]
    }
  ]
}
HOOKEOF
    ok "Created $HOOKS_FILE"
fi

# ── Make hook executable ───────────────────────────────────────────────────
chmod +x "$INSTALL_DIR/hooks/start-gateway.sh"

# ── Verify installation ───────────────────────────────────────────────────
if python3 -c "import multillm" 2>/dev/null; then
    ok "Package verified"
else
    warn "Package import check failed — may need to restart shell"
fi

# ── Summary ────────────────────────────────────────────────────────────────
echo ""
echo -e "${C_BOLD}${C_GREEN}Installation complete!${C_RESET}"
echo ""
echo -e "${C_BOLD}Quick Start:${C_RESET}"
echo ""
echo "  1. Start the gateway:"
echo -e "     ${C_CYAN}python -m multillm.gateway${C_RESET}"
echo ""
echo "  2. Connect Claude Code:"
echo -e "     ${C_CYAN}export ANTHROPIC_BASE_URL=http://localhost:8080${C_RESET}"
echo -e "     ${C_CYAN}claude${C_RESET}"
echo ""
echo "  3. Use slash commands:"
echo -e "     ${C_CYAN}/llm-ask ollama/llama3 explain this code${C_RESET}"
echo -e "     ${C_CYAN}/llm-usage${C_RESET}"
echo -e "     ${C_CYAN}/llm-council what's the best approach?${C_RESET}"
echo ""
echo -e "  Dashboard: ${C_CYAN}http://localhost:8080/dashboard${C_RESET}"
echo -e "  Config:    ${C_CYAN}$INSTALL_DIR/.env${C_RESET}"
echo ""
echo -e "  The gateway auto-starts with Claude Code sessions via hooks."
echo -e "  Add API keys to .env for cloud backends (Ollama works out of the box)."
echo ""
