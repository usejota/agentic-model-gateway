#!/usr/bin/env sh
#
# install-claudim.sh — install the `claudim` launcher globally so developers can
# run Claude Code against the Jota AI Gateway from anywhere.
#
# One-liner (no repo clone needed):
#   curl -fsSL https://raw.githubusercontent.com/usejota/agentic-model-gateway/main/scripts/install-claudim.sh | sh
#
# Installs to ~/.local/bin/claudim by default (override with CLAUDIM_BIN_DIR).
# Pre-reqs the dev still needs: Tailscale (logged in to jota.ai) and Claude Code
# (`npm install -g @anthropic-ai/claude-code`). The script warns if either is missing.
set -eu

REPO_RAW="https://raw.githubusercontent.com/usejota/agentic-model-gateway/main"
SRC="${CLAUDIM_SRC:-${REPO_RAW}/deploy/claudim}"
BIN_DIR="${CLAUDIM_BIN_DIR:-${HOME}/.local/bin}"
DEST="${BIN_DIR}/claudim"
# The claudim-delegate skill (orchestrator recipe + kill switch). Installed
# globally so it loads in any Claude Code session, not just this repo.
SKILL_SRC="${CLAUDIM_SKILL_SRC:-${REPO_RAW}/.claude/skills/claudim-delegate/SKILL.md}"
SKILL_DIR="${HOME}/.claude/skills/claudim-delegate"
SKILL_DEST="${SKILL_DIR}/SKILL.md"

say()  { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

# fetch URL DEST — curl or wget, picked once. Returns 0 on success, 1 on failure
# without exiting, so callers can decide whether to fail or warn.
fetch() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$1" -o "$2" && return 0
    return 1
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$2" "$1" && return 0
    return 1
  else
    return 1
  fi
}

say "Installing claudim to ${DEST}"
mkdir -p "${BIN_DIR}"
fetch "${SRC}" "${DEST}" || fail "download failed from ${SRC}"
chmod +x "${DEST}"
say "Installed."

# Install the claudim-delegate skill globally so an Opus/Fable orchestrator
# picks it up in any session (when to delegate, model picks, parallelism, tmux
# observation, the unrestricted/gcloud path, and the workflow kill switch).
# Non-fatal: claudim works without the skill; it's just the orchestration recipe.
say "Installing claudim-delegate skill to ${SKILL_DEST}"
mkdir -p "${SKILL_DIR}"
if fetch "${SKILL_SRC}" "${SKILL_DEST}"; then
  say "Skill installed."
else
  warn "could not install claudim-delegate skill from ${SKILL_SRC}"
fi

# PATH check.
case ":${PATH}:" in
  *":${BIN_DIR}:"*) : ;;
  *)
    warn "${BIN_DIR} is not on your PATH."
    printf '       Add this to your shell rc (~/.zshrc or ~/.bashrc):\n'
    printf '         export PATH="%s:$PATH"\n' "${BIN_DIR}"
    ;;
esac

# Dependency checks (warn only — install doesn't require them present yet).
command -v tailscale >/dev/null 2>&1 || warn "tailscale not found — install it and log in to the jota.ai tailnet."
command -v claude    >/dev/null 2>&1 || warn "claude not found — install: npm install -g @anthropic-ai/claude-code"

cat <<'NEXT'

claudim installed. Next:
  1. Be on the company tailnet (Tailscale app logged in with @jota.ai) and
     permitted by the tailnet ACL to reach the gateway (ask an admin if unsure).
  2. Run it from any project directory:
       claudim
       claudim "explain this repo"
  Args pass straight through to Claude Code. Override the gateway host/tailnet
  with CLAUDIM_HOST / CLAUDIM_TAILNET if needed (see `claudim` header comments).
  The claudim-delegate skill was installed to ~/.claude/skills/ — it teaches an
  Opus/Fable orchestrator when/how to delegate to the cheap non-American models
  (and defers to native workflows if you say "workflow"/"fan out subagents").
  Update claudim later with: claudim upgrade
NEXT
