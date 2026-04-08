#!/usr/bin/env bash
# bench_prodcon.sh — Simultaneous producer+consumer load test (1 run × 2 brokers)
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$PROJECT_ROOT/env.sh"
source "$PROJECT_ROOT/scripts/_log.sh"
RESULTS_DIR="${RESULTS_DIR:-$PROJECT_ROOT/results}"
mkdir -p "$RESULTS_DIR"

# Start background metrics collection
"$PROJECT_ROOT/scripts/metrics_collector.sh" &
METRICS_PID=$!
trap "kill $METRICS_PID 2>/dev/null || true" EXIT

run_prodcon() {
  local name="$1" compose="$2" script="$3"
  log "========================================"
  log "  Simultaneous Producer+Consumer: $name (${TEST_DURATION_SEC}s)"
  log "  Producers: $NUM_PRODUCERS | Consumers: $NUM_CONSUMERS"
  log "========================================"

  docker compose -f "$compose" down -v 2>/dev/null || true
  docker compose -f "$compose" up -d
  wait_healthy "bench-${name}"

  uv run python3 "$script" | tee "$RESULTS_DIR/${name}_prodcon.json"
  echo ""

  docker compose -f "$compose" down -v
  echo ""
}

# TEMPORARILY SKIPPED — Kafka prodcon was fine, only NATS needed the multiprocessing fix
# run_prodcon "kafka" "$PROJECT_ROOT/infra/docker-compose.kafka.yml" "$PROJECT_ROOT/bench/prodcon_kafka.py"
run_prodcon "nats"  "$PROJECT_ROOT/infra/docker-compose.nats.yml"  "$PROJECT_ROOT/bench/prodcon_nats.py"

log "=== Simultaneous producer+consumer benchmark complete. Results in results/*_prodcon.json ==="
