#!/usr/bin/env bash
# Quick manual check against a running gateway.
#
#   export BGV_API_KEY=...        # if configured
#   ./scripts/smoke.sh [audio.wav]
set -euo pipefail

HOST="${HOST:-http://127.0.0.1:8125}"
AUDIO="${1:-}"
AUTH=()
if [[ -n "${BGV_API_KEY:-}" ]]; then
  AUTH=(-H "X-API-Key: ${BGV_API_KEY}")
fi

echo "== health"
curl -sS "${HOST}/v1/health?deep=true" "${AUTH[@]}" | python3 -m json.tool

echo
echo "== text command"
curl -sS -X POST "${HOST}/v1/command" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"text":"изключи лампата"}' | python3 -m json.tool

if [[ -n "${AUDIO}" ]]; then
  echo
  echo "== audio command (${AUDIO})"
  curl -sS -X POST "${HOST}/v1/voice" "${AUTH[@]}" \
    -F "audio=@${AUDIO}" -o /tmp/bgv_voice.json
  python3 -m json.tool < /tmp/bgv_voice.json
fi
