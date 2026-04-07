#!/usr/bin/env bash
# run_scenarios.sh — Run benchmarks across multiple hardware scenarios
#
# Usage:
#   ./run_scenarios.sh                       # all 3 scenarios (large/medium/small)
#   ./run_scenarios.sh --scenario large      # single scenario
#   ./run_scenarios.sh --scenario small --quick
#   ./run_scenarios.sh --list                # show scenario configs
#
# All extra flags are forwarded to run_all.sh (--quick, --duration, --reps, --ui, etc.)
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

# ─── Scenario definitions ────────────────────────────────────────────────────
#              NAME     CPUS   MEMORY
SCENARIOS=(
  "large    4.0   8g"
  "medium   3.0   4g"
  "small    2.0   2g"
)

# ─── CLI parsing ─────────────────────────────────────────────────────────────
SELECTED_SCENARIO=""
FORWARD_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scenario)
      SELECTED_SCENARIO="$2"; shift 2 ;;
    --list)
      echo "Available scenarios:"
      echo "  NAME      CPUs   Memory"
      echo "  ────────  ─────  ──────"
      for s in "${SCENARIOS[@]}"; do
        read -r name cpus mem <<< "$s"
        echo "  ${name}      ${cpus}    ${mem}"
      done
      exit 0 ;;
    *)
      FORWARD_ARGS+=("$1"); shift ;;
  esac
done

# ─── Filter scenarios ────────────────────────────────────────────────────────
ACTIVE_SCENARIOS=()
if [[ -n "$SELECTED_SCENARIO" ]]; then
  for s in "${SCENARIOS[@]}"; do
    read -r name _ _ <<< "$s"
    if [[ "$name" == "$SELECTED_SCENARIO" ]]; then
      ACTIVE_SCENARIOS+=("$s")
    fi
  done
  if [[ ${#ACTIVE_SCENARIOS[@]} -eq 0 ]]; then
    echo "ERROR: Unknown scenario '$SELECTED_SCENARIO'. Use --list to see options."
    exit 1
  fi
else
  ACTIVE_SCENARIOS=("${SCENARIOS[@]}")
fi

# ─── Centralised logging ────────────────────────────────────────────────────
MASTER_RESULTS_DIR="$PROJECT_ROOT/results"
mkdir -p "$MASTER_RESULTS_DIR"
MASTER_LOG="$MASTER_RESULTS_DIR/scenarios_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$MASTER_LOG") 2>&1

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

log "Master log: $MASTER_LOG"
# ─────────────────────────────────────────────────────────────────────────────

# ─── Run each scenario ───────────────────────────────────────────────────────
log "============================================================"
log "  Multi-Scenario Benchmark Runner"
log "  Scenarios: $(printf '%s ' "${ACTIVE_SCENARIOS[@]}" | sed 's/  */ /g')"
log "  Started: $(date -Iseconds)"
log "============================================================"
echo ""

for s in "${ACTIVE_SCENARIOS[@]}"; do
  read -r name cpus mem <<< "$s"

  log ""
  log "╔══════════════════════════════════════════════════════════╗"
  log "║  SCENARIO: ${name^^} (${cpus} CPUs, ${mem} RAM)"
  log "╚══════════════════════════════════════════════════════════╝"
  echo ""

  # Set scenario-specific env vars
  export BENCH_CPUS="$cpus"
  export BENCH_MEMORY="$mem"
  export RESULTS_DIR="$PROJECT_ROOT/results/${name}"

  mkdir -p "$RESULTS_DIR"

  # Run the full benchmark suite with forwarded args
  bash "$PROJECT_ROOT/run_all.sh" "${FORWARD_ARGS[@]}" || {
    log "WARNING: Scenario '${name}' failed. Continuing..."
  }

  log ""
  log ">>> Scenario '${name}' complete. Results in results/${name}/"
  echo ""
done

# ─── Cross-scenario comparison charts ────────────────────────────────────────
log "============================================================"
log "  Generating cross-scenario comparison charts..."
log "============================================================"

# Pass all scenario names to visualizer
SCENARIO_NAMES=$(for s in "${ACTIVE_SCENARIOS[@]}"; do read -r n _ _ <<< "$s"; echo -n "$n "; done)
export SCENARIO_NAMES
uv run python3 "$PROJECT_ROOT/bench/visualize.py" --compare

echo ""
log "============================================================"
log "  All scenarios complete: $(date -Iseconds)"
log "  Results in: $PROJECT_ROOT/results/"
log "  Master log: $MASTER_LOG"
log "============================================================"
