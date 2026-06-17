#!/usr/bin/env bash
#
# teardown.sh — tear down the free-claude-code proxy infra for an overlay, cleanly
# deregistering the VM's Tailscale node so the next deploy doesn't pile up
# fcc-proxy-N nodes.
#
# Order:
#   1. Delete the Instance MR (Crossplane stops managing the VM).
#      deletionPolicy: Orphan means the GCP VM is left running — so we...
#   2. Delete the GCP VM gracefully (ACPI shutdown fires the on-VM
#      fcc-tailscale-logout.service -> `tailscale logout` -> node deregisters).
#   3. (Optional belt-and-suspenders) if a Tailscale API token + tailnet are
#      provided via env, delete the node via the Tailscale API in case the
#      graceful logout didn't fire (e.g. hard crash). Set:
#        TS_API_TOKEN=tskey-api-...   TS_TAILNET=jota.ai
#
# Usage:
#   deploy/crossplane/teardown.sh stg \
#     --server=https://localhost:8443 --insecure-skip-tls-verify
#   (extra args after the overlay are passed to kubectl, e.g. the bastion-tunnel
#    --server override.)
#
# Requires: kubectl, gcloud. Does NOT delete the network/secrets/IAM (those are
# Orphan-policy and shared-ish) — only the Instance + its tailnet node.
set -euo pipefail

OVERLAY="${1:-stg}"; shift || true
KUBECTL_ARGS=("$@")

# Per-overlay GCP coordinates. Extend this case for new overlays.
case "$OVERLAY" in
  stg)  PROJECT="stp-core-dev"; ZONE="us-west1-a"; VM="fcc-proxy" ;;
  prod) PROJECT="jota-fcc-proxy"; ZONE="southamerica-east1-a"; VM="fcc-proxy" ;;
  *) echo "ERROR: unknown overlay '$OVERLAY' (expected stg|prod)" >&2; exit 1 ;;
esac

log() { echo "[teardown] $*"; }

# 1. Stop Crossplane managing the Instance (Orphan policy leaves the GCP VM).
log "Deleting the fcc-proxy Instance MR (Crossplane)..."
kubectl delete instance.compute.gcp.upbound.io/fcc-proxy "${KUBECTL_ARGS[@]}" --ignore-not-found

# 2. Delete the GCP VM gracefully so the shutdown hook deregisters the tailnet node.
if gcloud compute instances describe "$VM" --zone="$ZONE" --project="$PROJECT" >/dev/null 2>&1; then
  log "Deleting GCP VM $VM (graceful — fires tailscale logout on shutdown)..."
  gcloud compute instances delete "$VM" --zone="$ZONE" --project="$PROJECT" --quiet
else
  log "GCP VM $VM not found (already gone)."
fi

# 3. Belt-and-suspenders: delete the tailnet node via the Tailscale API if creds given.
if [ -n "${TS_API_TOKEN:-}" ] && [ -n "${TS_TAILNET:-}" ]; then
  log "Checking Tailscale API for a lingering '$VM' node..."
  NODE_ID="$(curl -s -u "${TS_API_TOKEN}:" \
    "https://api.tailscale.com/api/v2/tailnet/${TS_TAILNET}/devices" \
    | python3 -c '
import json, sys, os
vm = os.environ["VM"]
data = json.load(sys.stdin)
for d in data.get("devices", []):
    name = d.get("hostname", "") or d.get("name", "")
    if name == vm or name.startswith(vm + "."):
        print(d["id"]); break
' VM="$VM" 2>/dev/null || true)"
  if [ -n "${NODE_ID}" ]; then
    log "Deleting Tailscale node $NODE_ID ($VM) via API..."
    curl -s -u "${TS_API_TOKEN}:" -X DELETE \
      "https://api.tailscale.com/api/v2/device/${NODE_ID}" >/dev/null && log "Node deleted."
  else
    log "No lingering '$VM' node found via API (shutdown logout likely handled it)."
  fi
else
  log "TS_API_TOKEN/TS_TAILNET not set — skipping API node cleanup."
  log "If a stale '$VM' node lingers, delete it in the Tailscale admin console."
fi

log "Teardown complete. Network/Secrets/IAM left in place (deletionPolicy: Orphan)."
