---
name: claudim-delegate
description: REQUIRED when the user mentions claudim or asks to delegate/orchestrate with cheap models — load this skill. Do NOT improvise with the Agent tool (haiku/sonnet/opus subagents) when the user says claudim, "modelos baratos", "delegar", or "orquestrar"; use `claudim -p --model <alias>` via Bash instead, with the curated NON-AMERICAN models (deepseek / kimi / glm / minimax / mistral / codestral — US closed labs never called). If unsure, invoke via `/claudim-delegate`. Auto-triggers (any language) on: "claudim", "do claudim", "pelo claudim", "agentes claudim", "força bruta do claudim", "delegate to claudim", "use claudim", "delega pros modelos baratos do claudim", "delega pro claudim", "delegar com claudim", "delegue a execução para agentes claudim", "delegar para o claudim", "delegue pros modelos chineses", "use modelos chineses baratos", "use modelos não americanos", "modelos baratos não americanos", "orchestrate with cheap models", "claudim-delegate", or whenever a plan step is mechanical and the user implies delegation. KILL SWITCH — do NOT activate (and if already loaded, stand down and defer to Claude Code's NATIVE Workflow/Agent tools instead) when the user says: "workflow", "workflows", "fan out subagents", "fan-out subagents", "fan-out", "multi-agent", "use a workflow", "run a workflow", "orchestrate with subagents", or otherwise explicitly invokes Claude Code's built-in multi-agent orchestration (Workflow tool, Agent tool with model: haiku/sonnet/opus). Those are native features — claudim delegates are the alternative, not a layer on top of them. Only activate when the user wants CHEAP NON-AMERICAN gateway models via `claudim -p`.
---

# claudim-delegate

## KILL SWITCH — read this first

**If the user said any of these, STOP. Do not use claudim. Close this skill and
use Claude Code's NATIVE multi-agent tools instead:**

- "workflow" / "workflows" / "use a workflow" / "run a workflow"
- "fan out subagents" / "fan-out subagents" / "fan-out"
- "multi-agent" / "orchestrate with subagents"
- any explicit reference to the **Workflow** tool or the **Agent** tool with
  `model: haiku/sonnet/opus`

These are Claude Code's built-in features (the `Workflow` tool for deterministic
multi-agent scripts, the `Agent` tool for spawning subagents). They are NOT what
this skill is for. claudim delegates are the **alternative** to those — cheap
non-American gateway models over Tailscale, not a layer on top of the native
orchestration.

**Decision rule:** the user wants claudim only when they want **cheap
non-American gateway models** (`deepseek` / `kimi` / `glm` / `minimax` /
`mistral` / `codestral`) via `claudim -p`. If they want native Claude Code
subagents/workflows — which can use `model: haiku` routed to a cheap gateway
model, or spawn many in-process — defer to the native tools and do not invoke
`claudim`.

When in doubt and no gateway/cheap-model language is present, default to the
native tools and mention that `claudim` is available if they want the
non-American-model pool instead.

---

You are the **orchestrator** (Opus or Fable). You plan, decide, and integrate.
**Delegate** well-scoped, mechanical steps to a cheap model through `claudim -p`,
capture its stdout, and use it. This keeps quality (you) high and cost (delegates)
low.

## The command

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
delegate (those route via gateway config and may enable thinking → empty).

## Pick the model per task (non-American — auto-selection)

All aliases below are non-American: Chinese (DeepSeek, Moonshot/Kimi,
Zhipu/GLM, MiniMax) + Mistral (France). **US closed labs — openai, anthropic,
google, x-ai, amazon, nvidia, ibm-granite, liquid, rekaai, relace — are never
called** (cost-driven: they charge premium per-token; the rest are cheap, and
open-weight Llama + fine-tunes don't fund a US lab per call). Decide by what
the task needs — pick the **cheapest that can do the job**, escalate only when
the task demands:

- **Smartest / hardest reasoning** — algorithm design, multi-step logic,
  architecture tradeoffs → `deepseek-v4-pro`
- **Coding** — implement a function, refactor, fix a bug, write a test →
  `kimi-k2.7-code` (Chinese) or `codestral` (France, non-Chinese alt)
- **Fastest / cheapest** — triage, lookups, mechanical edits, simple answers,
  bulk work → `deepseek-v4-flash` (Chinese) or `ministral-8b` (France, non-Chinese alt)
- **Long-context general** — read/analyze large files, writing, summaries →
  `glm-5.2` (or `minimax-m3` as alternative) (Chinese) or `mistral-small` (France)

Default to `deepseek-v4-flash` for anything simple; reach for `kimi-k2.7-code`
or `codestral` on code; reach for `deepseek-v4-pro` only when real reasoning is
required. Prefer a non-Chinese model (Mistral) when the task or your preference
calls for it — both pools are cheap and non-American.

### Need a model outside the curated 8?

Run `claudim models --all` to list **every** non-American no-thinking model
the gateway currently offers (live, US closed labs filtered out). Pass any
listed id verbatim to `--model`:

```sh
claudim models --all          # grouped by vendor, all non-American, all no-thinking
claudim -p --model <id-from-list> "<task>"
```

Use this when a task wants a specialized non-American model the curated 8 don't
cover (e.g. a different coding model, a specific flash variant, a reasoning
model you want to try). Don't hand-pick American vendors — `--all` already
excludes US closed labs.

Run `claudim models` any time to reprint the curated cheat-sheet.

## When to delegate vs. do it yourself

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

## How to run a delegate

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

## Parallel delegates

For independent plan steps, launch several `claudim -p` calls in parallel
(Bash `run_in_background`, or `&` + `wait`). Collect outputs, then integrate.
Serialize steps that depend on each other — pass the previous delegate's output
into the next one's prompt.

## Observar delegates ao vivo (tmux)

**Preflight tmux — obrigatório antes do PRIMEIRO `claudim -p --tmux` da sessão.**

`claudim --tmux` assume que existe um tmux server rodando. Se o server caiu
(restart, sleep+resume, nunca subiu nesse shell), o delegate **pendura
silencioso esperando uma janela que nunca abre** — pode ficar 30-60 min
invisível até alguém matar. Sintoma: `exit 144` no Bash tool e tempo gigante
sem output. Checar:

```sh
tmux ls               # falha = server morto; sucesso = server vivo
[ -n "$TMUX" ] && echo "in-tmux" || echo "not-in-tmux"
```

Decidir antes de lançar:

- **Server morto (`tmux ls` falha):** suba um server primeiro
  (`tmux new -d -s scratch`) OU abra um shell novo dentro de tmux
  (`tmux new -s main`) e rode `claude --continue` lá. Sem server, **não lance
  `--tmux`** — o delegate vai pendurar. Sem tmux instalado, o launcher já cai
  em inline com nota, sem pane.
- **Server vivo, `$TMUX` setado:** orquestrador dentro de tmux — cada delegate
  abre como split pane na janela atual. **Setup ideal.**
- **Server vivo, `$TMUX` vazio:** delegates vão pra sessão detached `claudim` —
  o usuário precisa `tmux attach -t claudim` em OUTRO terminal pra assistir.
  Funciona, mas é pior. **Recomende migrar antes de continuar delegando:**
  `tmux new -s main` → `claude --continue` lá dentro → próximos delegates
  viram split panes do lado dele. (Mover a sessão atual pra dentro de tmux não
  rola sem reiniciar o Claude Code — o processo nasceu preso a este terminal.)

Só depois do preflight vale lançar o delegate. Não assuma que o setup está bom
só porque `--tmux` não deu erro na invocação — o erro é silencioso.

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
quando o delegate termina. `-p` only. Sem tmux instalado → inline com nota.

**Padrão: observação ON em todo delegate.** Passe `--tmux` em **cada** launch
de `claudim -p` — não espere o usuário pedir, não exija sinal no prompt. Só
pule se ele disser explicitamente que quer quieto/headless ("roda silencioso",
"sem observação", "modo autônomo"). Útil também pra debugar delegate
lento/travado.

## Limite de paralelismo (importante)

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

## gcloud / GCP / rede no delegate

Delegate `-p` é non-interactive → Bash tool não consegue aprovar prompts de
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

## After a delegate returns

1. **Check it didn't come back empty.** Empty stdout = the model failed (or you
   accidentally used a thinking alias). If empty, retry with a different alias or
   do the step yourself.
2. **Verify the work** if it touched files — read the diff. You are accountable
   for correctness, not the delegate.
3. **Integrate** into the plan / next step. You stitch outputs together.

## Failure modes to expect

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

## Example orchestration turn

> Plan: (1) find every caller of `foo()`, (2) rename to `bar()`, (3) run tests.
>
> Step 1 → delegate `kimi-k2.7-code`: "list every file:line that calls `foo()`
> in this repo; output as a plain list." Capture the list.
> Step 2 → delegate `deepseek-v4-flash`: "rename `foo` to `bar` at these exact
> locations: <paste list>. Don't touch comments." Verify the diff yourself.
> Step 3 → you run `uv run pytest` and interpret (judgment step, not delegated).
