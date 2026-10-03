#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

python3 main_process.py &
main_pid=$!

worker_pids=()
for id in 1 2 3; do
    python3 other_process.py "$id" &
    worker_pids+=("$!")
done

trap 'kill "$main_pid" "${worker_pids[@]}" 2>/dev/null || true' EXIT
wait "$main_pid"
for pid in "${worker_pids[@]}"; do
    wait "$pid"
done
trap - EXIT
