#!/bin/bash
# MultiLLM plugin: ensure the gateway is running on SessionStart
GATEWAY_PORT="${GATEWAY_PORT:-8080}"
GATEWAY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDFILE="$HOME/.multillm/gateway.pid"
LOGFILE="$HOME/.multillm/gateway.log"

mkdir -p "$HOME/.multillm"

# Load env vars from .env if it exists
if [[ -f "$GATEWAY_DIR/.env" ]]; then
    set -a
    source "$GATEWAY_DIR/.env"
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
