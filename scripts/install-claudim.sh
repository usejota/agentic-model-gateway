#!/usr/bin/env sh
#
# install-claudim.sh — install the `claudim` launcher globally so developers can
# run Claude Code against the Jota AI Gateway from anywhere.
#
# One-liner (no repo clone needed):
#   curl -fsSL https://raw.githubusercontent.com/usejota/agentic-model-gateway/main/scripts/install-claudim.sh | sh
#
# Renameable: set CLAUDIM_NAME=loclaudim (or any [A-Za-z0-9_-]+ name) to install
# side-by-side under that name. The launcher derives its own name from $0 at
# runtime, so a different install name just works. Local-test wrapper:
#   CLAUDIM_NAME=loclaudim CLAUDIM_DEFAULT_BASE_URL=http://localhost:8082 \
#     bash scripts/install-claudim.sh
#
# Installs to ~/.local/bin/${CLAUDIM_NAME:-claudim} (override with CLAUDIM_BIN_DIR).
# Pre-reqs the dev still needs: Tailscale (logged in to jota.ai) and Claude Code
# (`npm install -g @anthropic-ai/claude-code`). The script warns if either is missing.
set -eu

REPO_RAW="https://raw.githubusercontent.com/usejota/agentic-model-gateway/main"
SRC="${CLAUDIM_SRC:-${REPO_RAW}/deploy/claudim}"
BIN_DIR="${CLAUDIM_BIN_DIR:-${HOME}/.local/bin}"
# Install NAME — parameterizes the launcher so it is renameable. Defaults to
# `claudim` (retro-compat: unchanged behavior). Set CLAUDIM_NAME=loclaudim (or
# any name) to install side-by-side under that name; the launcher derives its
# own name from $0 at runtime, so a different install name just works. The repo
# SOURCE file keeps its canonical filename (deploy/claudim); only the installed
# binary takes NAME.
NAME="${CLAUDIM_NAME:-claudim}"
DEST="${BIN_DIR}/${NAME}"

say()  { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Validate NAME early: must be [A-Za-z0-9_-]+ so it is safe as a filename and
# (later, when skills are templated) as a sed replacement with no metacharacter
# (/ & . etc.) that would break the expression or produce an invalid skill
# directory. Runs after fail() is defined so the clear message prints (validation
# before any mkdir/fetch).
case "${NAME}" in
  *[!A-Za-z0-9_-]* | "" ) fail "CLAUDIM_NAME must match [A-Za-z0-9_-]+ (got: '${NAME}')" ;;
esac

# fetch URL DEST — curl or wget, picked once. Returns 0 on success, 1 on failure
# without exiting, so callers can decide whether to fail or warn. Downloads to a
# sibling temp file and atomically moves on success, so a mid-stream failure
# (flaky link, transient 404) leaves any existing destination intact instead of
# truncating it to a partial/empty file — a corrupt launcher would break every
# later claudim run.
fetch() {
  _ftmp="$2.tmp.$$"
  if command -v curl >/dev/null 2>&1; then
    if curl -fsSL "$1" -o "$_ftmp"; then mv -f "$_ftmp" "$2"; return 0; fi
  elif command -v wget >/dev/null 2>&1; then
    if wget -qO "$_ftmp" "$1"; then mv -f "$_ftmp" "$2"; return 0; fi
  fi
  rm -f "$_ftmp"
  return 1
}

mkdir -p "${BIN_DIR}"
say "Installing ${NAME} to ${DEST}"
fetch "${SRC}" "${DEST}" || fail "download failed from ${SRC}"
# Bake a default gateway URL into the installed launcher when
# CLAUDIM_DEFAULT_BASE_URL is set. This lets a side-by-side local-test wrapper
# (e.g. CLAUDIM_NAME=loclaudim) point at its own gateway without env vars, while
# CLAUDIM_BASE_URL at runtime still wins if set.
if [ -n "${CLAUDIM_DEFAULT_BASE_URL:-}" ]; then
  say "Baking default gateway URL: ${CLAUDIM_DEFAULT_BASE_URL}"
  _esc="$(printf '%s' "${CLAUDIM_DEFAULT_BASE_URL}" | sed 's/[&\\/]/\\&/g')"
  sed -i.bak "s|^CLAUDIM_BAKED_BASE_URL=\"\"|CLAUDIM_BAKED_BASE_URL=\"${_esc}\"|" "${DEST}" && rm -f "${DEST}.bak"
  grep -q "^CLAUDIM_BAKED_BASE_URL=\"${_esc}\"" "${DEST}" || fail "failed to bake CLAUDIM_DEFAULT_BASE_URL into ${DEST}"
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

cat <<NEXT

${NAME} installed. Next:
  1. Be on the company tailnet (Tailscale app logged in with @jota.ai) and
     permitted by the tailnet ACL to reach the gateway (ask an admin if unsure).
  2. Run it from any project directory:
       ${NAME}
       ${NAME} "explain this repo"
  Args pass straight through to Claude Code. Override the gateway host/tailnet
  with CLAUDIM_HOST / CLAUDIM_TAILNET if needed (see \`${NAME}\` header comments).
  Local-test wrapper (side-by-side with prod): install a second copy under
  another name pointing at your local gateway, e.g.:
    CLAUDIM_NAME=loclaudim CLAUDIM_DEFAULT_BASE_URL=http://localhost:8082 bash scripts/install-claudim.sh
  Update ${NAME} later with: ${NAME} upgrade
NEXT
