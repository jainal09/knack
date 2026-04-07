#!/usr/bin/env bash
# bench_idle.sh — AC-3: Measure idle resource footprint after 5 min with no connections
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$PROJECT_ROOT/env.sh"
mkdir -p "$PROJECT_ROOT/results"

IDLE_WAIT="${IDLE_WAIT:-300}"  # 5 minutes default, overridable via env

measure_idle() {
  local name="$1" compose="$2"
  echo "========================================"
  echo "  Idle footprint: $name"
  echo "========================================"

  # Clean start
  docker compose -f "$compose" down -v 2>/dev/null || true
  docker compose -f "$compose" up -d
  echo "Waiting ${IDLE_WAIT}s for idle stabilization (no connections)..."
  sleep "$IDLE_WAIT"

  # Capture docker stats snapshot
  echo "--- Docker stats snapshot ---"
  docker stats --no-stream --format \
    '{"container":"{{.Name}}","cpu_pct":"{{.CPUPerc}}","mem_usage":"{{.MemUsage}}","mem_pct":"{{.MemPerc}}","net_io":"{{.NetIO}}","block_io":"{{.BlockIO}}"}' \
    | grep "bench-${name}" | tee "$PROJECT_ROOT/results/${name}_idle_stats.json"

  # Capture RSS via /proc inside the container
  echo "--- RSS (from /proc) ---"
  local container="bench-${name}"
  local pid
  pid=$(docker inspect --format '{{.State.Pid}}' "$container")
  if [[ -f "/proc/$pid/status" ]]; then
    grep -i vmrss "/proc/$pid/status" | tee -a "$PROJECT_ROOT/results/${name}_idle_stats.json"
  fi

  # Disk usage of data directory
  echo "--- Disk metadata ---"
  if [[ "$name" == "kafka" ]]; then
    docker exec "$container" du -sh /var/lib/kafka/data 2>/dev/null | tee -a "$PROJECT_ROOT/results/${name}_idle_stats.json"
  else
    docker exec "$container" du -sh /data 2>/dev/null | tee -a "$PROJECT_ROOT/results/${name}_idle_stats.json"
  fi

  docker compose -f "$compose" down
  echo ""
}

measure_idle "kafka" "$PROJECT_ROOT/infra/docker-compose.kafka.yml"
measure_idle "nats"  "$PROJECT_ROOT/infra/docker-compose.nats.yml"

echo "=== Idle benchmark complete. Results in results/*_idle_stats.json ==="
