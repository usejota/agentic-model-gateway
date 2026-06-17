#!/usr/bin/env bash
#
# provision.sh — provision the GCP infrastructure for the free-claude-code (fcc)
# proxy reached over IAP TCP forwarding.
#
# This script is idempotent-ish: every create step tolerates "already exists"
# so it can be re-run safely. It does NOT delete anything.
#
# Design source of truth (read these for the rationale behind every value here):
#   domain_docs/free-claude-code-gcp-plan.md   (overall plan + sequence)
#   domain_docs/networking.md                  (CIDRs, NAT/router, firewall split, quota)
#   domain_docs/cloud_infra.md                 (VM, SA, OS Login, Private Google Access)
#   domain_docs/security.md                    (minimal IAM, secret-level binding, no key on disk)
#
# Order of operations:
#   1. Enable required APIs
#   2. VPC + subnet (explicit CIDR, Private Google Access)
#   3. Cloud Router  -> then Cloud NAT (router is a NAT prerequisite)
#   4. Two tag-scoped firewall rules (proxy tcp:8082, admin-ssh tcp:22)
#   5. Dedicated service account with MINIMAL roles
#   6. Secret Manager secret for the provider key + secret-level IAM binding
#   7. GCE VM (no external IP, dedicated SA, no broad scopes, tags, OS Login, startup-script)
#   8. IAP IAM binding for the engineering group
#
set -euo pipefail

# ---------------------------------------------------------------------------
# CONFIG — override any of these via environment variables before running.
# e.g.  PROJECT=jota-fcc-proxy REGION=southamerica-east1 ./provision.sh
# ---------------------------------------------------------------------------
PROJECT="${PROJECT:-jota-fcc-proxy}"
REGION="${REGION:-southamerica-east1}"
ZONE="${ZONE:-southamerica-east1-a}"

VPC="${VPC:-fcc-vpc}"
SUBNET="${SUBNET:-fcc-subnet}"
SUBNET_CIDR="${SUBNET_CIDR:-10.128.0.0/20}"      # explicit; avoid overlap with shared-VPC/on-prem

ROUTER="${ROUTER:-fcc-router}"
NAT="${NAT:-fcc-nat}"

VM_NAME="${VM_NAME:-fcc-proxy}"
MACHINE_TYPE="${MACHINE_TYPE:-e2-standard-2}"
IMAGE_FAMILY="${IMAGE_FAMILY:-debian-12}"
IMAGE_PROJECT="${IMAGE_PROJECT:-debian-cloud}"

SA_NAME="${SA_NAME:-fcc-sa}"
SA_EMAIL="${SA_EMAIL:-${SA_NAME}@${PROJECT}.iam.gserviceaccount.com}"

SECRET_NAME="${SECRET_NAME:-fcc-provider-key}"

# Network tags drive the tag-scoped firewall rules. Keep in sync with the rules below.
TAG_PROXY="fcc-proxy"
TAG_ADMIN="fcc-admin"

# Proxy listen port and the fixed IAP forwarding source range.
PROXY_PORT="${PROXY_PORT:-8082}"
IAP_CIDR="35.235.240.0/20"

# IAM groups (least privilege: tunnel users vs VM admins). Override per org.
IAP_USER_GROUP="${IAP_USER_GROUP:-group:ai-gateway@jota.ai}"   # gets iap.tunnelResourceAccessor
ADMIN_GROUP="${ADMIN_GROUP:-group:ai-gateway-admins@jota.ai}"          # gets osLogin for SSH admin

# Path to the startup script (defaults to alongside this script).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STARTUP_SCRIPT="${STARTUP_SCRIPT:-${SCRIPT_DIR}/startup.sh}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log() { echo "==> $*"; }

gcloud_base=(gcloud --project="${PROJECT}")

# ---------------------------------------------------------------------------
# 0. Sanity checks
# ---------------------------------------------------------------------------
command -v gcloud >/dev/null 2>&1 || { echo "ERROR: gcloud not found in PATH" >&2; exit 1; }
[[ -f "${STARTUP_SCRIPT}" ]] || { echo "ERROR: startup script not found at ${STARTUP_SCRIPT}" >&2; exit 1; }

log "Project=${PROJECT} Region=${REGION} Zone=${ZONE} VPC=${VPC} Subnet=${SUBNET} (${SUBNET_CIDR})"

# ---------------------------------------------------------------------------
# 1. Enable required APIs
# ---------------------------------------------------------------------------
log "Enabling required APIs (compute, iap, secretmanager, logging)..."
"${gcloud_base[@]}" services enable \
  compute.googleapis.com \
  iap.googleapis.com \
  secretmanager.googleapis.com \
  logging.googleapis.com

# ---------------------------------------------------------------------------
# 2. VPC + subnet with explicit CIDR and Private Google Access
# ---------------------------------------------------------------------------
log "Creating VPC '${VPC}' (custom subnet mode)..."
if ! "${gcloud_base[@]}" compute networks describe "${VPC}" >/dev/null 2>&1; then
  "${gcloud_base[@]}" compute networks create "${VPC}" --subnet-mode=custom
else
  log "VPC '${VPC}' already exists — skipping."
fi

log "Creating subnet '${SUBNET}' (${SUBNET_CIDR}) with Private Google Access..."
if ! "${gcloud_base[@]}" compute networks subnets describe "${SUBNET}" --region="${REGION}" >/dev/null 2>&1; then
  "${gcloud_base[@]}" compute networks subnets create "${SUBNET}" \
    --network="${VPC}" \
    --region="${REGION}" \
    --range="${SUBNET_CIDR}" \
    --enable-private-ip-google-access
else
  log "Subnet '${SUBNET}' already exists — ensuring Private Google Access is on."
  "${gcloud_base[@]}" compute networks subnets update "${SUBNET}" \
    --region="${REGION}" \
    --enable-private-ip-google-access
fi

# ---------------------------------------------------------------------------
# 3. Cloud Router THEN Cloud NAT (router is a hard prerequisite for NAT).
#    NAT provides outbound egress for the VM, which has no external IP.
# ---------------------------------------------------------------------------
log "Creating Cloud Router '${ROUTER}'..."
if ! "${gcloud_base[@]}" compute routers describe "${ROUTER}" --region="${REGION}" >/dev/null 2>&1; then
  "${gcloud_base[@]}" compute routers create "${ROUTER}" \
    --network="${VPC}" \
    --region="${REGION}"
else
  log "Router '${ROUTER}' already exists — skipping."
fi

log "Creating Cloud NAT '${NAT}' on router '${ROUTER}'..."
if ! "${gcloud_base[@]}" compute routers nats describe "${NAT}" --router="${ROUTER}" --region="${REGION}" >/dev/null 2>&1; then
  "${gcloud_base[@]}" compute routers nats create "${NAT}" \
    --router="${ROUTER}" \
    --region="${REGION}" \
    --nat-all-subnet-ip-ranges \
    --auto-allocate-nat-external-ips
else
  log "Cloud NAT '${NAT}' already exists — skipping."
fi

# ---------------------------------------------------------------------------
# 4. Two tag-scoped firewall rules. Tags (not the whole VPC) scope the ingress
#    so only the proxy VM accepts IAP-originated traffic. This also separates
#    the data plane (8082) from the admin plane (22).
# ---------------------------------------------------------------------------
log "Creating firewall rule 'allow-iap-fcc-proxy' (tcp:${PROXY_PORT}, target=${TAG_PROXY})..."
if ! "${gcloud_base[@]}" compute firewall-rules describe allow-iap-fcc-proxy >/dev/null 2>&1; then
  "${gcloud_base[@]}" compute firewall-rules create allow-iap-fcc-proxy \
    --network="${VPC}" \
    --direction=INGRESS \
    --action=ALLOW \
    --rules="tcp:${PROXY_PORT}" \
    --source-ranges="${IAP_CIDR}" \
    --target-tags="${TAG_PROXY}"
else
  log "Firewall rule 'allow-iap-fcc-proxy' already exists — skipping."
fi

log "Creating firewall rule 'allow-iap-fcc-ssh' (tcp:22, target=${TAG_ADMIN})..."
if ! "${gcloud_base[@]}" compute firewall-rules describe allow-iap-fcc-ssh >/dev/null 2>&1; then
  "${gcloud_base[@]}" compute firewall-rules create allow-iap-fcc-ssh \
    --network="${VPC}" \
    --direction=INGRESS \
    --action=ALLOW \
    --rules="tcp:22" \
    --source-ranges="${IAP_CIDR}" \
    --target-tags="${TAG_ADMIN}"
else
  log "Firewall rule 'allow-iap-fcc-ssh' already exists — skipping."
fi

# ---------------------------------------------------------------------------
# 5. Dedicated service account with MINIMAL roles.
#    We deliberately do NOT grant project-wide roles here; the only secret-level
#    binding is added in step 6. logging.logWriter (project-level) is optional
#    and is the single broad-ish grant — kept narrow on purpose.
# ---------------------------------------------------------------------------
log "Creating service account '${SA_EMAIL}'..."
if ! "${gcloud_base[@]}" iam service-accounts describe "${SA_EMAIL}" >/dev/null 2>&1; then
  "${gcloud_base[@]}" iam service-accounts create "${SA_NAME}" \
    --display-name="Free Claude Code Proxy SA"
else
  log "Service account '${SA_EMAIL}' already exists — skipping."
fi

# Optional: allow the VM to ship logs to Cloud Logging. Comment out if not wanted.
log "Granting roles/logging.logWriter to SA (optional, for Cloud Logging)..."
"${gcloud_base[@]}" projects add-iam-policy-binding "${PROJECT}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/logging.logWriter" \
  --condition=None >/dev/null

# ---------------------------------------------------------------------------
# 6. Secret Manager secret for the provider key + SECRET-LEVEL IAM binding.
#    The binding is scoped to this one secret (least privilege), not project-wide.
#    NOTE: this creates the secret container only. Add the actual key value with:
#      printf '%s' "$PROVIDER_KEY" | gcloud secrets versions add ${SECRET_NAME} \
#        --project=${PROJECT} --data-file=-
# ---------------------------------------------------------------------------
log "Creating Secret Manager secret '${SECRET_NAME}'..."
if ! "${gcloud_base[@]}" secrets describe "${SECRET_NAME}" >/dev/null 2>&1; then
  "${gcloud_base[@]}" secrets create "${SECRET_NAME}" --replication-policy="automatic"
  log "Secret '${SECRET_NAME}' created. Add the provider key with:"
  log "  printf '%s' \"\$PROVIDER_KEY\" | gcloud secrets versions add ${SECRET_NAME} --project=${PROJECT} --data-file=-"
else
  log "Secret '${SECRET_NAME}' already exists — skipping creation."
fi

log "Binding roles/secretmanager.secretAccessor on '${SECRET_NAME}' to SA (secret-level, least privilege)..."
"${gcloud_base[@]}" secrets add-iam-policy-binding "${SECRET_NAME}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor" >/dev/null

# ---------------------------------------------------------------------------
# 7. Create the VM.
#    - --no-address           : no external IP (private only; egress via NAT)
#    - --service-account      : the dedicated SA from step 5
#    - --no-scopes            : do NOT grant broad cloud-platform scopes; rely on IAM.
#    - --tags                 : drive the tag-scoped firewall rules
#    - enable-oslogin         : SSH access via OS Login + IAM (no project SSH keys)
#    - startup-script         : installs and runs the proxy (see startup.sh)
#    The proxy fetches the provider key from Secret Manager at runtime; the
#    PROVIDER_KEY_SECRET_RESOURCE metadata tells it which secret to read.
# ---------------------------------------------------------------------------
SECRET_RESOURCE="projects/${PROJECT}/secrets/${SECRET_NAME}/versions/latest"

log "Creating VM '${VM_NAME}' (${MACHINE_TYPE}, no external IP, minimal scopes, OS Login)..."
if ! "${gcloud_base[@]}" compute instances describe "${VM_NAME}" --zone="${ZONE}" >/dev/null 2>&1; then
  "${gcloud_base[@]}" compute instances create "${VM_NAME}" \
    --zone="${ZONE}" \
    --machine-type="${MACHINE_TYPE}" \
    --image-family="${IMAGE_FAMILY}" \
    --image-project="${IMAGE_PROJECT}" \
    --network="${VPC}" \
    --subnet="${SUBNET}" \
    --no-address \
    --service-account="${SA_EMAIL}" \
    --no-scopes \
    --tags="${TAG_PROXY},${TAG_ADMIN}" \
    --metadata=enable-oslogin=TRUE,PROVIDER_KEY_SECRET_RESOURCE="${SECRET_RESOURCE}",fcc-secret-name="${SECRET_NAME}",fcc-port="${PROXY_PORT}" \
    --metadata-from-file=startup-script="${STARTUP_SCRIPT}"
else
  log "VM '${VM_NAME}' already exists — skipping creation."
fi

# ---------------------------------------------------------------------------
# 8. IAP IAM bindings (group-based, so onboarding == group membership).
#    - tunnelResourceAccessor : lets the eng group open IAP tunnels to the VM
#    - osLogin                : lets the admin group SSH in via IAP for Admin UI
# ---------------------------------------------------------------------------
log "Granting roles/iap.tunnelResourceAccessor on '${VM_NAME}' to ${IAP_USER_GROUP}..."
"${gcloud_base[@]}" compute instances add-iam-policy-binding "${VM_NAME}" \
  --zone="${ZONE}" \
  --member="${IAP_USER_GROUP}" \
  --role="roles/iap.tunnelResourceAccessor" >/dev/null

log "Granting roles/compute.osLogin on '${VM_NAME}' to ${ADMIN_GROUP} (admin SSH path)..."
"${gcloud_base[@]}" compute instances add-iam-policy-binding "${VM_NAME}" \
  --zone="${ZONE}" \
  --member="${ADMIN_GROUP}" \
  --role="roles/compute.osLogin" >/dev/null

# ---------------------------------------------------------------------------
# Done — quota reminder.
# ---------------------------------------------------------------------------
cat <<EOF

==========================================================================
Provisioning complete.

NEXT STEPS
  1. Add the provider key to Secret Manager (if you have not already):
       printf '%s' "\$PROVIDER_KEY" | gcloud secrets versions add ${SECRET_NAME} \\
         --project=${PROJECT} --data-file=-

  2. IMPORTANT — IAP TCP forwarding tunnel quota.
     The default is 25 simultaneous tunnels per project. For ~50 users each
     opening a proxy tunnel + an SSH tunnel you may need ~100. Request an
     increase BEFORE rollout:
       Console: IAM & Admin > Quotas > filter "IAP TCP forwarding"
       Verify:  gcloud compute project-info describe --project ${PROJECT} | grep -A5 -i iap

  3. Verify with the checklist in deploy/README.md (and domain_docs/*).
==========================================================================
EOF
