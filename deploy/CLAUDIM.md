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
```

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
