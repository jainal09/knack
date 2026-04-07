#!/usr/bin/env bash
# bench_latency.sh — AC-6: Latency under load at 50% of peak throughput
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$PROJECT_ROOT/env.sh"
source "$PROJECT_ROOT/scripts/_log.sh"
RESULTS_DIR="${RESULTS_DIR:-$PROJECT_ROOT/results}"
mkdir -p "$RESULTS_DIR"

# Set PEAK_RATE from throughput results if available, otherwise use a default
if [[ -f "$RESULTS_DIR/kafka_throughput_run1.json" ]]; then
  KAFKA_PEAK=$(uv run python3 -c "import json; print(int(json.load(open('$RESULTS_DIR/kafka_throughput_run1.json'))['aggregate_rate']))")
else
  KAFKA_PEAK=${PEAK_RATE:-10000}
fi

if [[ -f "$RESULTS_DIR/nats_throughput_run1.json" ]]; then
  NATS_PEAK=$(uv run python3 -c "import json; print(int(json.load(open('$RESULTS_DIR/nats_throughput_run1.json'))['aggregate_rate']))")
else
  NATS_PEAK=${PEAK_RATE:-10000}
fi

log "========================================"
log "  Latency: Kafka (50% of peak ${KAFKA_PEAK})"
log "========================================"
docker compose -f "$PROJECT_ROOT/infra/docker-compose.kafka.yml" down -v 2>/dev/null || true
docker compose -f "$PROJECT_ROOT/infra/docker-compose.kafka.yml" up -d
wait_healthy "bench-kafka"
PEAK_RATE=$KAFKA_PEAK uv run python3 "$PROJECT_ROOT/bench/latency_kafka.py" | tee "$RESULTS_DIR/kafka_latency.json"
docker compose -f "$PROJECT_ROOT/infra/docker-compose.kafka.yml" down -v

log ""
log "========================================"
log "  Latency: NATS (50% of peak ${NATS_PEAK})"
log "========================================"
docker compose -f "$PROJECT_ROOT/infra/docker-compose.nats.yml" down -v 2>/dev/null || true
docker compose -f "$PROJECT_ROOT/infra/docker-compose.nats.yml" up -d
wait_healthy "bench-nats"
PEAK_RATE=$NATS_PEAK uv run python3 "$PROJECT_ROOT/bench/latency_nats.py" | tee "$RESULTS_DIR/nats_latency.json"
docker compose -f "$PROJECT_ROOT/infra/docker-compose.nats.yml" down -v

log "=== Latency benchmark complete. Results in results/*_latency.json ==="
