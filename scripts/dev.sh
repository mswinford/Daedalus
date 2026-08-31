#!/usr/bin/env bash
# Boot the full AI Forge dev stack in one terminal:
#   backend   127.0.0.1:3000   (FastAPI, auto-reload)
#   registry  127.0.0.1:3010   (capability registry, auto-reload)
#   frontend  127.0.0.1:5173   (Vite; proxies /api and /registry)
#
# Ctrl-C stops everything. If any service crashes, the rest are stopped too.
# Per-service logs: .dev/<name>.log
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/.dev"
mkdir -p "$LOG_DIR"

declare -a pids=()
names=()

stop_all() {
  for i in "${!pids[@]}"; do
    kill -- "-${pids[$i]}" 2>/dev/null || kill "${pids[$i]}" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}

on_signal() {
  trap - INT TERM EXIT
  echo
  echo "Stopping dev stack..."
  stop_all
  exit 0
}

on_exit() {
  local code=$?
  trap - INT TERM EXIT
  if [ ${#pids[@]} -gt 0 ]; then
    echo
    echo "A service exited (code $code) — stopping the rest."
    stop_all
  fi
  exit "$code"
}

trap on_signal INT TERM
trap on_exit EXIT

start() {
  local name="$1" dir="$2" cmd="$3"
  # setsid gives each service its own process group so we can kill the whole
  # tree (npm -> vite, uvicorn reload workers) with one signal.
  setsid bash -c "cd '$dir' && exec $cmd" >>"$LOG_DIR/$name.log" 2>&1 &
  pids+=("$!")
  names+=("$name")
}

start backend  "$ROOT"            "python backend/cli.py"
start registry "$ROOT"            "python -m registry.cli serve"
start frontend "$ROOT/frontend"   "npm run dev"

echo "AI Forge dev stack starting (logs in .dev/):"
echo "  backend    http://127.0.0.1:3000   .dev/backend.log"
echo "  registry   http://127.0.0.1:3010   .dev/registry.log"
echo "  frontend   http://127.0.0.1:5173   .dev/frontend.log"
echo "Press Ctrl-C to stop everything."

# Poll for a dead service. (A bare `wait -n` would block trap delivery until a
# job exits, so Ctrl-C would be ignored.) The sleep also bounds SIGINT latency.
while :; do
  for i in "${!pids[@]}"; do
    if ! kill -0 "${pids[$i]}" 2>/dev/null; then
      echo "${names[$i]} exited — stopping the rest."
      exit 1
    fi
  done
  sleep 1
done
