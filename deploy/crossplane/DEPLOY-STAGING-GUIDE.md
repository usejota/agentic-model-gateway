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

**4. How you actually reach it — Tailscale.** Two separate "how do I connect to something
private" problems exist here; don't conflate them:

  - **Reaching the GKE control plane** (to run `kubectl`). The staging cluster's API endpoint
    is **private** — it has no public address, only an internal VPC IP. From your laptop you
    can't route to a VPC-internal IP directly. Staging solves this with a **Tailscale subnet
    router**: a small VM *inside* the VPC (`tailscale-fw-default-stg`) that runs Tailscale and
    **advertises the VPC's internal CIDR ranges to the tailnet**. When your laptop is on the
    same tailnet, Tailscale installs routes for those CIDRs pointing at that VM, so traffic to
    a VPC-internal IP gets tunneled in. Net effect: with Tailscale up, the private cluster IP
    becomes reachable as if you were inside the VPC. This is the *same mechanism* you already
    use to reach the PKI group.
  - **Reaching the proxy VM** (for end users / you, to use the proxy). The proxy VM itself
    **joins the tailnet on boot** (our `startup.sh` runs `tailscale up`). So it gets its own
    `100.x` tailnet address + a MagicDNS name (`fcc-proxy.<tailnet>.ts.net`), and anyone on the
    tailnet whom the ACL allows can hit it directly — no NAT, no firewall dance, no bastion.
    That's what `deploy/fcc-connect-tailscale` uses.

  **MagicDNS** = Tailscale's built-in DNS that resolves those `*.ts.net` names to tailnet IPs.
  **Subnet router** = a tailnet node that forwards traffic for non-Tailscale IPs (the VPC
  ranges) — that's the forwarder. **Tailnet ACL** = Tailscale's allow-list of who can reach
  what; it's a *second* access-control layer alongside GCP IAM (a known tradeoff — see the
  governance note at the end).

  > IAP is still wired as a **fallback** (the firewall rules, `fcc-connect`). With Tailscale
  > working you won't use it, but it's there for environments without a tailnet.

That's the whole network. Now the steps.

---

## What needs a human with access (do this first)

None of this is in the repo; resolve before Step 4:

1. **GCP access to `stp-core-dev`** and rights to talk to the cluster. If you've never run
   `kubectl` here, someone with cluster admin grants you a role (typically a GKE RBAC binding).
   This is the usual first-time blocker.
2. **You're on the company tailnet** (Tailscale app logged in with @jota.ai), **and** the
   forwarder `tailscale-fw-default-stg` advertises the staging cluster's subnet (its
   "subnet routes" must be approved in the Tailscale admin console). Verify in Step 4; if the
   route isn't approved, an admin flips it.
3. **(Only for the proxy's own access, not for deploying)** Two Google Groups —
   `eng-claude@jota.ai`, `eng-claude-admins@jota.ai` — and a Tailscale OAuth client +
   ACL grant. Covered in Step 6 / Step 9; not needed to get the infra up.

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
gcloud auth login                         # browser → @jota.ai
gcloud config set project stp-core-dev
```

This authenticates *you* to GCP. It does **not** connect kubectl to the cluster yet (that's
Step 4) and changes no infra.

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

## Step 4 — Point kubectl at the staging cluster (over Tailscale)

**Confirm tailnet + that the forwarder routes the cluster subnet:**

```bash
tailscale status                          # you should be 'up'; forwarder listed
tailscale status --json | grep -A3 -i 'fw-default-stg'   # check it advertises a subnet route
```

If you're logged out: open the Tailscale app / `tailscale up`. If the forwarder shows no
subnet route covering the cluster, that's the admin-approval item from "What needs a human."

**Get cluster credentials.** This writes a context into your kubeconfig pointing at the
cluster's **private** endpoint; it's reachable because Tailscale routes that VPC CIDR:

```bash
gcloud container clusters get-credentials core-stg \
  --region=us-west1 --project=stp-core-dev --internal-ip
```

(`--internal-ip` = use the private endpoint, not a public one. Confirm the cluster name
`core-stg` with infra; it's from the octopus core/stg overlay.)

**Verify the connection and that Crossplane is healthy:**

```bash
kubectl config current-context                 # names the staging cluster
kubectl get nodes                              # returns a node list = you can reach the API
kubectl get providers.pkg.crossplane.io        # provider-gcp-* rows, INSTALLED=True HEALTHY=True
```

`kubectl get nodes` hanging means the network path is the problem: Tailscale down, or the
forwarder isn't advertising the cluster subnet, or you lack RBAC. The three commands above
isolate which.

> Why `get nodes` if we're not using pods? It's just the cheapest "can I reach and am I
> authorized against the API server" probe. We never schedule a pod.

---

## Step 5 — Server-side dry-run (validate without creating)

```bash
kubectl apply -k deploy/crossplane/overlays/stg --dry-run=server
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

```bash
kubectl apply -k deploy/crossplane/overlays/stg
```

Each line printed = an MR accepted into the cluster (`created`/`configured`). The reconcile
loop now starts calling GCP. Cost is small (one `e2-standard-2` + NAT, ~tens of $/mo) and
fully reversible (Step 10).

---

## Step 8 — Watch reconciliation

```bash
kubectl get managed -l service=free-claude-code -w
```

Wait for every row `READY=True SYNCED=True`. Order of readiness roughly follows dependencies
(network → router/NAT/firewall → SA/secret → IAM → instance). The VM and NAT are slowest.

When something stalls, the resource tells you why:

```bash
kubectl describe <kind> <name> -n crossplane-system
# e.g. kubectl describe instance fcc-proxy -n crossplane-system
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
kubectl delete -k deploy/crossplane/overlays/stg
```

Caveat: MRs use `deletionPolicy: Orphan`, so deleting the manifest **leaves the underlying GCP
resource in place** (deliberate — you don't lose a secret or a VM by accidentally deleting a
YAML). The VM/network/etc. that you want gone must be deleted in GCP directly (console or
`gcloud`), or flip the policy. For a staging experiment you can also just leave it running.

---

## One-screen reference

```bash
# tools + auth
brew install --cask google-cloud-sdk
gcloud auth login && gcloud config set project stp-core-dev

# connect kubectl to the private cluster (Tailscale must be up)
tailscale status
gcloud container clusters get-credentials core-stg --region=us-west1 --project=stp-core-dev --internal-ip
kubectl get nodes
kubectl get providers.pkg.crossplane.io

# render → validate → apply → watch
kubectl kustomize deploy/crossplane/overlays/stg
kubectl apply   -k deploy/crossplane/overlays/stg --dry-run=server
kubectl apply   -k deploy/crossplane/overlays/stg
kubectl get managed -l service=free-claude-code -w

# secret values (post-create)
echo -n "PROVIDER-API-KEY"            | gcloud secrets versions add fcc-provider-key   --project=stp-core-dev --data-file=-
echo -n "TAILSCALE-OAUTH-CLIENT-SECRET" | gcloud secrets versions add fcc-tailscale-oauth --project=stp-core-dev --data-file=-

# teardown (Orphan policy leaves GCP resources — delete them in GCP if you want them gone)
kubectl delete -k deploy/crossplane/overlays/stg
```

---

## Where to stop and ask

- `kubectl get nodes` hangs → network/access (Tailscale route or RBAC), not a manifest issue.
- `--dry-run=server` field errors → provider-schema mismatch; paste to me.
- A resource stuck `READY=False` → read its `describe` Conditions/Events; paste to me.
- Reaching the startup-script step → ping me to wire the patch.

## Governance note (one honest flag)
Tailscale access means the proxy is gated by the **tailnet ACL**, a second access-control
plane alongside GCP IAM. For a regulated bank that's normally a concern — but staging already
uses exactly this pattern for the PKI group, so we're following the established convention,
not introducing a new one. Worth a conscious decision before the same approach goes to prod.
