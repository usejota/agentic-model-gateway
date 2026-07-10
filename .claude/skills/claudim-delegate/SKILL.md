---
name: claudim-delegate
description: When spawning subagents/workflows INSIDE a claudim session, or when the user mentions claudim / cheap-model delegation — guidance on WHICH gateway model runs each subagent. Delegates never change WHETHER to spawn agents; inline work stays inline. TWO MODES by environment. (1) INSIDE a claudim session (env CLAUDIM=1 or delegate-* agents in the agent list): when you decide to spawn subagents or workflows, pick the best-fit delegate-* agent per task. (2) OUTSIDE claudim (plain claude session): fan-out / workflows / subagents use the NORMAL Anthropic models — do NOT reach for claudim there; only use `claudim -p --model <alias>` when the user explicitly asks for claudim/cheap/non-American models via the trigger phrases. Auto-triggers (any language): "claudim", "do claudim", "pelo claudim", "agentes claudim", "delegate to claudim", "use claudim", "delega pros modelos baratos do claudim", "delega pro claudim", "delegar com claudim", "delegue a execução para agentes claudim", "delegar para o claudim", "delegue pros modelos chineses", "use modelos chineses baratos", "use modelos não americanos", "modelos baratos não americanos", "orchestrate with cheap models", "claudim-delegate" — plus ANY subagent/workflow/fan-out spawn while inside a claudim session.
---

# claudim-delegate

## Decision tree — read this FIRST

**Step 1 — detect the environment.** You are INSIDE a claudim session when
**either** is true:

- The env var `CLAUDIM=1` is set.
- `delegate-*` agents appear in your agent list (check the system preamble or run `/agents`).

**Step 2 — apply the mode.**

### MODE A: inside claudim — delegates are ADDITIVE

Delegates change WHICH model runs a subagent, never WHETHER you spawn one. The
native decision of when to use agents or workflows is unchanged — this skill
only helps pick the right delegate model per task.

- **Agent tool** (single subagent, fan-out, parallel spawns) -> pick the best-fit
  `delegate-*` agent by its description (strength) for each task. Use the model
  table below as a starting shortcut, not a boundary.
- **Workflow tool** -> pass `agentType: "delegate-..."` in each `agent()` call.
  Do NOT pass raw `model` ids unless the model has no corresponding agent (check
  `claudim models --all` for the full pool).
- **Generic agents** (Explore, general-purpose, Plan, ...) -> these are native
  Claude Code agents and are NOT blocked. The hook (transparent mode) lets them
  through. If you want them on a delegate model instead, spawn a `delegate-*`
  agent with the appropriate task.
- **`approval-*` agents** -> premium models (anthropic/openai). Spawning one
  triggers a human approval prompt per spawn. If denied, pick a `delegate-*`
  alternative.
- **Strict mode** (`claudim --delegate`, env `CLAUDIM_ENFORCE=1`) -> the hook
  DENIES non-delegate agents. Only use this when the user explicitly opts into
  strict enforcement.

**Do NOT delegate work you would do inline.** The delegate pool is a model
choice, not a delegation mandate. Keep your normal judgment about what to
spawn.

### MODE B: outside claudim (plain claude session)

- Fan-out / workflows / subagents -> **normal Anthropic models** (the native
  default). Do NOT reach for claudim or force cheap models.
- `claudim -p --tmux --model <alias>` ONLY when the user explicitly asks for
  claudim / cheap / non-American models (the trigger phrases). That is the sole
  claudim entry point outside a claudim session — there are no `delegate-*`
  agents available.

---

You are the **orchestrator** (Opus or Fable). You plan, decide, and integrate.
**Delegate** well-scoped, mechanical steps to the cheapest competent model in the
gateway pool, capture its output, and use it. This keeps quality (you) high and
cost (delegates) low.

## PRIMARY: Native delegate agents

When inside a claudim session, the launcher auto-generates one `delegate-*`
subagent per gateway model (capped at `CLAUDIM_MAX_AGENTS`, default 30). Each
agent is wired to the gateway's **no-thinking** model id — mandatory: reasoning
backends otherwise stream an unsigned `thinking` block that the Agent tool
discards and you'd get empty output.

Use the **Agent tool** with these subagents. No Bash, no `claudim -p`, no
subprocess overhead. The session model spawns them natively.

### Pick the model per task — YOUR call, whole pool available

**The entire delegate pool is yours to choose from — every `delegate-*` agent
in your list, not just the 8 curated ones.** The curated table below is a
starting shortcut, not a boundary. If you know a better fit for the task
(qwen for data work, a specific coder variant, a flash model you trust), USE
IT. Trust your own knowledge of model strengths; the pool is already filtered
to non-American + admin-approved, so every listed agent is safe and cheap to
pick. Do not restrict yourself to the curated list when the task deserves
better.

All pool models are non-American: Chinese (DeepSeek, Qwen, Moonshot/Kimi,
Zhipu/GLM, MiniMax, ByteDance, ...) + Mistral (France) + others. **US closed
labs — openai, anthropic, google, x-ai, amazon, nvidia, ibm-granite, liquid,
rekaai, relace — are never in the pool** (filtered server-side). Pick the
**cheapest that can do the job well**; escalate freely when the task demands.

Curated shortcuts (when you have no stronger opinion):

- **Smartest / hardest reasoning** — algorithm design, multi-step logic,
  architecture tradeoffs -> `delegate-deepseek-v4-pro`
- **Coding** — implement a function, refactor, fix a bug, write a test ->
  `delegate-kimi-k2-7-code` (Chinese) or `delegate-codestral` (France, non-Chinese alt)
- **Fastest / cheapest** — triage, lookups, mechanical edits, simple answers,
  bulk work -> `delegate-deepseek-v4-flash` (Chinese) or `delegate-ministral-8b` (France, non-Chinese alt)
- **Long-context general** — read/analyze large files, writing, summaries ->
  `delegate-glm-5-2` (or `delegate-minimax-m3` as alternative) (Chinese) or `delegate-mistral-small` (France)

Examples of going beyond the shortcuts (encouraged):
- Data analysis / structured extraction -> a qwen max/plus variant often beats
  the curated picks.
- Vision / screenshots -> a `-vl` variant.
- A giant bulk sweep -> the cheapest flash/lite model in the pool, even one
  not in the table.

Agent names are sanitized from the model id tail (`[a-z0-9-]`), so `kimi-k2.7-code`
becomes `delegate-kimi-k2-7-code`, etc. Run `/agents` to see every delegate
available in this session — scan it before defaulting to a curated pick.

### Pool bigger than the agent list?

The launcher caps generated agents at `CLAUDIM_MAX_AGENTS` (default 30), so
the gateway may offer models with no `delegate-*` agent. Run
`claudim models --all` to list the full live pool; for a model without an
agent, use the `-p` fallback with its id verbatim:

```sh
claudim models --all          # full pool, grouped by vendor, all no-thinking
claudim -p --model <id-from-list> "<task>"
```

Run `claudim models` any time to reprint the curated cheat-sheet.

### When to delegate vs. do it yourself

**Delegate** when the step is well-scoped and mechanical:
- Implement a single function / small module from a clear spec.
- Refactor an isolated file or function.
- Explain or summarize a file / directory.
- Write a test for a known behavior.
- Run a command / lookup and report the result.
- Mechanical edits across files following an exact pattern.

**Do it yourself** (don't delegate) when:
- It needs architectural judgment or a design decision.
- It needs correctness review / verification of another step's output.
- The task is ambiguous and needs you to choose between approaches.
- Coordinating multiple delegates or integrating their outputs.

You own the plan and the final result. Delegates execute.

### How to use a native delegate agent

Use the **Agent tool** with the `delegate-<model>` name. Give it a
self-contained task: include file paths, the exact change, and any convention
it must follow. The delegate runs in-process — no subprocess, no tmux, no
permission walls.

Native agents have **no 2-3 process parallelism cap** (that cap was a `-p`
subprocess concern only). You can fan out to as many delegates as the task
needs — the Agent tool handles scheduling. Still be reasonable: don't spawn 20
delegates for a 3-file change.

### Delegate that further delegates (subagents of a delegate)

A delegate (native agent **or** `claudim -p`) is a real `claude` process with
the **same tools** as your own session: Read, Write, Edit, Bash, Grep, Glob,
plus anything loaded from its cwd's `CLAUDE.md` / skills. It can also spawn
more delegates of its own. If you're orchestrating a workflow where the
delegate will itself run more delegates (or just needs to read files and write
outputs), write the prompt so it can't talk itself out of the task:

- **Declare tool access up front.** Open the prompt with: *"You have full
  tool access: Read, Write, Edit, Bash, Grep, Glob. CWD is `<abs path>`."*
  Without this, a cheap non-thinking model sometimes sees a single tool error
  and concludes (wrongly) that "I'm running as a subagent in a restricted
  context where I have no access to tools" and gives up — even when its next
  tool call would have succeeded. The `claudim-render.py` pane feed (the
  `-p` observer pane) shows the error TEXT on `✗`, so you can see the real
  reason, but the prompt should still prime the delegate not to over-react.
- **`✗` on a tool_result is per-call, NOT a permission denial.** Tell the
  delegate: *"If a tool returns ✗, read the error message, fix the path or
  args, and retry. Do NOT conclude you have no tools."* Cheap models
  sometimes stop on the first error; one explicit instruction prevents it.
- **Pass ABSOLUTE file paths** for anything the delegate (or its sub-delegates)
  must read or write. Relative paths in subagent prompts are the most common
  reason a delegate reports a Read failure on a file the user can see.
- **CWD matches the parent invocation.** The delegate runs in the same
  working directory as the parent, not in `~`. If the task needs the delegate
  to operate in a different directory, either `cd` in the task prompt or pass
  an absolute path everywhere.
- **Cap recursion.** Each delegate is its own `claude` process; a delegate
  spawning N sub-delegates multiplies the resource cost. Tell the delegate
  explicitly: *"Spawn at most 2-3 sub-delegates in parallel; serialize
  dependent steps."* — the same parallelism cap you would follow. (Native
  Agent-tool delegates have no hard cap, but the same cost reasoning applies.)

A safe prompt body for a delegate-that-further-delegates (shown in the
`claudim -p` fallback form; for a native agent, pass the same body to the
Agent tool):

```sh
claudim -p --tmux --model kimi-k2.7-code "You have full tool access
(Read, Write, Edit, Bash, Grep, Glob). CWD is $(pwd). If a tool returns
✗, read the error and retry — do NOT conclude you have no tools.

Task: <self-contained, with absolute paths to every file the
delegate or its sub-delegates will need>. Spawn at most 2-3
sub-delegates in parallel; serialize dependent steps.

Output: write the final JSONL to /abs/path/to/output.jsonl when done."
```

### After a delegate returns

1. **Check it didn't come back empty.** Empty output = the model failed. If
   empty, retry with a different delegate or do the step yourself.
2. **Verify the work** if it touched files — read the diff. You are
   accountable for correctness, not the delegate.
3. **Integrate** into the plan / next step. You stitch outputs together.

### Parallel delegates

For independent plan steps, spawn several Agent tool calls in parallel.
Collect outputs, then integrate. Serialize steps that depend on each other —
pass the previous delegate's output into the next one's prompt.

---

## Fallback: `claudim -p --tmux`

Use the **fallback** when:

- You are **not** in a claudim session (no `CLAUDIM=1`, no `delegate-*` agents).
- The user **explicitly asks** for tmux observation panes.
- You are in a **headless / scripted** context where native agents are not
  available.
- The gateway was down at launch so no agents were generated.

In these cases, each delegate is a full `claude -p` subprocess launched via
`claudim -p --tmux`. This is heavier (Node process per delegate, ~300-500MB
RAM each) and has a hard parallelism cap.

### The command

**The base command is `claudim -p --tmux` — `--tmux` is part of the command,
not an option.** Every single delegate launch includes it (the user watches
delegates live in split panes). Omitting it makes the delegate invisible,
which the user treats as a bug. The ONLY exception: the user explicitly asked
for silent/headless.

```sh
claudim -p --tmux --model <alias> "<task>"                            # stdout = answer (text)
claudim -p --tmux --output-format json --model <alias> "<task>"       # JSON: result, stop_reason, is_error, usage
claudim -p --tmux --unrestricted --model <alias> "<task>"             # needs gcloud/network/file-writes
```

`claudim` is on the dev machine (gateway launcher over Tailscale). `--model <alias>`
is rewritten to the gateway's **no-thinking** model id — this is mandatory:
reasoning backends otherwise stream an unsigned `thinking` block that `claude -p`
discards and you'd get empty stdout. Never use `--model haiku/sonnet/opus` for a
delegate (those route via gateway config and may enable thinking -> empty).

### How to run a delegate (fallback)

The `claude -p` child runs in your cwd and can read files itself. Give it a
self-contained task: include file paths, the exact change, and any convention it
must follow (the child may not load your CLAUDE.md unless you skip `--bare`).

- **Text output** (default): stdout is the answer. Capture with `$(...)` or the
  Bash tool's stdout.
- **JSON output** (`--output-format json`): parse to check `is_error` and
  `stop_reason` before trusting the result. Prefer this when you'll feed the
  output into the next step or when you need to know it succeeded.
- **Context control**: add `--bare` to skip CLAUDE.md/hooks (smaller, faster,
  cheaper) — only for tasks that don't need repo conventions. For real code
  tasks in a repo, omit `--bare` so the delegate picks up CLAUDE.md, or spell
  out the convention in the prompt.

### Observar delegates ao vivo (tmux) — fallback only

**Preflight tmux — obrigatório antes do PRIMEIRO `claudim -p --tmux` da sessão.**

`claudim --tmux` assume que existe um tmux server rodando. Se o server caiu
(systemd restart, sleep+resume, nunca subiu nesse shell), o delegate **pendura
silencioso esperando uma janela que nunca abre** — pode ficar 30-60 min
invisível até alguém matar. O sintoma é `exit 144` no Bash tool e tempo
gigante sem output. Checar:

```sh
tmux ls               # falha = server morto; sucesso = server vivo
[ -n "$TMUX" ] && echo "in-tmux" || echo "not-in-tmux"
```

Decidir antes de lançar:

- **Server morto (`tmux ls` falha):** suba um server primeiro
  (`tmux new -d -s scratch`) OU abra um shell novo dentro de tmux
  (`tmux new -s main`) e rode `claude --continue` lá. Sem server, **não lance
  `--tmux`** — o delegate vai pendurar. Sem tmux instalado, o launcher já
  cai em inline com nota, sem pane.
- **Server vivo, `$TMUX` setado:** o orquestrador está dentro de tmux (o
  usuário abriu `tmux new -s main` e rodou `claude` lá). Cada delegate abre
  como split pane na janela atual. **Setup ideal — o usuário vê tudo ao vivo.**
- **Server vivo, `$TMUX` vazio:** delegates vão pra uma sessão detached
  `claudim` — o usuário precisa `tmux attach -t claudim` em OUTRO terminal
  pra assistir. Funciona, mas é pior que estar dentro de tmux. **Recomende
  pro usuário migrar antes de continuar delegando:**
  `tmux new -s main` → `claude --continue` lá dentro → próximos delegates
  viram split panes do lado dele, sem precisar attachar sessão separada.
  (Mover a sessão atual pra dentro de tmux não rola sem reiniciar o Claude
  Code — o processo já nasceu preso a este terminal.)

Só depois do preflight é que vale lançar o delegate. Não assuma que o setup
está bom só porque `--tmux` não deu erro na invocação — o erro é silencioso.

Delegates `-p` rodam invisíveis por padrão. Passe `--tmux` (ou
`CLAUDIM_TMUX=1`) em cada launch e o delegate abre uma janela tmux que o
usuário pode acompanhar ao vivo **e interagir** (aprovar prompt, digitar
instrução).

**Se a sessão do orquestrador roda dentro de tmux** (`$TMUX` setado — o
usuário abriu `tmux new -s main` e rodou `claude` lá): cada delegate abre como
**split pane na janela atual** (layout `main-vertical`): o pane do orquestrador
fica na metade esquerda em altura cheia; delegates empilham em fatias iguais na
metade direita, re-balanceando quando entram/saem. Título do pane =
`<modelo>-<pid>`. Usuário navega com `C-b` + setas ou clique (mouse on) e pode
digitar no pane do delegate.

**Fora de tmux:** não há pane pra dividir — delegates vão pra janelas na
sessão detached `claudim` (`tmux attach -t claudim` em outro terminal).

stdout capturado igual pro orquestrador (a janela é pros olhos/mãos do
usuário); stderr separado, `--output-format json` parseável. Janela fecha
quando o delegate termina. `-p` only. Sem tmux instalado -> inline com nota.

**Padrão: observação ON em todo delegate.** Passe `--tmux` em **cada** launch
de `claudim -p` — não espere o usuário pedir, não exija sinal no prompt. Só
pule se ele disser explicitamente que quer quieto/headless ("roda silencioso",
"sem observação", "modo autônomo"). Útil também pra debugar delegate
lento/travado.

### Limite de paralelismo (fallback — importante)

Cada delegate é um processo Claude Code (Node) completo — ~300-500MB RAM +
CPU de inicialização. **Máximo 2-3 delegates em paralelo**; 4+ trava a máquina
do usuário. Enfileire o resto: termine um lote de 2-3, dispare o próximo.
Serialize sempre que houver dependência entre passos (output de um alimenta o
prompt do outro).

**Importante:** chamadas do Bash tool **não carregam env entre si** — um
`export CLAUDIM_TMUX=1` mid-sessão não propaga pro próximo `claudim -p`. Use
`--tmux` por chamada. (Ou o usuário seta `export CLAUDIM_TMUX=1` no
`~/.zshenv` pra default geral sem precisar da flag — aí nem a skill precisa
lembrar.)

### gcloud / GCP / rede no delegate (fallback)

Delegate `-p` é non-interactive -> Bash tool não consegue aprovar prompts de
permissão do Claude Code. No default `auto`, comandos que precisam approval
(`gcloud`, rede, escrita de arquivo) são NEGADOS — o delegate responde
"preciso de aprovação" em vez de rodar. Resulta no orquestrador tendo que
rodar GCP read-only ele mesmo (o que você quer evitar).

Pra delegate rodar gcloud/GCP/rede/escrita: passe `--unrestricted` (ou set
`CLAUDIM_BYPASS=1`). claudim injeta `--dangerously-skip-permissions` no launch
do claude — sem checks, comando roda.

```sh
claudim -p --unrestricted --model deepseek-v4-flash \
  "rode 'gcloud compute instances list' e reporte o resultado tabular"
```

**Quando usar `--unrestricted`:** task precisa de gcloud, GCP API, rede
(curl/http), ou escrita de arquivo fora do sandbox. **Quando NÃO usar:**
read-only, análise, explicar arquivo, escrever teste que só usa Read/Edit
(stay sandboxed). Default é off — opt-in só quando precisa.

**Risco:** `--unrestricted` = bypass total de permissões num modelo chinês
barato na máquina do usuário. Mantenha tasks scoped e confiáveis. NÃO use pra
tasks que consomem conteúdo não-confiável (ex: "analise essa issue de
terceiro") sem revisão — prompt-injection no conteúdo da task poderia guiar
comandos destrutivos. Pra GCP read-only puro, prefira dar ao delegate um
comando `gcloud ...` explícito e scope restrito no prompt.

### After a delegate returns (fallback)

1. **Check it didn't come back empty.** Empty stdout = the model failed (or you
   accidentally used a thinking alias). If empty, retry with a different alias or
   do the step yourself.
2. **Verify the work** if it touched files — read the diff. You are accountable
   for correctness, not the delegate.
3. **Integrate** into the plan / next step. You stitch outputs together.

### Failure modes to expect (fallback)

- **Empty stdout**: wrong alias (thinking variant) or the model gave up. Retry
  with another alias; fall back to doing it yourself.
- **"Preciso de aprovação" / can't run gcloud or network**: the delegate hit
  the permission wall (non-interactive `-p` can't approve Bash commands). If the
  task genuinely needs gcloud/GCP/network/file-writes, re-run with
  `--unrestricted`. If it doesn't, the task is mis-scoped — do the shell step
  yourself and pass the output to a read-only delegate instead.
- **401 / hang**: a parent `ANTHROPIC_API_KEY` leaked in. `claudim` unsets it,
  but if you call `claude` directly, don't — go through `claudim`.
- **Slow first call**: `claudim` waits for the gateway node to wake on the
  tailnet (up to 30s). Subsequent calls are fast.

### Example orchestration turn (fallback)

> Plan: (1) find every caller of `foo()`, (2) rename to `bar()`, (3) run tests.
>
> Step 1 -> delegate `kimi-k2.7-code`: "list every file:line that calls `foo()`
> in this repo; output as a plain list." Capture the list.
> Step 2 -> delegate `deepseek-v4-flash`: "rename `foo` to `bar` at these exact
> locations: <paste list>. Don't touch comments." Verify the diff yourself.
> Step 3 -> you run `uv run pytest` and interpret (judgment step, not delegated).

---

## KILL SWITCH — when to stand down

The default mode is non-coercive — delegate agents are an additive model
choice, not a mandate. Use them when they fit the task; native agents work
alongside.
Native orchestration via `delegate-*` agents IS the claudim path — use them
for fan-out and multi-agent tasks.

Stand down and defer to plain Claude Code Workflow/Agent tools ONLY when:

- The user **explicitly** wants **anthropic-tier** subagents (haiku/sonnet/opus)
  and does NOT want cheap non-American models.
- The user wants a **fully custom workflow** the delegate pool cannot serve
  (e.g., specific agent tool permissions, custom system prompts per agent tier,
  or a deterministic Workflow-tool script that the delegate agents don't fit).

When in doubt and no gateway/cheap-model language is present, still try the
native `delegate-*` agents first — mention that plain Claude Code subagents are
available if they want the anthropic-tier pool instead.

---

## Model discovery and exclusions

Models are **auto-discovered** from the gateway at session launch (via
`GET /v1/models/delegates`). Run `claudim models --all` to list every
available model live.

Admins can exclude models from the delegate pool via
`MODEL_DELEGATE_EXCLUSIONS` in the gateway Admin UI (supports glob patterns,
e.g. `open_router/qwen/*`). Exclusions affect **only** delegation — the human
`/model` picker still sees every model. Enforcement is **hard**: the gateway
rejects any subagent request on an excluded model at request time (only the
Claude Code main loop is exempt), so do not try to spawn agents on excluded
models via explicit `model` params — they will fail. Exclusions are
hot-reloaded by the gateway; relaunch the claudim session to pick up changes.