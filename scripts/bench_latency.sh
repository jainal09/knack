#!/usr/bin/env bash
# bench_latency.sh — AC-6: Latency under load at 50% of peak throughput
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$PROJECT_ROOT/env.sh"
mkdir -p "$PROJECT_ROOT/results"

# Set PEAK_RATE from throughput results if available, otherwise use a default
if [[ -f "$PROJECT_ROOT/results/kafka_throughput_run1.json" ]]; then
  KAFKA_PEAK=$(uv run python3 -c "import json; print(int(json.load(open('$PROJECT_ROOT/results/kafka_throughput_run1.json'))['aggregate_rate']))")
else
  KAFKA_PEAK=${PEAK_RATE:-10000}
fi

if [[ -f "$PROJECT_ROOT/results/nats_throughput_run1.json" ]]; then
  NATS_PEAK=$(uv run python3 -c "import json; print(int(json.load(open('$PROJECT_ROOT/results/nats_throughput_run1.json'))['aggregate_rate']))")
else
  NATS_PEAK=${PEAK_RATE:-10000}
fi

echo "========================================"
echo "  Latency: Kafka (50% of peak ${KAFKA_PEAK})"
echo "========================================"
docker compose -f "$PROJECT_ROOT/infra/docker-compose.kafka.yml" down -v 2>/dev/null || true
docker compose -f "$PROJECT_ROOT/infra/docker-compose.kafka.yml" up -d
sleep 15
PEAK_RATE=$KAFKA_PEAK uv run python3 "$PROJECT_ROOT/bench/latency_kafka.py"
docker compose -f "$PROJECT_ROOT/infra/docker-compose.kafka.yml" down -v

echo ""
echo "========================================"
echo "  Latency: NATS (50% of peak ${NATS_PEAK})"
echo "========================================"
docker compose -f "$PROJECT_ROOT/infra/docker-compose.nats.yml" down -v 2>/dev/null || true
docker compose -f "$PROJECT_ROOT/infra/docker-compose.nats.yml" up -d
sleep 15
PEAK_RATE=$NATS_PEAK uv run python3 "$PROJECT_ROOT/bench/latency_nats.py"
docker compose -f "$PROJECT_ROOT/infra/docker-compose.nats.yml" down -v

echo "=== Latency benchmark complete. Results in results/*_latency.json ==="
