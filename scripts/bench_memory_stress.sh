#!/usr/bin/env bash
# bench_memory_stress.sh — AC-7: Find minimum viable RAM for each broker
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$PROJECT_ROOT/env.sh"
source "$PROJECT_ROOT/scripts/_log.sh"
RESULTS_DIR="${RESULTS_DIR:-$PROJECT_ROOT/results}"
mkdir -p "$RESULTS_DIR"

# Shorter duration for memory stress (2 min to save time)
export TEST_DURATION_SEC=120

# Memory tiers: use budget-computed levels if available, else fallback
if [[ -n "${MEMORY_STRESS_LEVELS:-}" ]]; then
  read -ra MEMORY_LEVELS <<< "$MEMORY_STRESS_LEVELS"
else
  MEMORY_LEVELS=("4g" "2g" "1g" "512m")
fi

# Helper: parse memory string to MB for scaling calculations
_mem_to_mb() {
  local mem="$1"
  if [[ "$mem" == *g ]]; then
    echo $(( ${mem%g} * 1024 ))
  elif [[ "$mem" == *m ]]; then
    echo "${mem%m}"
  else
    echo "0"
  fi
}

for MEM in "${MEMORY_LEVELS[@]}"; do
  export BENCH_MEMORY="$MEM"

  # Scale client-side params for this memory tier
  local_mb=$(_mem_to_mb "$MEM")

  # NATS server max_mem: 50% of broker RAM, min 64MB
  nats_mem_mb=$(( local_mb / 2 ))
  [[ $nats_mem_mb -lt 64 ]] && nats_mem_mb=64
  export NATS_MAX_MEM="${nats_mem_mb}MB"

  # Reduce producers for low-memory tiers to avoid overwhelming
  stress_producers="${NUM_PRODUCERS:-4}"
  if [[ $local_mb -le 512 ]]; then
    stress_producers=2
    export KAFKA_QUEUE_MAX=10000
  elif [[ $local_mb -le 1024 ]]; then
    stress_producers=4
    export KAFKA_QUEUE_MAX=25000
  fi
  export NUM_PRODUCERS="$stress_producers"
  export NUM_CONSUMERS="$stress_producers"

  for BROKER_NAME in kafka nats; do
    log "========================================"
    log "  Memory stress: $BROKER_NAME @ $MEM"
    log "  Workers: $stress_producers | NATS max_mem: $NATS_MAX_MEM"
    log "========================================"

    COMPOSE="$PROJECT_ROOT/infra/docker-compose.${BROKER_NAME}.yml"
    PRODUCER="$PROJECT_ROOT/bench/producer_${BROKER_NAME}.py"

    # Teardown any leftovers
    docker compose -f "$COMPOSE" down -v 2>/dev/null || true

    # Try to start
    if ! docker compose -f "$COMPOSE" up -d 2>&1; then
      echo "{\"broker\":\"$BROKER_NAME\",\"memory\":\"$MEM\",\"status\":\"FAIL_START\",\"error\":\"container failed to start\"}" \
        | tee "$RESULTS_DIR/${BROKER_NAME}_mem_${MEM}.json"
      docker compose -f "$COMPOSE" down -v 2>/dev/null || true
      continue
    fi

    wait_healthy "bench-${BROKER_NAME}" 60

    # Check if container is still running
    if ! docker compose -f "$COMPOSE" ps --status running | grep -q "bench-${BROKER_NAME}"; then
      echo "{\"broker\":\"$BROKER_NAME\",\"memory\":\"$MEM\",\"status\":\"FAIL_OOM\",\"error\":\"container died (likely OOM)\"}" \
        | tee "$RESULTS_DIR/${BROKER_NAME}_mem_${MEM}.json"
      docker compose -f "$COMPOSE" down -v 2>/dev/null || true
      continue
    fi

    # Run producer — stdout is pure JSON, stderr has status messages
    if uv run python3 "$PRODUCER" > "$RESULTS_DIR/${BROKER_NAME}_mem_${MEM}.json"; then
      log "PASS: $BROKER_NAME sustained workload at $MEM"
      # Add status field to the JSON
      uv run python3 -c "
import json
with open('$RESULTS_DIR/${BROKER_NAME}_mem_${MEM}.json') as f:
    data = json.load(f)
data['memory'] = '$MEM'
data['status'] = 'PASS'
with open('$RESULTS_DIR/${BROKER_NAME}_mem_${MEM}.json', 'w') as f:
    json.dump(data, f, indent=2)
"
    else
      echo "{\"broker\":\"$BROKER_NAME\",\"memory\":\"$MEM\",\"status\":\"FAIL_WORKLOAD\",\"error\":\"producer failed under load\"}" \
        | tee "$RESULTS_DIR/${BROKER_NAME}_mem_${MEM}.json"
    fi

    docker compose -f "$COMPOSE" down -v
    sleep 5
  done
done

log "=== Memory stress benchmark complete. Results in results/*_mem_*.json ==="

# Summary
log ""
log "=== Summary ==="
for BROKER_NAME in kafka nats; do
  log "--- $BROKER_NAME ---"
  for MEM in "${MEMORY_LEVELS[@]}"; do
    if [[ -f "$RESULTS_DIR/${BROKER_NAME}_mem_${MEM}.json" ]]; then
      STATUS=$(uv run python3 -c "import json; print(json.load(open('$RESULTS_DIR/${BROKER_NAME}_mem_${MEM}.json')).get('status','UNKNOWN'))" 2>/dev/null || echo "PARSE_ERROR")
      log "  $MEM: $STATUS"
    else
      log "  $MEM: NOT_RUN"
    fi
  done
done
