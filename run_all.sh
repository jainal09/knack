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

# ─── Preflight dependency check ──────────────────────────────────────────────
_missing=0
for cmd in docker uv nc; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: '$cmd' not found." >&2
    case "$cmd" in
      docker) echo "  Install: https://docs.docker.com/engine/install/" >&2 ;;
      uv)     echo "  Install: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2 ;;
      nc)     echo "  Install: sudo apt install netcat-openbsd  (or brew install netcat on macOS)" >&2 ;;
    esac
    _missing=1
  fi
done
if ! docker compose version &>/dev/null 2>&1; then
  echo "ERROR: 'docker compose' (v2) not available. Install the Compose plugin." >&2
  echo "  See: https://docs.docker.com/compose/install/" >&2
  _missing=1
fi
for opt_cmd in kcat nats; do
  case "$opt_cmd" in
    kcat)
      if ! command -v kcat &>/dev/null && ! command -v kafkacat &>/dev/null; then
        echo "WARNING: 'kcat' (or 'kafkacat') not found — Kafka CLI throughput benchmark will be skipped." >&2
        echo "  Install: sudo apt install kafkacat  (or brew install kcat on macOS)" >&2
      fi ;;
    nats)
      if ! command -v nats &>/dev/null; then
        echo "WARNING: 'nats' CLI not found — NATS CLI throughput benchmark will be skipped." >&2
        echo "  Install: brew install nats-io/nats-tools/nats" >&2
        echo "       or: https://github.com/nats-io/natscli/releases" >&2
      fi ;;
  esac
done
[[ $_missing -eq 0 ]] || { echo "\nFix the above and re-run." >&2; exit 1; }
# ─────────────────────────────────────────────────────────────────────────────

source "$PROJECT_ROOT/env.sh"

# --- CLI overrides ---
RESUME=0
RERUN_STEPS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quick)
      export TEST_DURATION_SEC=30
      export REPS=1
      export IDLE_WAIT=30
      export LATENCY_DURATION_SEC=30
      export NUM_PRODUCERS=2
      export SCALING_CPU_LEVELS="2.0 1.0 0.5"
      shift ;;
    --duration)
      export TEST_DURATION_SEC="$2"; shift 2 ;;
    --reps)
      export REPS="$2"; shift 2 ;;
    --idle-wait)
      export IDLE_WAIT="$2"; shift 2 ;;
    --producers)
      export NUM_PRODUCERS="$2"; shift 2 ;;
    --memory)
      export BENCH_MEMORY="$2"; shift 2 ;;
    --ui)
      export COMPOSE_PROFILES="tools"; shift ;;
    --results-dir)
      export RESULTS_DIR="$2"; shift 2 ;;
    --resume)
      RESUME=1; shift ;;
    --rerun)
      # Remove specific steps from checkpoint so they re-run with --resume
      # Usage: --rerun cli_throughput,consumer,prodcon
      RESUME=1
      IFS=',' read -ra RERUN_STEPS <<< "$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --quick              Quick smoke run (~15 min)"
      echo "  --duration SEC       Throughput test duration per run (default: 600)"
      echo "  --reps N             Number of throughput repetitions (default: 3)"
      echo "  --idle-wait SEC      Idle wait time in seconds (default: 300)"
      echo "  --producers N        Number of producers (default: 4)"
      echo "  --memory SIZE        Broker memory limit, e.g. 2g (default: 4g)"
      echo "  --ui                 Start UI containers alongside brokers"
      echo "  --results-dir DIR    Output directory for results"
      echo "  --resume             Resume from last checkpoint (skip completed steps)"
      echo "  --rerun STEPS        Re-run specific steps (comma-separated), implies --resume"
      echo "                       Steps: idle,startup,throughput,latency,memory_stress,"
      echo "                              cli_throughput,consumer,prodcon,resource_scaling"
      echo "  -h, --help           Show this help"
      exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

export RESULTS_DIR="${RESULTS_DIR:-$PROJECT_ROOT/results}"
mkdir -p "$RESULTS_DIR"

# ─── Centralised logging ────────────────────────────────────────────────────
LOG_FILE="$RESULTS_DIR/benchmark_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

log "Log file: $LOG_FILE"
# ─────────────────────────────────────────────────────────────────────────────

# ─── Checkpoint helpers ──────────────────────────────────────────────────────
CHECKPOINT_FILE="$RESULTS_DIR/checkpoint.log"

step_done() {
  # Returns 0 (true) if step already completed in checkpoint file
  [[ $RESUME -eq 1 ]] && grep -qxF "$1" "$CHECKPOINT_FILE" 2>/dev/null
}

mark_done() {
  echo "$1" >> "$CHECKPOINT_FILE"
}

if [[ $RESUME -eq 1 && -f "$CHECKPOINT_FILE" ]]; then
  # Remove steps requested for re-run from checkpoint
  if [[ ${#RERUN_STEPS[@]} -gt 0 ]]; then
    for step in "${RERUN_STEPS[@]}"; do
      sed -i "/^${step}$/d" "$CHECKPOINT_FILE"
    done
    echo "Re-running steps: ${RERUN_STEPS[*]}"
  fi
  echo "Resuming — completed steps: $(tr '\n' ', ' < "$CHECKPOINT_FILE")"
  echo ""
else
  # Fresh run — clear any stale checkpoint
  rm -f "$CHECKPOINT_FILE"
fi
# ─────────────────────────────────────────────────────────────────────────────

# ─── Colors (also available via _log.sh in child scripts) ────────────────────
_RST='\033[0m'
_BOLD='\033[1m'
_DIM='\033[2m'
_RED='\033[1;31m'
_GREEN='\033[1;32m'
_YELLOW='\033[1;33m'
_CYAN='\033[1;36m'
_BLUE='\033[1;34m'
_MAG='\033[1;35m'

# ─── Progress helpers ────────────────────────────────────────────────────────
TOTAL_STEPS=11
_STEP=0
_SUITE_START=$(date +%s)

progress() {
  _STEP=$((_STEP + 1))
  local elapsed=$(( $(date +%s) - _SUITE_START ))
  local mins=$(( elapsed / 60 ))
  local secs=$(( elapsed % 60 ))
  printf "\n${_CYAN}[%s] ► [%d/%d]${_RST} ${_DIM}(%dm%02ds elapsed)${_RST} ${_BOLD}%s${_RST}\n" \
    "$(date '+%Y-%m-%d %H:%M:%S')" "$_STEP" "$TOTAL_STEPS" "$mins" "$secs" "$1"
}
# ─────────────────────────────────────────────────────────────────────────────

printf "${_MAG}============================================${_RST}\n"
printf "${_MAG}  Kafka vs NATS JetStream Benchmark Suite${_RST}\n"
printf "${_MAG}  Started: $(date -Iseconds)${_RST}\n"
printf "${_BLUE}  CPUs: ${BENCH_CPUS} | RAM: ${BENCH_MEMORY} | UI: ${COMPOSE_PROFILES:-off}${_RST}\n"
printf "${_BLUE}  Duration: ${TEST_DURATION_SEC}s | Reps: ${REPS} | Producers: ${NUM_PRODUCERS} | Consumers: ${NUM_CONSUMERS} | Throughput: UNCAPPED${_RST}\n"
printf "${_BLUE}  Results:  ${RESULTS_DIR}${_RST}\n"
printf "${_MAG}============================================${_RST}\n"
echo ""

# ─── Ctrl+C guard ───────────────────────────────────────────────────────────
_cancel_confirmed=0
_handle_sigint() {
  echo ""
  printf "${_YELLOW}Ctrl+C detected. Are you sure you want to cancel? [y/N] ${_RST}"
  # Temporarily restore default SIGINT so a second Ctrl+C during the prompt kills immediately
  trap - INT
  local answer=""
  read -r -t 15 answer </dev/tty 2>/dev/null || answer="n"
  case "$answer" in
    [yY]|[yY][eE][sS])
      printf "${_RED}Aborting benchmark...${_RST}\n"
      _cancel_confirmed=1
      kill $METRICS_PID 2>/dev/null || true
      exit 130
      ;;
    *)
      printf "${_GREEN}Resuming benchmark.${_RST}\n"
      trap '_handle_sigint' INT
      ;;
  esac
}
trap '_handle_sigint' INT
# ─────────────────────────────────────────────────────────────────────────────

# Start background metrics collector
"$PROJECT_ROOT/scripts/metrics_collector.sh" &
METRICS_PID=$!
trap "kill $METRICS_PID 2>/dev/null || true; echo 'Metrics collector stopped.'" EXIT
trap '_handle_sigint' INT  # re-set after EXIT trap overwrites
log "Metrics collector running (PID $METRICS_PID)"
echo ""

# --- Scenario A: Idle footprint (AC-3) ---
if step_done "idle"; then
  progress "Idle footprint [SKIPPED]"
else
  progress "Idle footprint"
  bash "$PROJECT_ROOT/scripts/bench_idle.sh"
  printf "${_GREEN}[%s] ✔ Idle footprint complete${_RST}\n" "$(date '+%Y-%m-%d %H:%M:%S')"
  mark_done "idle"
fi
echo ""

# --- Scenario B: Startup & recovery (AC-4) ---
if step_done "startup"; then
  progress "Startup & Recovery [SKIPPED]"
else
  progress "Startup & Recovery"
  bash "$PROJECT_ROOT/scripts/bench_startup.sh"
  printf "${_GREEN}[%s] ✔ Startup & Recovery complete${_RST}\n" "$(date '+%Y-%m-%d %H:%M:%S')"
  mark_done "startup"
fi
echo ""

# --- Scenario C: Baseline throughput (AC-5) ---
if step_done "throughput"; then
  progress "Baseline Throughput [SKIPPED]"
else
  progress "Baseline Throughput"
  bash "$PROJECT_ROOT/scripts/bench_throughput.sh"
  printf "${_GREEN}[%s] ✔ Baseline Throughput complete${_RST}\n" "$(date '+%Y-%m-%d %H:%M:%S')"
  mark_done "throughput"
fi
echo ""

# --- Scenario D: Latency under load (AC-6) ---
if step_done "latency"; then
  progress "Latency Under Load [SKIPPED]"
else
  progress "Latency Under Load"
  bash "$PROJECT_ROOT/scripts/bench_latency.sh"
  printf "${_GREEN}[%s] ✔ Latency Under Load complete${_RST}\n" "$(date '+%Y-%m-%d %H:%M:%S')"
  mark_done "latency"
fi
echo ""

# --- Scenario F: Memory stress (AC-7) ---
if step_done "memory_stress"; then
  progress "Memory Stress [SKIPPED]"
else
  progress "Memory Stress"
  bash "$PROJECT_ROOT/scripts/bench_memory_stress.sh"
  printf "${_GREEN}[%s] ✔ Memory Stress complete${_RST}\n" "$(date '+%Y-%m-%d %H:%M:%S')"
  mark_done "memory_stress"
fi
echo ""

# --- Scenario G: CLI-native throughput ---
if step_done "cli_throughput"; then
  progress "CLI-Native Throughput [SKIPPED]"
else
  progress "CLI-Native Throughput"
  bash "$PROJECT_ROOT/scripts/bench_cli_throughput.sh"
  printf "${_GREEN}[%s] ✔ CLI-Native Throughput complete${_RST}\n" "$(date '+%Y-%m-%d %H:%M:%S')"
  mark_done "cli_throughput"
fi
echo ""

# --- Scenario H: Consumer throughput ---
if step_done "consumer"; then
  progress "Consumer Throughput [SKIPPED]"
else
  progress "Consumer Throughput"
  bash "$PROJECT_ROOT/scripts/bench_consumer.sh"
  printf "${_GREEN}[%s] ✔ Consumer Throughput complete${_RST}\n" "$(date '+%Y-%m-%d %H:%M:%S')"
  mark_done "consumer"
fi
echo ""

# --- Scenario I: Simultaneous Producer+Consumer ---
if step_done "prodcon"; then
  progress "Simultaneous Producer+Consumer [SKIPPED]"
else
  progress "Simultaneous Producer+Consumer"
  bash "$PROJECT_ROOT/scripts/bench_prodcon.sh"
  printf "${_GREEN}[%s] ✔ Simultaneous Producer+Consumer complete${_RST}\n" "$(date '+%Y-%m-%d %H:%M:%S')"
  mark_done "prodcon"
fi
echo ""

# --- Scenario J: Resource Scaling ---
if step_done "resource_scaling"; then
  progress "Resource Scaling [SKIPPED]"
else
  progress "Resource Scaling"
  bash "$PROJECT_ROOT/scripts/bench_resource_scaling.sh"
  printf "${_GREEN}[%s] ✔ Resource Scaling complete${_RST}\n" "$(date '+%Y-%m-%d %H:%M:%S')"
  mark_done "resource_scaling"
fi
echo ""

# --- Aggregate results (AC-8, AC-9) ---
progress "Aggregating Results"
uv run python3 "$PROJECT_ROOT/bench/aggregate_results.py"
echo ""

# --- Generate charts ---
progress "Generating Charts"
uv run python3 "$PROJECT_ROOT/bench/visualize.py"
echo ""

printf "\n${_GREEN}============================================${_RST}\n"
printf "${_GREEN}  Benchmark complete: $(date -Iseconds)${_RST}\n"
printf "${_GREEN}  Results in: $RESULTS_DIR/${_RST}\n"
printf "${_GREEN}  Full log:  $LOG_FILE${_RST}\n"
printf "${_GREEN}============================================${_RST}\n"
