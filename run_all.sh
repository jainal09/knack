#!/usr/bin/env bash
# run_all.sh — Master script to run the full benchmark suite in order
#
# Usage:
#   ./run_all.sh                    # full run (~3 hrs)
#   ./run_all.sh --quick            # quick smoke run (~15 min)
#   ./run_all.sh --duration 120     # 2 min per throughput run
#   ./run_all.sh --duration 300 --reps 2 --idle-wait 60
#
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
source "$PROJECT_ROOT/env.sh"

# --- CLI overrides ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quick)
      export TEST_DURATION_SEC=30
      export REPS=1
      export IDLE_WAIT=30
      export LATENCY_DURATION_SEC=30
      export BASELINE_RATE=100
      export NUM_PRODUCERS=1
      shift ;;
    --duration)
      export TEST_DURATION_SEC="$2"; shift 2 ;;
    --reps)
      export REPS="$2"; shift 2 ;;
    --idle-wait)
      export IDLE_WAIT="$2"; shift 2 ;;
    --rate)
      export BASELINE_RATE="$2"; shift 2 ;;
    --producers)
      export NUM_PRODUCERS="$2"; shift 2 ;;
    --memory)
      export BENCH_MEMORY="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --quick              Quick smoke run (~15 min)"
      echo "  --duration SEC       Throughput test duration per run (default: 600)"
      echo "  --reps N             Number of throughput repetitions (default: 3)"
      echo "  --idle-wait SEC      Idle wait time in seconds (default: 300)"
      echo "  --rate N             Baseline msg/sec per producer (default: 5000)"
      echo "  --producers N        Number of producers (default: 4)"
      echo "  --memory SIZE        Broker memory limit, e.g. 2g (default: 4g)"
      echo "  -h, --help           Show this help"
      exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

mkdir -p "$PROJECT_ROOT/results"

echo "============================================"
echo "  Kafka vs NATS JetStream Benchmark Suite"
echo "  Started: $(date -Iseconds)"
echo "  CPUs: ${BENCH_CPUS} | RAM: ${BENCH_MEMORY}"
echo "============================================"
echo ""

# Start background metrics collector
"$PROJECT_ROOT/scripts/metrics_collector.sh" &
METRICS_PID=$!
trap "kill $METRICS_PID 2>/dev/null || true; echo 'Metrics collector stopped.'" EXIT
echo "Metrics collector running (PID $METRICS_PID)"
echo ""

# --- Scenario A: Idle footprint (AC-3) ---
echo ">>> SCENARIO A: Idle footprint"
bash "$PROJECT_ROOT/scripts/bench_idle.sh"
echo ""

# --- Scenario B: Startup & recovery (AC-4) ---
echo ">>> SCENARIO B: Startup & Recovery"
bash "$PROJECT_ROOT/scripts/bench_startup.sh"
echo ""

# --- Scenario C: Baseline throughput (AC-5) ---
echo ">>> SCENARIO C: Baseline Throughput"
bash "$PROJECT_ROOT/scripts/bench_throughput.sh"
echo ""

# --- Scenario D: Latency under load (AC-6) ---
echo ">>> SCENARIO D: Latency Under Load"
bash "$PROJECT_ROOT/scripts/bench_latency.sh"
echo ""

# --- Scenario F: Memory stress (AC-7) ---
echo ">>> SCENARIO F: Memory Stress"
bash "$PROJECT_ROOT/scripts/bench_memory_stress.sh"
echo ""

# --- Aggregate results (AC-8, AC-9) ---
echo ">>> AGGREGATING RESULTS"
uv run python3 "$PROJECT_ROOT/bench/aggregate_results.py"
echo ""

echo "============================================"
echo "  Benchmark complete: $(date -Iseconds)"
echo "  Results in: $PROJECT_ROOT/results/"
echo "============================================"
