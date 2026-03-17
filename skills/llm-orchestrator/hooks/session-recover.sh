#!/usr/bin/env bash
# session-recover.sh — Retrieve the last checkpoint from MultiLLM shared memory
# Intended to be called at session start to provide continuity.
#
# Usage:
#   session-recover.sh [project_name]
#
# Outputs the most recent checkpoint for the project, if any.
# Requires the MultiLLM gateway running at localhost:8080.

set -euo pipefail

GATEWAY="${MULTILLM_GATEWAY_URL:-http://localhost:8080}"
PROJECT="${1:-$(basename "$(pwd)")}"

# Check gateway is reachable (silent, 2s timeout)
if ! curl -sf --max-time 2 "${GATEWAY}/health" > /dev/null 2>&1; then
    exit 0
fi

# Search for recent checkpoints
RESULT=$(curl -sf "${GATEWAY}/api/memory/search?q=checkpoint+${PROJECT}&limit=3" 2>/dev/null || echo '[]')

python3 -c "
import json, sys

data = json.loads(sys.argv[1])
memories = data if isinstance(data, list) else data.get('memories', [])

if not memories:
    sys.exit(0)

print('## Prior Context Found')
print()
for m in memories[:3]:
    print(f'### {m.get(\"title\", \"Untitled\")}')
    print(f'*{m.get(\"source_llm\", \"unknown\")} — {m.get(\"created_at\", \"\")}*')
    print()
    print(m.get('content', ''))
    print()
" "$RESULT"
