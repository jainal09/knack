#!/usr/bin/env bash
# metrics_collector.sh — Background docker stats poller → CSV
# Usage: ./scripts/metrics_collector.sh &   (run in background, kill when done)
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$PROJECT_ROOT/results"

OUTFILE="$PROJECT_ROOT/results/docker_stats.csv"

# Write header if file doesn't exist
if [[ ! -f "$OUTFILE" ]]; then
  echo "timestamp,container,cpu_pct,mem_usage,mem_limit,mem_pct,net_io,block_io,pids" > "$OUTFILE"
fi

echo "Metrics collector started (PID $$). Writing to $OUTFILE"
echo "Kill with: kill $$"

while true; do
  TS=$(date +%s)
  docker stats --no-stream --format "{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}},{{.NetIO}},{{.BlockIO}},{{.PIDs}}" 2>/dev/null \
    | while IFS= read -r line; do
        echo "${TS},${line}" >> "$OUTFILE"
      done
  sleep 5
done
