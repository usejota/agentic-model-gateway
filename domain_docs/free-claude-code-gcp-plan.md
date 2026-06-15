# Org-wide Free Claude Code on GCP, gated by IAP

**Goal:** run one shared `free-claude-code` proxy (`fcc-server`) in GCP and have every engineer's Claude Code connect to it, with access controlled by Google Identity-Aware Proxy (IAP).

**Decisions locked in:** IAP **TCP forwarding** (GCE VM + per-user `gcloud` tunnel), provider backend kept **swappable** via the Admin UI, sized for **10–50 users**.

---

## 0. Read this first — data governance flag

`free-claude-code` is a proxy that routes the full Anthropic Messages payload — prompts, file contents, diffs, tool output — to a **third-party model provider** (NVIDIA NIM, OpenRouter, Z.ai, DeepSeek, etc.). For the Octopus codebase this means **Jota banking source code, schemas, and possibly secrets would leave Anthropic and be sent to whichever provider is configured.**

Before rolling this out org-wide, get explicit sign-off on:

- Which provider(s) are acceptable, and their data-retention / training-opt-out terms (LGPD / BACEN exposure for a regulated bank).
- Whether self-hosted/local models (no third-party egress) are required instead.
- That a shared provider API key and its quota/billing are owned by a team.

The plan below keeps the provider **swappable** precisely so you can start with a self-hosted or no-retention provider and change it without touching the deployment. The architecture is sound regardless of provider; the *choice* of provider is the real risk decision.

---

## 1. Why TCP forwarding (not the HTTPS load balancer)

Claude Code authenticates to its endpoint by sending the proxy token in the `Authorization` header (`ANTHROPIC_AUTH_TOKEN`). IAP on an external HTTPS load balancer **also** wants to read a Google-signed identity token — historically from `Authorization`, with `Proxy-Authorization` as the programmatic fallback. That fallback works but the ID token expires every ~60 minutes, and Claude Code reads its headers once at launch, so a long coding session dies mid-flight unless you run a local token-refreshing forward proxy. That's fragile.

**IAP TCP forwarding sidesteps the collision entirely:**

```
engineer's laptop                          GCP
┌─────────────────────┐                ┌──────────────────────────────┐
│ claude (CLI/VSCode)  │                │  GCE VM  (no external IP)     │
│   ANTHROPIC_BASE_URL │   IAP tunnel   │  ┌────────────────────────┐  │
│   = localhost:8082   │◀──────────────▶│  │ fcc-server :8082       │  │
│ ANTHROPIC_AUTH_TOKEN │  (Google auth, │  │  → provider (swappable)│  │
│   = freecc (static)  │   gcloud creds)│  └────────────────────────┘  │
└─────────────────────┘                └──────────────────────────────┘
        ▲                                         ▲
  gcloud compute start-iap-tunnel          firewall: allow tcp:8082
                                           only from 35.235.240.0/20
```

- IAP authenticates the **user's Google Workspace identity** via their `gcloud` login + IAM. No public IP, no LB, no header juggling.
- The tunnel is a raw TCP pipe, so the proxy's own static `freecc` token rides inside it untouched.
- gcloud refreshes IAP credentials transparently for the life of the tunnel — no hourly breakage.
- Bonus: no external HTTPS load balancer to pay for or manage.

The trade: each user needs `gcloud` installed and a tunnel running. We wrap that in a one-line launcher (§6).

---

## 2. GCP resources to create

| Resource | Recommendation | Notes |
|---|---|---|
| Project | Existing or dedicated `jota-fcc-proxy` | Dedicated project makes IAM/billing cleanly scoped. |
| VPC + subnet | One subnet in your primary region (e.g. `us-central1` / `southamerica-east1` for latency to BR) | No external IP on the VM. |
| Cloud NAT | Yes | The VM has no public IP but must reach the provider API outbound. Cloud NAT provides egress. |
| GCE VM | `e2-standard-2` (2 vCPU, 8 GB) | Proxy is async I/O-bound (streaming); this comfortably covers 10–50 users with ~10–20 concurrent sessions. Start here, resize if needed. |
| Firewall rule | Allow `tcp:8082` ingress from `35.235.240.0/20` only | That CIDR is IAP's forwarding range. Also allow IAP SSH (`tcp:22` from same range) for admin. |
| Secret Manager | One secret for the provider API key | Fetched at boot; keeps the key out of disk images and git. |
| Service account | Dedicated SA for the VM | Grant Secret Manager accessor only. |

Cost ballpark: ~US$50/mo for the VM + small NAT/egress, plus provider usage. No LB cost.

---

## 3. Build the VM and install the proxy

Provision the VM with **no external IP**, attach the dedicated service account, and run the install via startup script (or bake an image / use a container). Outline:

```bash
# 3.1 Create firewall rules (run once)
gcloud compute firewall-rules create allow-iap-fcc \
  --network=<VPC> --direction=INGRESS --action=ALLOW \
  --rules=tcp:8082,tcp:22 --source-ranges=35.235.240.0/20

# 3.2 Create the VM (no external IP)
gcloud compute instances create fcc-proxy \
  --zone=<ZONE> --machine-type=e2-standard-2 \
  --network=<VPC> --subnet=<SUBNET> --no-address \
  --service-account=<VM_SA_EMAIL> \
  --scopes=cloud-platform \
  --metadata-from-file=startup-script=startup.sh
```

`startup.sh` (runs as root on boot):

```bash
#!/usr/bin/env bash
set -euo pipefail

# Install uv + Python 3.14 + the proxy as a dedicated 'fcc' user
useradd -m -s /bin/bash fcc || true
sudo -u fcc bash -lc '
  curl -LsSf https://astral.sh/uv/install.sh | sh
  git clone https://github.com/Alishahryar1/free-claude-code.git ~/free-claude-code
  cd ~/free-claude-code && uv sync
'

# Pull the provider key from Secret Manager into ~/.fcc/.env
mkdir -p /home/fcc/.fcc
KEY=$(gcloud secrets versions access latest --secret=fcc-provider-key)
cat > /home/fcc/.fcc/.env <<EOF
PORT=8082
HOST=0.0.0.0
ANTHROPIC_AUTH_TOKEN=freecc
OPENROUTER_API_KEY=$KEY          # rename to the var your chosen provider uses
MODEL=open_router/...            # set per provider, or configure later via Admin UI
EOF
chown -R fcc:fcc /home/fcc/.fcc

# systemd unit so the proxy is always running
cat > /etc/systemd/system/fcc.service <<'EOF'
[Unit]
Description=free-claude-code proxy
After=network-online.target
[Service]
User=fcc
WorkingDirectory=/home/fcc/free-claude-code
ExecStart=/home/fcc/.local/bin/uv run uvicorn server:app --host 0.0.0.0 --port 8082 --workers 3
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload && systemctl enable --now fcc.service
```

Notes:
- **`HOST=0.0.0.0`** so IAP forwarding can reach the port on the instance. The firewall (IAP range only) is what keeps it private — it is never exposed to the public internet.
- **`--workers 3`** is a sane start for 10–50 users; each uvicorn worker handles many concurrent async/streaming connections. Tune up if you see saturation.
- The Admin UI is **loopback-only by design** — it refuses non-127.0.0.1 requests. That's good (it won't be exposed through the tunnel) but means config changes happen from *on the VM* (see §5).

---

## 4. IAM — who is allowed through IAP

Grant the IAP tunnel role to a **Google Group** (e.g. `eng-claude@jota.ai`), not individuals, so onboarding/offboarding is just group membership:

```bash
gcloud compute instances add-iam-policy-binding fcc-proxy --zone=<ZONE> \
  --member='group:eng-claude@jota.ai' \
  --role='roles/iap.tunnelResourceAccessor'
```

(For SSH-based admin access, the same group or an admins group also needs `roles/compute.osLogin` + IAP. Restrict the Admin/SSH path to a small admins group.)

This is the entire access-control surface: if you're in the group you can tunnel; if not, IAP rejects you before a single byte reaches the proxy.

---

## 5. Configure / change the provider (admin task)

Because the Admin UI is loopback-only, admins manage it from the VM over an IAP SSH tunnel:

```bash
# SSH into the VM through IAP, forwarding the admin port to your laptop
gcloud compute ssh fcc-proxy --zone=<ZONE> --tunnel-through-iap -- -L 8082:localhost:8082
# now open http://localhost:8082/admin  — the request originates on the VM's loopback,
# so the Admin UI's local-only check passes
```

In the Admin UI: paste the provider key, set `MODEL` / `MODEL_OPUS` / `MODEL_SONNET` / `MODEL_HAIKU`, **Validate**, **Apply**. To swap providers later, change only these fields — no redeploy. For durable config, also update the Secret Manager secret so a VM rebuild restores it.

---

## 6. Onboard the engineers (client side)

Each engineer needs `gcloud` and membership in the IAP group. Ship one wrapper script internally, e.g. `fcc-connect`:

```bash
#!/usr/bin/env bash
# Opens the IAP tunnel (if not already up) and launches Claude Code against it.
set -euo pipefail
ZONE="<ZONE>"; PROJECT="<PROJECT>"

# Start tunnel in background if port 8082 isn't already listening
if ! nc -z localhost 8082 2>/dev/null; then
  gcloud compute start-iap-tunnel fcc-proxy 8082 \
    --local-host-port=localhost:8082 --zone="$ZONE" --project="$PROJECT" &
  # wait for it to come up
  for _ in $(seq 1 20); do nc -z localhost 8082 && break; sleep 0.5; done
fi

export ANTHROPIC_BASE_URL="http://localhost:8082"
export ANTHROPIC_AUTH_TOKEN="freecc"
export CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1   # enables the /model picker
export CLAUDE_CODE_AUTO_COMPACT_WINDOW=190000
exec claude "$@"
```

One-time per engineer: `gcloud auth login`. After that, `fcc-connect` just works. The same three env vars also drop into the **VS Code** (`claudeCode.environmentVariables`) and **JetBrains ACP** configs shown in the repo README — point them at `http://localhost:8082` while the tunnel runs.

---

## 7. Verification checklist

1. **Server health:** SSH in, `systemctl status fcc`, `curl -s localhost:8082/v1/models` returns the model list.
2. **Firewall is private:** confirm the VM has no external IP and the only ingress rule is the IAP CIDR. From a non-tunnel host, the port must be unreachable.
3. **IAP gate works:** a user *not* in the group running `start-iap-tunnel` must be rejected; a member must succeed.
4. **End-to-end:** member runs `fcc-connect`, sends a real Claude Code prompt, gets a streamed response; confirm provider dashboard shows the call.
5. **Token expiry:** keep a session open >60 min to confirm the tunnel survives (it should — gcloud refreshes).
6. **Concurrency smoke test:** 5–10 simultaneous sessions; watch VM CPU and provider rate-limit headers.

---

## 8. Operations & scaling

- **Monitoring:** Ops Agent on the VM for CPU/mem; alert on `fcc.service` restarts. Loguru logs go to journald → Cloud Logging.
- **Updates:** `git pull && uv sync && systemctl restart fcc` over IAP SSH, or rebuild from the startup script.
- **Provider rate limits:** the single shared key is the main bottleneck at 10–50 users. Watch 429s; a paid provider or a higher quota tier removes this. Free tiers will throttle under concurrent org load.
- **Scaling past one VM:** if 3 workers saturate, first resize the VM. Beyond that, a managed instance group behind an **internal** TCP load balancer (still IAP-tunneled) gives horizontal scale — but only go there if metrics demand it.
- **Backups:** the only state is `~/.fcc/.env` (and Secret Manager). No database.

---

## 9. Open questions to settle before building

1. **Provider choice + data terms** — the §0 governance decision. Blocks go-live, not the build.
2. **Region** — `southamerica-east1` for BR latency, or wherever your provider has lowest latency.
3. **Who owns the provider billing/quota** and what's the monthly cap.
4. **Admin group vs user group** — confirm the two IAM groups (tunnel users vs VM admins).

---

### TL;DR sequence
Sign off on provider/data → create project, VPC, NAT, firewall, Secret Manager → boot VM with startup script (proxy as systemd) → grant `iap.tunnelResourceAccessor` to the eng group → admin sets provider via Admin UI over IAP SSH → distribute `fcc-connect` wrapper → verify with the §7 checklist.
