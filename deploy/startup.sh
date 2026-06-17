#!/usr/bin/env bash
#
# startup.sh — GCE VM startup script for the free-claude-code (fcc) proxy.
# Runs as root on every boot. Installs uv + Python + the proxy under a dedicated
# 'fcc' user and runs it as a systemd service with Restart=always.
#
# SECURITY HARDENING (see domain_docs/security.md #3, #4):
#   The provider API key is NEVER written to a plaintext .env on persistent disk.
#   Two supported models:
#
#     (a) RUNTIME FETCH (preferred). The proxy reads the key from Secret Manager
#         itself at startup using the resource name in PROVIDER_KEY_SECRET_RESOURCE.
#         A runtime Secret Manager feature is being added to the app separately.
#         When that ships, the key never touches disk at all and rotation takes
#         effect on service restart with no rebuild. This script passes the
#         resource name through to the service environment for that path.
#
#     (b) TMPFS FALLBACK (only if the app build in use cannot yet fetch at
#         runtime). We fetch the key into a RAM-only tmpfs mount, chmod 600.
#         It is lost on reboot and never lands on the boot disk / snapshots.
#         Enable by setting metadata fcc-use-tmpfs-env=TRUE on the VM.
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Read instance metadata set by provision.sh.
# ---------------------------------------------------------------------------
META="http://metadata.google.internal/computeMetadata/v1/instance/attributes"
md() { curl -s -H "Metadata-Flavor: Google" "${META}/$1" 2>/dev/null || true; }

SECRET_RESOURCE="$(md PROVIDER_KEY_SECRET_RESOURCE)"
SECRET_NAME="$(md fcc-secret-name)"
PORT="$(md fcc-port)"; PORT="${PORT:-8082}"
USE_TMPFS_ENV="$(md fcc-use-tmpfs-env)"   # "TRUE" enables the tmpfs fallback

# Tailscale: when fcc-tailscale-enabled=TRUE the VM joins the tailnet on boot so
# engineers reach it at its MagicDNS name (matches the staging tailscale-fw /
# PKI access pattern). Auth uses a Tailscale OAuth client whose secret lives in
# Secret Manager; the node is tagged so the tailnet ACL governs who may reach it.
TS_ENABLED="$(md fcc-tailscale-enabled)"            # "TRUE" to join the tailnet
TS_OAUTH_SECRET_NAME="$(md fcc-tailscale-oauth-secret)"  # Secret Manager secret holding the OAuth client secret
TS_TAGS="$(md fcc-tailscale-tags)"; TS_TAGS="${TS_TAGS:-tag:fcc-proxy}"
TS_HOSTNAME="$(md fcc-tailscale-hostname)"; TS_HOSTNAME="${TS_HOSTNAME:-fcc-proxy}"

FCC_USER="fcc"
FCC_HOME="/home/${FCC_USER}"
APP_DIR="${FCC_HOME}/free-claude-code"
ENV_DIR="${FCC_HOME}/.fcc"               # tmpfs-mounted when fallback is used
ENV_FILE="${ENV_DIR}/env"
REPO_URL="$(md fcc-repo-url)"; REPO_URL="${REPO_URL:-https://github.com/usejota/agentic-model-gateway.git}"
REPO_BRANCH="$(md fcc-repo-branch)"; REPO_BRANCH="${REPO_BRANCH:-main}"

log() { echo "[startup] $*"; }

# ---------------------------------------------------------------------------
# 1. Base packages + dedicated unprivileged user.
# ---------------------------------------------------------------------------
log "Installing base packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends git curl ca-certificates

log "Creating '${FCC_USER}' user..."
id -u "${FCC_USER}" >/dev/null 2>&1 || useradd -m -s /bin/bash "${FCC_USER}"

# ---------------------------------------------------------------------------
# 2. Install uv + Python + the proxy as the fcc user.
# ---------------------------------------------------------------------------
log "Installing uv, Python and the proxy as '${FCC_USER}'..."
# Install the gcp extra (google-cloud-secret-manager) only when the runtime
# Secret Manager fetch is actually configured — otherwise base deps are enough.
# Without this, setting PROVIDER_KEY_SECRET_RESOURCE crashes the proxy on a
# missing-import (the validator needs the extra). See Option A vs runtime-fetch.
SYNC_EXTRAS=""
if [ -n "${SECRET_RESOURCE}" ]; then
  SYNC_EXTRAS="--extra gcp"
fi
sudo -u "${FCC_USER}" REPO_URL="${REPO_URL}" REPO_BRANCH="${REPO_BRANCH}" SYNC_EXTRAS="${SYNC_EXTRAS}" bash -lc '
  set -euo pipefail
  if [ ! -x "$HOME/.local/bin/uv" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
  export PATH="$HOME/.local/bin:$PATH"
  if [ ! -d "$HOME/free-claude-code/.git" ]; then
    git clone --branch "$REPO_BRANCH" "$REPO_URL" "$HOME/free-claude-code"
  else
    git -C "$HOME/free-claude-code" fetch origin "$REPO_BRANCH"
    git -C "$HOME/free-claude-code" checkout "$REPO_BRANCH"
    git -C "$HOME/free-claude-code" pull --ff-only origin "$REPO_BRANCH"
  fi
  cd "$HOME/free-claude-code"
  uv python install 3.14.0
  uv sync ${SYNC_EXTRAS}
'

# ---------------------------------------------------------------------------
# 3. Provider key handling — preferred runtime fetch vs tmpfs fallback.
# ---------------------------------------------------------------------------
SYSTEMD_ENV_LINES=()
SYSTEMD_ENV_LINES+=("Environment=PORT=${PORT}")
SYSTEMD_ENV_LINES+=("Environment=HOST=0.0.0.0")
SYSTEMD_ENV_LINES+=("Environment=ANTHROPIC_AUTH_TOKEN=freecc")

if [ "${USE_TMPFS_ENV}" = "TRUE" ]; then
  # ---- (b) TMPFS FALLBACK ------------------------------------------------
  # Mount a RAM-only filesystem and write the key there with 0600 perms. This
  # is lost on reboot and never persisted to the boot disk or any snapshot.
  log "tmpfs fallback enabled — fetching provider key into RAM-only ${ENV_DIR}."
  mkdir -p "${ENV_DIR}"
  mountpoint -q "${ENV_DIR}" || mount -t tmpfs -o size=1m,mode=0700,uid="${FCC_USER}",gid="${FCC_USER}" tmpfs "${ENV_DIR}"

  if [ -z "${SECRET_NAME}" ]; then
    log "ERROR: fcc-secret-name metadata is empty; cannot fetch provider key."
    exit 1
  fi
  KEY="$(gcloud secrets versions access latest --secret="${SECRET_NAME}")"
  umask 077
  # NOTE: rename the variable below to whatever your chosen provider expects
  # (e.g. OPENROUTER_API_KEY). The Admin UI can also set/override this later.
  printf 'PROVIDER_API_KEY=%s\n' "${KEY}" > "${ENV_FILE}"
  unset KEY
  chown "${FCC_USER}:${FCC_USER}" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  SYSTEMD_ENV_LINES+=("EnvironmentFile=${ENV_FILE}")
else
  # ---- (a) RUNTIME FETCH (preferred) -------------------------------------
  # Hand the app the Secret Manager resource name; it reads the key into memory
  # at startup. The key never touches disk. Rotation = restart, no rebuild.
  log "Runtime-fetch path: app will read PROVIDER_KEY_SECRET_RESOURCE at startup."
  SYSTEMD_ENV_LINES+=("Environment=PROVIDER_KEY_SECRET_RESOURCE=${SECRET_RESOURCE}")
fi

# ---------------------------------------------------------------------------
# 3b. Tailscale — join the tailnet so engineers reach the VM by MagicDNS name.
#     Auth via a Tailscale OAuth client (secret in Secret Manager) + ACL tag.
#     OAuth-client auth mints a fresh node key per boot and never expires the
#     way a static auth key does — better for a long-lived unattended VM.
# ---------------------------------------------------------------------------
if [ "${TS_ENABLED}" = "TRUE" ]; then
  log "Tailscale enabled — installing and joining the tailnet as '${TS_HOSTNAME}' (${TS_TAGS})."
  if ! command -v tailscale >/dev/null 2>&1; then
    curl -fsSL https://tailscale.com/install.sh | sh
  fi
  systemctl enable --now tailscaled

  if [ -z "${TS_OAUTH_SECRET_NAME}" ]; then
    log "ERROR: fcc-tailscale-oauth-secret metadata is empty; cannot authenticate to Tailscale."
    exit 1
  fi
  # The credential in the secret is a Tailscale auth key (tskey-auth-...) OR an
  # OAuth client secret — both are accepted as --authkey. We advertise the tag so
  # the node is tagged tag:fcc-proxy even when the key itself is untagged (the key
  # owner is in group:infra, which owns the tag). Non-ephemeral: the node persists
  # across reboots rather than vanishing when offline.
  TS_OAUTH_SECRET="$(gcloud secrets versions access latest --secret="${TS_OAUTH_SECRET_NAME}")"
  tailscale up \
    --authkey="${TS_OAUTH_SECRET}" \
    --advertise-tags="${TS_TAGS}" \
    --hostname="${TS_HOSTNAME}" \
    --ssh \
    --accept-dns=true
  unset TS_OAUTH_SECRET
  log "Tailscale up. Node should appear as ${TS_HOSTNAME} on the tailnet."
fi

# ---------------------------------------------------------------------------
# 4. systemd unit — Restart=always, runs as the fcc user.
# ---------------------------------------------------------------------------
log "Writing systemd unit..."
{
  echo "[Unit]"
  echo "Description=free-claude-code proxy"
  echo "After=network-online.target"
  echo "Wants=network-online.target"
  echo ""
  echo "[Service]"
  echo "User=${FCC_USER}"
  echo "WorkingDirectory=${APP_DIR}"
  for line in "${SYSTEMD_ENV_LINES[@]}"; do
    echo "${line}"
  done
  echo "ExecStart=${FCC_HOME}/.local/bin/uv run uvicorn server:app --host 0.0.0.0 --port ${PORT} --workers 3"
  echo "Restart=always"
  echo "RestartSec=3"
  echo ""
  echo "[Install]"
  echo "WantedBy=multi-user.target"
} > /etc/systemd/system/fcc.service

log "Enabling and starting fcc.service..."
systemctl daemon-reload
systemctl enable --now fcc.service

log "Startup complete. 'systemctl status fcc' to verify; key is not on persistent disk."
