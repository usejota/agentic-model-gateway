#!/usr/bin/env bash
#
# Local smoke for claudim delegate mode. Verifies:
#   - `claudim -p --model <alias>` returns NON-empty stdout (the empty-response
#     regression: reasoning backends stream an unsigned `thinking` block that
#     Claude Code -p discards; the alias rewrites to the no-thinking gateway id).
#   - CLAUDIM_TMUX=1 wraps a delegate in a tmux window and still returns stdout;
#     falls back to inline (with a stderr note) when tmux is absent from PATH.
#   - `--unrestricted` lets a -p delegate run gcloud (bypasses the permission
#     wall that non-interactive -p can't answer).
#
# Run locally before a PR:
#   bash smoke/claudim_delegate.sh
#
# This is a LIVE test: it needs Tailscale up and the fcc-proxy gateway reachable,
# so it does NOT run in CI (GitHub Actions has no tailnet). If either is missing
# it skips (exit 0) rather than false-failing. Port to pytest (smoke/skips.py
# pattern) if you later want CI to gate on a self-hosted runner with tailnet.
#
set -euo pipefail

# Resolve repo root from this script's location (works via path or `bash <file>`).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CLAUDIM="${REPO_ROOT}/deploy/claudim"

PROMPT="quanto é 17*23? Responda só o número, nada mais."
EXPECT="391"
ALIASES=(deepseek-v4-pro kimi-k2.7-code deepseek-v4-flash glm-5.2 minimax-m3 \
         mistral-small ministral-8b codestral)

pass=0; fail=0
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail+1)); }
skip() { printf '  \033[33mSKIP\033[0m %s\n' "$1"; }
step() { printf '\n== %s ==\n' "$1"; }

# Skip cleanly without tailscale or gateway (CI / machines without tailnet).
if ! command -v tailscale >/dev/null 2>&1; then
  echo "skip: tailscale not on PATH (no tailnet -> can't reach fcc-proxy)."; exit 0
fi
if ! tailscale status >/dev/null 2>&1; then
  echo "skip: not connected to the tailnet."; exit 0
fi
if ! curl -s -o /dev/null -m 5 "http://fcc-proxy.tail576af6.ts.net:8082/v1/models" -H "x-api-key: freecc"; then
  echo "skip: fcc-proxy gateway not reachable (set CLAUDIM_HOST/CLAUDIM_TAILNET if renamed)."; exit 0
fi

step "alias -> non-empty stdout containing ${EXPECT}"
for a in "${ALIASES[@]}"; do
  out="$("${CLAUDIM}" -p --model "${a}" "${PROMPT}" 2>/dev/null || true)"
  if [ -n "${out}" ] && echo "${out}" | grep -q "${EXPECT}"; then
    ok "${a} -> $(echo "${out}" | head -c 60 | tr -d '\n')"
  else
    bad "${a} -> empty/wrong: [$(echo "${out}" | head -c 60)]"
  fi
done

step "--model=<alias> (= form) rewrites too"
out="$("${CLAUDIM}" -p --model=glm-5.2 "${PROMPT}" 2>/dev/null || true)"
if [ -n "${out}" ] && echo "${out}" | grep -q "${EXPECT}"; then ok "glm-5.2 (= form)"; else bad "glm-5.2 (= form): [${out}]"; fi

step "--output-format json: result non-empty, stop_reason=end_turn, is_error=false"
json="$("${CLAUDIM}" -p --output-format json --model deepseek-v4-pro "${PROMPT}" 2>/dev/null || true)"
if python3 - "${json}" <<'PY' 2>&1
import json, sys
d = json.loads(sys.argv[1])
ok = bool(d.get("result")) and d.get("stop_reason") == "end_turn" and d.get("is_error") is False
print(f"result={d.get('result')!r:.60} stop={d.get('stop_reason')} is_error={d.get('is_error')}")
sys.exit(0 if ok else 1)
PY
then ok "json contract"; else bad "json contract (see above)"; fi

step "auth: ANTHROPIC_API_KEY=fake (parent subscription) must NOT poison the child"
out="$(ANTHROPIC_API_KEY=fakeinvalid "${CLAUDIM}" -p --model deepseek-v4-flash "diga: ok" 2>/dev/null || true)"
if [ -n "${out}" ]; then ok "child ignored inherited API_KEY -> $(echo "${out}" | head -c 40)"; else bad "child 401/hang on fake API_KEY"; fi

step "models --all: non-American only (US closed labs excluded, Mistral present)"
all="$("${CLAUDIM}" models --all 2>/dev/null || true)"
if [ -z "${all}" ]; then
  bad "models --all returned nothing (gateway reachable?)"
else
  leak=""
  for v in openai anthropic google x-ai amazon nvidia ibm-granite liquid rekaai relace openrouter; do
    echo "${all}" | grep -q "\[${v}\]" && leak="${leak} ${v}"
  done
  if [ -n "${leak}" ]; then
    bad "US closed labs leaked into --all:${leak}"
  elif echo "${all}" | grep -q "\[mistralai\]"; then
    ok "no US closed labs; Mistral present ($(echo "${all}" | grep -c '^\[') vendors)"
  else
    bad "no US leak but Mistral missing (expected non-American)"
  fi
fi

step "CLAUDIM_TMUX=1: delegate runs in a tmux window, stdout intact"
if command -v tmux >/dev/null 2>&1; then
  out="$(CLAUDIM_TMUX=1 "${CLAUDIM}" -p --model deepseek-v4-flash "${PROMPT}" 2>/dev/null || true)"
  if [ -n "${out}" ] && echo "${out}" | grep -q "${EXPECT}"; then ok "tmux wrap -> $(echo "${out}" | head -c 40)"; else bad "tmux wrap: [$(echo "${out}" | head -c 60)]"; fi
else
  skip "tmux not installed (brew install tmux)"
fi

step "inside tmux: delegate window opens in the caller's session, stdout intact"
if command -v tmux >/dev/null 2>&1; then
  tmux kill-session -t claudim-smoke 2>/dev/null || true
  tmux new-session -d -s claudim-smoke
  tmux send-keys -t claudim-smoke "cd $(pwd) && CLAUDIM_TMUX=1 '${CLAUDIM}' -p --model deepseek-v4-flash '${PROMPT}' > /tmp/claudim-smoke-in.txt 2>/tmp/claudim-smoke-err.txt" Enter
  waited=0
  while [ ! -s /tmp/claudim-smoke-in.txt ] && [ "${waited}" -lt 120 ]; do sleep 2; waited=$((waited+2)); done
  out="$(cat /tmp/claudim-smoke-in.txt 2>/dev/null || true)"
  errnote="$(grep -c "your current session" /tmp/claudim-smoke-err.txt 2>/dev/null || echo 0)"
  tmux kill-session -t claudim-smoke 2>/dev/null || true
  rm -f /tmp/claudim-smoke-in.txt /tmp/claudim-smoke-err.txt
  if [ -n "${out}" ] && echo "${out}" | grep -q "${EXPECT}" && [ "${errnote}" -ge 1 ]; then
    ok "in-session window -> $(echo "${out}" | head -c 40)"
  else
    bad "in-session: out=[$(echo "${out}" | head -c 40)] note=${errnote}"
  fi
else
  skip "tmux not installed"
fi

step "no tmux on PATH: inline fallback with stderr note, stdout intact"
fbdir="$(mktemp -d 2>/dev/null || mktemp -d)"
for b in claude tailscale curl nc python3; do
  p="$(command -v "$b" 2>/dev/null)" && [ -n "$p" ] && ln -sf "$p" "$fbdir/$b"
done
out="$(PATH="$fbdir:/usr/bin:/bin" "${CLAUDIM}" -p --tmux --model deepseek-v4-flash "${PROMPT}" 2>/dev/null || true)"
if [ -n "${out}" ] && echo "${out}" | grep -q "${EXPECT}"; then ok "fallback stdout intact -> $(echo "${out}" | head -c 40)"; else bad "fallback: [$(echo "${out}" | head -c 60)]"; fi
rm -rf "$fbdir"

step "--unrestricted: delegate runs gcloud --version (bypasses permission wall)"
if command -v gcloud >/dev/null 2>&1; then
  out="$("${CLAUDIM}" -p --unrestricted --model deepseek-v4-flash "Use the bash tool to run: gcloud --version. Then report ONLY the line starting with 'Google Cloud SDK'. Nothing else." 2>/dev/null || true)"
  if [ -n "${out}" ] && echo "${out}" | grep -q "Google Cloud SDK"; then ok "gcloud ran -> $(echo "${out}" | head -c 50)"; else bad "gcloud: [$(echo "${out}" | head -c 60)]"; fi
else
  skip "gcloud not installed"
fi

echo ""
echo "result: ${pass} passed, ${fail} failed"
[ "${fail}" -eq 0 ]
