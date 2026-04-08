#!/usr/bin/env bash
# bench_resource_scaling.sh — Throughput under varying CPU limits
#
# Captures throughput at the highest CPU cap first (unbounded baseline),
# then progressively restricts CPU to find the degradation slope / "knee".
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$PROJECT_ROOT/env.sh"
source "$PROJECT_ROOT/scripts/_log.sh"
RESULTS_DIR="${RESULTS_DIR:-$PROJECT_ROOT/results}"
mkdir -p "$RESULTS_DIR"

# Shorter duration per level to keep total time manageable
SCALING_DURATION="${SCALING_DURATION:-120}"
export TEST_DURATION_SEC="$SCALING_DURATION"

# CPU levels (space-separated, highest first)
# Default: 4.0 3.0 2.0 1.5 1.0 0.5
# --quick mode sets SCALING_CPU_LEVELS="2.0 1.0 0.5" via run_all.sh
read -ra CPU_LEVELS <<< "${SCALING_CPU_LEVELS}"

log "========================================"
log "  Resource Scaling Benchmark"
log "  CPU levels: ${CPU_LEVELS[*]}"
log "  Duration per level: ${SCALING_DURATION}s"
log "========================================"

# Start background metrics collection
"$PROJECT_ROOT/scripts/metrics_collector.sh" &
METRICS_PID=$!
trap "kill $METRICS_PID 2>/dev/null || true" EXIT

# Resolve kcat binary (newer distros ship "kcat", older ones ship "kafkacat")
KCAT=""
if command -v kcat &>/dev/null; then
  KCAT="kcat"
elif command -v kafkacat &>/dev/null; then
  KCAT="kafkacat"
fi

CLI_TOTAL_MESSAGES="${CLI_TOTAL_MESSAGES:-500000}"

# ── CLI throughput helpers (run on host, not in container) ────────────────────

_cli_throughput_kafka() {
  # Returns msgs/sec via kcat producer
  if [[ -z "$KCAT" ]]; then echo "0"; return; fi

  # Create topic
  docker exec bench-kafka kafka-topics --bootstrap-server localhost:9092 \
    --create --topic bench-scaling-cli --partitions 1 --replication-factor 1 \
    --config min.insync.replicas=1 >/dev/null 2>&1 || true

  local payload
  payload=$(head -c "$PAYLOAD_BYTES" /dev/urandom | base64 | tr -d '\n' | head -c "$PAYLOAD_BYTES")

  local start_ns end_ns elapsed_ms
  start_ns=$(date +%s%N)

  ( set +o pipefail
    yes "$payload" | head -n "$CLI_TOTAL_MESSAGES" | \
      timeout 300 "$KCAT" -P -b localhost:9092 -t bench-scaling-cli \
        -X acks=all \
        -X queue.buffering.max.messages=100000 \
        -X queue.buffering.max.kbytes=524288 \
        -X batch.num.messages=10000 \
        -X linger.ms=5
  )

  end_ns=$(date +%s%N)
  elapsed_ms=$(( (end_ns - start_ns) / 1000000 ))
  uv run python3 -c "print(f'{$CLI_TOTAL_MESSAGES / ($elapsed_ms / 1000):.1f}')" 2>/dev/null || echo "0"

  # Clean up topic for next iteration
  docker exec bench-kafka kafka-topics --bootstrap-server localhost:9092 \
    --delete --topic bench-scaling-cli >/dev/null 2>&1 || true
}

_cli_throughput_nats() {
  # Returns msgs/sec via nats bench
  if ! command -v nats &>/dev/null; then echo "0"; return; fi

  # Delete the Python producer's stream to free storage quota
  nats stream delete BENCH --server="nats://localhost:4222" -f >/dev/null 2>&1 || true

  local raw
  raw=$(nats bench js pub async scaling.cli.bench \
    --server="nats://localhost:4222" \
    --create --storage=file --purge \
    --stream="BENCH-SCALING-CLI" \
    --maxbytes="1GB" \
    --clients "$NUM_PRODUCERS" \
    --size "$PAYLOAD_BYTES" \
    --msgs "$CLI_TOTAL_MESSAGES" \
    --no-progress 2>&1) || true

  local msgs_sec
  msgs_sec=$(echo "$raw" | grep -i "aggregated stats" | grep -oE '[0-9,]+ msgs/sec' | tr -d ',' | cut -d' ' -f1)
  if [[ -z "$msgs_sec" ]]; then
    msgs_sec=$(echo "$raw" | grep -oE '[0-9,]+ msgs/sec' | tail -1 | tr -d ',' | cut -d' ' -f1)
  fi
  echo "${msgs_sec:-0}"
}

# ── Main scaling loop ────────────────────────────────────────────────────────

run_scaling() {
  local broker_name="$1" compose="$2" producer="$3"
  local tmp_json
  tmp_json=$(mktemp)
  echo "[]" > "$tmp_json"

  log "--- Resource scaling: $broker_name ---"

  for cpu_level in "${CPU_LEVELS[@]}"; do
    log "  CPU limit: $cpu_level cores"
    export BENCH_CPUS="$cpu_level"

    # Clean start
    docker compose -f "$compose" down -v 2>/dev/null || true

    local status="PASS"
    local throughput=0
    local cli_throughput=0
    local peak_cpu=0
    local peak_mem_mb=0

    # Try to start broker
    if ! docker compose -f "$compose" up -d 2>/dev/null; then
      log_warn "  Failed to start $broker_name at CPU=$cpu_level"
      status="FAIL_START"
    else
      # Wait for healthy (with short timeout)
      if ! wait_healthy "bench-${broker_name}" 90; then
        log_warn "  $broker_name failed health check at CPU=$cpu_level"
        local container_count
        container_count=$(docker compose -f "$compose" ps --status running -q 2>/dev/null | wc -l)
        if [[ "$container_count" -eq 0 ]]; then
          status="FAIL_START"
        else
          status="FAIL_HEALTH"
        fi
      fi
    fi

    if [[ "$status" == "PASS" ]]; then
      # Capture peak stats in background during the run
      local stats_file
      stats_file=$(mktemp)

      (
        while true; do
          docker stats --no-stream --format '{{.CPUPerc}},{{.MemUsage}}' "bench-${broker_name}" 2>/dev/null >> "$stats_file" || true
          sleep 5
        done
      ) &
      local stats_pid=$!

      # Run Python client producer benchmark
      local run_output
      if run_output=$(uv run python3 "$producer" 2>/dev/null); then
        throughput=$(echo "$run_output" | uv run python3 -c "import sys,json; print(json.load(sys.stdin).get('aggregate_rate', 0))" 2>/dev/null || echo "0")
      else
        log_warn "  Producer failed at CPU=$cpu_level"
        status="FAIL_WORKLOAD"
      fi

      # Run CLI-native throughput (host-side, no Python overhead)
      if [[ "$status" == "PASS" ]]; then
        log "    Running CLI throughput at CPU=$cpu_level ..."
        cli_throughput=$(_cli_throughput_${broker_name})
        log "    CLI: ${cli_throughput} msg/s"
      fi

      # Stop stats collection
      kill $stats_pid 2>/dev/null || true
      wait $stats_pid 2>/dev/null || true

      # Parse peak stats from collected data
      if [[ -s "$stats_file" ]]; then
        local parsed
        parsed=$(uv run python3 -c "
max_cpu = 0
max_mem = 0
for line in open('$stats_file'):
    parts = line.strip().split(',')
    if len(parts) >= 2:
        try:
            cpu = float(parts[0].replace('%', ''))
            if cpu > max_cpu: max_cpu = cpu
            mem_str = parts[1].split('/')[0].strip()
            if 'GiB' in mem_str: mem = float(mem_str.replace('GiB', '').strip()) * 1024
            elif 'MiB' in mem_str: mem = float(mem_str.replace('MiB', '').strip())
            elif 'KiB' in mem_str: mem = float(mem_str.replace('KiB', '').strip()) / 1024
            else: mem = 0
            if mem > max_mem: max_mem = mem
        except (ValueError, IndexError): pass
print(f'{max_cpu:.1f},{max_mem:.1f}')
" 2>/dev/null || echo "0.0,0.0")
        peak_cpu=$(echo "$parsed" | cut -d',' -f1)
        peak_mem_mb=$(echo "$parsed" | cut -d',' -f2)
      fi

      rm -f "$stats_file"
    fi

    docker compose -f "$compose" down -v 2>/dev/null || true

    # Append entry to JSON array via Python (reliable JSON building)
    uv run python3 -c "
import json
data = json.load(open('$tmp_json'))
data.append({
    'cpu_limit': $cpu_level,
    'throughput': $throughput,
    'cli_throughput': $cli_throughput,
    'peak_cpu_pct': $peak_cpu,
    'peak_mem_mb': $peak_mem_mb,
    'status': '$status'
})
with open('$tmp_json', 'w') as f:
    json.dump(data, f, indent=2)
"

    if [[ "$status" == "PASS" ]]; then
      log_ok "  CPU=$cpu_level → Python: ${throughput} msg/s | CLI: ${cli_throughput} msg/s | peak CPU=${peak_cpu}% | peak mem=${peak_mem_mb}MB"
    else
      log_warn "  CPU=$cpu_level → $status"
    fi
  done

  # Move final JSON to results
  mv "$tmp_json" "$RESULTS_DIR/${broker_name}_scaling.json"
  log "  Saved: ${broker_name}_scaling.json"
  echo ""
}

run_scaling "kafka" "$PROJECT_ROOT/infra/docker-compose.kafka.yml" "$PROJECT_ROOT/bench/producer_kafka.py"
run_scaling "nats"  "$PROJECT_ROOT/infra/docker-compose.nats.yml"  "$PROJECT_ROOT/bench/producer_nats.py"

log "=== Resource scaling benchmark complete. Results in results/*_scaling.json ==="
