# free-claude-code GCP deployment — operator runbook

Deploys one shared `free-claude-code` proxy on a GCE VM with **no external IP**,
reached by every engineer's Claude Code over **GCP IAP TCP forwarding**. Access
is controlled entirely by IAP + IAM group membership.

> **Infra is now provisioned declaratively via Crossplane** —
> see [`deploy/crossplane/`](crossplane/README.md). That is the **primary** source of
> truth (matches the org's `octopus` Crossplane setup). The `provision.sh` script below
> is kept as a **reference / fallback** for environments without the Crossplane control
> plane. `startup.sh` and `fcc-connect` are used by **both** paths.

These scripts are the runnable form of the design in:

- [`domain_docs/free-claude-code-gcp-plan.md`](../domain_docs/free-claude-code-gcp-plan.md) — overall plan, architecture, sequence.
- [`domain_docs/networking.md`](../domain_docs/networking.md) — CIDRs, NAT/router, tag-scoped firewall, IAP quota, wrapper fixes.
- [`domain_docs/cloud_infra.md`](../domain_docs/cloud_infra.md) — VM, service account, OS Login, Private Google Access.
- [`domain_docs/security.md`](../domain_docs/security.md) — minimal IAM, secret-level binding, no key on disk, admin-plane split.

## Files

| File | Role |
|---|---|
| `crossplane/` | **Primary.** GCP infra as Crossplane Managed Resources + kustomize overlays. See [`crossplane/README.md`](crossplane/README.md). |
| `provision.sh` | **Reference/fallback.** Imperative `gcloud` provisioning of the same infra (VPC, NAT, firewall, SA, secret, VM, IAP IAM). Re-runnable. |
| `startup.sh` | VM startup script (used by both paths). Installs the proxy under systemd, joins the Tailscale tailnet on boot. Provider key is fetched at runtime / via tmpfs — never written to persistent disk. |
| `fcc-connect-tailscale` | **Primary client wrapper.** Reaches the proxy by its tailnet MagicDNS name (matches the staging Tailscale access pattern). No gcloud needed. |
| `fcc-connect` | **Fallback** client wrapper. Opens an IAP tunnel and launches Claude Code (for environments without Tailscale). |

## Architecture

```
engineer laptop                         GCP (no external IP)
┌──────────────────┐    IAP tunnel    ┌──────────────────────────┐
│ claude (CLI/IDE) │◀───(Google auth)─▶│ GCE VM  fcc-server :8082 │
│ BASE_URL=        │                  │  → provider (swappable)  │
│  localhost:<port>│                  └──────────────────────────┘
└──────────────────┘                  firewall: tcp:8082 only from
   gcloud start-iap-tunnel             35.235.240.0/20, tag fcc-proxy
```

- Subnet CIDR: `10.128.0.0/20`, Private Google Access enabled (so the no-IP VM reaches Secret Manager / Logging).
- Egress: Cloud Router + Cloud NAT (the VM has no public IP but must reach the provider API).
- Firewall: two **tag-scoped** rules — `allow-iap-fcc-proxy` (tcp:8082, tag `fcc-proxy`) and `allow-iap-fcc-ssh` (tcp:22, tag `fcc-admin`), both sourced only from the IAP range `35.235.240.0/20`.
- Service account `fcc-sa`: `roles/secretmanager.secretAccessor` on the one secret (secret-level binding) + optional `roles/logging.logWriter`. No broad `cloud-platform` scope on the VM.

## Prerequisites

1. **gcloud** installed and authenticated (`gcloud auth login`) with rights to create infra in the target project.
2. **Project** exists (default `jota-fcc-proxy`) and billing is enabled.
3. **IAM groups** decided:
   - tunnel users — get `roles/iap.tunnelResourceAccessor` (default `ai-gateway@jota.ai`).
   - VM admins — get `roles/compute.osLogin` for SSH to the Admin UI.
4. **OS Login** is enabled on the VM (set by `provision.sh` via `enable-oslogin=TRUE`).
5. **IAP TCP forwarding quota.** Default is **25 tunnels/project** — too low for 50 users (proxy + SSH tunnel each ≈ 100). Request an increase **before** rollout: Console → IAM & Admin → Quotas → filter "IAP TCP forwarding". Verify with `gcloud compute project-info describe --project <PROJECT> | grep -A5 -i iap`.
6. **Provider/data sign-off** — see `free-claude-code-gcp-plan.md` §0. The proxy sends full prompts/code to a third-party provider; get governance approval first.

## Order of operations

```bash
# 1. Configure (override any default via env var).
export PROJECT=jota-fcc-proxy
export REGION=southamerica-east1
export ZONE=southamerica-east1-a
export IAP_USER_GROUP='group:ai-gateway@jota.ai'
export ADMIN_GROUP='group:ai-gateway-admins@jota.ai'

# 2. Provision everything. Re-runnable; create steps tolerate "already exists".
cd deploy
./provision.sh

# 3. Add the provider API key to Secret Manager (container created by provision.sh).
printf '%s' "$PROVIDER_KEY" | gcloud secrets versions add fcc-provider-key \
  --project="$PROJECT" --data-file=-

# 4. (If using the tmpfs key path instead of runtime fetch) re-run the startup
#    script by resetting the VM, or just reboot it:
#    gcloud compute instances reset fcc-proxy --zone="$ZONE" --project="$PROJECT"
```

`provision.sh` runs, in order: enable APIs → VPC + subnet (Private Google Access)
→ Cloud Router → Cloud NAT → two tag-scoped firewall rules → service account
→ Secret Manager secret + secret-level IAM binding → VM (no IP, minimal scopes,
OS Login, startup script) → IAP/OS-Login IAM bindings → quota reminder.

## Admin: reach the Admin UI over IAP SSH

The Admin UI is loopback-only on the VM, so admins manage it from **on the VM**
through an IAP SSH tunnel that forwards the port to the laptop:

```bash
gcloud compute ssh fcc-proxy --zone="$ZONE" --project="$PROJECT" \
  --tunnel-through-iap -- -L 8082:localhost:8082
# then open http://localhost:8082/admin  (request originates on the VM loopback)
```

In the Admin UI: paste the provider key, set `MODEL` / `MODEL_OPUS` /
`MODEL_SONNET` / `MODEL_HAIKU`, **Validate**, **Apply**. To swap providers later,
change only these fields — no redeploy. Keep the Secret Manager secret in sync so
a VM rebuild restores config.

The gateway also exposes `GET /v1/models/delegates` — the delegate catalog
(no-thinking ids) for the `claudim` launcher. The catalog is the union of
`MODEL_DELEGATE_ALLOWLIST` (free delegates) and `MODEL_DELEGATE_APPROVAL`
(human-gated per spawn); both `fnmatch` globs, both empty = no delegates.
`/v1/models` is unfiltered so the human `/model` picker still sees every
model, but enforcement is hard: once at least one list is configured, the
gateway rejects subagent requests on models outside the catalog at request
time (only Claude Code's main conversation loop, detected by its
system-prompt marker, is exempt).

> Note: `security.md` #5 flags that the loopback check is not real authentication
> over a tunnel. Restrict the `fcc-admin` SSH path (`roles/compute.osLogin`) to a
> small admins group, and track adding real Admin UI auth as follow-up.

## Engineers: connect with `fcc-connect`

One-time: `gcloud auth login` and confirm membership in the tunnel group.

```bash
# distribute deploy/fcc-connect internally (e.g. drop on PATH), then:
fcc-connect            # opens the tunnel on a free local port, launches claude
fcc-connect "$@"       # all args pass through to claude
```

`fcc-connect` picks a **free** local port (not a hardcoded 8082), starts the
tunnel with `--iap-tunnel-disable-connection-timeout`, waits up to 60s with a
hard failure gate, then exports `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`,
`CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW`
and execs `claude`. For VS Code / JetBrains, point their env config at the
`http://localhost:<port>` the tunnel is using.

**Recovery if the tunnel wedges:**

```bash
pkill -f 'compute start-iap-tunnel'   # kill stale tunnels
fcc-connect                           # reconnect
```

## Renaming & local-test installs (claudim)

The `claudim` wrapper derives its name from `$0` at runtime — the name is not
a hard dependency. **`CLAUDIM_NAME`** at install time produces a different
binary name, skill names, and allowlist:

```sh
CLAUDIM_NAME=buxexa bash scripts/install-claudim.sh  # → command `buxexa`
```

**`loclaudim`** is a **convention** for a local-test install, not the canonical
name:

```sh
CLAUDIM_NAME=loclaudim CLAUDIM_DEFAULT_BASE_URL=http://localhost:8082 \
  bash scripts/install-claudim.sh
```

Gateway URL precedence: `CLAUDIM_BASE_URL` (env) > baked `CLAUDIM_DEFAULT_BASE_URL` > `http://<host>.<tailnet>:<port>`.

Env vars keep the `CLAUDIM_` prefix regardless of binary name (stable interface).

## Verification checklist

Mirrors the checklists in the domain docs (plan §7, networking, cloud_infra, security).

Infra / networking:
- [ ] VM has **no external IP**: `gcloud compute instances describe fcc-proxy --zone="$ZONE" --format="get(networkInterfaces[0].accessConfigs)"` is empty.
- [ ] Subnet Private Google Access on: `gcloud compute networks subnets describe fcc-subnet --region="$REGION" --format="get(privateIpGoogleAccess)"` → `True`.
- [ ] Cloud Router exists; Cloud NAT created on it: `gcloud compute routers nats list --router=fcc-router --region="$REGION"`.
- [ ] Firewall rules are **tag-scoped**: `allow-iap-fcc-proxy` (tcp:8082, target `fcc-proxy`) and `allow-iap-fcc-ssh` (tcp:22, target `fcc-admin`), source `35.235.240.0/20` only.
- [ ] VM has tags `fcc-proxy,fcc-admin`.
- [ ] IAP tunnel quota raised to ≥100.

Security:
- [ ] Service account has only the secret accessor (secret-level) + optional logging roles; VM has **no** `cloud-platform` scope.
- [ ] Provider key is **not** on persistent disk: `startup.sh` writes no plaintext `.env`; runtime fetch is the default, tmpfs (`mount | grep .fcc`) is the only fallback and is `chmod 600`.
- [ ] Proxy process has no key in env on the runtime-fetch path: `sudo cat /proc/$(pgrep -f uvicorn | head -1)/environ | tr '\0' '\n' | grep -i key` shows none.

Access / end-to-end:
- [ ] SSH via IAP works with OS Login: `gcloud compute ssh fcc-proxy --zone="$ZONE" --tunnel-through-iap`.
- [ ] Server healthy on the VM: `systemctl status fcc` and `curl -s localhost:8082/v1/models` returns models.
- [ ] Delegate endpoint: `curl -s localhost:8082/v1/models/delegates` returns the delegate catalog (only no-thinking ids you listed in `MODEL_DELEGATE_ALLOWLIST`/`MODEL_DELEGATE_APPROVAL`; empty when both are unset).
- [ ] IAP gate works: a user **not** in the group is rejected by `start-iap-tunnel`; a member succeeds.
- [ ] End-to-end: a member runs `fcc-connect`, sends a real prompt, gets a streamed response; the provider dashboard shows the call.
- [ ] Long session: a session open >60 min survives (gcloud refreshes IAP creds; timeout disabled in the wrapper).
