#!/bin/bash
# MultiLLM plugin: ensure the gateway is running on SessionStart
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

GATEWAY_DIR="${MULTILLM_GATEWAY_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENVFILE="${MULTILLM_ENV_FILE:-$GATEWAY_DIR/.env}"

load_env_file() {
    local file="$1"
    local line key value
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
        [[ "$line" == export\ * ]] && line="${line#export }"
        [[ "$line" != *=* ]] && continue
        key="${line%%=*}"
        value="${line#*=}"
        key="${key//[[:space:]]/}"
        [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"
        if [[ "$value" == \"*\" && "$value" == *\" ]]; then
            value="${value:1:${#value}-2}"
        elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
            value="${value:1:${#value}-2}"
        fi
        export "$key=$value"
    done < "$file"
}

# Load KEY=VALUE env vars from .env if it exists without executing the file.
if [[ -f "$ENVFILE" ]]; then
    load_env_file "$ENVFILE"
fi

GATEWAY_PORT="${MULTILLM_GATEWAY_PORT:-${GATEWAY_PORT:-8080}}"
MULTILLM_HOME_DIR="${MULTILLM_HOME:-${MULTILLM_DATA_DIR:-$HOME/.multillm}}"
PIDFILE="$MULTILLM_HOME_DIR/gateway.pid"
LOGFILE="$MULTILLM_HOME_DIR/gateway.log"
HEALTH_URL="${MULTILLM_GATEWAY_URL:-http://127.0.0.1:$GATEWAY_PORT}"

mkdir -p "$MULTILLM_HOME_DIR"

# Check if gateway is already responding
if curl -s --connect-timeout 1 "$HEALTH_URL/health" >/dev/null 2>&1; then
    exit 0
fi

# Check stale PID
if [[ -f "$PIDFILE" ]]; then
    OLD_PID=$(cat "$PIDFILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        sleep 1
        if curl -s --connect-timeout 2 "$HEALTH_URL/health" >/dev/null 2>&1; then
            exit 0
        fi
        kill "$OLD_PID" 2>/dev/null
    fi
    rm -f "$PIDFILE"
fi

cd "$GATEWAY_DIR" || exit 0
nohup python -m multillm.gateway >> "$LOGFILE" 2>&1 &
echo "$!" > "$PIDFILE"

for i in $(seq 1 10); do
    if curl -s --connect-timeout 1 "$HEALTH_URL/health" >/dev/null 2>&1; then
        echo "MultiLLM gateway started (PID $(cat $PIDFILE))" >&2
        exit 0
    fi
    sleep 0.5
done
echo "MultiLLM gateway starting (may need a moment)" >&2
exit 0
