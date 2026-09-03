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

# Prefer the repo venv; fall back to whatever python is on PATH.
PY="python"
[ -x "$ROOT/.venv/bin/python" ] && PY="$ROOT/.venv/bin/python"

# Import the real app module (what uvicorn loads) so ANY missing dependency is
# caught here with a clear hint instead of crashing mid-startup.
if ! "$PY" -c 'import sys; sys.path[:0] = ["backend", "."]; import app.main' >/dev/null 2>&1; then
  echo "Python dependencies missing or incomplete (checked with $PY)." >&2
  echo "Run ./scripts/setup.sh first." >&2
  exit 1
fi
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "Frontend dependencies missing (no frontend/node_modules)." >&2
  echo "Run ./scripts/setup.sh first." >&2
  exit 1
fi

declare -a pids=()
names=()

HAS_SETSID=0
command -v setsid >/dev/null 2>&1 && HAS_SETSID=1

stop_all() {
  for i in "${!pids[@]}"; do
    local pid="${pids[$i]}"
    if [ "$HAS_SETSID" = 1 ]; then
      # setsid made the service a session leader: kill its whole process group.
      kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    else
      # No setsid (macOS): kill direct children first (e.g. vite under npm),
      # then the service itself.
      pkill -P "$pid" 2>/dev/null || true
      kill "$pid" 2>/dev/null || true
    fi
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
  # With setsid (Linux) each service gets its own process group so the whole
  # tree (npm -> vite, uvicorn reload workers) dies with one signal. Without it
  # (macOS) we fall back to a plain background launch; stop_all cleans up
  # children via pkill -P.
  if [ "$HAS_SETSID" = 1 ]; then
    setsid bash -c "cd '$dir' && exec $cmd" >>"$LOG_DIR/$name.log" 2>&1 &
  else
    bash -c "cd '$dir' && exec $cmd" >>"$LOG_DIR/$name.log" 2>&1 &
  fi
  pids+=("$!")
  names+=("$name")
}

start backend  "$ROOT"            "'$PY' backend/cli.py"
start registry "$ROOT"            "'$PY' -m registry.cli serve"
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
