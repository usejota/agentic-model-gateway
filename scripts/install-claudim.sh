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

say()  { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

say "Installing claudim to ${DEST}"
mkdir -p "${BIN_DIR}"

if command -v curl >/dev/null 2>&1; then
  curl -fsSL "${SRC}" -o "${DEST}" || fail "download failed from ${SRC}"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "${DEST}" "${SRC}" || fail "download failed from ${SRC}"
else
  fail "need curl or wget to download claudim"
fi
chmod +x "${DEST}"
say "Installed."

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
  Update claudim later with: claudim upgrade
NEXT
