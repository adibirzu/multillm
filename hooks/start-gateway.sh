#!/bin/bash
# MultiLLM plugin: ensure the gateway is running on SessionStart
GATEWAY_PORT="${MULTILLM_GATEWAY_PORT:-${GATEWAY_PORT:-8080}}"
GATEWAY_DIR="${MULTILLM_GATEWAY_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
MULTILLM_HOME_DIR="${MULTILLM_HOME:-${MULTILLM_DATA_DIR:-$HOME/.multillm}}"
PIDFILE="$MULTILLM_HOME_DIR/gateway.pid"
LOGFILE="$MULTILLM_HOME_DIR/gateway.log"
ENVFILE="${MULTILLM_ENV_FILE:-$GATEWAY_DIR/.env}"

mkdir -p "$MULTILLM_HOME_DIR"

# Load env vars from .env if it exists
if [[ -f "$ENVFILE" ]]; then
    set -a
    source "$ENVFILE"
    set +a
fi

# Check if gateway is already responding
if curl -s --connect-timeout 1 "http://localhost:$GATEWAY_PORT/health" >/dev/null 2>&1; then
    exit 0
fi

# Check stale PID
if [[ -f "$PIDFILE" ]]; then
    OLD_PID=$(cat "$PIDFILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        sleep 1
        if curl -s --connect-timeout 2 "http://localhost:$GATEWAY_PORT/health" >/dev/null 2>&1; then
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
    if curl -s --connect-timeout 1 "http://localhost:$GATEWAY_PORT/health" >/dev/null 2>&1; then
        echo "MultiLLM gateway started (PID $(cat $PIDFILE))" >&2
        exit 0
    fi
    sleep 0.5
done
echo "MultiLLM gateway starting (may need a moment)" >&2
exit 0
