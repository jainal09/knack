#!/usr/bin/env bash
# bench_throughput.sh — AC-5: Baseline throughput (3 reps × 2 brokers)
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$PROJECT_ROOT/env.sh"
mkdir -p "$PROJECT_ROOT/results"

# Start background metrics collection
"$PROJECT_ROOT/scripts/metrics_collector.sh" &
METRICS_PID=$!
trap "kill $METRICS_PID 2>/dev/null || true" EXIT

run_throughput() {
  local name="$1" compose="$2" producer="$3"
  echo "========================================"
  echo "  Throughput: $name (${REPS} reps × ${TEST_DURATION_SEC}s)"
  echo "========================================"

  docker compose -f "$compose" down -v 2>/dev/null || true
  docker compose -f "$compose" up -d
  echo "Waiting 15s for broker to stabilize..."
  sleep 15

  for rep in $(seq 1 "$REPS"); do
    echo "--- Run $rep / $REPS ---"
    uv run python3 "$producer" | tee "$PROJECT_ROOT/results/${name}_throughput_run${rep}.json"
    echo ""
    sleep 5
  done

  docker compose -f "$compose" down -v
  echo ""
}

run_throughput "kafka" "$PROJECT_ROOT/infra/docker-compose.kafka.yml" "$PROJECT_ROOT/bench/producer_kafka.py"
run_throughput "nats"  "$PROJECT_ROOT/infra/docker-compose.nats.yml"  "$PROJECT_ROOT/bench/producer_nats.py"

echo "=== Throughput benchmark complete. Results in results/*_throughput_run*.json ==="
