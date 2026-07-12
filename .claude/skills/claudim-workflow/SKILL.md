---
name: claudim-workflow
description: Explicit routed Workflow DAG across gateway delegate models.
disable-model-invocation: true
argument-hint: <workflow goal, stages, and optional model names>
---

# Routed Workflow

Parse `$ARGUMENTS`, form a small DAG, and resolve named models using `claudim models resolve "<query>"`. Never resolve ambiguity silently. Generate canonical JavaScript where every call is `agent(prompt, {agentType: "delegate-..."})` or `agent(prompt, {model: "<catalog-id>"})`. Before invoking Workflow, count `agent(` and `agentType:` plus `model:` occurrences; routing count must be at least the agent-call count.

Execute the Workflow, synthesize stage outputs, and verify any claimed file effects. An approval denial must be reported and replaced explicitly with a free same-capability delegate.
