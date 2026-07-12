---
name: claudim-delegate
description: Manual only. Invoke via /claudim-delegate or when the user literally writes "claudim-delegate". Runs delegation through claudim -p subprocesses.
disable-model-invocation: true
argument-hint: <task and optional model/parallelism>
---

# External subprocess delegation

Use this skill only when explicitly invoked. Parse `$ARGUMENTS`, split independent work, and launch bounded parallel `claudim -p --model <model> "<task>"` subprocesses. Use tmux when requested, capture every result and exit status, then verify claimed file effects before synthesis. Never treat fan-out, workflow, cheap models, or generic delegation language as an implicit trigger.
