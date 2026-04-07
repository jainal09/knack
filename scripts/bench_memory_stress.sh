#!/usr/bin/env bash
# bench_memory_stress.sh — AC-7: Find minimum viable RAM for each broker
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$PROJECT_ROOT/env.sh"
mkdir -p "$PROJECT_ROOT/results"

# Shorter duration for memory stress (2 min to save time)
export TEST_DURATION_SEC=120

MEMORY_LEVELS=("4g" "2g" "1g" "512m")

for MEM in "${MEMORY_LEVELS[@]}"; do
  export BENCH_MEMORY="$MEM"

  for BROKER_NAME in kafka nats; do
    echo "========================================"
    echo "  Memory stress: $BROKER_NAME @ $MEM"
    echo "========================================"

    COMPOSE="$PROJECT_ROOT/infra/docker-compose.${BROKER_NAME}.yml"
    PRODUCER="$PROJECT_ROOT/bench/producer_${BROKER_NAME}.py"

    # Teardown any leftovers
    docker compose -f "$COMPOSE" down -v 2>/dev/null || true

    # Try to start
    if ! docker compose -f "$COMPOSE" up -d 2>&1; then
      echo "{\"broker\":\"$BROKER_NAME\",\"memory\":\"$MEM\",\"status\":\"FAIL_START\",\"error\":\"container failed to start\"}" \
        | tee "$PROJECT_ROOT/results/${BROKER_NAME}_mem_${MEM}.json"
      docker compose -f "$COMPOSE" down -v 2>/dev/null || true
      continue
    fi

    sleep 15  # stabilize

    # Check if container is still running
    if ! docker compose -f "$COMPOSE" ps --status running | grep -q "bench-${BROKER_NAME}"; then
      echo "{\"broker\":\"$BROKER_NAME\",\"memory\":\"$MEM\",\"status\":\"FAIL_OOM\",\"error\":\"container died (likely OOM)\"}" \
        | tee "$PROJECT_ROOT/results/${BROKER_NAME}_mem_${MEM}.json"
      docker compose -f "$COMPOSE" down -v 2>/dev/null || true
      continue
    fi

    # Run producer
    if uv run python3 "$PRODUCER" > "$PROJECT_ROOT/results/${BROKER_NAME}_mem_${MEM}.json" 2>&1; then
      echo "PASS: $BROKER_NAME sustained workload at $MEM"
      # Add status field
      uv run python3 -c "
import json
with open('$PROJECT_ROOT/results/${BROKER_NAME}_mem_${MEM}.json') as f:
    data = json.load(f)
data['memory'] = '$MEM'
data['status'] = 'PASS'
with open('$PROJECT_ROOT/results/${BROKER_NAME}_mem_${MEM}.json', 'w') as f:
    json.dump(data, f, indent=2)
"
    else
      echo "{\"broker\":\"$BROKER_NAME\",\"memory\":\"$MEM\",\"status\":\"FAIL_WORKLOAD\",\"error\":\"producer failed under load\"}" \
        | tee "$PROJECT_ROOT/results/${BROKER_NAME}_mem_${MEM}.json"
    fi

    docker compose -f "$COMPOSE" down -v
    sleep 5
  done
done

echo "=== Memory stress benchmark complete. Results in results/*_mem_*.json ==="

# Summary
echo ""
echo "=== Summary ==="
for BROKER_NAME in kafka nats; do
  echo "--- $BROKER_NAME ---"
  for MEM in "${MEMORY_LEVELS[@]}"; do
    if [[ -f "$PROJECT_ROOT/results/${BROKER_NAME}_mem_${MEM}.json" ]]; then
      STATUS=$(python3 -c "import json; print(json.load(open('$PROJECT_ROOT/results/${BROKER_NAME}_mem_${MEM}.json')).get('status','UNKNOWN'))" 2>/dev/null || echo "PARSE_ERROR")
      echo "  $MEM: $STATUS"
    else
      echo "  $MEM: NOT_RUN"
    fi
  done
done
