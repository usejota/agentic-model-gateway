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

## Configuration (env overrides)

| Var | Default | Meaning |
|-----|---------|---------|
| `CLAUDIM_HOST` | `fcc-proxy` | proxy MagicDNS host on the tailnet |
| `CLAUDIM_TAILNET` | `tail576af6.ts.net` | tailnet DNS suffix |
| `CLAUDIM_PORT` | `8082` | proxy port |
| `CLAUDIM_TOKEN` | `freecc` | proxy auth token |
| `CLAUDIM_WAIT` | `30` | seconds to wait for the gateway |

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
