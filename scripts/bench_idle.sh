#!/usr/bin/env bash
# bench_idle.sh — AC-3: Measure idle resource footprint after 5 min with no connections
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$PROJECT_ROOT/env.sh"
source "$PROJECT_ROOT/scripts/_log.sh"
RESULTS_DIR="${RESULTS_DIR:-$PROJECT_ROOT/results}"
mkdir -p "$RESULTS_DIR"

IDLE_WAIT="${IDLE_WAIT:-150}"  # 2.5 minutes default, overridable via env

measure_idle() {
  local name="$1" compose="$2"
  log "========================================"
  log "  Idle footprint: $name"
  log "========================================"

  # Clean start
  docker compose -f "$compose" down -v 2>/dev/null || true
  docker compose -f "$compose" up -d
  wait_healthy "bench-${name}"
  wait_with_progress "$IDLE_WAIT" "Idle stabilization ($name)"

  # Capture docker stats snapshot (JSON)
  log "--- Docker stats snapshot ---"
  docker stats --no-stream --format \
    '{"container":"{{.Name}}","cpu_pct":"{{.CPUPerc}}","mem_usage":"{{.MemUsage}}","mem_pct":"{{.MemPerc}}","net_io":"{{.NetIO}}","block_io":"{{.BlockIO}}"}' \
    | grep "bench-${name}" | tee "$RESULTS_DIR/${name}_idle_stats.json"

  # Capture RSS via /proc inside the container (display only, not in JSON)
  log "--- RSS (from /proc) ---"
  local container="bench-${name}"
  local pid
  pid=$(docker inspect --format '{{.State.Pid}}' "$container")
  if [[ -f "/proc/$pid/status" ]]; then
    grep -i vmrss "/proc/$pid/status"
  fi

  # Disk usage of data directory (display only, not in JSON)
  log "--- Disk metadata ---"
  if [[ "$name" == "kafka" ]]; then
    docker exec "$container" du -sh /var/lib/kafka/data 2>/dev/null || true
  else
    docker exec "$container" du -sh /data 2>/dev/null || true
  fi

  docker compose -f "$compose" down
  echo ""
}

measure_idle "kafka" "$PROJECT_ROOT/infra/docker-compose.kafka.yml"
measure_idle "nats"  "$PROJECT_ROOT/infra/docker-compose.nats.yml"

log "=== Idle benchmark complete. Results in results/*_idle_stats.json ==="
