#!/usr/bin/env bash
# bench_consumer.sh — Consumer throughput benchmark (REPS reps × 2 brokers)
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

run_consumer() {
  local name="$1" compose="$2" consumer="$3"
  log "========================================"
  log "  Consumer Throughput: $name (${REPS} reps)"
  log "========================================"

  docker compose -f "$compose" down -v 2>/dev/null || true
  docker compose -f "$compose" up -d
  wait_healthy "bench-${name}"

  for rep in $(seq 1 "$REPS"); do
    log "--- Run $rep / $REPS ---"
    # Delete topic/stream between reps to reset state
    if [[ "$rep" -gt 1 ]]; then
      if [[ "$name" == "kafka" ]]; then
        docker exec bench-kafka kafka-topics --bootstrap-server localhost:9092 \
          --delete --topic bench-consumer 2>/dev/null || true
        sleep 2
      elif [[ "$name" == "nats" ]]; then
        uv run python3 -c "
import asyncio, nats
async def delete_stream():
    nc = await nats.connect('nats://localhost:4222')
    js = nc.jetstream()
    await js.delete_stream('BENCH_CONSUMER')
    print('Deleted stream BENCH_CONSUMER')
    await nc.close()
asyncio.run(delete_stream())
" || true
        sleep 2
      fi
    fi
    uv run python3 "$consumer" | tee "$RESULTS_DIR/${name}_consumer_run${rep}.json"
    echo ""
    wait_with_progress 5 "Cooldown between reps"
  done

  docker compose -f "$compose" down -v
  echo ""
}

run_consumer "kafka" "$PROJECT_ROOT/infra/docker-compose.kafka.yml" "$PROJECT_ROOT/bench/consumer_kafka.py"
run_consumer "nats"  "$PROJECT_ROOT/infra/docker-compose.nats.yml"  "$PROJECT_ROOT/bench/consumer_nats.py"

log "=== Consumer throughput benchmark complete. Results in results/*_consumer_run*.json ==="
