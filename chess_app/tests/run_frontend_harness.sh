#!/usr/bin/env bash
# Verifica frontend senza browser: avvia un backend ISOLATO (DB scratch su
# porta dedicata, il dev server dell'utente non viene toccato), installa jsdom
# in tests/.harness_deps (gitignorata — unica dipendenza del tooling di test,
# il frontend dell'app resta a zero dipendenze npm) e lancia frontend_harness.mjs.
#
# Uso:   tests/run_frontend_harness.sh          (dalla dir chess_app/)
#        PYTHON=/path/venv/bin/python HARNESS_PORT=8977 tests/run_frontend_harness.sh
set -euo pipefail
cd "$(dirname "$0")/.."   # chess_app/

export HARNESS_PORT="${HARNESS_PORT:-8977}"
SCRATCH_DIR="$(mktemp -d -t chesslab_harness_XXXXXX)"
export CHESS_LAB_DB="$SCRATCH_DIR/harness.db"

# Interprete python: $PYTHON se dato, altrimenti il venv del repo se esiste,
# altrimenti python3 (con le dipendenze del backend installate).
if [ -z "${PYTHON:-}" ]; then
  REPO_ROOT="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null | xargs dirname || true)"
  if [ -n "$REPO_ROOT" ] && [ -x "$REPO_ROOT/venv/bin/python" ]; then
    PYTHON="$REPO_ROOT/venv/bin/python"
  else
    PYTHON="python3"
  fi
fi

# jsdom: install una-tantum nella dir nascosta (fuori da git)
if [ ! -d tests/.harness_deps/node_modules/jsdom ]; then
  echo "[harness] installo jsdom in tests/.harness_deps (solo la prima volta)..."
  mkdir -p tests/.harness_deps
  npm install --prefix tests/.harness_deps --no-fund --no-audit --loglevel=error jsdom
fi

echo "[harness] backend isolato su porta $HARNESS_PORT (DB: $CHESS_LAB_DB)"
"$PYTHON" -m uvicorn backend.main:app --port "$HARNESS_PORT" --log-level warning &
BACKEND_PID=$!
cleanup() {
  kill "$BACKEND_PID" 2>/dev/null || true
  wait "$BACKEND_PID" 2>/dev/null || true
  rm -rf "$SCRATCH_DIR"
}
trap cleanup EXIT

# Attesa readiness (max ~15s)
for _ in $(seq 1 60); do
  if curl -sf "http://localhost:$HARNESS_PORT/health" >/dev/null 2>&1; then break; fi
  sleep 0.25
done
curl -sf "http://localhost:$HARNESS_PORT/health" >/dev/null || {
  echo "[harness] ERRORE: backend non raggiungibile su porta $HARNESS_PORT" >&2
  exit 1
}

node tests/frontend_harness.mjs
