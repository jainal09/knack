#!/usr/bin/env bash
# _log.sh — Shared logging helper sourced by bench scripts.
# Provides log() and wait_with_progress() functions.
log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

# wait_with_progress SECONDS "message"
# Replaces plain `sleep` with a countdown that prints every 10s.
wait_with_progress() {
  local total="$1" msg="${2:-Waiting}"
  local elapsed=0 remaining
  while (( elapsed < total )); do
    remaining=$(( total - elapsed ))
    printf '\r[%s]   %s ... %ds remaining   ' "$(date '+%H:%M:%S')" "$msg" "$remaining"
    local chunk=10
    (( chunk > remaining )) && chunk=$remaining
    sleep "$chunk"
    elapsed=$(( elapsed + chunk ))
  done
  printf '\r[%s]   %s ... done (waited %ds)            \n' "$(date '+%H:%M:%S')" "$msg" "$total"
}

# wait_healthy CONTAINER [TIMEOUT_SEC]
# Polls docker health status until healthy or timeout. Much faster than fixed sleep.
wait_healthy() {
  local container="$1" timeout="${2:-120}"
  local elapsed=0
  log "Waiting for $container to be healthy (timeout ${timeout}s)..."
  while (( elapsed < timeout )); do
    local status
    status=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "missing")
    if [[ "$status" == "healthy" ]]; then
      log "$container is healthy (took ${elapsed}s)"
      return 0
    fi
    printf '\r[%s]   %s: %s ... %ds   ' "$(date '+%H:%M:%S')" "$container" "$status" "$elapsed"
    sleep 2
    elapsed=$(( elapsed + 2 ))
  done
  printf '\n'
  log "WARNING: $container not healthy after ${timeout}s (status: $status)"
  return 1
}
