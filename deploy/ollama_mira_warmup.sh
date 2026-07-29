#!/bin/sh
# Forces Ollama to load llama3.1:8b into memory immediately on startup,
# rather than waiting for the first real dashboard request to pay that cost
# (observed ~60s on this hardware for a model of comparable size). Called
# from deploy/ollama-mira.service's ExecStartPost, backgrounded there so it
# doesn't hold up systemd's own startup sequence while the model loads.
#
# An empty-prompt /api/generate call is Ollama's documented way to load a
# model into memory without running any actual inference.
set -eu

HOST="${OLLAMA_MIRA_HOST:-http://127.0.0.1:11435}"
MODEL="${OLLAMA_MIRA_MODEL:-llama3.1:8b}"

# Give the server a moment to start accepting connections before the first
# request — this itself does not need to be precise, curl will just fail
# fast and retry.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -s -o /dev/null "$HOST/api/tags"; then
    break
  fi
  sleep 1
done

curl -s -X POST "$HOST/api/generate" \
  -H "Content-Type: application/json" \
  -d "{\"model\": \"$MODEL\", \"keep_alive\": -1}" \
  > /dev/null 2>&1 || true
