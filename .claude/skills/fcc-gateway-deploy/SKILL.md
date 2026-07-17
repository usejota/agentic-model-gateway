---
name: fcc-gateway-deploy
description: >
  Deploy, restart, and ship changes to the Agentic Model Gateway / free-claude-code (fcc) proxy
  on its GCP VM, and manage the buxexa/claudim client. Use when: deploying a merged PR to the
  fcc-proxy VM, restarting fcc.service, bumping the version, pushing the systemd startup-script
  metadata, cutting a client (buxexa) upgrade, or running the PR merge→deploy loop for this repo.
  Encodes the exact, verified command sequences + the versioning rules + the reboot-safety gotcha.
  Trigger on: "deploy fcc", "deploy the gateway", "restart fcc-proxy", "ship this to the VM",
  "deploy buxexa", "push to the gateway VM", "bump fcc version".
---

# Deploy the fcc / Agentic Model Gateway

Repo: `github.com/usejota/agentic-model-gateway` (fork; upstream `Alishahryar1/free-claude-code`).
VM: `fcc-proxy`, project `stp-core-dev`, zone `us-west1-a`, reached over IAP. Service: `fcc.service`
(user `fcc`, `/home/fcc/free-claude-code`, `ExecStart=…/uv run fcc-server`, port 8082).

## Versioning (enforced by CLAUDE.md — required on every prod-file commit on main)

Bump `[project].version` in `pyproject.toml` + run `uv lock` in the SAME commit as any change to a
production path (`src/free_claude_code/**`, `deploy/*.sh`, `scripts/install*.sh`, `.env.example`,
`pyproject.toml`). semver: PATCH = fix/refactor/ops/deps; MINOR = new capability (field, provider,
route, admin field); MAJOR = breaking (renamed env var, removed entrypoint, client rename).
Tests/docs alone → no bump. Current line: 4.11.x.

`uv.lock` note: on macOS with a newer local uv, `uv lock` may rewrite nvidia-cuda markers (bare vs
`sys_platform`). Main's lock is self-consistent (`uv lock --check` passes) with mixed markers — do
NOT "clean" it. If a rebase shows only marker churn, take main's uv.lock and bump only the
`free-claude-code` version line.

## Local CI gate (run before pushing — mirrors GitHub required checks)

```sh
uv run ruff format && uv run ruff check && uv run ty check && uv run pytest
```
5 required checks: suppression grep, ruff-format, ruff-check, ty, pytest. Do NOT add
`# type: ignore` / `from __future__ import annotations` (banned). Known flaky:
`tests/scripts/test_uninstallers.py::…purge_failure…` fails under parallel order when a local
fcc-server runs — passes in isolation; ignore it, it's not your change.

## PR → merge → deploy loop

```sh
git checkout -b <type>/<slug>            # never commit prod change straight to main
# … edit, bump version, uv lock, CI green …
git commit -m "…" && git push -u origin HEAD
gh pr create --repo usejota/agentic-model-gateway --base main --title "…" --body "…"
```
- **Poll checks** (branch protection needs all 5 green): loop `gh pr checks <N> --repo usejota/…`
  until no `pending` / no `fail`.
- **Merge:** `gh pr merge <N> --repo usejota/agentic-model-gateway --squash`. Do NOT use `--admin`
  (classifier blocks the bypass; checks satisfy protection anyway).
- **Stacked-PR conflict:** if a PR was branched off another PR's branch and that PR squash-merged,
  the child conflicts (version/lock/shared file). Fix = rebase the child onto `origin/main`
  (`git rebase origin/main`; git auto-skips the already-squashed commit), resolve version to the
  new top, `--force-with-lease` push, re-poll, merge.

## Deploy to the VM (standard — code change, no unit change)

```sh
gcloud compute ssh fcc-proxy --zone=us-west1-a --project=stp-core-dev --tunnel-through-iap --command='
set -e
sudo -u fcc -H bash -lc "
  export PATH=\$HOME/.local/bin:\$PATH
  cd \$HOME/free-claude-code
  git fetch origin main -q && git checkout -q main && git pull --ff-only origin main -q
  git rev-parse --short HEAD; grep -m1 \"^version\" pyproject.toml
  uv sync --extra gcp 2>&1 | tail -2
"
sudo systemctl restart fcc.service
sleep 6
systemctl is-active fcc.service
for i in 1 2 3 4 5; do c=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8082/health); echo "try $i: $c"; [ "$c" = "200" ] && break; sleep 2; done
'
```
Then verify the feature (bearer auth!):
`curl -s localhost:8082/v1/models -H "Authorization: Bearer freecc"` (+ grep for `[1m]` variants, etc).

**Restarts always trigger a burst of `500 Internal Server Error` + `CancelledError: timeout graceful
shutdown` in the log at the restart instant — that's the OLD workers being torn down, NOT a bug.**

## Deploy when the systemd unit must change (entrypoint / env var)

The unit is written by `deploy/startup.sh` ON EVERY BOOT from the VM's `startup-script` METADATA
(a copy, not the repo file). So a change to how the service runs is TWO places:

1. **Live unit** (takes effect now, on restart) — edit `/etc/systemd/system/fcc.service`, e.g.:
   ```sh
   sudo cp /etc/systemd/system/fcc.service /etc/systemd/system/fcc.service.bak-<why>
   sudo sed -i 's#^ExecStart=.*#ExecStart=/home/fcc/.local/bin/uv run fcc-server#' /etc/systemd/system/fcc.service   # example
   sudo systemctl daemon-reload && sudo systemctl restart fcc.service
   ```
   (Show the diff before applying — the auto-mode classifier blocks blind live edits to the unit;
   present the exact `sed` and get an explicit "go".)
2. **Metadata** (so REBOOT keeps it) — commit the change in `deploy/startup.sh`, merge, then:
   ```sh
   gcloud compute instances add-metadata fcc-proxy --zone=us-west1-a --project=stp-core-dev \
     --metadata-from-file startup-script=deploy/startup.sh
   ```
   `add-metadata` only replaces the stored script — it does NOT re-run it, does NOT touch the running
   service. Safe. Verify: `gcloud compute instances describe … --format="value(metadata.items.filter(
   "key:startup-script").extract("value"))" | grep -c '<the new line>'`.

If you skip step 2, the next reboot silently reverts step 1. This exact trap crash-looped the service
after the 4.x sync (unit still ran the removed `uvicorn server:app`).

Env vars already in the unit: `PORT=8082`, `HOST=0.0.0.0`, `FCC_OPEN_BROWSER=false`,
`FCC_JSON_LOGS=true`, `ANTHROPIC_AUTH_TOKEN=freecc`, `PROVIDER_KEY_SECRET_RESOURCE=…`.

## gcloud auth

If any `gcloud` call errors `Reauthentication failed / cannot prompt during non-interactive
execution` → the token expired. Ask the user to run `gcloud auth login` (suggest the `!` prefix so
output lands in-session), then retry. IAP SSH may still work briefly after other gcloud calls fail.

## Admin config (change routes/models without a deploy)

Settings persist in `/home/fcc/.fcc/.env` and hot-reload (mtime-tracked). Edit via the Admin UI
(reach it with an SSH local-forward, then Chrome — Safari datalist is broken):
```sh
gcloud compute ssh fcc-proxy --zone=us-west1-a --project=stp-core-dev --tunnel-through-iap -- -L 8085:localhost:8082 -N
# then http://localhost:8085/admin  (hard-reload Cmd-Shift-R; datalist search = type to filter)
```
Or set a value directly in `/home/fcc/.fcc/.env` (as fcc) + restart. An explicit empty value in
`.env` (e.g. `FALLBACK_MODELS=`) OVERRIDES the manifest default — clear the line, don't blank it,
to fall back to the seeded default.

## The buxexa (client) side

The launcher (`deploy/buxexa`, installed via `scripts/install-buxexa.sh`) is CLIENT-SIDE bash on dev
machines — a rename or launcher change is NOT a server deploy. Server serves any client name.
- Users install/upgrade: `curl -fsSL https://raw.githubusercontent.com/usejota/agentic-model-gateway/main/scripts/install-buxexa.sh | sh`
  or `buxexa upgrade` (self-reinstalls under its own name). Raw-GitHub caches ~5 min after a push.
- Env vars are `BUXEXA_*` (HOST/TAILNET/PORT/TOKEN/BASE_URL/NAME/…). Renameable via `BUXEXA_NAME`.
- Client changes need users to re-run the installer; server changes do not.

## Post-deploy checklist

- [ ] `systemctl is-active fcc.service` → active; `NRestarts` not climbing (no crash-loop).
- [ ] `/health` 200; a real `POST /v1/messages` returns 200 in the log.
- [ ] Feature verified with a bearer-auth curl (or the relevant trace in `server.log`).
- [ ] If the unit changed: metadata pushed (reboot-safe) + backup unit left in place.
- [ ] `main` version == deployed version (CLAUDE.md consistency rule).

## Companion
For diagnosing WHAT to deploy (log reading, request pipeline, detectors), use the
`fcc-gateway-investigate` skill.
