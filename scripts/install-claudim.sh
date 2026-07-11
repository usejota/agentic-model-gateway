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
# Renderer is the sibling of the launcher (claudim finds it next to itself at
# runtime). Installing them together keeps `claudim upgrade` consistent.
RENDER_SRC="${CLAUDIM_RENDER_SRC:-${REPO_RAW}/deploy/claudim-render.py}"
# Enforce hook — PreToolUse hook for --delegate mode. Installed alongside the
# launcher and renderer so `claudim upgrade` keeps all three in sync.
HOOK_SRC="${CLAUDIM_HOOK_SRC:-${REPO_RAW}/deploy/claudim-enforce-hook.py}"
BIN_DIR="${CLAUDIM_BIN_DIR:-${HOME}/.local/bin}"
# Install NAME — parameterizes the launcher so it is renameable. Defaults to
# `claudim` (retro-compat: unchanged behavior). Set CLAUDIM_NAME=loclaudim (or
# any name) to install side-by-side under that name; the launcher derives its
# own name from $0 at runtime, so a different install name just works. The repo
# SOURCE files keep their canonical filenames (deploy/claudim,
# deploy/claudim-render.py); only the installed binary/renderer/skill take NAME.
NAME="${CLAUDIM_NAME:-claudim}"
DEST="${BIN_DIR}/${NAME}"
RENDER_DEST="${BIN_DIR}/${NAME}-render.py"
HOOK_DEST="${BIN_DIR}/${NAME}-enforce-hook.py"
# The claudim-delegate skill (orchestrator recipe + kill switch). Installed
# globally so it loads in any Claude Code session, not just this repo. Installed
# under ${NAME}-delegate so a renamed launcher gets its own skill slot.
# Skills installed globally so they load in any Claude Code session, not just this
# repo. Installed under ${NAME}-<name> so a renamed launcher gets its own skill slots.
# Each skill is templated: every `claudim` reference is replaced with the installed
# NAME so a renameable install (CLAUDIM_NAME=loclaudim) gets matching skills that
# reference the correct binary.
SKILL_DELEGATE_SRC="${CLAUDIM_SKILL_SRC:-${REPO_RAW}/.claude/skills/claudim-delegate/SKILL.md}"
SKILL_PANEL_SRC="${CLAUDIM_PANEL_SKILL_SRC:-${REPO_RAW}/.claude/skills/claudim-panel/SKILL.md}"

say()  { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Validate NAME early: must be [A-Za-z0-9_-]+ so downstream sed "s/claudim/${NAME}/g"
# never sees a metacharacter (/ & . etc.) that would break the expression or produce
# an invalid filename / skill directory. Runs after fail() is defined so the clear
# message prints (validation before any mkdir/fetch).
case "${NAME}" in
  *[!A-Za-z0-9_-]* | "" ) fail "CLAUDIM_NAME must match [A-Za-z0-9_-]+ (got: '${NAME}')" ;;
esac

# fetch URL DEST — curl or wget, picked once. Returns 0 on success, 1 on failure
# without exiting, so callers can decide whether to fail or warn. Downloads to a
# sibling temp file and atomically moves on success, so a mid-stream failure
# (flaky link, transient 404) leaves any existing destination intact instead of
# truncating it to a partial/empty file — a corrupt launcher or renderer would
# break every later claudim -p / upgrade run.
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
# Install the renderer BEFORE the launcher: the new launcher depends on the
# renderer for tmux observation, so a flaky renderer download must abort BEFORE
# the (working) old launcher is replaced — leaving the old launcher + old
# renderer paired and functional. If the launcher download then fails, the old
# launcher still runs (inline path, no renderer dependency) with the new
# renderer sitting unused. Reversing this order would leave a new launcher
# pointing at an absent/old renderer and break every --tmux run.
say "Installing renderer to ${RENDER_DEST}"
fetch "${RENDER_SRC}" "${RENDER_DEST}" || fail "download failed from ${RENDER_SRC}"
chmod +x "${RENDER_DEST}"
say "Installing enforce hook to ${HOOK_DEST}"
fetch "${HOOK_SRC}" "${HOOK_DEST}" || fail "download failed from ${HOOK_SRC}"
chmod +x "${HOOK_DEST}"
say "Installing ${NAME} to ${DEST}"
fetch "${SRC}" "${DEST}" || fail "download failed from ${SRC}"
# Bake a default gateway URL into the installed launcher when
# CLAUDIM_DEFAULT_BASE_URL is set. This lets a side-by-side local-test
# wrapper (e.g. CLAUDIM_NAME=loclaudim) point at its own gateway without
# env vars, while CLAUDIM_BASE_URL at runtime still wins if set.
if [ -n "${CLAUDIM_DEFAULT_BASE_URL:-}" ]; then
  say "Baking default gateway URL: ${CLAUDIM_DEFAULT_BASE_URL}"
  _esc="$(printf '%s' "${CLAUDIM_DEFAULT_BASE_URL}" | sed 's/[&\\/]/\\&/g')"
  sed -i.bak "s|^CLAUDIM_BAKED_BASE_URL=\"\"|CLAUDIM_BAKED_BASE_URL=\"${_esc}\"|" "${DEST}" && rm -f "${DEST}.bak"
  grep -q "^CLAUDIM_BAKED_BASE_URL=\"${_esc}\"" "${DEST}" || fail "failed to bake CLAUDIM_DEFAULT_BASE_URL into ${DEST}"
fi
chmod +x "${DEST}"
say "Installed."

# Install skills globally. Non-fatal: claudim works without them.
# Each skill is templated: every `claudim` reference → installed NAME.
for _sk in delegate panel; do
  case "$_sk" in
    delegate) _src="${SKILL_DELEGATE_SRC}" ;;
    panel)    _src="${SKILL_PANEL_SRC}" ;;
  esac
  _dir="${HOME}/.claude/skills/${NAME}-${_sk}"
  _dest="${_dir}/SKILL.md"
  say "Installing ${NAME}-${_sk} skill to ${_dest}"
  mkdir -p "${_dir}"
  _tmp="${_dest}.tmp.$$"
  if fetch "${_src}" "${_tmp}"; then
    # NAME is validated [A-Za-z0-9_-]+ above — no sed metachar risk.
    sed "s/claudim/${NAME}/g" "${_tmp}" > "${_dest}"
    rm -f "${_tmp}"
    say "Skill ${NAME}-${_sk} installed."
  else
    rm -f "${_tmp}"
    warn "could not install ${NAME}-${_sk} skill from ${_src}"
  fi
done

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
  The ${NAME}-delegate and ${NAME}-panel skills were installed to ~/.claude/skills/ — they teach an
  Opus/Fable orchestrator when/how to delegate to the cheap non-American models
  (and defers to native workflows if you say "workflow"/"fan out subagents").
  Local-test wrapper (side-by-side with prod): install a second copy under
  another name pointing at your local gateway, e.g.:
    CLAUDIM_NAME=loclaudim CLAUDIM_DEFAULT_BASE_URL=http://localhost:8082 bash scripts/install-claudim.sh
  Update ${NAME} later with: ${NAME} upgrade
NEXT
