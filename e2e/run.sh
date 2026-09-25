#!/usr/bin/env bash
# End-to-end tests against a throwaway sandbox. Nothing here touches your real
# database, your running server on :8765, or GitHub:
#   - code: a clone of this checkout (plus uncommitted changes) whose git
#     `origin` is a local bare repo, so auto-publish really pushes, but locally;
#   - data: a .backup copy of data/scout.db, fresh for every suite;
#   - server: run.py on :8766 with auto-publish after 1 idle minute;
#   - AI: off, or (reassess suite) the real Anthropic SDK pointed at
#     fake_anthropic.py, which replays a stored assessment: no paid calls.
#
# Usage: e2e/run.sh [startup] [ux] [replay] [reassess] [published] [availability]
#   default: startup ux replay reassess published. `availability` drives the real extension in
#   Chromium against the live listing sites (~10 min; sites that block
#   automated browsers come back "unclear", which is the expected, safe result).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
E2E=$ROOT/e2e
PY=$ROOT/.venv/bin/python
PORT=8766
FAKE_PORT=8799
SUITES=${*:-startup ux replay reassess published}
SB=${SANDBOX:-$(mktemp -d -t hoopty-e2e)}
export SANDBOX=$SB
echo "sandbox: $SB"

if lsof -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then echo "port $PORT is in use; stop that server first" >&2; exit 1; fi

# Code with a local-only origin.
git init -q --bare "$SB/origin.git"
git clone -q "$ROOT" "$SB/repo"
git -C "$SB/repo" remote set-url origin "$SB/origin.git"
git -C "$SB/repo" push -q origin HEAD:main
rsync -a --exclude .git --exclude .venv --exclude data --exclude .env --exclude __pycache__ --exclude node_modules "$ROOT/" "$SB/repo/"
[ "$(git -C "$SB/repo" remote get-url origin)" = "$SB/origin.git" ] || { echo "sandbox origin is not local; refusing" >&2; exit 1; }

# The extension, pointed at the sandbox port.
cp -R "$ROOT/extension" "$SB/ext"
sed -i '' "s/8765/$PORT/g" "$SB/ext/manifest.json" "$SB/ext/background.js" "$SB/ext/popup.js"
mkdir -p "$SB/out"

(cd "$E2E" && [ -d node_modules/playwright ] || PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install --silent)

PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT

fresh_db() {
  rm -f "$SB/scout.db"; sqlite3 "$ROOT/data/scout.db" ".backup '$SB/scout.db'"
  # The suites start from tier 1: drop a re-assess cycle your real board is part-way through.
  sqlite3 "$SB/scout.db" "DELETE FROM settings WHERE key='reassess_cycle'"
}
start_server() {   # $1 = "ai" to use the fake Anthropic API
  local extra=(ANTHROPIC_API_KEY= SCOUT_STARTUP_RESCORE_DELAY=${RESCORE_DELAY:-0})
  if [ "${1:-}" = ai ]; then extra=(SCOUT_STARTUP_RESCORE_DELAY=0 ANTHROPIC_API_KEY=fake-e2e-key ANTHROPIC_BASE_URL=http://127.0.0.1:$FAKE_PORT); fi
  export SERVER_STARTED=$(date -u +%Y-%m-%dT%H:%M:%S)
  (cd "$SB/repo" && exec env SCOUT_PORT=$PORT SCOUT_DB_PATH="$SB/scout.db" SCOUT_BACKUP_DIR=off \
     SCOUT_AUTOPUBLISH=1 SCOUT_AUTOPUBLISH_IDLE_MIN=1 SCOUT_AUTOPUBLISH_CHECKPOINT_MIN=30 "${extra[@]}" \
     "$PY" run.py >"$SB/out/server.log" 2>&1) &
  SERVER=$!; PIDS+=("$SERVER")
  until curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null; do sleep 0.5; done
}
wait_startup_rescore() {   # this server's own update, not events copied in with the DB
  for _ in $(seq 1 240); do
    curl -sf -H "Origin: http://127.0.0.1:$PORT" "http://127.0.0.1:$PORT/api/events" | \
      "$PY" -c "import json,sys,os; sys.exit(0 if any(e['kind'].startswith('startup_rescore') and e['ts'][:19] >= os.environ['SERVER_STARTED'] for e in json.load(sys.stdin)) else 1)" && return 0
    sleep 0.5
  done
}
stop_server() { kill "$SERVER" 2>/dev/null || true; wait "$SERVER" 2>/dev/null || true; }

FAILED=0
for suite in $SUITES; do
  echo "== $suite"
  fresh_db
  case $suite in
    ux)           start_server; wait_startup_rescore; (cd "$E2E" && node e2e_ux.js) || FAILED=1 ;;
    replay)       start_server; (cd "$E2E" && "$PY" replay_real_pages.py) | tee "$SB/out/replay.txt"; grep -q "^12/12 correct" "$SB/out/replay.txt" || FAILED=1 ;;
    reassess)     rm -f "$SB/fake_requests.jsonl"
                  (cd "$SB" && exec "$PY" "$E2E/fake_anthropic.py" $FAKE_PORT "$SB/scout.db") & PIDS+=("$!")
                  start_server ai; (cd "$E2E" && node e2e_reassess.js) || FAILED=1 ;;
    availability) start_server; (cd "$E2E" && node e2e_availability.js) || FAILED=1 ;;
    startup)      # Pretend the copy predates the current policy, so the boot-time update always runs.
                  sqlite3 "$SB/scout.db" "UPDATE assessments SET assessment_json=json_remove(json_set(assessment_json,'\$.policy_version','0.0-e2e'),'\$.next_step','\$.merit')"
                  RESCORE_DELAY=8 start_server; (cd "$E2E" && node e2e_startup_board.js) || FAILED=1
                  (cd "$E2E" && "$PY" e2e_startup.py "$SB/out/results-startup.json") || FAILED=1 ;;
    published)    start_server
                  wait_startup_rescore   # publish what the board will show, not a half-re-derived state
                  curl -sf -X POST -H "Origin: http://127.0.0.1:$PORT" "http://127.0.0.1:$PORT/api/publish" >"$SB/out/publish.json" || { echo "publish failed"; cat "$SB/out/publish.json"; FAILED=1; }
                  rm -rf "$SB/site"; mkdir -p "$SB/site"
                  git --git-dir="$SB/origin.git" archive gh-pages | tar -x -C "$SB/site"
                  (cd "$SB/site" && exec "$PY" -m http.server 8767 --bind 127.0.0.1 >/dev/null 2>&1) & PIDS+=("$!")
                  until curl -sf http://127.0.0.1:8767/ >/dev/null; do sleep 0.3; done
                  (cd "$E2E" && node e2e_published.js) || FAILED=1 ;;
    *) echo "unknown suite $suite" >&2; FAILED=1; continue ;;
  esac
  stop_server
done

for f in "$SB"/out/results-*.json; do
  [ -f "$f" ] && "$PY" -c "import json,sys; r=json.load(open(sys.argv[1])); bad=[k for k,v in r.items() if not v['ok']]; print(sys.argv[1].rsplit('/',1)[1], len(r)-len(bad), '/', len(r), 'passed', ('FAILED: ' + '; '.join(bad)) if bad else '')" "$f"
  grep -q '"ok": false' "$f" && FAILED=1
done
echo "artifacts: $SB/out"
exit $FAILED
