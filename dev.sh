#!/usr/bin/env bash
# Start the three demo platforms (six servers) from one terminal.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$ROOT/.dev/logs"
PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if command -v python3.12 >/dev/null 2>&1; then
    PYTHON=python3.12
  else
    PYTHON=python3
  fi
fi

usage() {
  cat <<'EOF'
Usage: ./dev.sh [up|setup|logs|help]

  setup  Install the Python and Node dependencies for all three platforms.
  up     Start all six servers (default). Press Ctrl+C to stop them all.
  logs   Follow the server logs in .dev/logs/.

Set PYTHON=python3.12 to choose a Python interpreter during setup.
Set DEV_PORT_OFFSET=100 to shift every port when the defaults are occupied.
EOF
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing $1. Install it and try again." >&2
    exit 1
  fi
}

check_versions() {
  require_command "$PYTHON"
  require_command node
  require_command npm
  require_command curl
  require_command pgrep
  "$PYTHON" -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] < (3, 15) else 1)' || {
    echo "Python 3.11–3.14 is required (3.12 recommended)." >&2
    exit 1
  }
  node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 18 || (major === 18 && minor >= 18) ? 0 : 1)' || {
    echo "Node.js 18.18+ is required (20 recommended)." >&2
    exit 1
  }
}

setup() {
  check_versions
  local platform frontend
  for platform in shopping message-platform admin-dashboard; do
    if [[ "$platform" == "admin-dashboard" ]]; then frontend=web; else frontend=frontend; fi
    echo "Installing $platform backend..."
    "$PYTHON" -m venv "$ROOT/$platform/backend/.venv"
    "$ROOT/$platform/backend/.venv/bin/python" -m pip install -r "$ROOT/$platform/backend/requirements.txt"
    echo "Installing $platform frontend..."
    (cd "$ROOT/$platform/$frontend" && npm ci)
  done
  echo "Setup complete. Run ./dev.sh to start all six servers."
}

check_dependencies() {
  local missing=0 platform frontend
  for platform in shopping message-platform admin-dashboard; do
    if [[ "$platform" == "admin-dashboard" ]]; then frontend=web; else frontend=frontend; fi
    if [[ ! -x "$ROOT/$platform/backend/.venv/bin/python" ]]; then
      echo "Missing $platform backend environment." >&2
      missing=1
    fi
    if [[ ! -x "$ROOT/$platform/$frontend/node_modules/.bin/next" ]]; then
      echo "Missing $platform frontend dependencies." >&2
      missing=1
    fi
  done
  if ((missing)); then
    echo "Run ./dev.sh setup first." >&2
    exit 1
  fi
}

check_port() {
  local port="$1"
  if ! "$PYTHON" - "$port" <<'PY'
import socket
import sys

with socket.socket() as sock:
    try:
        sock.bind(("127.0.0.1", int(sys.argv[1])))
    except OSError:
        sys.exit(1)
PY
  then
    echo "Port $port is already in use. Stop that server before running ./dev.sh." >&2
    exit 1
  fi
}

NAMES=()
PIDS=()

start() {
  local name="$1" directory="$2"
  shift 2
  (cd "$directory" && exec "$@") >"$LOG_DIR/$name.log" 2>&1 &
  NAMES+=("$name")
  PIDS+=("$!")
}

stop_tree() {
  local pid="$1" child
  while read -r child; do
    [[ -n "$child" ]] && stop_tree "$child"
  done < <(pgrep -P "$pid" 2>/dev/null || true)
  kill -TERM "$pid" 2>/dev/null || true
}

cleanup() {
  trap - EXIT INT TERM
  if ((${#PIDS[@]})); then
    echo "Stopping all six servers..."
    local pid
    for pid in "${PIDS[@]}"; do stop_tree "$pid"; done
    for pid in "${PIDS[@]}"; do wait "$pid" 2>/dev/null || true; done
  fi
}

wait_ready() {
  local name="$1" url="$2" pid="$3" attempt
  for ((attempt=0; attempt<60; attempt++)); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "$name exited during startup. Recent log output:" >&2
      tail -n 20 "$LOG_DIR/$name.log" >&2
      exit 1
    fi
    if curl -fsS --max-time 2 -o /dev/null "$url" 2>/dev/null; then
      echo "Ready: $name  $url"
      return
    fi
    sleep 1
  done
  echo "$name did not become ready within 60 seconds. Recent log output:" >&2
  tail -n 20 "$LOG_DIR/$name.log" >&2
  exit 1
}

up() {
  check_versions
  check_dependencies
  local offset="${DEV_PORT_OFFSET:-0}" port
  if [[ ! "$offset" =~ ^[0-9]+$ ]] || ((10#$offset > 57235)); then
    echo "DEV_PORT_OFFSET must be an integer from 0 to 57235." >&2
    exit 1
  fi
  local mart_web=$((3100 + offset)) mart_api=$((8100 + offset))
  local connect_web=$((3200 + offset)) connect_api=$((8200 + offset))
  local relay_web=$((3300 + offset)) relay_api=$((8300 + offset))
  for port in "$mart_web" "$mart_api" "$connect_web" "$connect_api" "$relay_web" "$relay_api"; do check_port "$port"; done
  mkdir -p "$LOG_DIR"
  trap cleanup EXIT
  trap 'exit 0' INT TERM

  # Let both applications become ready before starting the uptime worker. This
  # avoids recording a false outage while Next.js is still compiling.
  start novamart-api "$ROOT/shopping/backend" "$ROOT/shopping/backend/.venv/bin/uvicorn" app.main:app --reload --port "$mart_api"
  start novamart-web "$ROOT/shopping/frontend" env BACKEND_URL="http://127.0.0.1:$mart_api" "$ROOT/shopping/frontend/node_modules/.bin/next" dev -p "$mart_web"
  start novaconnect-api "$ROOT/message-platform/backend" "$ROOT/message-platform/backend/.venv/bin/uvicorn" app.main:app --reload --port "$connect_api"
  start novaconnect-web "$ROOT/message-platform/frontend" env BACKEND_URL="http://127.0.0.1:$connect_api" NEXT_PUBLIC_WS_URL="ws://127.0.0.1:$connect_api" "$ROOT/message-platform/frontend/node_modules/.bin/next" dev -p "$connect_web"

  wait_ready novamart-api "http://127.0.0.1:$mart_api/api/health" "${PIDS[0]}"
  wait_ready novamart-web "http://127.0.0.1:$mart_web" "${PIDS[1]}"
  wait_ready novaconnect-api "http://127.0.0.1:$connect_api/health" "${PIDS[2]}"
  wait_ready novaconnect-web "http://127.0.0.1:$connect_web" "${PIDS[3]}"

  start opsrelay-api "$ROOT/admin-dashboard/backend" env OPSRELAY_NOVAMART_FRONTEND_URL="http://127.0.0.1:$mart_web" OPSRELAY_NOVAMART_BACKEND_URL="http://127.0.0.1:$mart_api" OPSRELAY_NOVACONNECT_FRONTEND_URL="http://127.0.0.1:$connect_web" OPSRELAY_NOVACONNECT_BACKEND_URL="http://127.0.0.1:$connect_api" "$ROOT/admin-dashboard/backend/.venv/bin/python" -m uptime serve --port "$relay_api"
  start opsrelay-web "$ROOT/admin-dashboard/web" env OPSRELAY_UPTIME_API_URL="http://127.0.0.1:$relay_api" "$ROOT/admin-dashboard/web/node_modules/.bin/next" dev -p "$relay_web"
  wait_ready opsrelay-api "http://127.0.0.1:$relay_api/api/v1/health" "${PIDS[4]}"
  wait_ready opsrelay-web "http://127.0.0.1:$relay_web" "${PIDS[5]}"

  echo "All six servers are running: NovaMart http://localhost:$mart_web, NovaConnect http://localhost:$connect_web, OpsRelay http://localhost:$relay_web"
  echo "Logs: $LOG_DIR"
  echo "Press Ctrl+C to stop them. In another terminal, run ./dev.sh logs to follow output."
  local i code
  while true; do
    for ((i=0; i<${#PIDS[@]}; i++)); do
      if ! kill -0 "${PIDS[i]}" 2>/dev/null; then
        code=0
        wait "${PIDS[i]}" || code=$?
        echo "${NAMES[i]} exited (status $code). Recent log output:" >&2
        tail -n 20 "$LOG_DIR/${NAMES[i]}.log" >&2
        exit 1
      fi
    done
    sleep 1
  done
}

case "${1:-up}" in
  up) up ;;
  setup) setup ;;
  logs)
    if ! compgen -G "$LOG_DIR/*.log" >/dev/null; then
      echo "No logs yet. Run ./dev.sh first." >&2
      exit 1
    fi
    tail -n 20 -f "$LOG_DIR"/*.log
    ;;
  help|-h|--help) usage ;;
  *) usage >&2; exit 2 ;;
esac
