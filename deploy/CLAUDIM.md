# claudim — team launcher for the Jota AI Gateway

`claudim` is a tiny global command that launches Claude Code against the shared
free-claude-code proxy ("AI Gateway") over Tailscale. Developers install it once and
run it from any directory — no repo clone, no local proxy, no env vars to remember.

## Install (developer, one line)

```sh
curl -fsSL https://raw.githubusercontent.com/usejota/agentic-model-gateway/main/scripts/install-claudim.sh | sh
```

Installs `claudim` to `~/.local/bin/`. If that's not on your `PATH`, the installer
tells you the line to add to your shell rc.

## Prerequisites (developer)

1. **Tailscale** — installed and logged in to the **jota.ai** tailnet, and your user
   permitted by the tailnet ACL to reach `tag:fcc-proxy`. Ask an admin to be added to
   the tailnet group that has the grant if `claudim` reports "not reachable".
2. **Claude Code** — `npm install -g @anthropic-ai/claude-code`.

> Note: being in the `ai-gateway@jota.ai` Google group is for the IAP fallback path,
> **not** the Tailscale path. Tailscale access is governed by the **tailnet ACL**.

## Use

```sh
claudim                 # interactive Claude Code via the gateway
claudim "explain this"  # args pass straight through to claude
claudim models          # list delegate aliases + their strengths
```

### Native delegate agents (interactive mode)

In interactive mode, `claudim` auto-generates **one native Agent-tool subagent per
gateway delegate model** (`delegate-<model>`). The orchestrator (your session model)
picks the cheapest competent delegate per task — no `-p` or tmux needed. The agent
list is fetched live from the gateway at launch via `GET /v1/models/delegates`.

The endpoint returns curated, no-thinking, non-US-closed model ids ready for
delegation. Exclusions are configurable server-side (`MODEL_DELEGATE_EXCLUSIONS` —
see [Exclusions and the delegate endpoint](#exclusions-and-the-delegate-endpoint))
and apply **only** to the delegate pool — `/v1/models` is unfiltered so the human
`/model` picker still sees every model.

`CLAUDIM_MAX_AGENTS` (default 30) caps the number of auto-generated agents to
bound system-prompt bloat. The session gets `CLAUDIM=1` exported into its
environment, and an `--append-system-prompt` tells the model it is inside claudim
and should use the `delegate-*` subagents for delegation.

### Delegate mode (`-p` / `--tmux`, fallback)

Run a single task on a cheap gateway model and get the answer on stdout — so an
Opus/Fable orchestrator can delegate mechanical steps and keep cost down:

```sh
claudim -p --model deepseek-v4-pro "explain what foo() does"
claudim -p --output-format json --model kimi-k2.7-code "add a test for bar()"
```

`--model <alias>` is rewritten to the gateway's **no-thinking** model id. This is
mandatory for `claude -p`: reasoning backends otherwise stream a `thinking` block
with an empty signature, which Claude Code in `-p`/SDK mode treats as an invalid
turn and discards — yielding **empty stdout** even though the text was sent. The
`claude-3-freecc-no-thinking/` prefix both tells Claude Code the model doesn't
support thinking and tells the gateway to disable thinking upstream.

Aliases (run `claudim models` to reprint):

| Alias | Strength |
|-------|----------|
| `deepseek-v4-pro` | smartest: heavy reasoning, multi-step logic |
| `kimi-k2.7-code` | coding: implement, refactor, bug fix, tests |
| `deepseek-v4-flash` | fastest/cheapest: triage, lookups, mechanical edits |
| `glm-5.2` | long-context general: read/analyze big files, writing |
| `minimax-m3` | long-context general alternative |
| `mistral-small` | non-Chinese general (France): cheap, writing/analysis |
| `ministral-8b` | non-Chinese cheapest (France): triage, lookups |
| `codestral` | non-Chinese coding (France): implement, refactor |

All eight are non-American: five Chinese (DeepSeek, Moonshot/Kimi, Zhipu/GLM,
MiniMax) + three French (Mistral). US closed labs — `openai`, `anthropic`,
`google`, `x-ai`, `amazon`, `nvidia`, `ibm-granite`, `liquid`, `rekaai`,
`relace` — are never called for delegation (cost-driven: they charge premium
per-token; the rest are cheap, and open-weight Llama + fine-tunes don't fund a
US lab per call). The US_CLOSED filter lives **server-side** in the gateway
(`GET /v1/models/delegates`) — the launcher no longer maintains its own
vendor list. To see every non-American no-thinking model the gateway currently
offers, run `claudim models --all` and pass any listed id verbatim to `--model`.

A full routed id (contains `/`) or a Claude alias (`haiku`/`sonnet`/`opus`) is
passed through unchanged — but don't use the Claude aliases for delegates, they
route via gateway config and may enable thinking (same empty-stdout bug). For the
full orchestration recipe (when to delegate, model selection, parallelism,
output handling), see the `claudim-delegate` skill — it ships in this repo at
[`.claude/skills/claudim-delegate/SKILL.md`](../.claude/skills/claudim-delegate/SKILL.md)
and the installer downloads it into `~/.claude/skills/` so it loads globally.
It has a kill switch: if you say "workflow"/"fan out subagents" it defers to
Claude Code's native Workflow/Agent tools instead.

`claudim` also unsets `ANTHROPIC_API_KEY` before launching, so an inherited
parent-subscription API key (which Claude Code prefers over `ANTHROPIC_AUTH_TOKEN`)
can't poison the child's auth against the gateway.

### Observing delegates (tmux)

`-p` delegates run invisibly by default. Set `CLAUDIM_TMUX=1` (or pass `--tmux`)
and each `claudim -p` delegate opens a tmux window you can watch live **and
interact with** (approve a permission prompt, type an instruction):

```sh
CLAUDIM_TMUX=1 claudim -p --model kimi-k2.7-code "add a test for bar()"
```

**Recommended workflow — run your orchestrator inside tmux.** Start your Claude
Code session inside tmux (`tmux new -s main`, then `claude`). When the
orchestrator launches delegates with `--tmux`, each delegate opens as a **split
pane in your current window** using tmux's `main-vertical` layout:

```
+----------------------+----------------------+
|                      |  kimi-k2.7-code-8424 |
|                      +----------------------+
|  orchestrator        |  glm-5.2-8423        |
|  (left, full height) +----------------------+
|                      |  codestral-2508-8425 |
+----------------------+----------------------+
```

Your orchestrator pane keeps the left half at full height; delegates stack as
equal-height slices in the right half and re-balance as they come and go. Pane
titles (shown in each pane's top border) carry `<model>-<pid>` so you can tell
delegates apart. `C-b` + arrow keys moves between panes — click works too
(mouse mode) — and you can type into a delegate's pane to interact with it.
When a delegate finishes its pane closes and the rest re-balance.

**Outside tmux:** there's no pane to split, so delegates go to windows in a
detached session named `claudim`:
```sh
tmux attach -t claudim     # in another terminal
```

stdout is captured identically to the orchestrator (the pane is purely for
your eyes/hands); stderr is separate, so `--output-format json` stays
parseable. `-p`-only. If tmux isn't installed (`brew install tmux`), delegates
run inline with a stderr note.

**Parallelism note:** each delegate is a full Claude Code (Node) process.
3-4 in parallel is heavy on RAM/CPU — cap parallel delegates at 2-3 and
serialize the rest.

### gcloud / unrestricted delegates

A `-p` delegate is non-interactive, so the Bash tool can't answer Claude Code's
permission prompts. With the default `auto` permission mode, commands that need
approval — `gcloud`, anything hitting the network, file writes — get denied, and
the delegate reports it "needs approval" instead of running them. Pass
`--unrestricted` (or set `CLAUDIM_BYPASS=1`) and `claudim` injects
`--dangerously-skip-permissions` so the delegate can run those commands:

```sh
claudim -p --unrestricted --model deepseek-v4-flash \
  "rode 'gcloud compute instances list' e reporte o resultado"
```

Off by default — use it only when the task needs gcloud/GCP/network/file-writes.
**Risk:** `--unrestricted` is a full permission bypass on a cheap gateway model
on your machine. Keep delegate tasks scoped and trusted; don't use it for tasks
that consume untrusted content (e.g. "analyze this third-party issue") without
review — a prompt-injection in the task could drive destructive commands. For
read-only analysis that doesn't need the shell, omit it and stay sandboxed.

## Configuration (env overrides)

| Var | Default | Meaning |
|-----|---------|---------|
| `CLAUDIM_HOST` | `fcc-proxy` | proxy MagicDNS host on the tailnet |
| `CLAUDIM_TAILNET` | `tail576af6.ts.net` | tailnet DNS suffix |
| `CLAUDIM_PORT` | `8082` | proxy port |
| `CLAUDIM_TOKEN` | `freecc` | proxy auth token |
| `CLAUDIM_WAIT` | `30` | seconds to wait for the gateway |
| `CLAUDIM_MAX_WAIT` | `3600` | max seconds to wait for a tmux delegate sentinel before killing its pane/window; `0` disables |
| `CLAUDIM_BYPASS` | _unset_ | `1` = inject `--dangerously-skip-permissions` so `-p` delegates can run gcloud/network/file-writes (off by default; see gcloud / unrestricted delegates) |
| `CLAUDIM_TMUX` | _unset_ | `1` = show each `-p` delegate live — split pane in your current tmux window (main-vertical: orchestrator left, delegates stacked right) if inside tmux, else a window in a detached session `claudim` (see Observing delegates) |
| `CLAUDIM_BASE_URL` | `http://<host>.<tailnet>:<port>` | override the gateway URL (skips tailscale checks; for local testing against `localhost`) |
| `CLAUDIM_MAX_AGENTS` | `30` | cap on auto-generated `delegate-*` agents (bounds system-prompt bloat) |

Example — point at a differently-named gateway node:

```sh
CLAUDIM_HOST=fcc-proxy-staging claudim
```
Or set it permanently in your `~/.zshrc`:
```sh
export CLAUDIM_HOST=fcc-proxy-staging
```
(The staging node is `fcc-proxy`, which is the default — no override needed.)

## How it works

`claudim` checks you're on the tailnet, waits for the gateway to be reachable at
`http://<host>.<tailnet>:8082`, exports the Claude Code env vars
(`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, model-discovery, compact window), and
`exec`s `claude` with your args. It's the global-install form of
`deploy/fcc-connect-tailscale`.

## Exclusions and the delegate endpoint

`GET /v1/models/delegates` returns the curated delegate pool (no-thinking,
non-US-closed ids, after exclusions). The launcher calls this endpoint at
startup to generate the `delegate-*` agents. It is **not** the same as
`/v1/models` — the human `/model` picker still sees every model the gateway
can route.

### MODEL_DELEGATE_EXCLUSIONS

Set in the gateway Admin UI (or via the `MODEL_DELEGATE_EXCLUSIONS` env var on
the gateway server). Comma-separated `fnmatch` globs that hide models from the
delegate pool **only**:

```
open_router/deepseek/*    # hide all DeepSeek models routed through OpenRouter
*gpt-oss*                 # hide any model with "gpt-oss" in its ref
open_router/qwen/qwen-coder-plus  # hide a specific model
```

A glob without a wildcard is an exact match. The exclusion is checked against
the full model ref (e.g. `open_router/deepseek/deepseek-v4-flash`).

### Hard enforcement (subagents only)

Exclusions are enforced at request time by the gateway: any `/v1/messages`
request whose resolved model matches a `MODEL_DELEGATE_EXCLUSIONS` pattern is
rejected with an invalid-request error — **unless** it is Claude Code's main
conversation loop (detected by the CLI's system-prompt marker, `"You are
Claude Code"`). So the human `/model` picker keeps working for every model,
while Agent-tool subagents (or any side-channel request) cannot use excluded
models — even if the session model passes an explicit `model` param or writes
its own `.claude/agents/*.md`. Caveat: the marker is a heuristic — a client
that forges a main-loop system prompt bypasses it, and if a future Claude Code
release changes its system-prompt opening, the main loop would start being
blocked too (fix: update `_MAIN_LOOP_MARKERS` in `api/services.py`).

## Troubleshooting

- **"not reachable within 30s"** → `tailscale status` (are you up? is the gateway node
  listed?); confirm the ACL grant; check the host name (`CLAUDIM_HOST`).
- **"claude not found"** → `npm install -g @anthropic-ai/claude-code`.
- **401 from the gateway** → token mismatch; the gateway's `ANTHROPIC_AUTH_TOKEN` isn't
  `freecc` — set `CLAUDIM_TOKEN` to match.
- **500 from the gateway** → provider-side (admin's gateway config); ping an admin.
