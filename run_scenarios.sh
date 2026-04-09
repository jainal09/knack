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

# ─── Scenario definitions (defaults — overridden by --budget) ────────────────
#              NAME     CPUS   MEMORY
SCENARIOS=(
  "large    4.0   8g"
  "medium   3.0   4g"
  "small    2.0   2g"
)

# ─── Host hardware detection ────────────────────────────────────────────────
_detect_host_cpus() {
  if command -v nproc &>/dev/null; then
    nproc
  elif command -v sysctl &>/dev/null; then
    sysctl -n hw.ncpu 2>/dev/null || echo "4"
  else
    echo "4"
  fi
}

_detect_host_ram_gb() {
  if command -v free &>/dev/null; then
    free -g 2>/dev/null | awk '/^Mem:/{print $2}'
  elif command -v sysctl &>/dev/null; then
    local bytes
    bytes=$(sysctl -n hw.memsize 2>/dev/null || echo "0")
    echo $(( bytes / 1073741824 ))
  else
    echo "8"
  fi
}

# ─── Budget auto-configuration ──────────────────────────────────────────────
# Computes optimal benchmark parameters from a resource budget.
# Inputs: "16c/64g", "max", or "75%"
# Outputs: overrides SCENARIOS array + exports env vars for downstream scripts.
compute_budget() {
  local budget="$1"
  local budget_cpus budget_ram

  # ── Parse budget string ──
  if [[ "$budget" == "max" ]]; then
    budget_cpus=$(_detect_host_cpus)
    budget_ram=$(_detect_host_ram_gb)
  elif [[ "$budget" == *"%" ]]; then
    local pct="${budget%\%}"
    local host_cpus host_ram
    host_cpus=$(_detect_host_cpus)
    host_ram=$(_detect_host_ram_gb)
    budget_cpus=$(( host_cpus * pct / 100 ))
    budget_ram=$(( host_ram * pct / 100 ))
    # Clamp minimums
    [[ $budget_cpus -lt 2 ]] && budget_cpus=2
    [[ $budget_ram -lt 2 ]] && budget_ram=2
  elif [[ "$budget" =~ ^([0-9]+)c/([0-9]+)g$ ]]; then
    budget_cpus="${BASH_REMATCH[1]}"
    budget_ram="${BASH_REMATCH[2]}"
  else
    echo "ERROR: Invalid budget format '$budget'. Use: 16c/64g, max, or 75%" >&2
    exit 1
  fi

  # ── Usable resources (reserve 20% for host OS + Docker + Python workers) ──
  local usable_cpus usable_ram
  usable_cpus=$(( budget_cpus * 80 / 100 ))
  usable_ram=$(( budget_ram * 80 / 100 ))
  [[ $usable_cpus -lt 2 ]] && usable_cpus=2
  [[ $usable_ram -lt 2 ]] && usable_ram=2

  # ── Scenario CPU/RAM tiers ──
  # large  = full usable resources
  # medium = 60% CPUs, 50% RAM
  # small  = 30% CPUs, 25% RAM
  local large_cpus large_ram med_cpus med_ram small_cpus small_ram

  large_cpus="${usable_cpus}.0"
  large_ram=$(( usable_ram ))
  [[ $large_ram -lt 1 ]] && large_ram=1

  med_cpus=$(( usable_cpus * 60 / 100 ))
  [[ $med_cpus -lt 1 ]] && med_cpus=1
  med_cpus="${med_cpus}.0"
  med_ram=$(( usable_ram * 50 / 100 ))
  [[ $med_ram -lt 1 ]] && med_ram=1

  small_cpus=$(( usable_cpus * 30 / 100 ))
  [[ $small_cpus -lt 1 ]] && small_cpus=1
  small_cpus="${small_cpus}.0"
  small_ram=$(( usable_ram * 25 / 100 ))
  [[ $small_ram -lt 1 ]] && small_ram=1

  SCENARIOS=(
    "large    ${large_cpus}   ${large_ram}g"
    "medium   ${med_cpus}     ${med_ram}g"
    "small    ${small_cpus}   ${small_ram}g"
  )

  # ── Workers: up to 1 per usable core, capped at 16 ──
  local computed_producers=$usable_cpus
  [[ $computed_producers -lt 2 ]] && computed_producers=2
  [[ $computed_producers -gt 16 ]] && computed_producers=16

  # ── Payload: bigger RAM enables bigger messages ──
  local computed_payload=1024
  if [[ $usable_ram -ge 32 ]]; then
    computed_payload=16384   # 16 KB
  elif [[ $usable_ram -ge 16 ]]; then
    computed_payload=4096    #  4 KB
  elif [[ $usable_ram -ge 8 ]]; then
    computed_payload=2048    #  2 KB
  fi

  # ── Scaling CPU levels: from usable_cpus down to 0.5 ──
  local scaling_levels=""
  local level=$usable_cpus
  while [[ $level -ge 2 ]]; do
    scaling_levels="${scaling_levels}${level}.0 "
    level=$(( level - 2 ))
    [[ $level -lt 2 && $level -gt 0 ]] && { scaling_levels="${scaling_levels}${level}.0 "; break; }
  done
  scaling_levels="${scaling_levels}1.0 0.5"
  # Deduplicate (in case usable_cpus was 2 or 1)
  scaling_levels=$(echo "$scaling_levels" | tr ' ' '\n' | awk '!seen[$0]++' | tr '\n' ' ' | sed 's/ $//')

  # ── Duration: more resources → longer for stability ──
  local computed_duration=300
  if [[ $usable_cpus -ge 12 ]]; then
    computed_duration=900
  elif [[ $usable_cpus -ge 6 ]]; then
    computed_duration=600
  fi

  # ── Prepopulate: scale with RAM ──
  local computed_prepopulate=$(( usable_ram * 125000 ))
  [[ $computed_prepopulate -lt 500000 ]] && computed_prepopulate=500000
  [[ $computed_prepopulate -gt 5000000 ]] && computed_prepopulate=5000000

  # ── Export env vars (picked up by run_all.sh → env.sh → Python) ──
  # Only export if not already overridden by explicit CLI flags
  export NUM_PRODUCERS="${NUM_PRODUCERS_OVERRIDE:-$computed_producers}"
  export NUM_CONSUMERS="${NUM_CONSUMERS_OVERRIDE:-$computed_producers}"
  export PAYLOAD_BYTES="${PAYLOAD_BYTES_OVERRIDE:-$computed_payload}"
  export SCALING_CPU_LEVELS="${SCALING_CPU_LEVELS_OVERRIDE:-$scaling_levels}"
  export TEST_DURATION_SEC="${TEST_DURATION_SEC_OVERRIDE:-$computed_duration}"
  export PREPOPULATE_COUNT="${PREPOPULATE_COUNT_OVERRIDE:-$computed_prepopulate}"
  export CLI_TOTAL_MESSAGES="${CLI_TOTAL_MESSAGES_OVERRIDE:-$computed_prepopulate}"

  # ── Print the computed plan ──
  echo ""
  printf "${_CYAN}  ⚡ Auto-configured from budget: %d CPUs / %d GB RAM${_RST}\n" "$budget_cpus" "$budget_ram"
  printf "${_DIM}     (usable after 20%% host reserve: %d CPUs / %d GB RAM)${_RST}\n\n" "$usable_cpus" "$usable_ram"
  printf "${_BOLD}  ┌──────────────────────────────────────────────────────────────┐${_RST}\n"
  printf "${_BOLD}  │  SCENARIO    BROKER CPUs    BROKER RAM     WORKERS          │${_RST}\n"
  for s in "${SCENARIOS[@]}"; do
    read -r sname scpus smem <<< "$s"
    printf "  │  ${_GREEN}%-10s${_RST}  %-13s  %-13s  %-16s │\n" "$sname" "$scpus" "$smem" "$NUM_PRODUCERS"
  done
  printf "${_BOLD}  ├──────────────────────────────────────────────────────────────┤${_RST}\n"
  printf "  │  Payload: ${_CYAN}%s B${_RST}  │  Duration: ${_CYAN}%ss${_RST}  │  Reps: ${_CYAN}%s${_RST}            │\n" \
    "$PAYLOAD_BYTES" "$TEST_DURATION_SEC" "${REPS:-3}"
  printf "  │  Scaling: ${_CYAN}%s${_RST}  │\n" "$SCALING_CPU_LEVELS"
  printf "  │  Prepopulate: ${_CYAN}%s msgs${_RST}                                   │\n" \
    "$(printf '%d' "$PREPOPULATE_COUNT" | awk '{len=length($0); for(i=1;i<=len;i++){printf "%s",substr($0,i,1); if((len-i)%3==0 && i!=len) printf ","}}')"
  printf "${_BOLD}  └──────────────────────────────────────────────────────────────┘${_RST}\n"
  echo ""
}

# ─── CLI parsing ─────────────────────────────────────────────────────────────
SELECTED_SCENARIO=""
BUDGET=""
FORWARD_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scenario)
      SELECTED_SCENARIO="$2"; shift 2 ;;
    --budget)
      BUDGET="$2"; shift 2 ;;
    --list)
      if [[ -n "$BUDGET" ]]; then
        compute_budget "$BUDGET"
      fi
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

# Apply budget if specified (must happen after CLI parsing so --list works)
if [[ -n "$BUDGET" ]]; then
  compute_budget "$BUDGET"
fi

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
exec > >(trap '' INT; tee -a "$MASTER_LOG") 2>&1

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
# tee is protected from SIGINT (trap '' INT in process substitution above).
# The child runs in a subshell that also ignores INT, so only THIS script
# handles Ctrl+C. On abort we kill the child explicitly.
_CHILD_PID=""

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
        if [[ -n "$_CHILD_PID" ]] && kill -0 "$_CHILD_PID" 2>/dev/null; then
          kill -TERM -- -"$_CHILD_PID" 2>/dev/null || kill -TERM "$_CHILD_PID" 2>/dev/null || true
        fi
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

  # Run child in a subshell that ignores INT so Ctrl+C doesn't kill it.
  # (portable — works on both Linux and macOS, unlike setsid)
  ( trap '' INT; exec bash "$PROJECT_ROOT/run_all.sh" "${FORWARD_ARGS[@]}" ) &
  _CHILD_PID=$!

  # Wait for the child. Signals interrupt wait, so loop until child exits.
  _child_exit=0
  while kill -0 "$_CHILD_PID" 2>/dev/null; do
    wait "$_CHILD_PID" 2>/dev/null && _child_exit=$? || _child_exit=$?
  done
  _CHILD_PID=""

  if [[ $_child_exit -eq 0 ]]; then
    printf "${_GREEN}[%s] ✔ Scenario '${name}' PASSED. Results in results/${name}/${_RST}\n" "$(date '+%Y-%m-%d %H:%M:%S')"
  else
    printf "${_RED}[%s] ✘ Scenario '${name}' FAILED (exit code ${_child_exit}). Partial results in results/${name}/${_RST}\n" "$(date '+%Y-%m-%d %H:%M:%S')"
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
