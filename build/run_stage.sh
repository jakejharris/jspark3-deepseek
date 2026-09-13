#!/usr/bin/env bash
set -euo pipefail
stage="$1"
touch "/receipts/stage${stage}.build.log"
timeout 7200 python3 /tempo/build/stage.py --stage "$stage" --pins /input/pins.json &
stage_pid=$!
tail --pid="$stage_pid" -n +1 -F "/receipts/stage${stage}.build.log" &
tail_pid=$!
result=0
wait "$stage_pid" || result=$?
wait "$tail_pid" || true
exit "$result"
