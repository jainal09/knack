#!/usr/bin/env bash
# bench_startup.sh — AC-4: Measure startup time and SIGKILL recovery time
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$PROJECT_ROOT/env.sh"
source "$PROJECT_ROOT/scripts/_log.sh"
RESULTS_DIR="${RESULTS_DIR:-$PROJECT_ROOT/results}"
mkdir -p "$RESULTS_DIR"

time_to_ready() {
  local name="$1" compose="$2" port="$3"
  log "========================================"
  log "  Startup & Recovery: $name"
  log "========================================"

  # --- Clean startup ---
  docker compose -f "$compose" down -v 2>/dev/null || true

  local start end ms
  start=$(date +%s%N)
  docker compose -f "$compose" up -d

  # Poll until port accepts TCP connection
  local timeout_count=0
  until nc -z localhost "$port" 2>/dev/null; do
    sleep 0.1
    timeout_count=$((timeout_count + 1))
    if [[ $timeout_count -gt 600 ]]; then
      echo "ERROR: $name did not become ready within 60s" >&2
      docker compose -f "$compose" logs
      docker compose -f "$compose" down -v
      return 1
    fi
  done
  end=$(date +%s%N)

  ms=$(( (end - start) / 1000000 ))
  log "Startup: ${ms} ms"
  echo "{\"broker\":\"$name\",\"type\":\"startup\",\"ms\":$ms}" | tee "$RESULTS_DIR/${name}_startup.json"

  # --- Recovery after SIGKILL ---
  echo ""
  log "Hard-killing $name (SIGKILL)..."
  docker compose -f "$compose" kill -s SIGKILL
  sleep 2

  # Wait for port to close before starting timer
  local close_count=0
  while nc -z localhost "$port" 2>/dev/null; do
    sleep 0.1
    close_count=$((close_count + 1))
    if [[ $close_count -gt 100 ]]; then
      break
    fi
  done

  start=$(date +%s%N)
  docker compose -f "$compose" up -d

  timeout_count=0
  until nc -z localhost "$port" 2>/dev/null; do
    sleep 0.1
    timeout_count=$((timeout_count + 1))
    if [[ $timeout_count -gt 600 ]]; then
      echo "ERROR: $name did not recover within 60s" >&2
      docker compose -f "$compose" logs
      docker compose -f "$compose" down -v
      return 1
    fi
  done
  end=$(date +%s%N)

  ms=$(( (end - start) / 1000000 ))
  log "Recovery: ${ms} ms"
  echo "{\"broker\":\"$name\",\"type\":\"recovery\",\"ms\":$ms}" >> "$RESULTS_DIR/${name}_startup.json"

  docker compose -f "$compose" down -v
  echo ""
}

time_to_ready "kafka" "$PROJECT_ROOT/infra/docker-compose.kafka.yml" 9092
time_to_ready "nats"  "$PROJECT_ROOT/infra/docker-compose.nats.yml"  4222

log "=== Startup/recovery benchmark complete. Results in results/*_startup.json ==="
