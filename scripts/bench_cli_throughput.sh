#!/usr/bin/env bash
# bench_cli_throughput.sh — CLI-native throughput test using official tools
#   Kafka: kcat (librdkafka-based, runs on host — no container overhead)
#   NATS:  nats bench (host-installed CLI)
#
# Both tools run on the host so they don't compete with broker containers for resources.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$PROJECT_ROOT/env.sh"
source "$PROJECT_ROOT/scripts/_log.sh"
RESULTS_DIR="${RESULTS_DIR:-$PROJECT_ROOT/results}"
mkdir -p "$RESULTS_DIR"

TOTAL_MESSAGES="${CLI_TOTAL_MESSAGES:-50000}"

# Resolve kcat binary (newer distros ship "kcat", older ones ship "kafkacat")
KCAT=""
if command -v kcat &>/dev/null; then
  KCAT="kcat"
elif command -v kafkacat &>/dev/null; then
  KCAT="kafkacat"
fi
# ─── Kafka ────────────────────────────────────────────────────────────────────
run_kafka_cli() {
  log "========================================"
  log "  CLI Throughput: Kafka (kcat on host)"
  log "  Messages: $TOTAL_MESSAGES × ${PAYLOAD_BYTES}B"
  log "========================================"

  docker compose -f "$PROJECT_ROOT/infra/docker-compose.kafka.yml" down -v 2>/dev/null || true
  docker compose -f "$PROJECT_ROOT/infra/docker-compose.kafka.yml" up -d
  wait_healthy "bench-kafka"

  # Create topic via broker container (admin command, no load)
  docker exec bench-kafka kafka-topics --bootstrap-server localhost:9092 \
    --create --topic bench-cli --partitions 1 --replication-factor 1 \
    --config min.insync.replicas=1 2>/dev/null || true

  # Generate payload line (base64-encoded random bytes, trimmed to PAYLOAD_BYTES chars)
  local payload
  payload=$(head -c "$PAYLOAD_BYTES" /dev/urandom | base64 | tr -d '\n' | head -c "$PAYLOAD_BYTES")

  log "Producing $TOTAL_MESSAGES messages with kcat (acks=all)..."
  local start_ns end_ns elapsed_ms
  start_ns=$(date +%s%N)

  # Pipe N identical messages into kcat — each line = 1 message
  # Use timeout to prevent hangs; 300s should be plenty for 50K msgs
  yes "$payload" | head -n "$TOTAL_MESSAGES" | \
    timeout 300 "$KCAT" -P -b localhost:9092 -t bench-cli \
      -X acks=all \
      -X queue.buffering.max.messages=100000 \
      -X queue.buffering.max.kbytes=524288 \
      -X batch.num.messages=10000 \
      -X linger.ms=5

  end_ns=$(date +%s%N)
  elapsed_ms=$(( (end_ns - start_ns) / 1000000 ))
  local elapsed_sec records_sec
  elapsed_sec=$(uv run python3 -c "print(f'{$elapsed_ms / 1000:.3f}')")
  records_sec=$(uv run python3 -c "print(f'{$TOTAL_MESSAGES / ($elapsed_ms / 1000):.1f}')")

  log "Done in ${elapsed_sec}s"

  cat > "$RESULTS_DIR/kafka_cli_throughput.json" <<EOF
{
  "broker": "kafka",
  "tool": "kcat",
  "total_messages": $TOTAL_MESSAGES,
  "payload_bytes": $PAYLOAD_BYTES,
  "elapsed_sec": $elapsed_sec,
  "msgs_per_sec": $records_sec,
  "acks": "all"
}
EOF
  echo ""
  log "Result: $records_sec records/sec (${elapsed_sec}s for $TOTAL_MESSAGES msgs)"
  cat "$RESULTS_DIR/kafka_cli_throughput.json"

  docker compose -f "$PROJECT_ROOT/infra/docker-compose.kafka.yml" down -v
  echo ""
}

# ─── NATS ─────────────────────────────────────────────────────────────────────
run_nats_cli() {
  log "========================================"
  log "  CLI Throughput: NATS (nats bench)"
  log "  Messages: $TOTAL_MESSAGES × ${PAYLOAD_BYTES}B"
  log "========================================"

  docker compose -f "$PROJECT_ROOT/infra/docker-compose.nats.yml" down -v 2>/dev/null || true
  docker compose -f "$PROJECT_ROOT/infra/docker-compose.nats.yml" up -d
  wait_healthy "bench-nats"

  # nats bench with JetStream async publish, file storage, N publishers
  log "Running nats bench js pub async..."
  local raw
  raw=$(nats bench js pub async bench.cli \
    --server="nats://localhost:4222" \
    --create --storage=file --purge \
    --stream="BENCH-CLI" \
    --maxbytes="10GB" \
    --clients "$NUM_PRODUCERS" \
    --size "$PAYLOAD_BYTES" \
    --msgs "$TOTAL_MESSAGES" \
    --no-progress 2>&1) || true  # nats bench may exit non-zero

  echo "$raw"

  # Parse aggregated stats line: "... aggregated stats: 54,148 msgs/sec ~ 53 MiB/sec"
  local msgs_sec
  msgs_sec=$(echo "$raw" | grep -i "aggregated stats" | grep -oE '[0-9,]+ msgs/sec' | tr -d ',' | cut -d' ' -f1)
  local mb_sec
  mb_sec=$(echo "$raw" | grep -i "aggregated stats" | grep -oE '[0-9]+ [KMGT]iB/sec' | cut -d' ' -f1)

  # Fallback
  if [[ -z "$msgs_sec" ]]; then
    msgs_sec=$(echo "$raw" | grep -oE '[0-9,]+ msgs/sec' | tail -1 | tr -d ',' | cut -d' ' -f1)
  fi

  cat > "$RESULTS_DIR/nats_cli_throughput.json" <<EOF
{
  "broker": "nats",
  "tool": "nats bench",
  "total_messages": $TOTAL_MESSAGES,
  "payload_bytes": $PAYLOAD_BYTES,
  "msgs_per_sec": ${msgs_sec:-0},
  "mb_per_sec": ${mb_sec:-0},
  "storage": "file",
  "publishers": $NUM_PRODUCERS
}
EOF
  echo ""
  log "Result: ${msgs_sec:-0} msgs/sec"
  cat "$RESULTS_DIR/nats_cli_throughput.json"

  docker compose -f "$PROJECT_ROOT/infra/docker-compose.nats.yml" down -v
  echo ""
}

# ─── Run ──────────────────────────────────────────────────────────────────────
if [[ -n "$KCAT" ]]; then
  run_kafka_cli
else
  log "SKIPPED: 'kcat' (or 'kafkacat') not found — install via: sudo apt install kafkacat (or brew install kcat)"
fi

if command -v nats &>/dev/null; then
  run_nats_cli
else
  log "SKIPPED: 'nats' CLI not found — install via: brew install nats-io/nats-tools/nats"
fi

log "=== CLI throughput benchmark complete. Results in $RESULTS_DIR/*_cli_throughput.json ==="
