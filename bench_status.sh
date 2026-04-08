#!/usr/bin/env bash
# bench_status.sh — Parse benchmark logs to show progress, errors, and timing
#
# Usage:
#   ./bench_status.sh                        # auto-detect latest scenario log
#   ./bench_status.sh results/scenarios_*.log # specific log file
#   ./bench_status.sh --all                  # show all scenario logs
#   ./bench_status.sh --watch                # watch mode (refresh every 5s, auto-exit)
#   ./bench_status.sh -w 10                  # watch mode with 10s interval
#
set -uo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"

# ─── Watch mode globals ──────────────────────────────────────────────────────
WATCH_MODE=0
WATCH_INTERVAL=5
_ALL_DONE=1  # assume done; parse functions set to 0 if anything is still running

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
_WHITE='\033[1;37m'

# ─── Helpers ─────────────────────────────────────────────────────────────────
strip_ansi() {
  sed 's/\x1b\[[0-9;]*m//g'
}

human_duration() {
  local secs=$1
  local h=$(( secs / 3600 ))
  local m=$(( (secs % 3600) / 60 ))
  local s=$(( secs % 60 ))
  if (( h > 0 )); then
    printf "%dh %dm %ds" "$h" "$m" "$s"
  elif (( m > 0 )); then
    printf "%dm %ds" "$m" "$s"
  else
    printf "%ds" "$s"
  fi
}

# ─── All 11 benchmark steps in order ────────────────────────────────────────
STEP_NAMES=(
  "Idle footprint"
  "Startup & Recovery"
  "Baseline Throughput"
  "Latency Under Load"
  "Memory Stress"
  "CLI-Native Throughput"
  "Consumer Throughput"
  "Simultaneous Producer+Consumer"
  "Resource Scaling"
  "Aggregating Results"
  "Generating Charts"
)

STEP_KEYS=(
  idle startup throughput latency memory_stress
  cli_throughput consumer prodcon resource_scaling
  aggregate charts
)

# ─── Count completed steps for a benchmark log ─────────────────────────────
# Sets COMPLETED_STEPS array as a side-effect. Requires $plain and $checkpoint_file.
_count_completed_steps() {
  local plain="$1"
  local checkpoint_file="$2"
  COMPLETED_STEPS=()

  for i in "${!STEP_NAMES[@]}"; do
    local name="${STEP_NAMES[$i]}"
    local key="${STEP_KEYS[$i]}"
    local escaped_name
    escaped_name=$(printf '%s' "$name" | sed 's/[+.*?[\^${}()|\\]/\\&/g')

    if [[ -f "$checkpoint_file" ]] && grep -qxF "$key" "$checkpoint_file" 2>/dev/null; then
      COMPLETED_STEPS+=("$i")
    elif grep -qP "✔\s+${escaped_name}\s+complete|✔\s+${escaped_name}\s+\[SKIPPED\]" <<< "$plain" 2>/dev/null; then
      COMPLETED_STEPS+=("$i")
    elif grep -qP "► \[[0-9]+/[0-9]+\].*${escaped_name}\s+\[SKIPPED\]" <<< "$plain" 2>/dev/null; then
      COMPLETED_STEPS+=("$i")
    fi
  done

  # Aggregation (index 9): done if step 11/11 started
  if [[ ! " ${COMPLETED_STEPS[*]} " =~ " 9 " ]]; then
    if grep -qP '► \[11/11\]' <<< "$plain"; then
      COMPLETED_STEPS+=(9)
    fi
  fi
  # Charts (index 10): done if "Benchmark complete" appears
  if [[ ! " ${COMPLETED_STEPS[*]} " =~ " 10 " ]]; then
    if grep -q "Benchmark complete" <<< "$plain"; then
      COMPLETED_STEPS+=(10)
    fi
  fi
}

# ─── Parse a single run_all.sh benchmark log ────────────────────────────────
parse_benchmark_log() {
  local log_file="$1"
  local scenario_label="${2:-}"
  local plain
  plain=$(strip_ansi < "$log_file")

  # Extract start time from banner
  local start_ts
  start_ts=$(grep -oP 'Started: \K[0-9T:.+\-]+' <<< "$plain" | head -1)

  # Extract config from banner
  local config_line
  config_line=$(grep -oP 'CPUs: .* Throughput: UNCAPPED' <<< "$plain" | head -1 || echo "")

  # Detect completed steps via checkpoint + log markers
  local bench_dir
  bench_dir=$(dirname "$log_file")
  local checkpoint_file="$bench_dir/checkpoint.log"
  _count_completed_steps "$plain" "$checkpoint_file"
  local -a completed_steps=("${COMPLETED_STEPS[@]}")

  local num_done=${#completed_steps[@]}
  local total=11

  # Determine current step (first not in completed list)
  local current_step_idx=-1
  local current_step_name=""
  for i in "${!STEP_NAMES[@]}"; do
    if [[ ! " ${completed_steps[*]} " =~ " $i " ]]; then
      current_step_idx=$i
      current_step_name="${STEP_NAMES[$i]}"
      break
    fi
  done

  # Is the run still active? Check if a bash process has this log open
  local is_active=0
  local log_age
  log_age=$(( $(date +%s) - $(stat -c %Y "$log_file") ))
  if (( log_age < 120 )); then
    local writers
    writers=$(lsof "$log_file" 2>/dev/null | awk '$4 ~ /w/ && $1 == "bash"' | wc -l)
    if (( writers > 0 )); then
      is_active=1
    fi
  fi

  # Check for "Benchmark complete" or scenario PASSED/FAILED
  local final_status="IN PROGRESS"
  if grep -q "Benchmark complete" <<< "$plain"; then
    final_status="COMPLETED"
  fi
  if grep -q "FAILED" <<< "$plain"; then
    final_status="FAILED"
  fi
  # If log seems stale, double-check for any writer (tee pipe from parent)
  if (( is_active == 0 )); then
    local any_writer
    any_writer=$(lsof "$log_file" 2>/dev/null | awk '$4 ~ /w/' | wc -l)
    (( any_writer > 0 )) && is_active=1
  fi
  if (( is_active == 0 && num_done < total )); then
    if [[ "$final_status" == "IN PROGRESS" ]]; then
      final_status="INTERRUPTED"
    fi
  fi

  # Elapsed time
  local elapsed_str=""
  local elapsed_match
  elapsed_match=$(grep -oP '\((\d+m\d+s) elapsed\)' <<< "$plain" | tail -1 || echo "")
  if [[ -n "$elapsed_match" ]]; then
    elapsed_str=$(echo "$elapsed_match" | grep -oP '\d+m\d+s')
  fi

  # Count real errors (exclude JSON fields, benchmark data lines, informational messages)
  local error_filter='("errors"|"total_errors"|_errors|error_rate|errors_per|\(0 errors\)|0 errors\)|Pre-populated|WARNING:)'
  local error_count
  error_count=$(grep -iP '(^|\s)(ERROR|Traceback|FATAL|Exception|✘)' <<< "$plain" \
    | grep -vP "$error_filter" \
    | wc -l)

  # Extract error lines (first 10)
  local -a error_lines=()
  while IFS= read -r line; do
    error_lines+=("$line")
  done < <(grep -iP '(^|\s)(ERROR|Traceback|FATAL|Exception|✘)' <<< "$plain" \
    | grep -vP "$error_filter" \
    | head -10)

  # ─── Print report ────────────────────────────────────────────────────────
  if [[ -n "$scenario_label" ]]; then
    printf "${_MAG}┌─── Scenario: ${scenario_label^^} ───${_RST}\n"
  fi
  printf "${_CYAN}│ Log: ${_RST}%s\n" "$log_file"
  if [[ -n "$start_ts" ]]; then
    printf "${_CYAN}│ Started: ${_RST}%s\n" "$start_ts"
  fi
  if [[ -n "$config_line" ]]; then
    printf "${_CYAN}│ Config: ${_RST}%s\n" "$config_line"
  fi

  # Status badge
  case "$final_status" in
    COMPLETED)   printf "${_CYAN}│ Status: ${_GREEN}● COMPLETED${_RST}\n" ;;
    FAILED)      printf "${_CYAN}│ Status: ${_RED}● FAILED${_RST}\n" ;;
    INTERRUPTED) printf "${_CYAN}│ Status: ${_YELLOW}● INTERRUPTED${_RST} (log stale ${log_age}s ago)\n" ;;
    *)
      if (( is_active )); then
        printf "${_CYAN}│ Status: ${_BLUE}● RUNNING${_RST} (log updated ${log_age}s ago)\n"
      else
        printf "${_CYAN}│ Status: ${_YELLOW}● UNKNOWN${_RST}\n"
      fi ;;
  esac

  if [[ -n "$elapsed_str" ]]; then
    printf "${_CYAN}│ Elapsed: ${_RST}%s\n" "$elapsed_str"
  fi

  # Progress bar
  local pct=$(( num_done * 100 / total ))
  local bar_len=30
  local filled=$(( num_done * bar_len / total ))
  local empty=$(( bar_len - filled ))
  printf "${_CYAN}│ Progress: ${_RST}["
  (( filled > 0 )) && printf "${_GREEN}%0.s█" $(seq 1 $filled)
  (( empty > 0 )) && printf "${_DIM}%0.s░" $(seq 1 $empty)
  printf "${_RST}] %d/%d (%d%%)\n" "$num_done" "$total" "$pct"

  # Step-by-step status
  printf "${_CYAN}│${_RST}\n"
  printf "${_CYAN}│ Steps:${_RST}\n"
  for i in "${!STEP_NAMES[@]}"; do
    local name="${STEP_NAMES[$i]}"
    local step_num=$(( i + 1 ))
    if [[ " ${completed_steps[*]} " =~ " $i " ]]; then
      printf "${_CYAN}│${_RST}   ${_GREEN}✔${_RST} [%2d/11] %s\n" "$step_num" "$name"
    elif (( i == current_step_idx )); then
      if (( is_active )); then
        printf "${_CYAN}│${_RST}   ${_YELLOW}▶${_RST} [%2d/11] %s ${_YELLOW}← running${_RST}\n" "$step_num" "$name"
      else
        printf "${_CYAN}│${_RST}   ${_RED}✘${_RST} [%2d/11] %s ${_RED}← stopped here${_RST}\n" "$step_num" "$name"
      fi
    else
      printf "${_CYAN}│${_RST}   ${_DIM}○${_RST} [%2d/11] %s\n" "$step_num" "$name"
    fi
  done

  # Remaining steps
  local remaining=$(( total - num_done ))
  if (( remaining > 0 && num_done < total )); then
    printf "${_CYAN}│${_RST}\n"
    printf "${_CYAN}│ Remaining: ${_YELLOW}%d step(s)${_RST}\n" "$remaining"
  fi

  # Errors
  printf "${_CYAN}│${_RST}\n"
  if (( error_count == 0 )); then
    printf "${_CYAN}│ Errors: ${_GREEN}0${_RST}\n"
  else
    printf "${_CYAN}│ Errors: ${_RED}%d${_RST}\n" "$error_count"
    for line in "${error_lines[@]}"; do
      printf "${_CYAN}│${_RST}   ${_RED}▸${_RST} %.120s\n" "$line"
    done
    if (( error_count > 10 )); then
      printf "${_CYAN}│${_RST}   ${_DIM}... and %d more${_RST}\n" $(( error_count - 10 ))
    fi
  fi

  printf "${_CYAN}└────────────────────────────────────────────${_RST}\n"

  # Track whether this run is still in progress for watch mode
  if [[ "$final_status" != "COMPLETED" && "$final_status" != "FAILED" ]]; then
    _ALL_DONE=0
  fi
}

# ─── Parse a scenario runner log (run_scenarios.sh) ──────────────────────────
parse_scenario_log() {
  local log_file="$1"
  local plain
  plain=$(strip_ansi < "$log_file")

  # Extract scenario list
  local scenarios_line
  scenarios_line=$(grep -oP 'Scenarios: \K.*' <<< "$plain" | head -1 || echo "")
  local start_ts
  start_ts=$(grep -oP 'Started: \K[0-9T:.+\-]+' <<< "$plain" | head -1 || echo "")

  # Log age for active detection — check if a writer process actually has the file open
  local log_age
  log_age=$(( $(date +%s) - $(stat -c %Y "$log_file") ))
  local is_active=0
  # Check if any bash process (not just tee) has this log open for writing
  if (( log_age < 120 )); then
    local writers
    writers=$(lsof "$log_file" 2>/dev/null | awk '$4 ~ /w/ && $1 == "bash"' | wc -l)
    if (( writers > 0 )); then
      is_active=1
    fi
  fi

  # Parse per-scenario results first (needed to determine overall status)
  local -a scenario_names=()
  local -a scenario_statuses=()
  while IFS= read -r line; do
    local name
    name=$(echo "$line" | grep -oP 'SCENARIO: \K\w+')
    scenario_names+=("$name")
  done < <(grep 'SCENARIO:' <<< "$plain")

  # For each scenario, find PASSED/FAILED or check sub-log
  local any_child_running=0
  for name in "${scenario_names[@]}"; do
    local lname="${name,,}"  # lowercase
    if grep -q "Scenario '${lname}' PASSED" <<< "$plain"; then
      scenario_statuses+=("PASSED")
    elif grep -q "Scenario '${lname}' FAILED" <<< "$plain"; then
      scenario_statuses+=("FAILED")
    else
      # Check if this child scenario's benchmark log is actively being written
      local child_log
      child_log=$(ls -t "$PROJECT_ROOT/results/${lname}"/benchmark_*.log 2>/dev/null | head -1 || echo "")
      if [[ -n "$child_log" && -f "$child_log" ]]; then
        local child_age
        child_age=$(( $(date +%s) - $(stat -c %Y "$child_log") ))
        local child_writers
        child_writers=$(lsof "$child_log" 2>/dev/null | awk '$4 ~ /w/' | wc -l)
        if (( child_age < 120 && child_writers > 0 )); then
          any_child_running=1
        fi
      fi
      scenario_statuses+=("IN_PROGRESS")
    fi
  done

  # Determine overall status
  local overall_status
  if grep -q "All scenarios complete" <<< "$plain"; then
    overall_status="ALL_COMPLETE"
  elif (( is_active || any_child_running )); then
    overall_status="RUNNING"
  else
    overall_status="INTERRUPTED"
  fi

  printf "\n${_MAG}════════════════════════════════════════════════════════════${_RST}\n"
  printf "${_MAG}  Benchmark Run Summary${_RST}\n"
  printf "${_CYAN}  Log: ${_RST}%s\n" "$log_file"
  if [[ -n "$start_ts" ]]; then
    printf "${_CYAN}  Started: ${_RST}%s\n" "$start_ts"
  fi
  case "$overall_status" in
    RUNNING)      printf "${_CYAN}  Status: ${_BLUE}● RUNNING${_RST}\n" ;;
    ALL_COMPLETE) printf "${_CYAN}  Status: ${_GREEN}● ALL COMPLETE${_RST}\n" ;;
    INTERRUPTED)  printf "${_CYAN}  Status: ${_YELLOW}● INTERRUPTED${_RST}\n" ;;
  esac
  printf "${_MAG}════════════════════════════════════════════════════════════${_RST}\n\n"

  # Scenario overview table with timing
  printf "${_BOLD}  %-12s %-14s %-8s %s${_RST}\n" "Scenario" "Status" "Steps" "Elapsed"
  printf "  %-12s %-14s %-8s %s\n" "────────" "──────" "─────" "───────"
  for i in "${!scenario_names[@]}"; do
    local name="${scenario_names[$i]}"
    local status="${scenario_statuses[$i]}"
    local lname="${name,,}"

    # Get elapsed time + step count from per-scenario benchmark log
    local bench_dir="$PROJECT_ROOT/results/${lname}"
    local bench_log elapsed_display steps_display
    bench_log=$(ls -t "${bench_dir}"/benchmark_*.log 2>/dev/null | head -1 || echo "")
    elapsed_display="-"
    steps_display="-"
    if [[ -n "$bench_log" && -f "$bench_log" ]]; then
      local bench_plain
      bench_plain=$(strip_ansi < "$bench_log")
      # Get last elapsed marker
      local em
      em=$(grep -oP '\(\K\d+m\d+s(?= elapsed)' <<< "$bench_plain" | tail -1 || echo "")
      [[ -n "$em" ]] && elapsed_display="$em"
      # Count completed steps using shared helper
      _count_completed_steps "$bench_plain" "$bench_dir/checkpoint.log"
      steps_display="${#COMPLETED_STEPS[@]}/11"
    fi

    case "$status" in
      PASSED)      printf "    ${_GREEN}✔${_RST} %-10s ${_GREEN}%-12s${_RST} %-8s %s\n" "$name" "PASSED" "$steps_display" "$elapsed_display" ;;
      FAILED)      printf "    ${_RED}✘${_RST} %-10s ${_RED}%-12s${_RST} %-8s %s\n" "$name" "FAILED" "$steps_display" "$elapsed_display" ;;
      IN_PROGRESS) printf "    ${_YELLOW}▶${_RST} %-10s ${_YELLOW}%-12s${_RST} %-8s %s\n" "$name" "IN PROGRESS" "$steps_display" "$elapsed_display" ;;
    esac
  done
  echo ""

  # Parse each scenario's per-step benchmark log
  for name in "${scenario_names[@]}"; do
    local lname="${name,,}"
    local bench_dir="$PROJECT_ROOT/results/${lname}"
    local bench_log
    bench_log=$(ls -t "${bench_dir}"/benchmark_*.log 2>/dev/null | head -1 || echo "")
    if [[ -n "$bench_log" && -f "$bench_log" ]]; then
      parse_benchmark_log "$bench_log" "$lname"
      echo ""
    else
      printf "${_YELLOW}  No benchmark log found for scenario '%s' in %s${_RST}\n\n" "$lname" "$bench_dir"
    fi
  done

  # Cross-scenario comparison status
  if grep -q "cross-scenario comparison" <<< "$plain"; then
    if grep -q "All scenarios complete" <<< "$plain"; then
      printf "${_GREEN}  ✔ Cross-scenario comparison charts generated${_RST}\n"
    else
      printf "${_YELLOW}  ▶ Cross-scenario comparison charts pending${_RST}\n"
    fi
  fi

  # Total elapsed (from first scenario start to last log entry)
  local first_ts last_ts
  first_ts=$(grep -oP '^\[\K[0-9-]+ [0-9:]+' <<< "$plain" | head -1 || echo "")
  last_ts=$(grep -oP '^\[\K[0-9-]+ [0-9:]+' <<< "$plain" | tail -1 || echo "")
  if [[ -n "$first_ts" && -n "$last_ts" ]]; then
    local first_epoch last_epoch
    first_epoch=$(date -d "$first_ts" +%s 2>/dev/null || echo 0)
    last_epoch=$(date -d "$last_ts" +%s 2>/dev/null || echo 0)
    if (( first_epoch > 0 && last_epoch > 0 )); then
      local total_elapsed=$(( last_epoch - first_epoch ))
      printf "\n${_CYAN}  Total wall time: ${_WHITE}%s${_RST}\n" "$(human_duration $total_elapsed)"
    fi
  fi

  echo ""

  # Track whether this run is still in progress for watch mode
  if [[ "$overall_status" != "ALL_COMPLETE" ]]; then
    _ALL_DONE=0
  fi
  # Also mark not done if any individual scenario failed (but still running others)
  for s in "${scenario_statuses[@]}"; do
    if [[ "$s" == "IN_PROGRESS" ]]; then
      _ALL_DONE=0
      break
    fi
  done
}

# ─── Watch-mode animation engine ────────────────────────────────────────────
_R=0 _G=0 _B=0

_hue_to_rgb() {
  local hue=$(( $1 % 360 ))
  local sector=$(( hue / 60 ))
  local f=$(( hue % 60 ))
  local rise=$(( 255 * f / 60 ))
  local fall=$(( 255 * (60 - f) / 60 ))
  case $sector in
    0) _R=255; _G=$rise; _B=0    ;;
    1) _R=$fall; _G=255;  _B=0    ;;
    2) _R=0;    _G=255;  _B=$rise ;;
    3) _R=0;    _G=$fall; _B=255  ;;
    4) _R=$rise; _G=0;    _B=255  ;;
    *) _R=255;  _G=0;    _B=$fall ;;
  esac
}

# Render one animated frame of the live spinner line (in-place via \r)
# Uses segment-based gradient (no inner loops) for ~30fps in bash.
_render_watch_line() {
  local frame=$1 label="$2" done=$3 total=$4 start_epoch=${5:-0}
  local cols
  cols=$(tput cols 2>/dev/null || echo 80)

  local -a spin=( '⣾' '⣽' '⣻' '⢿' '⡿' '⣟' '⣯' '⣷' )

  # Truncate label to fit
  local max_label=$(( cols / 2 ))
  local dlabel="$label"
  (( ${#dlabel} > max_label )) && dlabel="${dlabel:0:$((max_label-2))}…"

  # Live elapsed timer (updates every frame, no subshell)
  local elapsed_str=""
  if (( start_epoch > 0 )); then
    local now_e
    printf -v now_e '%(%s)T' -1
    local el=$(( now_e - start_epoch ))
    elapsed_str=" $(human_duration "$el")"
  fi

  # Progress bar sizing
  local counter="[${done}/${total}]"
  local prefix_len=$(( 4 + ${#dlabel} + 1 + ${#counter} + ${#elapsed_str} + 1 ))
  local bar_w=$(( cols - prefix_len - 2 ))
  (( bar_w < 8 )) && bar_w=8
  (( bar_w > 50 )) && bar_w=50

  local fill=$(( total > 0 ? done * bar_w / total : 0 ))
  (( fill > bar_w )) && fill=$bar_w

  # Scrolling rainbow phase
  local phase=$(( frame * 4 ))
  # Shimmer: one segment gets a brightness boost, cycling across
  local n_seg=8
  local shimmer_seg=$(( (frame / 3) % n_seg ))
  local bar="" _tmp

  for (( s = 0; s < n_seg; s++ )); do
    local seg_start=$(( s * bar_w / n_seg ))
    local seg_end=$(( (s + 1) * bar_w / n_seg ))
    local seg_len=$(( seg_end - seg_start ))
    (( seg_len <= 0 )) && continue

    local hue=$(( (s * 360 / n_seg + phase) % 360 ))
    _hue_to_rgb "$hue"

    # Shimmer boost on the traveling highlight segment
    if (( s == shimmer_seg )); then
      (( _R = _R + 60 > 255 ? 255 : _R + 60 ))
      (( _G = _G + 60 > 255 ? 255 : _G + 60 ))
      (( _B = _B + 60 > 255 ? 255 : _B + 60 ))
    fi

    if (( fill >= seg_end )); then
      # Entire segment is filled
      bar+="\e[38;2;${_R};${_G};${_B}m"
      printf -v _tmp '%*s' "$seg_len" ''; bar+="${_tmp// /█}"
    elif (( fill <= seg_start )); then
      # Entire segment is empty — subtle pulsing dim
      local dim=$(( 30 + (frame + s) % 3 * 10 ))
      bar+="\e[38;2;${dim};${dim};${dim}m"
      printf -v _tmp '%*s' "$seg_len" ''; bar+="${_tmp// /░}"
    else
      # Edge falls within this segment: filled ▓ empty
      local n_fill=$(( fill - seg_start ))
      local n_empty=$(( seg_end - fill - 1 ))
      if (( n_fill > 0 )); then
        bar+="\e[38;2;${_R};${_G};${_B}m"
        printf -v _tmp '%*s' "$n_fill" ''; bar+="${_tmp// /█}"
      fi
      bar+="\e[38;2;${_R};${_G};${_B}m▓"
      if (( n_empty > 0 )); then
        local dim=$(( 30 + (frame + s) % 3 * 10 ))
        bar+="\e[38;2;${dim};${dim};${dim}m"
        printf -v _tmp '%*s' "$n_empty" ''; bar+="${_tmp// /░}"
      fi
    fi
  done

  # Spinner with rainbow color cycling
  local sh=$(( frame * 25 % 360 ))
  _hue_to_rgb "$sh"
  local si=$(( frame % ${#spin[@]} ))

  # Breathing color for label text
  local breath=$(( frame % 40 ))
  local bright
  if (( breath < 20 )); then
    bright=$(( 130 + breath * 125 / 20 ))
  else
    bright=$(( 130 + (40 - breath) * 125 / 20 ))
  fi

  printf '\r\e[K \e[38;2;%d;%d;%dm%s\e[0m \e[38;2;%d;%d;%dm%s\e[0m \e[2m%s\e[0m\e[38;2;255;180;60m%s\e[0m %b\e[0m' \
    "$_R" "$_G" "$_B" "${spin[$si]}" \
    "$bright" "$bright" "$bright" "$dlabel" \
    "$counter" \
    "$elapsed_str" \
    "$bar"
}

# Celebration animation on completion
_celebrate() {
  local cols
  cols=$(tput cols 2>/dev/null || echo 80)
  local msg="✨ All benchmarks complete! ✨"
  local msg_len=${#msg}
  local pad=$(( (cols - msg_len) / 2 ))
  (( pad < 0 )) && pad=0

  printf '\e[?25l'
  # Rainbow flash across the text
  for (( f = 0; f < 24; f++ )); do
    local hue=$(( f * 15 % 360 ))
    _hue_to_rgb "$hue"
    printf '\r\e[K%*s\e[1;38;2;%d;%d;%dm%s\e[0m' "$pad" "" "$_R" "$_G" "$_B" "$msg"
    sleep 0.04
  done
  # Settle on green
  printf '\r\e[K%*s\e[1;38;2;80;255;120m%s\e[0m\n' "$pad" "" "$msg"
  printf '\e[?25h'
}

# ─── Smart watch mode ───────────────────────────────────────────────────────
# Prints full status once, then only shows new step completions + animated
# spinner line in-place. Exits automatically when everything finishes.
_watch_mode() {
  local -a log_files=("$@")

  # 1) Full static display (stays on screen forever)
  _display_status
  if (( _ALL_DONE )); then
    _celebrate
    return 0
  fi

  printf "\n${_DIM}──── Live watch (every %ds) · Ctrl-C to stop ─────────────────${_RST}\n\n" "$WATCH_INTERVAL"

  # 2) State tracking — associative arrays survive across iterations
  declare -A _wprev           # "label:step_idx" -> 1 if already seen completed
  declare -A _wprev_scenario  # "scenario_name" -> 1 if PASSED/FAILED already printed

  # Current running info (displayed on the spinner line)
  local _wlabel="…" _wdone=0 _wtotal=11 _wstart_epoch=0

  # ── Check one benchmark log for new step completions ──
  # Prints timestamped lines for any newly completed steps.
  # Sets _wlabel/_wdone/_wtotal if there's still work to do.
  # Returns 0 if this benchmark is fully done, 1 if still running.
  _wcheck_bench() {
    local bench_log="$1" label="$2"
    local bench_dir
    bench_dir=$(dirname "$bench_log")
    local bp
    bp=$(strip_ansi < "$bench_log")
    _count_completed_steps "$bp" "$bench_dir/checkpoint.log"
    local done_count=${#COMPLETED_STEPS[@]}

    # Detect new completions
    for idx in "${COMPLETED_STEPS[@]}"; do
      local key="${label}:${idx}"
      if [[ -z "${_wprev[$key]+x}" ]]; then
        _wprev["$key"]=1
        local step_name="${STEP_NAMES[$idx]}"
        local ts
        ts=$(date +%H:%M:%S)
        # Clear spinner line, emit completion, spinner will redraw next frame
        printf '\r\e[K'
        printf " ${_DIM}[%s]${_RST} ${_GREEN}✔${_RST} %s" "$ts" "$step_name"
        [[ -n "$label" ]] && printf " ${_DIM}(%s)${_RST}" "$label"
        printf "\n"
      fi
    done

    # Find current (first non-completed) step
    local current_idx=-1
    for i in "${!STEP_NAMES[@]}"; do
      local key2="${label}:${i}"
      if [[ -z "${_wprev[$key2]+x}" ]]; then
        current_idx=$i
        break
      fi
    done

    if (( current_idx >= 0 )); then
      _wlabel="${STEP_NAMES[$current_idx]}"
      [[ -n "$label" ]] && _wlabel="$label ▸ $_wlabel"
      _wdone=$done_count
      _wtotal=11
      # Extract start timestamp for live elapsed display
      local start_ts
      start_ts=$(grep -oP 'Started: \K[0-9T:.+\-]+' <<< "$bp" | head -1)
      if [[ -n "$start_ts" ]]; then
        _wstart_epoch=$(date -d "$start_ts" +%s 2>/dev/null || echo 0)
      fi
      return 1 # still running
    fi

    # All steps done
    _wdone=$done_count
    if grep -q "Benchmark complete" <<< "$bp" || grep -q "FAILED" <<< "$bp"; then
      return 0
    fi
    return 1
  }

  # ── Scan all watched logs, detect changes, return 0 if everything done ──
  _wscan_all() {
    local all_done=1

    for lf in "${log_files[@]}"; do
      local plain
      plain=$(strip_ansi < "$lf")

      if grep -q "Multi-Scenario Benchmark Runner" <<< "$plain"; then
        # Scenario log — iterate each scenario's benchmark log
        while IFS= read -r _wline; do
          local sname
          sname=$(echo "$_wline" | grep -oP 'SCENARIO: \K\w+')
          local lname="${sname,,}"

          # Scenario-level PASSED/FAILED announcements
          if grep -q "Scenario '${lname}' PASSED" <<< "$plain"; then
            if [[ -z "${_wprev_scenario[$lname]+x}" ]]; then
              _wprev_scenario["$lname"]=1
              printf '\r\e[K'
              printf " ${_DIM}[%s]${_RST} ${_GREEN}✔ Scenario %s PASSED${_RST}\n" "$(date +%H:%M:%S)" "${lname^^}"
            fi
          elif grep -q "Scenario '${lname}' FAILED" <<< "$plain"; then
            if [[ -z "${_wprev_scenario[$lname]+x}" ]]; then
              _wprev_scenario["$lname"]=1
              printf '\r\e[K'
              printf " ${_DIM}[%s]${_RST} ${_RED}✘ Scenario %s FAILED${_RST}\n" "$(date +%H:%M:%S)" "${lname^^}"
            fi
          fi

          # Per-step progress
          local bench_log
          bench_log=$(ls -t "$PROJECT_ROOT/results/${lname}"/benchmark_*.log 2>/dev/null | head -1)
          if [[ -n "$bench_log" && -f "$bench_log" ]]; then
            _wcheck_bench "$bench_log" "$lname" || all_done=0
          else
            all_done=0
          fi
        done < <(grep 'SCENARIO:' <<< "$plain")

        if ! grep -q "All scenarios complete" <<< "$plain"; then
          all_done=0
        fi
      else
        # Single benchmark log
        _wcheck_bench "$lf" "" || all_done=0
      fi
    done

    return $(( !all_done ))  # 0 = all done
  }

  # 3) Seed initial state (mark everything already completed, suppress output)
  {
    _wscan_all
  } > /dev/null 2>&1

  # 4) Enter animated watch loop
  printf '\e[?25l' # hide cursor
  trap 'printf "\e[?25h\r\e[K\n${_DIM}Watch stopped.${_RST}\n"; exit 0' INT

  local frame=0 last_check
  printf -v last_check '%(%s)T' -1

  while true; do
    local now_s
    printf -v now_s '%(%s)T' -1

    # Periodic log re-check
    if (( now_s - last_check >= WATCH_INTERVAL )); then
      last_check=$now_s
      if _wscan_all; then
        printf '\r\e[K'
        printf '\e[?25h'
        _celebrate
        return 0
      fi
    fi

    # Render animated spinner line in-place
    _render_watch_line "$frame" "$_wlabel" "$_wdone" "$_wtotal" "$_wstart_epoch"
    (( frame++ ))
    sleep 0.06
  done
}

# ─── Main ────────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  echo "Usage: $0 [OPTIONS] [LOG_FILE]"
  echo ""
  echo "Parse benchmark logs and show progress, errors, and timing."
  echo ""
  echo "Options:"
  echo "  (no args)        Auto-detect latest scenario log in results/"
  echo "  --all            Show all scenario logs"
  echo "  --latest N       Show latest N logs (default: 1)"
  echo "  -w, --watch [N]  Watch mode: refresh every N seconds (default: 5),"
  echo "                   auto-exit when all benchmarks complete"
  echo "  FILE             Parse a specific log file"
  echo "  -h, --help       Show this help"
  exit 0
fi

SHOW_COUNT=1

# Parse flags (order-independent)
while [[ $# -gt 0 ]]; do
  case "${1:-}" in
    -w|--watch)
      WATCH_MODE=1
      # Optional interval argument (next arg if it's a number)
      if [[ "${2:-}" =~ ^[0-9]+$ ]]; then
        WATCH_INTERVAL="$2"
        shift
      fi
      shift
      ;;
    --all)
      SHOW_COUNT=999
      shift
      ;;
    --latest)
      SHOW_COUNT="${2:-1}"
      shift 2
      ;;
    *)
      break
      ;;
  esac
done

WATCH_LOGS=()

if [[ $# -gt 0 && -f "$1" ]]; then
  # Specific file given
  log_file="$1"
  WATCH_LOGS=("$log_file")
  _display_status() {
    _ALL_DONE=1
    if grep -q "Multi-Scenario Benchmark Runner" <(strip_ansi < "$log_file"); then
      parse_scenario_log "$log_file"
    else
      parse_benchmark_log "$log_file"
    fi
  }
else
  # Auto-detect from results/
  RESULTS_DIR="${RESULTS_DIR:-$PROJECT_ROOT/results}"
  _find_logs() {
    logs=()
    while IFS= read -r f; do
      logs+=("$f")
    done < <(ls -t "${RESULTS_DIR}"/scenarios_*.log 2>/dev/null | head -"$SHOW_COUNT")

    if [[ ${#logs[@]} -eq 0 ]]; then
      while IFS= read -r f; do
        logs+=("$f")
      done < <(ls -t "${RESULTS_DIR}"/benchmark_*.log 2>/dev/null | head -"$SHOW_COUNT")
    fi
  }
  _find_logs

  if [[ ${#logs[@]} -eq 0 ]]; then
    echo "No benchmark logs found in $RESULTS_DIR/"
    echo "Run ./run_all.sh or ./run_scenarios.sh first."
    exit 1
  fi

  WATCH_LOGS=("${logs[@]}")

  _display_status() {
    _ALL_DONE=1
    _find_logs
    for log_file in "${logs[@]}"; do
      if grep -q "Multi-Scenario Benchmark Runner" <(strip_ansi < "$log_file"); then
        parse_scenario_log "$log_file"
      else
        parse_benchmark_log "$log_file"
      fi
    done
  }
fi

# ─── Execute (single-shot or watch mode) ─────────────────────────────────────
if (( WATCH_MODE )); then
  _watch_mode "${WATCH_LOGS[@]}"
else
  _display_status
fi
