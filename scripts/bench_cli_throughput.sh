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
  # Run in subshell with pipefail off: yes exits with SIGPIPE (141) when head
  # closes, which is expected — not an error.
  ( set +o pipefail
    yes "$payload" | head -n "$TOTAL_MESSAGES" | \
      timeout 300 "$KCAT" -P -b localhost:9092 -t bench-cli \
        -X acks=all \
        -X queue.buffering.max.messages=100000 \
        -X queue.buffering.max.kbytes=524288 \
        -X batch.num.messages=10000 \
        -X linger.ms=5
  )

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

# ─── Kafka CLI Consumer ──────────────────────────────────────────────────────
run_kafka_cli_consumer() {
  log "========================================"
  log "  CLI Consumer: Kafka (kcat -C on host)"
  log "  Messages: $TOTAL_MESSAGES × ${PAYLOAD_BYTES}B"
  log "========================================"

  docker compose -f "$PROJECT_ROOT/infra/docker-compose.kafka.yml" down -v 2>/dev/null || true
  docker compose -f "$PROJECT_ROOT/infra/docker-compose.kafka.yml" up -d
  wait_healthy "bench-kafka"

  # Create topic
  docker exec bench-kafka kafka-topics --bootstrap-server localhost:9092 \
    --create --topic bench-cli-consumer --partitions 1 --replication-factor 1 \
    --config min.insync.replicas=1 2>/dev/null || true

  # Pre-populate messages
  local payload
  payload=$(head -c "$PAYLOAD_BYTES" /dev/urandom | base64 | tr -d '\n' | head -c "$PAYLOAD_BYTES")

  log "Pre-populating $TOTAL_MESSAGES messages..."
  ( set +o pipefail
    yes "$payload" | head -n "$TOTAL_MESSAGES" | \
      timeout 300 "$KCAT" -P -b localhost:9092 -t bench-cli-consumer \
        -X acks=all \
        -X queue.buffering.max.messages=100000 \
        -X queue.buffering.max.kbytes=524288 \
        -X batch.num.messages=10000 \
        -X linger.ms=5
  )
  sleep 2

  log "Consuming $TOTAL_MESSAGES messages with kcat..."
  local start_ns end_ns elapsed_ms
  start_ns=$(date +%s%N)

  # Consume all messages from beginning, exit after TOTAL_MESSAGES
  timeout 300 "$KCAT" -C -b localhost:9092 -t bench-cli-consumer \
    -e -o beginning -c "$TOTAL_MESSAGES" -q > /dev/null

  end_ns=$(date +%s%N)
  elapsed_ms=$(( (end_ns - start_ns) / 1000000 ))
  local elapsed_sec records_sec
  elapsed_sec=$(uv run python3 -c "print(f'{$elapsed_ms / 1000:.3f}')")
  records_sec=$(uv run python3 -c "print(f'{$TOTAL_MESSAGES / ($elapsed_ms / 1000):.1f}')")

  log "Consumed in ${elapsed_sec}s"

  cat > "$RESULTS_DIR/kafka_cli_consumer.json" <<EOF
{
  "broker": "kafka",
  "tool": "kcat",
  "mode": "consumer",
  "total_messages": $TOTAL_MESSAGES,
  "payload_bytes": $PAYLOAD_BYTES,
  "elapsed_sec": $elapsed_sec,
  "msgs_per_sec": $records_sec
}
EOF
  echo ""
  log "Result: $records_sec records/sec (${elapsed_sec}s for $TOTAL_MESSAGES msgs)"
  cat "$RESULTS_DIR/kafka_cli_consumer.json"

  docker compose -f "$PROJECT_ROOT/infra/docker-compose.kafka.yml" down -v
  echo ""
}

# ─── NATS CLI Consumer ──────────────────────────────────────────────────────
run_nats_cli_consumer() {
  log "========================================"
  log "  CLI Consumer: NATS (nats bench sub)"
  log "  Messages: $TOTAL_MESSAGES × ${PAYLOAD_BYTES}B"
  log "========================================"

  docker compose -f "$PROJECT_ROOT/infra/docker-compose.nats.yml" down -v 2>/dev/null || true
  docker compose -f "$PROJECT_ROOT/infra/docker-compose.nats.yml" up -d
  wait_healthy "bench-nats"

  # Pre-populate with nats bench pub
  log "Pre-populating $TOTAL_MESSAGES messages..."
  nats bench js pub async bench.cli.consumer \
    --server="nats://localhost:4222" \
    --create --storage=file --purge \
    --stream="BENCH-CLI-CONSUMER" \
    --maxbytes="10GB" \
    --clients "$NUM_PRODUCERS" \
    --size "$PAYLOAD_BYTES" \
    --msgs "$TOTAL_MESSAGES" \
    --no-progress 2>&1 || true

  sleep 2

  # Consume
  log "Consuming $TOTAL_MESSAGES messages with nats bench consume..."
  local raw
  raw=$(nats bench js consume \
    --server="nats://localhost:4222" \
    --stream="BENCH-CLI-CONSUMER" \
    --clients "$NUM_CONSUMERS" \
    --msgs "$TOTAL_MESSAGES" \
    --no-progress 2>&1) || true

  echo "$raw"

  local msgs_sec
  msgs_sec=$(echo "$raw" | grep -i "aggregated stats" | grep -oE '[0-9,]+ msgs/sec' | tr -d ',' | cut -d' ' -f1)
  if [[ -z "$msgs_sec" ]]; then
    msgs_sec=$(echo "$raw" | grep -oE '[0-9,]+ msgs/sec' | tail -1 | tr -d ',' | cut -d' ' -f1)
  fi

  cat > "$RESULTS_DIR/nats_cli_consumer.json" <<EOF
{
  "broker": "nats",
  "tool": "nats bench",
  "mode": "consumer",
  "total_messages": $TOTAL_MESSAGES,
  "payload_bytes": $PAYLOAD_BYTES,
  "msgs_per_sec": ${msgs_sec:-0},
  "subscribers": $NUM_CONSUMERS
}
EOF
  echo ""
  log "Result: ${msgs_sec:-0} msgs/sec"
  cat "$RESULTS_DIR/nats_cli_consumer.json"

  docker compose -f "$PROJECT_ROOT/infra/docker-compose.nats.yml" down -v
  echo ""
}

# ─── Kafka CLI ProdCon ───────────────────────────────────────────────────────
run_kafka_cli_prodcon() {
  log "========================================"
  log "  CLI ProdCon: Kafka (kcat on host)"
  log "  Messages: $TOTAL_MESSAGES × ${PAYLOAD_BYTES}B"
  log "========================================"

  docker compose -f "$PROJECT_ROOT/infra/docker-compose.kafka.yml" down -v 2>/dev/null || true
  docker compose -f "$PROJECT_ROOT/infra/docker-compose.kafka.yml" up -d
  wait_healthy "bench-kafka"

  docker exec bench-kafka kafka-topics --bootstrap-server localhost:9092 \
    --create --topic bench-cli-prodcon --partitions 1 --replication-factor 1 \
    --config min.insync.replicas=1 2>/dev/null || true

  local payload
  payload=$(head -c "$PAYLOAD_BYTES" /dev/urandom | base64 | tr -d '\n' | head -c "$PAYLOAD_BYTES")

  # Start consumer in background — consume continuously, count lines
  local cons_out
  cons_out=$(mktemp)
  timeout 300 "$KCAT" -C -b localhost:9092 -t bench-cli-prodcon -q > "$cons_out" &
  local cons_pid=$!
  sleep 1

  log "Producing $TOTAL_MESSAGES messages while consuming simultaneously..."
  local start_ns end_ns elapsed_ms
  start_ns=$(date +%s%N)

  ( set +o pipefail
    yes "$payload" | head -n "$TOTAL_MESSAGES" | \
      timeout 300 "$KCAT" -P -b localhost:9092 -t bench-cli-prodcon \
        -X acks=all \
        -X queue.buffering.max.messages=100000 \
        -X queue.buffering.max.kbytes=524288 \
        -X batch.num.messages=10000 \
        -X linger.ms=5
  )

  end_ns=$(date +%s%N)
  elapsed_ms=$(( (end_ns - start_ns) / 1000000 ))

  # Let consumer drain for a few seconds
  sleep 3
  kill $cons_pid 2>/dev/null || true
  wait $cons_pid 2>/dev/null || true

  local consumed
  consumed=$(wc -l < "$cons_out")
  rm -f "$cons_out"

  local elapsed_sec prod_rate cons_rate
  elapsed_sec=$(uv run python3 -c "print(f'{$elapsed_ms / 1000:.3f}')")
  prod_rate=$(uv run python3 -c "print(f'{$TOTAL_MESSAGES / ($elapsed_ms / 1000):.1f}')")
  cons_rate=$(uv run python3 -c "print(f'{$consumed / ($elapsed_ms / 1000 + 3):.1f}')")

  log "Done in ${elapsed_sec}s — produced: $TOTAL_MESSAGES, consumed: $consumed"

  cat > "$RESULTS_DIR/kafka_cli_prodcon.json" <<EOF
{
  "broker": "kafka",
  "tool": "kcat",
  "mode": "prodcon",
  "total_produced": $TOTAL_MESSAGES,
  "total_consumed": $consumed,
  "payload_bytes": $PAYLOAD_BYTES,
  "elapsed_sec": $elapsed_sec,
  "producer_msgs_per_sec": $prod_rate,
  "consumer_msgs_per_sec": $cons_rate,
  "acks": "all"
}
EOF
  echo ""
  cat "$RESULTS_DIR/kafka_cli_prodcon.json"

  docker compose -f "$PROJECT_ROOT/infra/docker-compose.kafka.yml" down -v
  echo ""
}

# ─── NATS CLI ProdCon ────────────────────────────────────────────────────────
run_nats_cli_prodcon() {
  log "========================================"
  log "  CLI ProdCon: NATS (nats bench pub + consume)"
  log "  Messages: $TOTAL_MESSAGES × ${PAYLOAD_BYTES}B"
  log "========================================"

  docker compose -f "$PROJECT_ROOT/infra/docker-compose.nats.yml" down -v 2>/dev/null || true
  docker compose -f "$PROJECT_ROOT/infra/docker-compose.nats.yml" up -d
  wait_healthy "bench-nats"

  # Create stream first via a small pub run
  nats bench js pub async bench.cli.prodcon \
    --server="nats://localhost:4222" \
    --create --storage=file --purge \
    --stream="BENCH-CLI-PRODCON" \
    --maxbytes="10GB" \
    --clients 1 \
    --size "$PAYLOAD_BYTES" \
    --msgs 1 \
    --no-progress 2>&1 || true

  # Start consumer in background
  log "Starting nats bench js consume in background..."
  local cons_out
  cons_out=$(mktemp)
  nats bench js consume \
    --server="nats://localhost:4222" \
    --stream="BENCH-CLI-PRODCON" \
    --clients "$NUM_CONSUMERS" \
    --msgs "$TOTAL_MESSAGES" \
    --no-progress 2>&1 > "$cons_out" &
  local CONS_PID=$!

  sleep 2

  # Run producer in foreground
  log "Running nats bench js pub..."
  local pub_raw
  pub_raw=$(nats bench js pub async bench.cli.prodcon \
    --server="nats://localhost:4222" \
    --stream="BENCH-CLI-PRODCON" \
    --clients "$NUM_PRODUCERS" \
    --size "$PAYLOAD_BYTES" \
    --msgs "$TOTAL_MESSAGES" \
    --no-progress 2>&1) || true

  echo "=== Producer output ==="
  echo "$pub_raw"

  # Wait for consumer to finish (timeout 60s)
  local waited=0
  while kill -0 "$CONS_PID" 2>/dev/null && [[ $waited -lt 60 ]]; do
    sleep 1
    waited=$((waited + 1))
  done
  kill "$CONS_PID" 2>/dev/null || true
  wait "$CONS_PID" 2>/dev/null || true

  local cons_raw
  cons_raw=$(cat "$cons_out")
  rm -f "$cons_out"

  echo "=== Consumer output ==="
  echo "$cons_raw"

  # Parse producer aggregated stats
  local pub_msgs_sec sub_msgs_sec
  pub_msgs_sec=$(echo "$pub_raw" | grep -i "aggregated stats" | grep -oE '[0-9,]+ msgs/sec' | tr -d ',' | cut -d' ' -f1)
  if [[ -z "$pub_msgs_sec" ]]; then
    pub_msgs_sec=$(echo "$pub_raw" | grep -oE '[0-9,]+ msgs/sec' | tail -1 | tr -d ',' | cut -d' ' -f1)
  fi

  # Parse consumer aggregated stats
  sub_msgs_sec=$(echo "$cons_raw" | grep -i "aggregated stats" | grep -oE '[0-9,]+ msgs/sec' | tr -d ',' | cut -d' ' -f1)
  if [[ -z "$sub_msgs_sec" ]]; then
    sub_msgs_sec=$(echo "$cons_raw" | grep -oE '[0-9,]+ msgs/sec' | tail -1 | tr -d ',' | cut -d' ' -f1)
  fi

  cat > "$RESULTS_DIR/nats_cli_prodcon.json" <<EOF
{
  "broker": "nats",
  "tool": "nats bench",
  "mode": "prodcon",
  "total_messages": $TOTAL_MESSAGES,
  "payload_bytes": $PAYLOAD_BYTES,
  "producer_msgs_per_sec": ${pub_msgs_sec:-0},
  "consumer_msgs_per_sec": ${sub_msgs_sec:-0},
  "storage": "file",
  "publishers": $NUM_PRODUCERS,
  "subscribers": $NUM_CONSUMERS
}
EOF
  echo ""
  log "Result: pub=${pub_msgs_sec:-0} msgs/sec, sub=${sub_msgs_sec:-0} msgs/sec"
  cat "$RESULTS_DIR/nats_cli_prodcon.json"

  docker compose -f "$PROJECT_ROOT/infra/docker-compose.nats.yml" down -v
  echo ""
}

# ─── Run ──────────────────────────────────────────────────────────────────────
# Producer throughput (existing)
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

# Consumer throughput
if [[ -n "$KCAT" ]]; then
  run_kafka_cli_consumer
else
  log "SKIPPED: Kafka CLI consumer — kcat not found"
fi

if command -v nats &>/dev/null; then
  run_nats_cli_consumer
else
  log "SKIPPED: NATS CLI consumer — nats CLI not found"
fi

# Simultaneous producer+consumer
if [[ -n "$KCAT" ]]; then
  run_kafka_cli_prodcon
else
  log "SKIPPED: Kafka CLI prodcon — kcat not found"
fi

if command -v nats &>/dev/null; then
  run_nats_cli_prodcon
else
  log "SKIPPED: NATS CLI prodcon — nats CLI not found"
fi

log "=== CLI throughput benchmark complete. Results in $RESULTS_DIR/*_cli_*.json ==="
