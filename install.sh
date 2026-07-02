#!/bin/bash
set -euo pipefail

# MultiLLM selective installer.
# Usage: ./install.sh [--component NAME ...] [--dry-run]

REPO_URL="https://github.com/adibirzu/multillm.git"
INSTALL_DIR="${MULTILLM_INSTALL_DIR:-$HOME/.local/share/multillm}"
DATA_DIR="${MULTILLM_HOME:-$HOME/.multillm}"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
CODEX_DIR="${CODEX_HOME:-$HOME/.codex}"
LOCAL_BIN_DIR="${MULTILLM_BIN_DIR:-$HOME/.local/bin}"

C_GREEN='\033[0;32m'
C_CYAN='\033[0;36m'
C_YELLOW='\033[0;33m'
C_RED='\033[0;31m'
C_BOLD='\033[1m'
C_RESET='\033[0m'

info() { echo -e "${C_CYAN}▸${C_RESET} $*"; }
ok() { echo -e "${C_GREEN}✓${C_RESET} $*"; }
warn() { echo -e "${C_YELLOW}!${C_RESET} $*"; }
fail() { echo -e "${C_RED}✗${C_RESET} $*" >&2; exit 1; }

list_components() {
    cat <<'EOF'
Available components:
  gateway       Core Python gateway, configuration template, and runtime data
  codex-mcp     Codex MCP registration and launcher (depends on: gateway)
  codex-skills  Reusable Codex workflow skills (standalone)
  claude        Claude hooks, MCP configuration, and launcher (depends on: gateway)
  all           Complete backward-compatible installation
EOF
}

usage() {
    cat <<'EOF'
Usage: ./install.sh [OPTIONS]

Options:
  --component NAME   Install a component; may be repeated
  --list-components  List available components and dependencies
  --dry-run          Print the resolved plan without making changes
  -h, --help         Show this help

With no --component option, the installer selects "all".
EOF
}

is_known_component() {
    case "$1" in
        gateway|codex-mcp|codex-skills|claude|all) return 0 ;;
        *) return 1 ;;
    esac
}

contains_component() {
    local needle="$1"
    shift
    local item
    for item in "$@"; do
        [[ "$item" == "$needle" ]] && return 0
    done
    return 1
}

append_resolved() {
    local component="$1"
    if ! contains_component "$component" "${RESOLVED_COMPONENTS[@]:-}"; then
        RESOLVED_COMPONENTS+=("$component")
    fi
}

SELECTED_COMPONENTS=()
RESOLVED_COMPONENTS=()
DRY_RUN=false
SHOW_COMPONENTS=false

# Argument parsing and validation deliberately happen before all checks and writes.
while [[ $# -gt 0 ]]; do
    case "$1" in
        --component)
            [[ $# -ge 2 ]] || fail "--component requires a value"
            is_known_component "$2" || fail "Unknown component: $2"
            SELECTED_COMPONENTS+=("$2")
            shift 2
            ;;
        --list-components)
            SHOW_COMPONENTS=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "Unknown option: $1"
            ;;
    esac
done

if [[ "$SHOW_COMPONENTS" == true ]]; then
    list_components
    exit 0
fi

if [[ ${#SELECTED_COMPONENTS[@]} -eq 0 ]]; then
    SELECTED_COMPONENTS=(all)
fi

for component in "${SELECTED_COMPONENTS[@]}"; do
    case "$component" in
        gateway) append_resolved gateway ;;
        codex-mcp)
            append_resolved gateway
            append_resolved codex-mcp
            ;;
        codex-skills) append_resolved codex-skills ;;
        claude)
            append_resolved gateway
            append_resolved claude
            ;;
        all)
            append_resolved gateway
            append_resolved codex-mcp
            append_resolved codex-skills
            append_resolved claude
            ;;
    esac
done

join_components() {
    local output=""
    local component
    for component in "$@"; do
        [[ -z "$output" ]] || output="$output, "
        output="$output$component"
    done
    printf '%s' "$output"
}

echo "Selected components: $(join_components "${SELECTED_COMPONENTS[@]}")"
echo "Resolved components: $(join_components "${RESOLVED_COMPONENTS[@]}")"

if [[ "$DRY_RUN" == true ]]; then
    echo "Dry run; no changes will be made."
    for component in "${RESOLVED_COMPONENTS[@]}"; do
        echo "Would install: $component"
    done
    exit 0
fi

echo -e "\n${C_BOLD}MultiLLM — Installer${C_RESET}\n"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/multillm/gateway.py" ]]; then
    INSTALL_DIR="$SCRIPT_DIR"
    info "Running from repo directory: $INSTALL_DIR"
else
    command -v git >/dev/null 2>&1 || fail "git is required to download MultiLLM."
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        info "Updating existing installation..."
        git -C "$INSTALL_DIR" pull --ff-only
    else
        info "Cloning MultiLLM to $INSTALL_DIR..."
        mkdir -p "$(dirname "$INSTALL_DIR")"
        git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
    fi
fi
ok "Source ready at $INSTALL_DIR"

install_gateway() {
    command -v python3 >/dev/null 2>&1 || fail "python3 is required. Install Python 3.11+."
    local python_version python_major python_minor venv_python
    python_version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    python_major="${python_version%%.*}"
    python_minor="${python_version#*.}"
    if [[ "$python_major" -lt 3 ]] || [[ "$python_major" -eq 3 && "$python_minor" -lt 11 ]]; then
        fail "Python 3.11+ required (found $python_version)"
    fi
    ok "Python $python_version"

    if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
        info "Creating isolated Python runtime..."
        python3 -m venv "$INSTALL_DIR/.venv"
    fi
    venv_python="$INSTALL_DIR/.venv/bin/python"
    info "Installing gateway package..."
    "$venv_python" -m pip install --quiet --editable "$INSTALL_DIR"

    mkdir -p "$DATA_DIR" "$LOCAL_BIN_DIR"
    if [[ ! -f "$INSTALL_DIR/.env" && -f "$INSTALL_DIR/.env.example" ]]; then
        cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
        ok "Created configuration template at $INSTALL_DIR/.env"
    fi

    cat > "$LOCAL_BIN_DIR/multillm-gateway" <<EOF
#!/bin/bash
export MULTILLM_HOME="\${MULTILLM_HOME:-$DATA_DIR}"
exec "$venv_python" -m multillm.gateway "\$@"
EOF
    chmod +x "$LOCAL_BIN_DIR/multillm-gateway"
    ok "Gateway installed"
}

install_mcp_launcher() {
    local venv_python="$INSTALL_DIR/.venv/bin/python"
    mkdir -p "$LOCAL_BIN_DIR"
    cat > "$LOCAL_BIN_DIR/multillm-mcp" <<EOF
#!/bin/bash
export MULTILLM_HOME="\${MULTILLM_HOME:-$DATA_DIR}"
export LLM_GATEWAY_URL="\${LLM_GATEWAY_URL:-http://localhost:8080}"
exec "$venv_python" -m multillm.mcp_server "\$@"
EOF
    chmod +x "$LOCAL_BIN_DIR/multillm-mcp"
}

install_codex_mcp() {
    local mcp_launcher="$LOCAL_BIN_DIR/multillm-mcp"
    install_mcp_launcher
    cat > "$LOCAL_BIN_DIR/codex-multillm" <<EOF
#!/bin/bash
export MULTILLM_HOME="\${MULTILLM_HOME:-$DATA_DIR}"
exec codex "\$@"
EOF
    chmod +x "$LOCAL_BIN_DIR/codex-multillm"

    if command -v codex >/dev/null 2>&1; then
        local existing=""
        existing="$(codex mcp get multillm 2>&1 || true)"
        if [[ "$existing" != *"$mcp_launcher"* ]]; then
            if [[ -n "$existing" ]]; then
                codex mcp remove multillm >/dev/null 2>&1 || true
            fi
            codex mcp add multillm -- "$mcp_launcher" >/dev/null
        fi
        ok "Codex MCP registration ready"
    else
        warn "Codex CLI not found; launcher installed but MCP registration skipped"
    fi
}

install_codex_skills() {
    local skill_dir skill_name target_dir
    mkdir -p "$CODEX_DIR/skills"
    for skill_dir in "$INSTALL_DIR"/skills/*; do
        [[ -d "$skill_dir" ]] || continue
        skill_name="$(basename "$skill_dir")"
        target_dir="$CODEX_DIR/skills/$skill_name"
        mkdir -p "$target_dir"
        cp -R "$skill_dir/." "$target_dir/"
    done
    ok "Codex skills installed at $CODEX_DIR/skills"
}

write_claude_configuration() {
    local hooks_file="$CLAUDE_DIR/hooks.json"
    local mcp_file="$CLAUDE_DIR/.mcp.json"
    local hook_command="$INSTALL_DIR/hooks/start-gateway.sh"
    local mcp_launcher="$LOCAL_BIN_DIR/multillm-mcp"

    mkdir -p "$CLAUDE_DIR"
    chmod +x "$hook_command"
    HOOKS_FILE="$hooks_file" HOOK_COMMAND="$hook_command" python3 <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["HOOKS_FILE"])
current = json.loads(path.read_text()) if path.exists() else {}
session_start = current.get("SessionStart", [])
preserved = [
    entry
    for entry in session_start
    if "start-gateway.sh" not in json.dumps(entry)
]
multillm_entry = {
    "hooks": [
        {
            "type": "command",
            "command": os.environ["HOOK_COMMAND"],
            "timeout": 15,
            "statusMessage": "Starting MultiLLM gateway...",
        }
    ]
}
updated = {**current, "SessionStart": [*preserved, multillm_entry]}
path.write_text(json.dumps(updated, indent=2) + "\n")
PY

    MCP_FILE="$mcp_file" MCP_LAUNCHER="$mcp_launcher" python3 <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["MCP_FILE"])
current = json.loads(path.read_text()) if path.exists() else {}
servers = current.get("mcpServers", {})
multillm = {"command": os.environ["MCP_LAUNCHER"], "args": []}
updated = {**current, "mcpServers": {**servers, "multillm": multillm}}
path.write_text(json.dumps(updated, indent=2) + "\n")
PY
}

install_claude() {
    install_mcp_launcher
    mkdir -p "$LOCAL_BIN_DIR"
    cat > "$LOCAL_BIN_DIR/claude-multillm" <<EOF
#!/bin/bash
export MULTILLM_HOME="\${MULTILLM_HOME:-$DATA_DIR}"
export ANTHROPIC_BASE_URL="\${ANTHROPIC_BASE_URL:-http://localhost:8080}"
exec claude "\$@"
EOF
    chmod +x "$LOCAL_BIN_DIR/claude-multillm"
    write_claude_configuration
    ok "Claude hooks, MCP configuration, and launcher installed"
}

for component in "${RESOLVED_COMPONENTS[@]}"; do
    case "$component" in
        gateway) install_gateway ;;
        codex-mcp) install_codex_mcp ;;
        codex-skills) install_codex_skills ;;
        claude) install_claude ;;
    esac
done

echo ""
echo -e "${C_BOLD}${C_GREEN}Installation complete!${C_RESET}"
echo "Installed components: $(join_components "${RESOLVED_COMPONENTS[@]}")"
if contains_component gateway "${RESOLVED_COMPONENTS[@]}"; then
    echo "Start the gateway: $LOCAL_BIN_DIR/multillm-gateway"
    echo "Dashboard: http://localhost:8080/dashboard"
    echo "Configuration: $INSTALL_DIR/.env"
fi
if contains_component codex-mcp "${RESOLVED_COMPONENTS[@]}" || \
   contains_component codex-skills "${RESOLVED_COMPONENTS[@]}"; then
    echo "Start a fresh Codex thread to load newly installed tools and skills."
fi
