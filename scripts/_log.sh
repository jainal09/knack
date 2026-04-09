#!/usr/bin/env bash
# _log.sh — Shared logging helper sourced by bench scripts.
# Provides colored log(), wait_with_progress(), wait_healthy() functions.

# ─── ANSI colors ─────────────────────────────────────────────────────────────
_RST='\033[0m'
_BOLD='\033[1m'
_DIM='\033[2m'
_RED='\033[1;31m'
_GREEN='\033[1;32m'
_YELLOW='\033[1;33m'
_CYAN='\033[1;36m'
_BLUE='\033[1;34m'
_MAG='\033[1;35m'

# Spinner output target: /dev/tty if available (interactive), else stderr.
# Background processes launched via ( ... ) & may not have a controlling tty.
if [[ -w /dev/tty ]] 2>/dev/null; then
  _TTY_OUT=/dev/tty
else
  _TTY_OUT=/dev/stderr
fi

log()     { printf "${_DIM}[%s]${_RST} %s\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
log_step()  { printf "${_CYAN}[%s] ► %s${_RST}\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
log_ok()    { printf "${_GREEN}[%s] ✔ %s${_RST}\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
log_warn()  { printf "${_YELLOW}[%s] ⚠ %s${_RST}\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
log_err()   { printf "${_RED}[%s] ✘ %s${_RST}\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
log_info()  { printf "${_BLUE}[%s] ℹ %s${_RST}\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

# Spinner frames — wide bar style, unmissable
_SPIN_FRAMES=('[    =   ]' '[   ==   ]' '[  ===   ]' '[ ====   ]' '[  ====  ]' '[   ==== ]' '[   ===  ]' '[   ==   ]' '[   =    ]')
_SPINNER_MSG_FILE=""
_SPINNER_PID=""

# _start_spinner "message"  — launches background spinner, sets _SPINNER_PID
# Spinner reads message from a temp file so it can be updated without restart.
_start_spinner() {
  _SPINNER_MSG_FILE=$(mktemp /tmp/.spinner_msg.XXXXXX)
  echo "$1" > "$_SPINNER_MSG_FILE"
  local msgfile="$_SPINNER_MSG_FILE"
  local tty_out="$_TTY_OUT"
  (
    local i=0 len=${#_SPIN_FRAMES[@]}
    while true; do
      local frame="${_SPIN_FRAMES[i % len]}"
      local msg
      msg=$(cat "$msgfile" 2>/dev/null) || msg=""
      printf "\r  \033[1;36m%s\033[0m \033[1;97m%s\033[0m        " "$frame" "$msg" > "$tty_out"
      sleep 0.12
      i=$(( i + 1 ))
    done
  ) &
  _SPINNER_PID=$!
}

# _update_spinner "new message"  — update text without restarting
_update_spinner() {
  [[ -n "$_SPINNER_MSG_FILE" ]] && echo "$1" > "$_SPINNER_MSG_FILE"
}

# _stop_spinner "done message"
_stop_spinner() {
  kill "$_SPINNER_PID" 2>/dev/null; wait "$_SPINNER_PID" 2>/dev/null || true
  rm -f "$_SPINNER_MSG_FILE" 2>/dev/null
  printf "\r  \033[1;32m✔ %s\033[0m                                                  \n" "$1" > "$_TTY_OUT"
}

# wait_with_progress SECONDS "message"
# Countdown with a spinning indicator, updates every second.
wait_with_progress() {
  local total="$1" msg="${2:-Waiting}"
  local elapsed=0 remaining
  _start_spinner "$msg ... ${total}s remaining"
  while (( elapsed < total )); do
    remaining=$(( total - elapsed ))
    _update_spinner "$msg ... ${remaining}s remaining"
    sleep 1
    elapsed=$(( elapsed + 1 ))
  done
  _stop_spinner "$msg done (waited ${total}s)"
  log "$msg done (waited ${total}s)"
}

# wait_healthy CONTAINER [TIMEOUT_SEC]
# Polls docker health status until healthy or timeout. Much faster than fixed sleep.
wait_healthy() {
  local container="$1" timeout="${2:-120}"
  local elapsed=0
  log_info "Waiting for $container to be healthy (timeout ${timeout}s)..."
  _start_spinner "$container: starting ..."
  while (( elapsed < timeout )); do
    local status
    status=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "missing")
    if [[ "$status" == "healthy" ]]; then
      _stop_spinner "$container is healthy (took ${elapsed}s)"
      return 0
    fi
    _update_spinner "$container: $status ... ${elapsed}s"
    sleep 2
    elapsed=$(( elapsed + 2 ))
  done
  _stop_spinner "$container not healthy after ${timeout}s (status: $status)"
  log_warn "$container not healthy after ${timeout}s — continuing anyway"
  return 0
}
