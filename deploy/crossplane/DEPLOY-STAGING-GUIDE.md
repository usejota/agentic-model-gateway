# Deploying the free-claude-code proxy to staging

Audience: you're comfortable with the terminal, git, and general infra, but **new to
Kubernetes and Crossplane**, and you want the **networking explained rather than assumed**.
This guide front-loads the two concepts you're missing, then runs the deploy. Skip the
concept sections if they're old news.

Target: GCP project `stp-core-dev`, region `us-west1`, zone `us-west1-a`. Values are already
baked into `deploy/crossplane/overlays/stg/kustomization.yaml`.

---

## Concept 1 — Crossplane in 3 minutes (the part you don't know yet)

**The problem it solves:** instead of running `gcloud` commands to create a VM, a network,
a firewall — which is imperative and drifts over time — you *declare* the desired infra as
Kubernetes objects, and a controller continuously makes GCP match. It's Terraform's job, but
running as an always-on reconciler inside a cluster instead of a CLI you invoke.

**The pieces, mapped to things you know:**

- **Kubernetes cluster** — here it's just a *control plane*. We are **not** running the proxy
  as a pod. The cluster's only job in this story is to host Crossplane. Think of it as "the
  server where the reconciler lives."
- **Crossplane** — a set of Kubernetes controllers. You install **providers** (plugins) into
  it; the GCP providers know how to CRUD GCP resources via the Google APIs.
- **Managed Resource (MR)** — a Kubernetes custom resource that maps 1:1 to a real cloud
  resource. `kind: Network` ⇒ a GCP VPC. `kind: Instance` ⇒ a GCE VM. `kind: Firewall` ⇒ a
  firewall rule. You write these as YAML. The full list of kinds = whatever the installed
  providers support.
- **The reconcile loop** — after you `apply` an MR, the controller calls the GCP API to make
  it exist, then **keeps checking forever**. Two status fields tell you where it is:
  - `SYNCED=True` — Crossplane successfully talked to GCP about this resource (no API/auth
    error).
  - `READY=True` — GCP reports the resource actually exists and is usable.
  Both `True` = done. This polling model is why "apply" returns instantly but the VM takes a
  few minutes — you're watching an async controller, not a blocking script.
- **`ProviderConfig`** — tells the GCP provider *which credentials* to use. Here it's named
  `default` and uses **Workload Identity** (the cluster's own GCP identity), so there are no
  JSON keys anywhere. You inherit whatever that identity is allowed to do.
- **How `gcloud` vs `kubectl` split:** `kubectl` talks to the **cluster** (apply MRs, read
  status). `gcloud` talks to **GCP directly** (log in, connect to the cluster, set a secret
  value). You use both, for different things.

**kustomize / overlays** — our infra is written once in `base/` with project/region left
blank, and an *overlay* (`overlays/stg/`) patches in the staging values. `kubectl kustomize`
renders base+overlay into final YAML. It's templating-by-merge; no separate tool to install,
it's built into `kubectl`.

So the whole deploy is: **render YAML (kustomize) → hand it to the cluster (`kubectl apply`)
→ Crossplane reconciles it into real GCP resources → you watch status until ready.**

---

## Concept 2 — The networking (your weak spot, so in full)

Four design choices, each with a *why*:

**1. The VM has no external (public) IP.** A public IP is attack surface. The proxy carries
banking source code, so we don't give it one. Consequence: nothing on the internet can reach
it, *and* it can't reach the internet on its own — which the next two points fix.

**2. Cloud NAT (+ Cloud Router) for outbound.** The proxy still needs to call the upstream
model provider's API (outbound HTTPS). With no public IP, that needs **NAT** — a gateway that
lets private VMs make outbound connections through a shared external IP, while still accepting
nothing inbound. Cloud NAT is GCP's managed version; it requires a **Cloud Router** to exist
first (the router is the control-plane piece NAT attaches to). That's why you see both a
`Router` and a `RouterNAT` MR. This is outbound-only — it does not make the VM reachable.

**3. Two tag-scoped firewall rules.** Firewall rules in GCP attach to VMs by **network tags**
(labels on the instance), not by IP. Our VM has tags `fcc-proxy` and `fcc-admin`. One rule
allows tcp:8082 (the proxy) to instances tagged `fcc-proxy`; another allows tcp:22 (SSH) to
`fcc-admin`. Both only from the IAP source range `35.235.240.0/20` (Google's range for IAP
tunneling) — so even the "allowed" traffic must come through Google's IAP, not the open
internet. Tag-scoping matters because a firewall rule with no target applies to *every* VM in
the VPC; tags pin it to ours.

**4. How you actually reach things — two different paths for two different jobs.** Don't
conflate them:

  - **Reaching the Crossplane GKE control plane (to run `kubectl` and deploy).** This is what
    *you* do to deploy. The control plane is the private cluster `gke-crossplane-stg` (master
    `172.31.255.2`) in the **`jota-infra`** project. The supported way in is a purpose-built
    **bastion**: `vm-crossplane-bastion-stg` (`jota-infra`, us-west1-a). You SSH to it through
    Google **IAP** and run `kubectl` from there. The bastion sits inside the VPC, so it can
    reach the private master; you don't need Tailscale for this. (There is also a
    `jota-infra-tailscale-fw-us-west1` forwarder in that project, but the bastion is the
    named, intended deploy path — use it.)
  - **Reaching the proxy VM (for engineers, to USE the proxy once deployed).** Separate job.
    The proxy VM **joins the tailnet on boot** (our `startup.sh` runs `tailscale up`), gets its
    own `100.x` address + MagicDNS name `fcc-proxy.<tailnet>.ts.net`, and anyone on the tailnet
    the ACL allows hits it directly — `deploy/fcc-connect-tailscale`.

  **The project topology (worth fixing in your head):**

  | Thing | Project | Notes |
  |-------|---------|-------|
  | Crossplane control plane `gke-crossplane-stg` | **`jota-infra`** | where you `kubectl apply` |
  | Bastion `vm-crossplane-bastion-stg` | **`jota-infra`** | your SSH-in deploy box (IAP) |
  | The fcc infra we create (VM, network, …) | **`stp-core-dev`** | our overlay sets `project: stp-core-dev` on every resource |
  | App/staging cluster `gke-core-stg-dzrw2` | `stp-core-dev` | unrelated to our deploy |
  | PKI forwarder `tailscale-fw-default-stg` (`10.138.0.0/20`) | `stp-core-dev` | reaches the PKI group + the fcc VM's subnet; a fallback path |

  So: **you deploy *from* `jota-infra` (via the bastion), Crossplane creates resources *in*
  `stp-core-dev`** (because that's what our YAML says). This works only if the Crossplane
  control plane's identity is allowed to create resources in `stp-core-dev` — confirmed at
  dry-run; if not, you'll see "permission denied" and need a cross-project IAM grant.

  **MagicDNS** = Tailscale DNS resolving `*.ts.net` names to tailnet IPs.
  **Subnet router / forwarder** = a tailnet node that forwards traffic for VPC IP ranges.
  **Bastion** = a hardened jump host inside the VPC you SSH through to reach private resources.
  **Tailnet ACL** = Tailscale's allow-list of who reaches what; a *second* access-control layer
  alongside GCP IAM (a known tradeoff — see the
  governance note at the end).

  > IAP is still wired as a **fallback** (the firewall rules, `fcc-connect`). With Tailscale
  > working you won't use it, but it's there for environments without a tailnet.

That's the whole network. Now the steps.

---

## What needs a human with access (do this first)

None of this is in the repo; resolve before Step 4:

1. **IAP SSH access to the bastion** `vm-crossplane-bastion-stg` in `jota-infra`
   (`roles/iap.tunnelResourceAccessor` + OS Login on that VM/project), and **kubectl/RBAC
   access** to `gke-crossplane-stg`. If you've never deployed to the Crossplane cluster, this
   is the usual first-time blocker — ask whoever owns `jota-infra` (the cluster is shared infra).
2. **(For the proxy's own access, not for deploying)** the Tailscale **OAuth client** +
   tailnet **ACL** for `tag:fcc-proxy`, and the two Google Groups
   `eng-claude@jota.ai` / `eng-claude-admins@jota.ai`. Covered in Step 6 / Step 9; not needed
   to get the infra up.

> Note on Tailscale: you do **not** need it to *deploy* — deploying goes through the bastion.
> Tailscale only matters later, for engineers to *use* the proxy (model A: the fcc VM joins the
> tailnet). The `tailscale-fw-default-stg` forwarder you may have fixed is for the PKI group and
> as a fallback route to the fcc subnet — not part of the deploy path.

---

## Step 1 — Tools

You have `git` and `kubectl`. You're **missing `gcloud`**:

```bash
brew install --cask google-cloud-sdk     # or: curl https://sdk.cloud.google.com | bash
gcloud --version && kubectl version --client
```

`kubectl` you already have because it's the cluster client; `gcloud` is the GCP client. You
need both (see Concept 1).

---

## Step 2 — Authenticate to GCP

```bash
gcloud auth login                         # browser → your @jota.ai (you're adm-paulo@)
gcloud config set project jota-infra      # where the bastion + Crossplane cluster live
```

This authenticates *you* to GCP. It does **not** connect kubectl to anything yet and changes
no infra. Note the project is **`jota-infra`** (the control-plane/bastion project) — the fcc
resources land in `stp-core-dev` later, but that's Crossplane's doing, not yours.

**Sanity check you can see the pieces:**

```bash
gcloud compute instances list --project=jota-infra --filter="name~bastion OR name~crossplane"
gcloud container clusters list --project=jota-infra      # gke-crossplane-stg, master 172.31.255.2
```

---

## Step 3 — Render the manifests and read them

```bash
kubectl kustomize deploy/crossplane/overlays/stg
```

This runs the base+overlay merge locally and prints final YAML. Nothing leaves your machine.
You'll see ~15 MRs. Worth actually reading once, now that you know what each kind maps to:

| Kind | Real GCP thing | Notes |
|------|----------------|-------|
| `Network` + `Subnetwork` | VPC + subnet | subnet has `privateIpGoogleAccess: true`, CIDR `10.128.0.0/20` |
| `Router` + `RouterNAT` | Cloud Router + NAT | outbound egress (Concept 2 #2) |
| `Firewall` ×2 | firewall rules | tag-scoped, IAP range only (Concept 2 #3) |
| `ServiceAccount` | the VM's GCP identity | least-privilege |
| `Secret` ×2 | Secret Manager containers | `fcc-provider-key`, `fcc-tailscale-oauth` — **values added later, out-of-band** |
| `SecretIAMMember` ×2 + `ProjectIAMMember` | IAM grants | secret-level accessor + log writer |
| `Instance` | the GCE VM | `e2-standard-2`, no public IP, joins tailnet on boot |
| `InstanceIAMMember` ×2 | who may IAP-tunnel / OS-Login SSH | fallback access path |

Confirm every resource says `project: stp-core-dev` / `us-west1` — that's your "aimed at
staging" check.

---

## Step 4 — Open a tunnel to the Crossplane cluster (the octopus pattern)

This is the part everyone gets wrong at first (I did too). **You do NOT shell into the bastion
and run kubectl there.** The bastion's own service account can't touch the cluster, and that's
fine — the bastion is just a **dumb TCP tunnel**. You run kubectl on **your laptop**, as
**yourself**, with traffic forwarded through the bastion to the cluster's private API. This is
exactly how octopus deploys Marble (`mise run crossplane-tunnel-stg` + `infra-marble-stg`).

Helon's words: *"eu não acesso o cluster do crossplane, só envio o yaml"* — you don't log into
the cluster, you just send YAML through a forwarded port.

**Get the cluster's private endpoint** (you'll plug it into the tunnel):

```bash
gcloud container clusters describe gke-crossplane-stg \
  --region=us-west1 --project=jota-infra \
  --format='value(privateClusterConfig.privateEndpoint)'
# e.g. 172.31.255.2
```

**Terminal 1 — open the tunnel and LEAVE IT RUNNING.** `-L 8443:<ENDPOINT>:443 -N` means
"forward my laptop's localhost:8443 → through the bastion → to the cluster API on :443, and
run no remote command (`-N`)":

```bash
gcloud compute ssh vm-crossplane-bastion-stg --project=jota-infra --zone=us-west1-a \
  --tunnel-through-iap -- -L 8443:172.31.255.2:443 -N
```

(If SSH fails on permissions, you need `roles/iap.tunnelResourceAccessor` + OS Login on the
bastion — the "human with access" item.)

**Terminal 2 — verify the tunnel, then point kubectl at it.** The override flags
`--server=https://localhost:8443 --insecure-skip-tls-verify` tell kubectl to talk to the local
tunnel instead of the real endpoint (the cert won't match localhost, hence skip-tls-verify):

```bash
curl -sk https://localhost:8443/healthz && echo "  <- tunnel OK"

# kubectl needs the GKE auth plugin — install once if it complains:
gcloud components install gke-gcloud-auth-plugin

# write a kubeconfig entry (the --server override below replaces its endpoint)
gcloud container clusters get-credentials gke-crossplane-stg --region=us-west1 --project=jota-infra

# sanity: Crossplane providers healthy? (flags typed inline — see the zsh note below)
kubectl get providers.pkg.crossplane.io \
  --server=https://localhost:8443 --insecure-skip-tls-verify
```

If that lists healthy `provider-gcp-*` rows, you're connected. Run every later `kubectl` from
**this laptop terminal** with those two flags appended.

> **zsh gotcha — don't use `CP="--server=... --insecure-skip-tls-verify"`.** A plain string
> var holding multiple flags is passed to kubectl as **one argument**, giving:
> `error: host must be a URL or a host:port pair: "https://localhost:8443 --insecure-skip-tls-verify"`.
> Two safe options:
>   1. **Type the flags inline** every time (what the commands here do).
>   2. **Use a zsh array** so it word-splits correctly:
>      ```bash
>      CP=(--server=https://localhost:8443 --insecure-skip-tls-verify)
>      kubectl get providers.pkg.crossplane.io $CP    # array expands to 2 separate args ✓
>      ```
> (In bash, an unquoted string `$CP` happens to word-split and works; in zsh it does not by
> default — hence the array. The array form is portable across both.)

> Why this beats shelling into the bastion: kubectl authenticates as **your** GCP user
> (`adm-paulo@`, which has cluster RBAC), not the bastion VM's service account (which doesn't).
> The 403 you hit earlier was from running kubectl *on* the bastion as its SA.
>
> You may also see `CRITICAL: ACTION REQUIRED: gke-gcloud-auth-plugin ... not found` — that's
> the plugin install above; run it and re-run get-credentials.

---

## Step 5 — Server-side dry-run (validate without creating)

From your **laptop** (Terminal 2, tunnel still up in Terminal 1), with `$CP` appended:

```bash
kubectl apply -k deploy/crossplane/overlays/stg $CP --dry-run=server
```

`--dry-run=server` sends the objects to the cluster's API server, which validates them
against the **actual installed CRDs** (the provider schemas) — catching wrong field names or
types — then discards them. This is stronger than client-side validation and is the real test
that our manifests match the installed provider versions.

If you see an error on a field like `instanceNameRef` or `secretIdRef`, that's a
provider-schema mismatch I flagged as unverified (I couldn't reach a live control plane). Show
me the error; to inspect the true schema yourself:

```bash
kubectl explain instanceiammember.spec.forProvider
kubectl explain instance.spec.forProvider.networkInterface
```

---

## Step 6 — Access groups (only affects the 2 access-control MRs)

The `InstanceIAMMember` MRs grant `group:eng-claude@jota.ai` (proxy/tunnel users) and
`group:eng-claude-admins@jota.ai` (SSH). If those groups don't exist yet, those two MRs will
sit `READY=False` — **the rest of the stack still comes up fine.** To unblock without waiting
on group creation, point them at yourself in `overlays/stg/kustomization.yaml`:

```yaml
        value: group:eng-claude@jota.ai      →   value: user:paulo@jota.ai
```

(both the `fcc-iap-tunnel-users` and `fcc-oslogin-admins` patches). Re-apply later to switch
back. Note: with Tailscale as the real access path, these IAP/OS-Login grants are the
*fallback* plane — getting them perfect isn't blocking for a staging trial.

---

## Step 7 — Apply (first step that creates real infra)

From your laptop, tunnel up, `$CP` appended:

```bash
kubectl apply -k deploy/crossplane/overlays/stg $CP
```

Each line printed = an MR accepted into the cluster (`created`/`configured`). The reconcile
loop now starts calling GCP — creating the resources **in `stp-core-dev`** (per the overlay).
Cost is small (one `e2-standard-2` + NAT, ~tens of $/mo) and fully reversible (Step 10).

---

## Step 8 — Watch reconciliation

```bash
kubectl get managed -l service=free-claude-code $CP -w
```

Wait for every row `READY=True SYNCED=True`. Order of readiness roughly follows dependencies
(network → router/NAT/firewall → SA/secret → IAM → instance). The VM and NAT are slowest.

When something stalls, the resource tells you why:

```bash
kubectl describe <kind> <name> -n crossplane-system $CP
# e.g. kubectl describe instance fcc-proxy -n crossplane-system $CP
```

Read the **Conditions** and **Events** at the bottom — Crossplane surfaces the raw GCP API
error there ("API not enabled", "permission denied", "field X invalid", "group not found").
That message *is* the diagnosis. Common causes: a needed GCP API not enabled on the project,
the cluster's Workload Identity lacking a role in `stp-core-dev`, or the Step-6 group issue.
Paste it to me and I'll give the fix.

> Mental model for debugging: `SYNCED=False` → Crossplane↔GCP API problem (auth/quota/field).
> `SYNCED=True, READY=False` → GCP accepted the request but the resource isn't usable yet
> (still creating, or a dependency missing).

---

## Step 9 — Post-create wiring (secret values + startup script)

Crossplane created **empty** Secret Manager containers (we never commit secret values). Add
them out-of-band:

```bash
# upstream model-provider API key (the proxy reads this at runtime, never on disk)
echo -n "PROVIDER-API-KEY" | gcloud secrets versions add fcc-provider-key \
  --project=stp-core-dev --data-file=-

# Tailscale OAuth client secret (the VM uses this to join the tailnet on boot)
echo -n "TAILSCALE-OAUTH-CLIENT-SECRET" | gcloud secrets versions add fcc-tailscale-oauth \
  --project=stp-core-dev --data-file=-
```

The Tailscale OAuth client is created once in the Tailscale admin console (Settings → OAuth
clients, scope `devices:write`, tag `tag:fcc-proxy`) plus an ACL grant letting the eng group
reach `tag:fcc-proxy:8082`. Exact steps in `deploy/crossplane/README.md`.

**Still TODO before the VM is functional — the startup script.** The `Instance` MR has an
empty `metadataStartupScript`; the VM needs `deploy/startup.sh` attached so it installs the
proxy, wires the runtime secret fetch, and runs `tailscale up` on boot. This is one kustomize
patch that embeds the script — easy to mangle by hand. **Tell me when you reach this point and
I'll wire it in.**

**Smoke test once it's up:** `deploy/fcc-connect-tailscale` → it resolves the proxy's MagicDNS
name, points Claude Code at it, you send a prompt and get a streamed reply.

---

## Step 10 — Teardown

```bash
kubectl delete -k deploy/crossplane/overlays/stg $CP
```

Caveat: MRs use `deletionPolicy: Orphan`, so deleting the manifest **leaves the underlying GCP
resource in place** (deliberate — you don't lose a secret or a VM by accidentally deleting a
YAML). The VM/network/etc. that you want gone must be deleted in GCP directly (console or
`gcloud`), or flip the policy. For a staging experiment you can also just leave it running.

---

## One-screen reference

```bash
# --- one-time ---
brew install --cask google-cloud-sdk
gcloud auth login && gcloud config set project jota-infra
ENDPOINT=$(gcloud container clusters describe gke-crossplane-stg --region=us-west1 \
  --project=jota-infra --format='value(privateClusterConfig.privateEndpoint)')

# --- TERMINAL 1: open the tunnel, leave running ---
gcloud compute ssh vm-crossplane-bastion-stg --project=jota-infra --zone=us-west1-a \
  --tunnel-through-iap -- -L 8443:$ENDPOINT:443 -N

# --- TERMINAL 2: deploy from your laptop, through the tunnel ---
curl -sk https://localhost:8443/healthz && echo " tunnel OK"
gcloud components install gke-gcloud-auth-plugin    # once, if kubectl complains
gcloud container clusters get-credentials gke-crossplane-stg --region=us-west1 --project=jota-infra
CP=(--server=https://localhost:8443 --insecure-skip-tls-verify)   # zsh ARRAY (not a string)

kubectl get providers.pkg.crossplane.io $CP                          # sanity
kubectl apply -k deploy/crossplane/overlays/stg $CP --dry-run=server # validate
kubectl apply -k deploy/crossplane/overlays/stg $CP                  # CREATE (lands in stp-core-dev)
kubectl get managed -l service=free-claude-code $CP -w               # watch until READY+SYNCED

# secret values (post-create; the secrets live in stp-core-dev)
echo -n "PROVIDER-API-KEY"              | gcloud secrets versions add fcc-provider-key   --project=stp-core-dev --data-file=-
echo -n "TAILSCALE-OAUTH-CLIENT-SECRET" | gcloud secrets versions add fcc-tailscale-oauth --project=stp-core-dev --data-file=-

# teardown (Orphan policy leaves GCP resources — delete them in GCP if you want them gone)
kubectl delete -k deploy/crossplane/overlays/stg $CP
```

---

## Where to stop and ask

- Tunnel SSH (Terminal 1) fails → IAP permission (`roles/iap.tunnelResourceAccessor` + OS
  Login on `vm-crossplane-bastion-stg`, project `jota-infra`).
- `curl https://localhost:8443/healthz` fails → tunnel (Terminal 1) isn't up / wrong endpoint.
- `kubectl ... $CP` returns 403 → your GCP user lacks cluster RBAC on `gke-crossplane-stg`
  (NOT a tunnel issue). Note: run kubectl on your LAPTOP with `$CP`, never on the bastion —
  on the bastion it auths as the VM's SA and 403s.
- `--dry-run=server` field errors → provider-schema mismatch; paste to me.
- A resource stuck `READY=False` → read its `describe` Conditions/Events; paste to me.
- Reaching the startup-script step → ping me to wire the patch.

## Governance note (one honest flag)
Tailscale access means the proxy is gated by the **tailnet ACL**, a second access-control
plane alongside GCP IAM. For a regulated bank that's normally a concern — but staging already
uses exactly this pattern for the PKI group, so we're following the established convention,
not introducing a new one. Worth a conscious decision before the same approach goes to prod.
