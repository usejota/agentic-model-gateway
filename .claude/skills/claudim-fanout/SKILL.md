---
name: claudim-fanout
description: Explicit native fan-out across diverse gateway delegate models.
disable-model-invocation: true
argument-hint: <roles, count, tasks, and optional model names>
---

# Native fan-out

Parse `$ARGUMENTS` into count, roles, tasks, and named models. Resolve every named model with `claudim models resolve "<query>"`; use the returned `agent_name`. If resolution is ambiguous, ask the user instead of choosing silently. For unnamed roles, select free `delegate-*` agents by capability and useful model diversity.

Issue every independent Agent call in the same assistant message. If the user denies an `approval-*` spawn, say so and replace it with a free delegate of the same capability; never claim the denied agent ran. Synthesize agreements, disagreements, evidence, and a conclusion.
