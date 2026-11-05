#!/usr/bin/env bash
# Smoke check: bring the full Docker Compose stack up healthy and
# confirm the browser-facing path (nginx -> api -> db) actually works, not
# just that containers started.
set -euo pipefail

HEALTH_URL="http://localhost:8080/api/health"

echo "Starting docker compose stack..."
docker compose up -d --wait

echo "Curling ${HEALTH_URL}..."
response="$(curl -sf "${HEALTH_URL}")"

echo "Response: ${response}"

if [[ "${response}" != *'"status":"ok"'* ]]; then
  echo "FAIL: expected status \"ok\" in response, got: ${response}" >&2
  exit 1
fi

echo "OK: ${HEALTH_URL} reports status ok"
