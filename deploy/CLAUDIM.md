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

### Delegate mode (cheap model per task)

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

All five are Chinese providers (DeepSeek, Moonshot/Kimi, Zhipu/GLM, MiniMax).
`openai`, `anthropic`, `google`, and `x-ai` are never called. To see every
Chinese-vendor no-thinking model the gateway currently offers (live, filtered
to Chinese providers only), run `claudim models --all` and pass any listed id
verbatim to `--model`.

A full routed id (contains `/`) or a Claude alias (`haiku`/`sonnet`/`opus`) is
passed through unchanged — but don't use the Claude aliases for delegates, they
route via gateway config and may enable thinking (same empty-stdout bug). For the
full orchestration recipe (when to delegate, model selection, parallelism,
output handling), see the `claudim-delegate` skill.

`claudim` also unsets `ANTHROPIC_API_KEY` before launching, so an inherited
parent-subscription API key (which Claude Code prefers over `ANTHROPIC_AUTH_TOKEN`)
can't poison the child's auth against the gateway.

### Observing delegates (tmux)

`-p` delegates run invisibly by default. Set `CLAUDIM_TMUX=1` (or pass `--tmux`)
and each `claudim -p` delegate runs inside its own window of a tmux session named
`claudim`, so you can watch it stream live:

```sh
CLAUDIM_TMUX=1 claudim -p --model kimi-k2.7-code "add a test for bar()"
tmux attach -t claudim     # C-b w lists windows; C-b n / C-b p cycles
```

Each delegate gets a window `del-<pid>` (unique, so parallel delegates don't
collide). stdout is teed to a log and returned on `claudim`'s stdout exactly like
the inline path — the orchestrator captures the same result; you just get to
watch. stderr (spinner/progress) goes to a separate file, so `--output-format
json` stays parseable. The window is killed when the delegate finishes; with
parallel delegates the session stays alive until the last one exits.

Needs `tmux` on your PATH (`brew install tmux`). If absent, `claudim` falls back
to inline execution with a stderr note — delegates still work, you just can't
watch. tmux observation is `-p`-only (ignored for interactive `claudim`).

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
| `CLAUDIM_BYPASS` | _unset_ | `1` = inject `--dangerously-skip-permissions` so `-p` delegates can run gcloud/network/file-writes (off by default; see gcloud / unrestricted delegates) |
| `CLAUDIM_TMUX` | _unset_ | `1` = wrap `-p` delegates in a tmux window for live observation (needs `tmux` on PATH; see Observing delegates) |

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

## Troubleshooting

- **"not reachable within 30s"** → `tailscale status` (are you up? is the gateway node
  listed?); confirm the ACL grant; check the host name (`CLAUDIM_HOST`).
- **"claude not found"** → `npm install -g @anthropic-ai/claude-code`.
- **401 from the gateway** → token mismatch; the gateway's `ANTHROPIC_AUTH_TOKEN` isn't
  `freecc` — set `CLAUDIM_TOKEN` to match.
- **500 from the gateway** → provider-side (admin's gateway config); ping an admin.
