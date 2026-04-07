#!/usr/bin/env bash
# bench_throughput.sh — AC-5: Baseline throughput (3 reps × 2 brokers)
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

run_throughput() {
  local name="$1" compose="$2" producer="$3"
  log "========================================"
  log "  Throughput: $name (${REPS} reps × ${TEST_DURATION_SEC}s)"
  log "========================================"

  docker compose -f "$compose" down -v 2>/dev/null || true
  docker compose -f "$compose" up -d
  wait_healthy "bench-${name}"

  for rep in $(seq 1 "$REPS"); do
    log "--- Run $rep / $REPS ---"
    # Delete NATS stream between reps to fully reclaim storage
    if [[ "$name" == "nats" && "$rep" -gt 1 ]]; then
      uv run python3 -c "
import asyncio, nats
async def delete_stream():
    nc = await nats.connect('nats://localhost:4222')
    js = nc.jetstream()
    await js.delete_stream('BENCH')
    print('Deleted stream BENCH')
    await nc.close()
asyncio.run(delete_stream())
" || true
      sleep 2
    fi
    uv run python3 "$producer" | tee "$RESULTS_DIR/${name}_throughput_run${rep}.json"
    echo ""
    wait_with_progress 5 "Cooldown between reps"
  done

  docker compose -f "$compose" down -v
  echo ""
}

run_throughput "kafka" "$PROJECT_ROOT/infra/docker-compose.kafka.yml" "$PROJECT_ROOT/bench/producer_kafka.py"
run_throughput "nats"  "$PROJECT_ROOT/infra/docker-compose.nats.yml"  "$PROJECT_ROOT/bench/producer_nats.py"

log "=== Throughput benchmark complete. Results in results/*_throughput_run*.json ==="
