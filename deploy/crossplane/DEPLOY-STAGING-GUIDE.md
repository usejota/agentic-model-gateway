# Deploying the free-claude-code proxy to **staging** — a guide for non-experts

This walks you through putting the proxy's infrastructure into the Jota **staging**
GCP environment, and explains *what each thing is* as you go. Take it slowly; you can
stop after any step. Nothing here changes anything real until **Step 7 (apply)** — every
step before that is "look but don't touch."

---

## The 60-second mental model

You are **not** going to click around the Google Cloud console creating servers. Instead:

1. We already wrote **YAML files** that *describe* the infrastructure we want
   (a virtual machine, a network, a firewall, etc.). Think of them as a **shopping list**.
2. Jota's staging cluster runs a tool called **Crossplane**. Crossplane is a robot that
   reads shopping lists and makes Google Cloud match them. If the list says "one VM with
   no public IP," Crossplane creates exactly that and keeps it that way.
3. Your job is to: get the tools, **connect** to that robot, hand it our shopping list,
   and watch it work.

Key vocabulary (you'll see these words a lot):

| Word | Plain meaning |
|------|---------------|
| **GCP** | Google Cloud Platform — Google's servers we rent. |
| **Crossplane** | The robot inside the cluster that turns YAML into real cloud resources. |
| **Kubernetes / cluster** | The system Crossplane runs inside. We talk to it with a tool called `kubectl`. |
| **`kubectl`** | Command-line tool to talk to the cluster. ("kube-control".) |
| **`gcloud`** | Command-line tool to talk to Google Cloud (log in, open tunnels). |
| **Manifest / YAML** | A text file describing one piece of infrastructure. |
| **kustomize / overlay** | A way to take the base shopping list and stamp it with environment-specific values (staging vs prod). |
| **Managed Resource (MR)** | One item Crossplane manages, e.g. our VM or our firewall. |
| **Apply** | The verb for "hand the shopping list to the robot." |

The staging values are already filled in for you in
`deploy/crossplane/overlays/stg/kustomization.yaml`:
project **`stp-core-dev`**, region **`us-west1`**, zone **`us-west1-a`**.

---

## Before you start: things only a human with access can do

These cannot be done from this repo and may need a teammate with GCP admin rights. Ask in
the infra channel if you're unsure:

- **You need permission** to use the staging project (`stp-core-dev`) and to reach its
  cluster. If you've never run `kubectl` against staging, you likely need someone to grant
  you access first. **This is the most common blocker — sort it out before Step 4.**
- **The staging GKE control plane is private** (no public address). The good news: staging
  already runs a **Tailscale** subnet router VM (`tailscale-fw-default-stg`) that bridges
  your laptop's tailnet to the private staging network — the same way you already reach the
  PKI group. So instead of a bastion you just need to **be on the company tailnet**
  (the Tailscale app running and logged in). Step 4 uses this.
- **Two Google Groups** should exist: `eng-claude@jota.ai` (engineers who may use the
  proxy) and `eng-claude-admins@jota.ai` (who may SSH in). If they don't exist yet, that's
  fine — see the note in Step 6.

> If any of this is intimidating, that's normal. The safe move is to do Steps 1–3 and 5
> (all harmless, local-only) yourself, then pair with an infra teammate for Steps 4 and 7.

---

## Step 1 — Install the two command-line tools

You already have `git` and `kubectl`. You are **missing `gcloud`** (confirmed on your
machine). Install it.

**On a Mac with Homebrew** (most Jota laptops have `brew`):

```bash
brew install --cask google-cloud-sdk
```

If you don't have Homebrew, use Google's installer instead:

```bash
curl https://sdk.cloud.google.com | bash
# then restart your terminal
```

**Check it worked:**

```bash
gcloud --version
kubectl version --client
```

*What just happened:* you installed the two "remote controls" — `gcloud` talks to Google
Cloud, `kubectl` talks to the cluster. They don't do anything on their own yet.

---

## Step 2 — Log in to Google Cloud

```bash
gcloud auth login
```

A browser opens; sign in with your **@jota.ai** account. Then point gcloud at staging:

```bash
gcloud config set project stp-core-dev
```

**Check:**

```bash
gcloud auth list          # shows your @jota.ai email with a *
gcloud config get project # shows stp-core-dev
```

*What just happened:* Google now knows it's you, and that you're working in the staging
project. Logging in does **not** change any infrastructure — it's like badging into the
building.

---

## Step 3 — Look at the shopping list (still 100% safe)

From the repo root, render the staging manifests. This just prints YAML to your screen —
it touches nothing in the cloud.

```bash
kubectl kustomize deploy/crossplane/overlays/stg
```

You'll see a big block of YAML. Scroll through it. You should spot, among others:

- a `Network` and `Subnetwork` (the private network the VM lives in),
- a `Router` + `RouterNAT` (lets the VM reach the internet *outbound* without a public IP),
- two `Firewall` rules (who may reach the VM, and on which ports),
- a `ServiceAccount` (the VM's identity),
- a `Secret` (a slot in Google's secret vault for the provider API key),
- an `Instance` (the VM itself — `e2-standard-2`, no public IP),
- a few `IAMMember` items (who's allowed to tunnel in / SSH in).

Everywhere you look it should say `project: stp-core-dev` and `us-west1`. That's how you
know you're aimed at staging, not prod.

*What just happened:* you read the exact description of what will be created. Reading is
free and reversible. Get comfortable here.

---

## Step 4 — Connect `kubectl` to the staging cluster (via Tailscale)

The cluster is private, but staging has a Tailscale forwarder (`tailscale-fw-default-stg`)
that lets your laptop reach the private network — no bastion needed.

**First, make sure you're on the tailnet:**

```bash
tailscale status
```

You should see a list of machines including `tailscale-fw-default-stg`. If `tailscale status`
says you're logged out, open the Tailscale app (or run `tailscale up`) and sign in with your
@jota.ai account, then re-run it.

**Then fetch the cluster credentials.** The forwarder routes the private endpoint, so the
`--internal-ip` form works over the tailnet (a teammate will confirm the exact cluster name):

```bash
gcloud container clusters get-credentials core-stg \
  --region=us-west1 --project=stp-core-dev --internal-ip
```

**Check you're connected:**

```bash
kubectl config current-context     # should now name the staging cluster
kubectl get nodes                  # lists machines — proves you can talk to it
```

If `kubectl get nodes` returns a list, you're in. If it hangs:
- `tailscale status` — are you connected, and is `tailscale-fw-default-stg` listed?
- Does the forwarder advertise the staging cluster's subnet? Check in the Tailscale admin
  console (Machines → the forwarder → Subnet routes), or ask infra. If the route isn't
  approved, that's the one thing a teammate needs to flip.
- Still stuck → that's the access conversation from the "Before you start" section.

*What just happened:* `kubectl` now points at the staging cluster's Crossplane robot,
reached privately over Tailscale. You still haven't changed anything.

**Confirm Crossplane is actually there and healthy:**

```bash
kubectl get providers.pkg.crossplane.io
```

You want to see rows like `provider-gcp-compute`, `provider-gcp-cloudplatform`,
`provider-gcp-secretmanager`, each `INSTALLED=True HEALTHY=True`. These are the robot's
"skills" for creating compute, IAM, and secret resources. They're already installed in
staging — you're just confirming.

---

## Step 5 — Dry-run: ask the robot "would this work?" (safe)

A **server dry-run** sends the shopping list to the cluster, which checks every field
against what Google Cloud actually accepts — **without creating anything**.

```bash
kubectl apply -k deploy/crossplane/overlays/stg --dry-run=server
```

- If it prints a list of resources each ending in `(server dry run)` with no errors → great,
  the list is valid.
- If it complains about an unknown field (for example on `instanceNameRef`), that's a
  field-name mismatch we flagged. Tell me the exact error and I'll fix the manifest. To
  see the correct field names yourself:
  ```bash
  kubectl explain instanceiammember.spec.forProvider
  ```

*What just happened:* the cluster validated the plan and threw it away. Still nothing
created. This is your last checkpoint before real changes.

---

## Step 6 — Sanity-check the access groups (safe, optional)

Our manifests grant proxy access to `group:eng-claude@jota.ai` and SSH to
`group:eng-claude-admins@jota.ai`. If those Google Groups don't exist, the two
`InstanceIAMMember` resources will fail to reconcile later (everything else still works).

If you're not sure they exist and don't want to block on it, you can temporarily point them
at just yourself. Edit `deploy/crossplane/overlays/stg/kustomization.yaml`, find the two
blocks near the bottom (`fcc-iap-tunnel-users` and `fcc-oslogin-admins`), and change:

```yaml
        value: group:eng-claude@jota.ai
```
to
```yaml
        value: user:paulo@jota.ai
```

(Do the same for the admins one.) You can switch back to the groups later by editing and
re-applying.

---

## Step 7 — Apply for real (this creates infrastructure)

This is the first step that changes the cloud. It hands the shopping list to Crossplane,
which starts creating the network, VM, firewall, etc. in `stp-core-dev`.

```bash
kubectl apply -k deploy/crossplane/overlays/stg
```

You'll see one line per resource saying `created`. That doesn't mean they're *ready* yet —
just that the robot accepted the order.

*What just happened:* Crossplane is now building real resources in staging. This **does
cost a little money** (a small VM + networking, roughly tens of dollars/month) and is
reversible (Step 9 explains teardown).

---

## Step 8 — Watch it come up

```bash
kubectl get managed -l service=free-claude-code
```

Each row has two columns that matter: **READY** and **SYNCED**. You're waiting for every
row to show `True True`. It can take a few minutes (the VM and NAT take the longest). Re-run
the command, or add `-w` to watch live:

```bash
kubectl get managed -l service=free-claude-code -w
```

If a row is stuck on `False`, ask it why:

```bash
kubectl describe <KIND> <NAME> -n crossplane-system
# example: kubectl describe instance fcc-proxy -n crossplane-system
```

Scroll to the **Events** / **Conditions** at the bottom — it states the problem in plain
English (e.g. "API not enabled," "permission denied," "group not found"). Common fixes:
enable an API, get an IAM role, or the group issue from Step 6. Paste the message to me and
I'll tell you the fix.

---

## Step 9 — After the infrastructure is up (the remaining wiring)

Crossplane created the **empty secret slot** but not its contents (we never put the real API
key in git, on purpose). Put the provider key in:

```bash
echo -n "PASTE-THE-PROVIDER-API-KEY-HERE" | \
  gcloud secrets versions add fcc-provider-key \
  --project=stp-core-dev --data-file=-
```

The proxy VM joins the **tailnet** on boot (matching how you reach the PKI group), so it
also needs a Tailscale credential. Put the **Tailscale OAuth client secret** in its slot:

```bash
echo -n "PASTE-THE-TAILSCALE-OAUTH-CLIENT-SECRET" | \
  gcloud secrets versions add fcc-tailscale-oauth \
  --project=stp-core-dev --data-file=-
```

You get that secret by creating an OAuth client once in the Tailscale admin console
(Settings → OAuth clients, scope `devices:write`, tag `tag:fcc-proxy`) — see
`deploy/crossplane/README.md` for the exact steps and the ACL grant that lets engineers
reach the proxy.

Then two more items that aren't code:

- **The VM startup script** — the VM needs the install script (`deploy/startup.sh`) attached
  so it installs/runs the proxy and joins the tailnet on boot. This is one extra patch; tell
  me when you reach this point and I'll wire it in (it's quick, but easy to get wrong by hand).
- **IAP tunnel quota** — only relevant if you fall back to the IAP path. With Tailscale as
  the access method you can ignore it.

**Final check that the proxy actually works:** an engineer runs
`deploy/fcc-connect-tailscale`, sends a prompt in Claude Code, and gets a streamed reply.
(`deploy/fcc-connect` is the IAP fallback.) Full instructions in
`deploy/crossplane/README.md`.

---

## Step 10 — How to undo everything (don't be afraid to experiment)

If you want to tear the staging stack back down:

```bash
kubectl delete -k deploy/crossplane/overlays/stg
```

Note: we set `deletionPolicy: Orphan` on resources, which means some items (like the secret)
are intentionally **left in place** in Google Cloud even after you delete the manifest, so
you don't lose data by accident. To fully remove those, delete them in the Cloud console or
ask infra. The VM and networking will go away.

---

## Quick reference — the whole flow in one screen

```bash
# one-time setup
brew install --cask google-cloud-sdk
gcloud auth login
gcloud config set project stp-core-dev

# connect to the staging cluster (need tunnel + access — ask infra first time)
gcloud container clusters get-credentials core-stg --region=us-west1 --project=stp-core-dev --internal-ip
kubectl get nodes                         # proves connection
kubectl get providers.pkg.crossplane.io   # proves Crossplane is healthy

# look, validate, then apply
kubectl kustomize deploy/crossplane/overlays/stg                      # read the plan
kubectl apply -k deploy/crossplane/overlays/stg --dry-run=server      # validate, no changes
kubectl apply -k deploy/crossplane/overlays/stg                       # CREATE (real)
kubectl get managed -l service=free-claude-code -w                    # watch until READY+SYNCED

# put the secret value in
echo -n "PROVIDER-API-KEY" | gcloud secrets versions add fcc-provider-key --project=stp-core-dev --data-file=-

# undo
kubectl delete -k deploy/crossplane/overlays/stg
```

---

## When to stop and ask for help (not a failure — the right move)

- You don't have access to the staging project or the cluster (Step 4 hangs).
- `--dry-run=server` reports an error you don't understand → paste it to me.
- A resource is stuck `READY=False` and the `describe` message mentions permissions, APIs,
  or "not found" → paste it to me.
- You're about to do Step 7 (apply) for the first time → consider pairing with infra.

There is no dumb question here. Reading (Steps 1–6) is always safe; only Step 7 onward
changes anything, and Step 10 reverses it.
