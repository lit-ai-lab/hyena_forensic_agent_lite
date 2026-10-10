#!/bin/bash
# Usage (from the repo root): bash exp/run.sh <model> <name> [reps=5] [jobs=4]
#   e.g. bash exp/run.sh openai/gpt-oss-20b high
# Output: runs/<model>/<name>/q<task>/r<rep>.traj.json and .log. Finished runs are skipped when run again.
set -u
export MODEL=$1 OUT="runs/${1//\//_}/$2"
REPS=${3:-5} JOBS=${4:-4}

run_one() {
    local q=$1 r=$2 f="$OUT/q$1/r$2.traj.json"
    mkdir -p "$OUT/q$q"
    if python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1]))["info"].get("exit_status") else 1)' "$f" 2>/dev/null; then
        echo "skip  q$q r$r"
        return
    fi
    echo "start q$q r$r  $(date +%T)"
    mini -c exp/case.yaml -m "hosted_vllm/$MODEL" -t "$(tr -d '\r' < "exp/tasks/q$q.md")" -o "$f" > "${f%.traj.json}.log" 2>&1
    echo "end   q$q r$r  $(date +%T)"
}
export -f run_one

for r in $(seq "$REPS"); do for q in $(seq 8); do echo "$q $r"; done; done |
    xargs -P "$JOBS" -n 2 bash -c 'run_one "$@"' _
