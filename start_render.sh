#!/usr/bin/env bash
set -euo pipefail

KOL_PORT="${KOL_PORT:-8001}"

echo "Starting KOL FastAPI service on 127.0.0.1:${KOL_PORT}"
(
  cd kol_web/backend
  uvicorn app.main:app --host 127.0.0.1 --port "${KOL_PORT}"
) &

echo "Starting Flask web service"
exec gunicorn app:app -c gunicorn_config.py
