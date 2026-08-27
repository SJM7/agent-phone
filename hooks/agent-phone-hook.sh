#!/bin/sh
# Claude Code hook: forward the hook JSON from stdin to the agent-phone daemon.
# Usage (from settings.json): agent-phone-hook.sh stop | user-prompt-submit
# Exits 0 no matter what -- a dead daemon must never break the Claude Code session.

ENDPOINT="${1:-stop}"

curl --silent --output /dev/null --max-time 2 \
    -X POST \
    -H "Content-Type: application/json" \
    --data-binary @- \
    "http://127.0.0.1:8489/hook/${ENDPOINT}" 2>/dev/null

exit 0
