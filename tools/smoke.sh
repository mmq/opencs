#!/usr/bin/env bash
# The one command to run after deploying: start OpenCS on a free port with a throwaway data
# directory, run the judge (all 19 checks, two scripted pilots through the Chamber and a game),
# stop the server. Exit 0 only if all 19 pass. Takes about a minute (real time).
#
#   tools/smoke.sh            # start a server, judge it, stop it
#   tools/smoke.sh 2399       # judge an already running server on that port instead
#                             # (it logs two test pilots in; use a staging server, not a busy one)
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -x .venv/bin/python ]; then
    run=(.venv/bin/python)
elif command -v uv >/dev/null 2>&1; then
    run=(uv run python)
else
    run=("${PYTHON:-python3}")
fi

"${run[@]}" -m opencs --version

if [ $# -ge 1 ]; then
    target=(--port "$1")
else
    target=(--server)
fi

out=$(mktemp)
trap 'rm -f "$out"' EXIT
status=0
"${run[@]}" tools/judge.py "${target[@]}" --stage 2 --checks 1-19 | tee "$out" || status=$?

passed=$(grep -c '^ok ' "$out" || true)
if [ "$status" -ne 0 ] || [ "$passed" -ne 19 ]; then
    echo "smoke: FAILED ($passed of 19 checks passed)" >&2
    exit 1
fi
echo "smoke: ok (19 of 19 checks passed)"
