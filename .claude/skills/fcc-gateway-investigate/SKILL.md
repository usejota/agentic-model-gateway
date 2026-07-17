---
name: fcc-gateway-investigate
description: >
  Investigate and debug the deployed Agentic Model Gateway / free-claude-code (fcc) proxy on
  its GCP VM. Use when debugging the fcc-proxy / claudim / buxexa gateway: auto mode broken,
  classifier not rerouting, model routing wrong, 1M context not working, admin UI fields missing,
  500s / timeouts / fail-closed denials, or any "the gateway is misbehaving in prod" report.
  Covers reaching the VM over IAP, reading the RIGHT logs (loguru file sink vs journald), the
  request-detection / reroute pipeline, and the known gotchas from prior incidents. Trigger on:
  "fcc", "claudim", "buxexa", "fcc-proxy", "auto mode", "classifier route", "gateway logs",
  "model gateway broken".
---

# Investigate the fcc / Agentic Model Gateway deployment

The gateway is a FastAPI proxy (`free-claude-code`, package `free_claude_code`) that sits between
Claude Code clients and OpenAI/Anthropic-compatible providers. Clients reach it via the `buxexa`
(formerly `claudim`) launcher, which points `ANTHROPIC_BASE_URL` at the proxy over Tailscale.

## Deployment facts (verify, don't assume — they drift)

- **VM:** `fcc-proxy`, project `stp-core-dev`, zone `us-west1-a`. (The repo default `jota-fcc-proxy`
  is a placeholder — the real project is `stp-core-dev`. Confirm with
  `gcloud compute instances list --project=stp-core-dev`.)
- **Access:** IAP tunnel (no external IP). `gcloud compute ssh fcc-proxy --zone=us-west1-a
  --project=stp-core-dev --tunnel-through-iap`.
- **Service:** systemd `fcc.service`, runs as user `fcc`, `WorkingDirectory=/home/fcc/free-claude-code`,
  `ExecStart=/home/fcc/.local/bin/uv run fcc-server` (a supervised single process — NOT
  `uvicorn --workers`; the old `uvicorn server:app` entrypoint was removed in the 4.x sync).
- **Port:** 8082. **Auth: BEARER-ONLY** since the 4.x sync (upstream #1096) — use
  `-H "Authorization: Bearer freecc"`. `x-api-key` returns 401. Default token `freecc`.
- **Managed env:** `/home/fcc/.fcc/.env` (Pydantic Settings reads it; admin UI writes it).
- **Repo/branch the VM runs:** `main` from `github.com/usejota/agentic-model-gateway`, pulled on boot
  by the metadata `startup-script` (a COPY of `deploy/startup.sh` — see the deploy skill).

## THE #1 GOTCHA: logs go to a FILE, not journald (unless FCC_JSON_LOGS=true)

loguru's default sink is a **file**: `/home/fcc/.fcc/logs/server.log` (rotated;
`server.<timestamp>.log`). Structured JSON to stdout→journald→Cloud Logging is **opt-in** via
`FCC_JSON_LOGS=true` (read from `os.getenv` at import in `config/logging_config.py`).

- If `FCC_JSON_LOGS` is set (it is, as of 4.11.4): `journalctl -u fcc.service` shows structured
  JSON lines (`"level":`, `"event":`, `"message":`, `request_id`, trace fields).
- If NOT set: journald shows ONLY uvicorn access lines (`INFO:  IP - "POST /v1/messages" 200`).
  All `logger.*` / `trace_event()` output is in the file sink. **Reading journald and seeing "0
  traces" does NOT mean nothing fired — it means you're reading the wrong channel.** This exact
  mistake blinded a whole classifier investigation. Always check BOTH:
  ```sh
  sudo journalctl -u fcc.service --since "30 min ago" | grep -iE '"event":|classifier'
  sudo grep -iE 'classifier|reroute|TRACE' /home/fcc/.fcc/logs/server.log | tail
  ```

## Trace events emitted by the proxy

`trace_event(stage=, event=, source=, **fields)` → `logger.bind(trace_payload=...).info("TRACE {}")`.
Grep the file sink (or journald if JSON on) for these:
- `free_claude_code.api.route.classifier_reroute` — a classifier turn was rerouted to CLASSIFIER_ROUTE.
- `free_claude_code.api.route.classifier_miss` — a classifier-SHAPED request that did NOT match
  (fields: `no_tools`, `has_transcript`, `has_verdict_block`, `has_security_monitor`). This is the
  near-miss diagnostic; if reroute isn't firing, this tells you which detector condition failed.
- image reroute / routing events similarly under `stage="routing"`.

## The request-detection / routing pipeline (where bugs live)

`src/free_claude_code/api/handlers/messages.py` → `_apply_message_routing_policies` runs, in order:
1. `_maybe_reroute_for_classifier` — if CLASSIFIER_ROUTE set AND `is_safety_classifier_request`
   matches, swap model to the classifier route (fast/cheap, e.g. `open_router/google/gemini-2.5-flash`).
2. `_maybe_reroute_for_images` — IMAGE_ROUTE for image-bearing turns.
3. then delegate-policy enforcement (if the delegate PRs are merged).

Detectors live in `src/free_claude_code/api/detection.py`:
- `is_safety_classifier_request` — match = `no_tools AND has_transcript AND (has_security_monitor
  OR has_verdict_block)`. The classifier request is TOOLLESS; its system prompt contains
  "security monitor for autonomous AI coding agents" and a `<transcript>`. It does NOT contain
  literal `yes</block>`/`no</block>` (those are the model's OUTPUT, gated by a `</block>`
  stop-sequence) — matching on the block literal alone silently missed 100% of real requests
  (the 4.11.3 fix).
- `classifier_detection_signals()` returns the per-condition dict; `is_classifier_shaped()` flags
  a near-miss worth tracing.

## Model-id encoding (why /v1/models ids look weird)

`src/free_claude_code/core/gateway_model_ids.py` (or `api/`):
- `anthropic/<provider>/<model>` — thinking gateway id.
- `claude-3-freecc-no-thinking/<provider>/<model>` — no-thinking variant (the `claude-3-` substring
  makes Claude Code treat it as non-thinking AND makes it ignore `CLAUDE_CODE_MAX_CONTEXT_TOKENS`).
- `[1m]` suffix on the CLIENT-FACING id → Claude Code's `has1mContext()` (regex `/\[1m\]/i`) reports
  1M. The proxy STRIPS `[1m]` before forwarding upstream (OpenRouter 400s on `model[1m]` — this
  took the gateway down once). Verify: `/v1/models` advertises `…[1m]`; a request with it reaches
  the provider WITHOUT it.
- Picker order (4.11.1+): pinned head Opus→Fable→Sonnet→Haiku (`[1m]` preferred), rest alphabetical.

## Auto-compact / context window

- `CLAUDE_CODE_AUTO_COMPACT_WINDOW` default is HIGH (1000000). Claude Code clamps it to each model's
  real context window, so 1M (`[1m]`) models compact near 1M; smaller clamp to ~200K.
- `[1m]` is binary (exactly 1M) — no per-model way to signal 256K/2M; those need
  `CLAUDE_CODE_MAX_CONTEXT_TOKENS` (global, and ignored for `claude-*`-normalized ids).

## Standard investigation flow

1. **Reach it + confirm health:**
   ```sh
   gcloud compute ssh fcc-proxy --zone=us-west1-a --project=stp-core-dev --tunnel-through-iap \
     --command='systemctl is-active fcc.service; curl -s localhost:8082/health'
   ```
2. **Version + HEAD on the VM:**
   `sudo -u fcc git -C /home/fcc/free-claude-code rev-parse --short HEAD; grep -m1 ^version /home/fcc/free-claude-code/pyproject.toml`
3. **Read BOTH log channels** (see gotcha above). Filter noise:
   `grep -vE 'GET /health|count_tokens.*200'`. The OTEL "No API key" ERROR lines are Datadog/telemetry
   noise, ignore. `resource_tracker leaked semaphore` on shutdown is benign.
4. **Reproduce a decision server-side** with curl (bearer auth). Example — confirm classifier reroute:
   ```sh
   curl -s localhost:8082/v1/messages -H "Authorization: Bearer freecc" -H "content-type: application/json" \
     --data '{"model":"claude-sonnet-4","max_tokens":32,"stream":false,
       "system":"You are a security monitor for autonomous AI coding agents.",
       "messages":[{"role":"user","content":"<transcript>\nUser: rm x\n</transcript> evaluate"}]}' \
     | python3 -c 'import sys,json;print("upstream model:",json.load(sys.stdin).get("model"))'
   ```
   If it reroutes, `"model"` is the CLASSIFIER_ROUTE target (gemini), and `server.log` shows a
   `classifier_reroute` trace. NOTE: a toolless synthetic request can match when the real one
   doesn't — reproduce the REAL shape (with the actual system prompt) when detection is the suspect.
5. **Inspect live Settings** (what the running proxy actually loaded — env precedence bugs hide here):
   write a probe to a tmp file and `uv run python /tmp/probe.py` as the fcc user:
   ```python
   from free_claude_code.config.settings import Settings
   from free_claude_code.config.paths import managed_env_path
   s = Settings()
   print(managed_env_path(), repr(s.classifier_route), repr(s.image_route), repr(s.model))
   ```
6. **Check admin config live:** `curl -s localhost:8082/admin/api/config -H "Authorization: Bearer freecc"`
   (loopback-gated; over the tunnel it works because it lands on the VM's loopback via SSH forward).

## Admin UI notes

- Admin fields come from `src/free_claude_code/config/admin/manifest.py` (`_NON_PROVIDER_FIELDS`).
  The 4.x re-port DROPPED IMAGE_ROUTE / CLASSIFIER_ROUTE / FALLBACK_MODELS from the manifest — the
  backend kept working (values load from `.env`) but the UI fields vanished. Re-added in 4.10–4.11.
  If a known setting is missing from the UI, check the manifest first.
- Model fields use `field_type="model"`/`"optional_model"` → a type-to-search **datalist**
  (269 discovered models). **Safari's datalist is broken** — the picker shows nothing in Safari;
  works in Chrome/Firefox. Tell users to use Chrome for /admin.
- Admin panel is loopback-only unless `ADMIN_API_TOKEN` set. Reach it via SSH local-forward
  (`-L 8085:localhost:8082`), NOT a raw IAP tunnel to 8082 (raw tunnel → 403, non-loopback source).

## Known past incidents (pattern-match against these first)

- **Auto mode fails closed** → classifier reroute not firing → classifier runs on slow session model
  → >15s → Claude Code aborts → denies tool. Fix was the detector (4.11.3). Check `classifier_miss`
  traces.
- **OpenRouter 400 "model[1m] is not a valid model ID"** → `[1m]` leaked upstream. The router must
  strip it before forwarding.
- **Admin edits silently revert on reload** → env precedence: managed env must be highest, and only
  `process` env is truly locked (not `explicit_env_file`).
- **Service crash-loops after a deploy** → the systemd unit ExecStart references a removed entrypoint
  (e.g. old `uvicorn server:app`). The unit is rewritten on boot by startup.sh — see the deploy skill.

## Companion
For deploying fixes / restarting / pushing config, use the `fcc-gateway-deploy` skill.
