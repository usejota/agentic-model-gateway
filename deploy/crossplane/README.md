# free-claude-code proxy — GCP infra (Crossplane)

Declarative GCP infrastructure for the shared `free-claude-code` proxy, as Crossplane
Managed Resources. This is the **primary** source of truth for the proxy's infra; the
imperative `deploy/provision.sh` is kept only as a reference/fallback.

Patterns mirror the org's existing Crossplane setup in the `octopus` repo
(`infra-as-code/`): Upbound `provider-gcp-*` family, `ProviderConfig: default` with
Workload Identity (`InjectedIdentity`), raw Managed Resources + kustomize overlays.

## What this provisions

| File | Resource(s) | Hardening |
|------|-------------|-----------|
| `base/network.yaml` | `Network` + `Subnetwork` | Private Google Access on; explicit `10.128.0.0/20` CIDR |
| `base/router-nat.yaml` | `Router` + `RouterNAT` | egress for no-external-IP VM (AUTO_ONLY) |
| `base/firewall.yaml` | 2× `Firewall` | tag-scoped: `fcc-proxy` tcp:8082, `fcc-admin` tcp:22, from IAP CIDR `35.235.240.0/20` |
| `base/service-account.yaml` | `ServiceAccount` `fcc-sa` | dedicated, least privilege |
| `base/secret.yaml` | `Secret` `fcc-provider-key` + `fcc-tailscale-oauth` | containers only — **values set out-of-band** |
| `base/iam.yaml` | `SecretIAMMember` ×2 + `ProjectIAMMember` + `InstanceIAMMember` ×2 | secret-LEVEL accessors + `logging.logWriter` + access-control |
| `base/instance.yaml` | `Instance` `fcc-proxy` | no external IP, OS Login, tags, runtime secret fetch, **joins tailnet on boot** |

## Access model: Tailscale (primary in staging)

Staging already runs a Tailscale subnet router (`tailscale-fw-default-stg`) and uses
Tailscale to reach private VMs (e.g. the Vault/PKI group). The proxy follows that
convention: on boot the VM **joins the tailnet** (startup.sh §3b) using a Tailscale
**OAuth client** secret from Secret Manager, tagged `tag:fcc-proxy`. Engineers reach it by
MagicDNS name with [`deploy/fcc-connect-tailscale`](../fcc-connect-tailscale) — no gcloud,
no IAP tunnel. Who may reach the node is governed by the **tailnet ACL** (allow your
user/group → `tag:fcc-proxy` on tcp:8082).

The IAP path (firewall on `35.235.240.0/20`, `fcc-iap-tunnel-users` IAM, `deploy/fcc-connect`)
is **kept as a fallback** for environments without Tailscale, and IAP SSH remains available
for admins via OS Login.

**One-time Tailscale admin setup (not in this repo):**
1. Create an OAuth client (Tailscale admin → Settings → OAuth clients) with scope
   `devices:write` and the tag `tag:fcc-proxy`. Copy the client secret.
2. Define `tag:fcc-proxy` as owned by that OAuth client in the tailnet policy file.
3. Add an ACL grant: the eng users/group → `tag:fcc-proxy:8082`.
4. Store the client secret: `echo -n "<secret>" | gcloud secrets versions add fcc-tailscale-oauth --project=<PROJECT> --data-file=-`

## Prerequisites

1. **Control plane reachable.** `kubectl` context points at the cluster running
   Crossplane (the octopus control plane). Verify providers are healthy:
   ```bash
   kubectl get providers.pkg.crossplane.io
   # expect provider-gcp-compute, provider-gcp-cloudplatform, provider-gcp-secretmanager INSTALLED+HEALTHY
   ```
   These are already installed per `octopus/infra-as-code/platform/providers/sub-providers-gcp.yaml`.
2. **Workload Identity SA** behind `ProviderConfig: default` must have rights to create
   these resources in the target project (compute, IAM, secretmanager).
3. **GCP project exists** and the compute/secretmanager/IAM APIs are enabled (or add a
   `ProjectService` MR / enable via the octopus project composition).
4. **IAP tunnel quota** — default 25 simultaneous tunnels/project is too low for ~50
   users (proxy + SSH tunnels). Request an increase (target 100+) via Cloud Console →
   IAM & Admin → Quotas, or a support ticket. **24–48h lead — start this first.** This
   is NOT a Crossplane resource.

## Apply

1. Fill placeholders in `overlays/prod/kustomization.yaml`:
   ```bash
   cd deploy/crossplane/overlays/prod
   sed -i '' 's/<PROJECT>/jota-fcc-proxy/g; s/<REGION>/southamerica-east1/g; s/<ZONE>/southamerica-east1-a/g' kustomization.yaml
   ```
   (Linux: drop the `''` after `-i`.)

2. Embed the VM startup script. The `Instance`'s `metadataStartupScript` is blank in
   base; inject the contents of `deploy/startup.sh` before applying. Either paste it in,
   or add a kustomize patch that sets `/spec/forProvider/metadataStartupScript`. The
   startup script reads the `PROVIDER_KEY_SECRET_RESOURCE` / `fcc-secret-name` / `fcc-port`
   metadata already wired on the instance.

3. Render and dry-run before applying for real:
   ```bash
   kubectl kustomize deploy/crossplane/overlays/prod            # static render
   kubectl apply -k deploy/crossplane/overlays/prod --dry-run=server   # validates against installed CRDs
   ```

4. Apply:
   ```bash
   kubectl apply -k deploy/crossplane/overlays/prod
   ```

5. Watch reconciliation until all resources are `READY=True SYNCED=True`:
   ```bash
   kubectl get managed -l service=free-claude-code
   kubectl describe <kind> <name> -n crossplane-system   # for any not-ready resource
   ```

## Set the provider key value (out-of-band — never in git)

The `Secret` MR creates the container only. Add the actual key as a SecretVersion:
```bash
echo -n "<provider-api-key>" | gcloud secrets versions add fcc-provider-key \
  --project=<PROJECT> --data-file=-
```
The proxy reads it at runtime via `PROVIDER_KEY_SECRET_RESOURCE`
(`projects/<PROJECT>/secrets/fcc-provider-key/versions/latest`) — the key never lands on
the VM disk. Rotation = add a new version + restart the service. See
`domain_docs/security.md` (#3, #4).

## Access-control IAM (target-project, set once)

Tunnel users and admins are gated by IAM on the existing project, not by these MRs.
Grant to a Google Group so onboarding is group membership:
```bash
# Tunnel users (the whole eng group)
gcloud compute instances add-iam-policy-binding fcc-proxy --zone=<ZONE> \
  --member='group:ai-gateway@jota.ai' --role='roles/iap.tunnelResourceAccessor'
# SSH admins (smaller group) — OS Login
gcloud compute instances add-iam-policy-binding fcc-proxy --zone=<ZONE> \
  --member='group:ai-gateway-admins@jota.ai' --role='roles/compute.osLogin'
```
These can be promoted to `ProjectIAMMember` / instance-level IAM MRs in a follow-up if
fully-declarative access control is wanted.

## Verification checklist

- [ ] `kubectl get managed -l service=free-claude-code` → all `READY=True SYNCED=True`
- [ ] VM has **no external IP**: `gcloud compute instances describe fcc-proxy --zone=<ZONE> --format='get(networkInterfaces[0].accessConfigs)'` is empty
- [ ] Subnet PGA on: `... subnets describe sn-fcc --region=<REGION> --format='get(privateIpGoogleAccess)'` → `True`
- [ ] Two tag-scoped firewall rules exist (`allow-iap-fcc-proxy`, `allow-iap-fcc-ssh`)
- [ ] Router + NAT present in `<REGION>`
- [ ] `fcc-sa` has secret-level accessor (`gcloud secrets get-iam-policy fcc-provider-key`)
- [ ] Secret value set (a SecretVersion exists)
- [ ] IAP quota raised; IAP group + OS Login IAM bound
- [ ] End-to-end: `deploy/fcc-connect` → prompt streams; admin UI requires `ADMIN_API_TOKEN`; per-user token audit line in JSON logs

## Octopus conventions: what's followed, what diverges

Checked against `octopus/infra-as-code` practices:

| Practice | Here |
|----------|------|
| Upbound `provider-gcp-*`, `ProviderConfig: default` + Workload Identity | ✅ followed |
| Raw MR + kustomize base/overlays | ✅ followed |
| `deletionPolicy: Orphan` on every MR | ✅ followed (all 13 MRs) |
| `service:` + `environment:` labels on all resources | ✅ followed (env via overlay `labels:`) |
| Least-privilege per-workload GSA | ✅ + stricter: **secret-LEVEL** accessor (marble uses project-level) |
| Secret value out-of-band (never in git) | ✅ followed |
| Fully declarative access-control IAM | ✅ `InstanceIAMMember` for IAP tunnel users + OS Login admins |
| **Secret delivery via ESO** (SecretStore + ExternalSecret, `gcpsm` provider) | ⚠️ **N/A — intentional divergence** (see below) |
| Vault (PKI/mTLS infra) | ⚠️ N/A — not applicable to a single proxy VM (see below) |
| `deletion_protection` on stateful infra | n/a — no GKE/CloudSQL here; Orphan covers the VM/secret |

### Why no ESO / Vault (intentional divergence)
Octopus delivers app secrets with **External Secrets Operator** (`gcpsm` SecretStore +
ExternalSecret → k8s Secret → pod env), and runs **Vault** purely for PKI/mTLS infra —
neither is used for workload secret delivery. **Both assume workloads run as pods in
GKE.** This proxy runs on a **standalone GCE VM, not GKE** — there is no kubelet to mount
a k8s Secret and no Workload Identity pod binding. The correct VM-native analog of the
ESO pattern is exactly what's implemented: the VM's GSA (`fcc-sa`) reads the secret
**directly from Secret Manager at runtime** via `PROVIDER_KEY_SECRET_RESOURCE` (in-memory,
never on disk) — same source of truth (GCP Secret Manager), same least-privilege WI/SA
auth, minus the k8s indirection that doesn't exist on a VM. If the proxy is ever moved
onto GKE, switch to the ESO SecretStore/ExternalSecret pattern to match octopus exactly.

Vault PKI is not introduced because there is no in-VM mTLS requirement here — transport
security is provided by the IAP tunnel (see `domain_docs/security.md` #6); adding a Vault
dependency for one VM would be unjustified scope.

## Notes / limitations

- **Raw-MR cross references** use explicit `*Ref.name` (the MR `metadata.name`), not
  `matchControllerRef` — that only works inside a Composition. Verify exact ref field
  names against the installed upbound CRD schema if a resource fails to resolve:
  `kubectl explain instance.spec.forProvider.networkInterface`.
- Single `prod` overlay (one shared proxy). Add `overlays/stg` by copying prod and
  changing project/region if a staging proxy is later needed.
- Octopus control-plane GitOps wiring (auto-applying these manifests from this repo) is
  a separate cross-repo follow-up; for now apply manually with `kubectl apply -k`.

See also: `domain_docs/networking.md`, `domain_docs/cloud_infra.md`,
`domain_docs/security.md`, and `deploy/README.md` (bash reference path).
