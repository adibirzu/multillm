#!/usr/bin/env bash
# auto-checkpoint.sh — Store a session checkpoint to MultiLLM shared memory
# Intended to be called as a Claude Code hook on session end or context compaction.
#
# Usage:
#   auto-checkpoint.sh [project_name] [summary]
#
# If no summary is given, stores a minimal checkpoint with the project name.
# Requires the MultiLLM gateway running at localhost:8080.

set -euo pipefail

GATEWAY="${MULTILLM_GATEWAY_URL:-http://localhost:8080}"
PROJECT="${1:-$(basename "$(pwd)")}"
SUMMARY="${2:-Session ended. No explicit checkpoint provided.}"
DATE="$(date +%Y-%m-%d)"
SOURCE_LLM="${MULTILLM_SOURCE_LLM:-claude}"

# Check gateway is reachable (silent, 2s timeout)
if ! curl -sf --max-time 2 "${GATEWAY}/health" > /dev/null 2>&1; then
    echo "[auto-checkpoint] Gateway unreachable at ${GATEWAY}, skipping."
    exit 0
fi

# Store checkpoint to shared memory
curl -sf -X POST "${GATEWAY}/api/memory" \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c "
import json, sys
print(json.dumps({
    'title': f'Checkpoint: {sys.argv[1]} {sys.argv[2]}',
    'content': sys.argv[3],
    'project': sys.argv[1],
    'category': 'context',
    'source_llm': sys.argv[4]
}))
" "$PROJECT" "$DATE" "$SUMMARY" "$SOURCE_LLM")" > /dev/null 2>&1

echo "[auto-checkpoint] Saved checkpoint for ${PROJECT} (${DATE})"
