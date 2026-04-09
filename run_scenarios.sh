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
    --rerun|--duration|--reps|--idle-wait|--producers|--memory|--results-dir)
      FORWARD_ARGS+=("$1" "$2"); shift 2 ;;
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

# ─── Colors ──────────────────────────────────────────────────────────────────
_RST='\033[0m'
_BOLD='\033[1m'
_DIM='\033[2m'
_RED='\033[1;31m'
_GREEN='\033[1;32m'
_YELLOW='\033[1;33m'
_CYAN='\033[1;36m'
_BLUE='\033[1;34m'
_MAG='\033[1;35m'

log() {
  printf "${_DIM}[%s]${_RST} %s\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

log "Master log: $MASTER_LOG"
# ─────────────────────────────────────────────────────────────────────────────

# ─── Ctrl+C guard ───────────────────────────────────────────────────────────
# All I/O goes directly to /dev/tty so it bypasses the exec > >(tee) redirect.
# Single-keypress detection: y = abort, n / Enter / Esc = resume.
# A second Ctrl+C during the prompt force-kills immediately.
_handle_sigint() {
  printf "\n\033[1;33m⚠  Ctrl+C detected. Abort benchmark? [y = abort / n or Esc = resume] \033[0m" >/dev/tty
  trap - INT  # second Ctrl+C during prompt kills immediately
  local key=""
  while true; do
    if ! IFS= read -rsn1 -t 15 key </dev/tty 2>/dev/null; then
      key="n"  # timeout → resume
    fi
    case "$key" in
      [yY])
        printf "\n\033[1;31mAborting benchmark run...\033[0m\n" >/dev/tty
        exit 130
        ;;
      [nN]|"")
        # n, N, or Enter → resume
        printf "\n\033[1;32mResuming benchmark.\033[0m\n" >/dev/tty
        break
        ;;
      $'\x1b')
        # Esc — flush any trailing escape sequence bytes and resume
        read -rsn2 -t 0.1 _ </dev/tty 2>/dev/null || true
        printf "\n\033[1;32mResuming benchmark.\033[0m\n" >/dev/tty
        break
        ;;
    esac
  done
  trap '_handle_sigint' INT
}
trap '_handle_sigint' INT
# ─────────────────────────────────────────────────────────────────────────────

# ─── Run each scenario ───────────────────────────────────────────────────────
printf "${_MAG}============================================================${_RST}\n"
printf "${_MAG}  ⚡ Knack — Multi-Scenario Benchmark Runner${_RST}\n"
printf "${_BLUE}  Scenarios: $(printf '%s ' "${ACTIVE_SCENARIOS[@]}" | sed 's/  */ /g')${_RST}\n"
printf "${_BLUE}  Started: $(date -Iseconds)${_RST}\n"
printf "${_MAG}============================================================${_RST}\n"
echo ""

for s in "${ACTIVE_SCENARIOS[@]}"; do
  read -r name cpus mem <<< "$s"

  printf "\n${_CYAN}╔══════════════════════════════════════════════════════════╗${_RST}\n"
  printf "${_CYAN}║  SCENARIO: ${name^^} (${cpus} CPUs, ${mem} RAM)${_RST}\n"
  printf "${_CYAN}╚══════════════════════════════════════════════════════════╝${_RST}\n"
  echo ""

  # Set scenario-specific env vars
  export BENCH_CPUS="$cpus"
  export BENCH_MEMORY="$mem"
  export RESULTS_DIR="$PROJECT_ROOT/results/${name}"

  mkdir -p "$RESULTS_DIR"

  # Run the full benchmark suite with forwarded args
  if bash "$PROJECT_ROOT/run_all.sh" "${FORWARD_ARGS[@]}"; then
    printf "${_GREEN}[%s] ✔ Scenario '${name}' PASSED. Results in results/${name}/${_RST}\n" "$(date '+%Y-%m-%d %H:%M:%S')"
  else
    printf "${_RED}[%s] ✘ Scenario '${name}' FAILED (exit code $?). Partial results in results/${name}/${_RST}\n" "$(date '+%Y-%m-%d %H:%M:%S')"
  fi

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
printf "\n${_GREEN}============================================================${_RST}\n"
printf "${_GREEN}  All scenarios complete: $(date -Iseconds)${_RST}\n"
printf "${_GREEN}  Results in: $PROJECT_ROOT/results/${_RST}\n"
printf "${_GREEN}  Master log: $MASTER_LOG${_RST}\n"
printf "${_GREEN}============================================================${_RST}\n"
