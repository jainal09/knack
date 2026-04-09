#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Knack installer
# curl -fsSL https://raw.githubusercontent.com/jainal09/knack/main/install.sh | bash
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

_RST='\033[0m'; _BOLD='\033[1m'; _DIM='\033[2m'
_CYAN='\033[36m'; _MAG='\033[35m'; _RED='\033[31m'; _GREEN='\033[32m'; _YELLOW='\033[33m'

REPO_URL="https://github.com/jainal09/knack.git"
INSTALL_DIR="${KNACK_INSTALL_DIR:-$HOME/.knack}"

printf "${_MAG}"
cat <<'EOF'
  _  __                 _
 | |/ /_ __   __ _  ___| | __
 | ' /| '_ \ / _` |/ __| |/ /
 | . \| | | | (_| | (__|   <
 |_|\_\_| |_|\__,_|\___|_|\_\
EOF
printf "${_RST}"
printf "${_DIM}  Kafka + NATS Benchmark Suite — Installer${_RST}\n\n"

# ─── Prerequisite checks ────────────────────────────────────────────────────
check_cmd() {
  if ! command -v "$1" &>/dev/null; then
    printf "${_RED}Missing prerequisite: %s${_RST}\n" "$1"
    printf "  %s\n" "$2"
    return 1
  fi
  printf "  ${_GREEN}✓${_RST} %s\n" "$1"
}

printf "${_BOLD}Checking prerequisites...${_RST}\n"
fail=0
check_cmd docker "Install from https://docs.docker.com/get-docker/" || fail=1
check_cmd uv     "Install with: curl -LsSf https://astral.sh/uv/install.sh | sh" || fail=1
check_cmd nc     "Install netcat (usually pre-installed on macOS/Linux)" || fail=1

if [[ "$fail" -ne 0 ]]; then
  printf "\n${_RED}Please install the missing prerequisites and re-run.${_RST}\n"
  exit 1
fi
printf "\n"

# ─── Install optional benchmark tools ─────────────────────────────────────
printf "${_BOLD}Installing benchmark CLI tools...${_RST}\n"

# kcat / kafkacat
if command -v kcat &>/dev/null || command -v kafkacat &>/dev/null; then
  printf "  ${_GREEN}✓${_RST} kcat (already installed)\n"
else
  if [[ "$(uname)" == "Darwin" ]]; then
    if command -v brew &>/dev/null; then
      printf "  ${_CYAN}Installing kcat via Homebrew...${_RST}\n"
      brew install kcat 2>/dev/null && printf "  ${_GREEN}✓${_RST} kcat\n" \
        || printf "  ${_YELLOW}⚠${_RST} kcat install failed — CLI throughput benchmarks will be skipped\n"
    else
      printf "  ${_YELLOW}⚠${_RST} kcat — Homebrew not found, install manually: brew install kcat\n"
    fi
  else
    printf "  ${_CYAN}Installing kafkacat via apt...${_RST}\n"
    sudo apt-get install -y kafkacat 2>/dev/null && printf "  ${_GREEN}✓${_RST} kafkacat\n" \
      || printf "  ${_YELLOW}⚠${_RST} kafkacat install failed — CLI throughput benchmarks will be skipped\n"
  fi
fi

# nats CLI
if command -v nats &>/dev/null; then
  printf "  ${_GREEN}✓${_RST} nats CLI (already installed)\n"
else
  if [[ "$(uname)" == "Darwin" ]]; then
    if command -v brew &>/dev/null; then
      printf "  ${_CYAN}Installing nats CLI via Homebrew...${_RST}\n"
      brew install nats-io/nats-tools/nats 2>/dev/null && printf "  ${_GREEN}✓${_RST} nats CLI\n" \
        || printf "  ${_YELLOW}⚠${_RST} nats CLI install failed — NATS CLI benchmarks will be skipped\n"
    else
      printf "  ${_YELLOW}⚠${_RST} nats CLI — Homebrew not found, install manually: brew install nats-io/nats-tools/nats\n"
    fi
  else
    printf "  ${_CYAN}Installing nats CLI...${_RST}\n"
    if curl -sf https://binaries.nats.dev/nats-io/natscli/nats@latest 2>/dev/null | sh 2>/dev/null; then
      # The nats install script drops the binary in the current directory
      if [[ -f "./nats" ]]; then
        mkdir -p "$HOME/.local/bin"
        mv ./nats "$HOME/.local/bin/nats"
        chmod +x "$HOME/.local/bin/nats"
      fi
      printf "  ${_GREEN}✓${_RST} nats CLI\n"
    else
      printf "  ${_YELLOW}⚠${_RST} nats CLI install failed — NATS CLI benchmarks will be skipped\n"
    fi
  fi
fi

printf "\n"

# ─── Clone or detect existing repo ──────────────────────────────────────────
if [[ -f "./knack" && -f "./pyproject.toml" ]]; then
  printf "${_CYAN}Detected existing Knack repo in current directory.${_RST}\n"
  INSTALL_DIR="$(pwd)"
elif [[ -d "$INSTALL_DIR" && -f "$INSTALL_DIR/knack" ]]; then
  printf "${_CYAN}Knack already installed at %s — updating...${_RST}\n" "$INSTALL_DIR"
  git -C "$INSTALL_DIR" pull --ff-only || true
else
  printf "${_CYAN}Cloning Knack to %s ...${_RST}\n" "$INSTALL_DIR"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi
printf "\n"

# ─── Install Python dependencies ────────────────────────────────────────────
printf "${_BOLD}Installing Python dependencies...${_RST}\n"
(cd "$INSTALL_DIR" && uv sync)
printf "\n"

# ─── Symlink to PATH ────────────────────────────────────────────────────────
BIN_DIR="/usr/local/bin"
if [[ ! -w "$BIN_DIR" ]]; then
  BIN_DIR="$HOME/.local/bin"
  mkdir -p "$BIN_DIR"
fi

LINK="$BIN_DIR/knack"
if [[ -L "$LINK" || -f "$LINK" ]]; then
  rm -f "$LINK"
fi
ln -s "$INSTALL_DIR/knack" "$LINK"
printf "${_GREEN}Symlinked:${_RST} %s → %s\n\n" "$LINK" "$INSTALL_DIR/knack"

# ─── Verify PATH ────────────────────────────────────────────────────────────
if ! echo "$PATH" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
  printf "${_YELLOW}Note:${_RST} %s is not in your PATH.\n" "$BIN_DIR"
  printf "  Add this to your shell profile:\n"
  printf "  ${_DIM}export PATH=\"%s:\$PATH\"${_RST}\n\n" "$BIN_DIR"
fi

# ─── Done ────────────────────────────────────────────────────────────────────
printf "${_GREEN}${_BOLD}Knack installed successfully!${_RST}\n\n"
printf "${_BOLD}Quick start:${_RST}\n"
printf "  ${_CYAN}knack infra up${_RST}        # Start Kafka + NATS\n"
printf "  ${_CYAN}knack run --quick${_RST}     # Quick benchmark (~45 min)\n"
printf "  ${_CYAN}knack status --watch${_RST}  # Monitor progress\n"
printf "  ${_CYAN}knack report${_RST}          # Generate reports\n"
printf "  ${_CYAN}knack infra down${_RST}      # Tear down\n\n"
printf "Full docs: ${_DIM}https://github.com/jainal09/knack${_RST}\n"
