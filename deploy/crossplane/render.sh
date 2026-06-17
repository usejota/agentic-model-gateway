#!/usr/bin/env bash
#
# render.sh — render a free-claude-code Crossplane overlay to final YAML, with the
# VM startup script (deploy/startup.sh) injected into the Instance's
# metadataStartupScript field.
#
# Pure kustomize can't slurp a 180-line shell script into a YAML field, so we render
# the overlay, then splice startup.sh into spec.forProvider.metadataStartupScript.
# Output goes to stdout — pipe it to a file and `kubectl apply -f`, e.g.:
#
#   deploy/crossplane/render.sh stg > /tmp/fcc-stg.yaml
#   kubectl apply -f /tmp/fcc-stg.yaml $CP --dry-run=server
#   kubectl apply -f /tmp/fcc-stg.yaml $CP
#
# Requires: kubectl (for `kubectl kustomize`) and python3 (stdlib only).
set -euo pipefail

OVERLAY="${1:-stg}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
OVERLAY_DIR="$HERE/overlays/$OVERLAY"
STARTUP="$REPO_ROOT/deploy/startup.sh"

[ -d "$OVERLAY_DIR" ] || { echo "ERROR: overlay not found: $OVERLAY_DIR" >&2; exit 1; }
[ -f "$STARTUP" ]     || { echo "ERROR: startup script not found: $STARTUP" >&2; exit 1; }

# Render the overlay, then inject the startup script into the Instance MR.
# The injection logic lives in _inject_startup.py to avoid shell-quoting issues.
kubectl kustomize "$OVERLAY_DIR" | STARTUP_PATH="$STARTUP" python3 "$HERE/_inject_startup.py"
