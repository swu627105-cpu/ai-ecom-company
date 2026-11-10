#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x apps/api/.venv/bin/python ]]; then
  python3 -m venv apps/api/.venv
  apps/api/.venv/bin/python -m pip install -r apps/api/requirements.txt
fi
if [[ ! -d node_modules ]]; then npm ci; fi
api_pid=''
web_pid=''
cleanup() {
  if [[ -n "$api_pid" ]]; then kill "$api_pid" 2>/dev/null || true; fi
  if [[ -n "$web_pid" ]]; then kill "$web_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM
if ! curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
  apps/api/.venv/bin/python -m uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port 8000 &
  api_pid=$!
fi
if ! curl -fsS http://localhost:5173/ >/dev/null 2>&1; then
  npm run dev -- --host localhost &
  web_pid=$!
fi
printf '运营台：http://localhost:5173/\n模型配置：在运营台填写自己的书生 API Key\n按 Ctrl+C 关闭由本脚本启动的服务。\n'
wait
