---
name: claudim-panel
description: User-invoked panel command — force a multi-agent panel on claudim delegate models. Trigger - the user invokes /claudim-panel <spec> describing the roles they want (e.g. "five agents - adversarial, architect, senior engineer, external consultant, devil's advocate"). Parse the roles, pick the best delegate-* model for each, spawn them in parallel, synthesize.
---

# claudim-panel

The user explicitly requested a delegate agent panel. Steps:

1. Parse the request into N distinct roles (default 3 if unspecified). Each role gets a one-paragraph mission derived from the user's words and the current conversation context.
2. For each role, pick the best-fit `delegate-*` agent from this session's agent list by its description (reasoning-heavy role → a pro/max model; bulk/scan role → a flash/lite model; code role → a code model; long-document role → a long-context model). Use a DIFFERENT model per role when the pool allows.
3. Spawn all roles in parallel via the Agent tool (single message, multiple calls). Each prompt: role mission, full task context, absolute file paths, and "return your analysis as structured text".
4. If a role genuinely needs a premium model, use an `approval-*` agent — the human will approve or deny; on deny, fall back to a `delegate-*`.
5. Synthesize: compare the panel's outputs, name agreements and conflicts, give YOUR final integrated recommendation. You are accountable for the result.

Never ask the user for model ids or gateway details — the roster is already in your agent list.