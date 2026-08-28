#!/bin/sh
# Agent hook: forward the hook JSON from stdin to the agent-phone daemon.
# Usage: agent-phone-hook.sh (stop|user-prompt-submit) [claude|codex]
# Works for both Claude Code and Codex hooks (both pass JSON on stdin).
# Exits 0 no matter what -- a dead daemon must never break the agent session.

ENDPOINT="${1:-stop}"
AGENT="${2:-claude}"

curl --silent --output /dev/null --max-time 2 \
    -X POST \
    -H "Content-Type: application/json" \
    --data-binary @- \
    "http://127.0.0.1:8489/hook/${ENDPOINT}?agent=${AGENT}" 2>/dev/null

exit 0
