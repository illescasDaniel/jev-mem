# jevmem

System-One agent memory for Claude Code and our own agents, based on
*Jev-Mem* (arXiv 2609.23986). [Jev](https://typesafe.ai) makes every memory-control
decision (typing, relations, routing, scoring, stopping) as batched typed
probabilities; the host agent (Claude) is System Two and does all writing/synthesis.
jevmem itself never calls a generative LLM.

> Status: **work in progress.** Core library, MCP server/CLI, skill, hooks, Python helpers, consolidation and an eval harness are in. See `/home/daniel/.claude/plans/` for the full plan.

## Installation

### 1. Prerequisites
- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node 22+ (for jev-mcp)
- A TypeSafe API key (Jev is waitlisted): put it in `.env`:
  ```
  TYPESAFE_API_KEY=...
  ```

### 2. Install the Python package
```bash
uv sync
uv run pytest
```

### 3. Add the Jev MCP server (general guardrail tools)
[jkudish/jev-mcp](https://github.com/jkudish/jev-mcp) exposes 12 stateless tools
(`jev_screen`, `jev_verify`, `jev_classify`, `jev_gate`, ...).

With the Claude Code CLI:
```bash
claude mcp add jev -e TYPESAFE_API_KEY="$TYPESAFE_API_KEY" -- npx -y @jkudish/jev-mcp
```
This repo also ships a project-scoped `.mcp.json` that does the same and loads the
key from `.env` at launch (no key stored in config), so opening this folder in Claude
Code is enough.

### 4. Add the jevmem memory server
Project scope (already in this repo's `.mcp.json`):
```bash
claude mcp add --scope project jevmem \
  -e JEVMEM_ENV_FILE=$PWD/.env -e JEVMEM_SCOPE=project:jev-things \
  -- uv run --directory $PWD jevmem-mcp
```
For every project, use `--scope user` and give a per-project `JEVMEM_SCOPE`, or let the agent pass `scope`.
Claude Code asks you to approve project-scoped servers the first time you open the folder.

| Env var | Purpose | Default |
|---|---|---|
| `TYPESAFE_API_KEY` | Jev key (via `JEVMEM_ENV_FILE`, `~/.jevmem/.env` or `.env`) | required |
| `JEVMEM_DB` | SQLite file | `~/.jevmem/memory.db` |
| `JEVMEM_SCOPE` | default scope for write/recall | `global` (hooks: `project:<folder name>`) |
| `JEVMEM_AUTOCAPTURE` | auto-store strongly stated preferences/decisions/conventions from prompts; set `0` to turn off | `1` (on) |

Tools: `memory_write`, `memory_recall`, `memory_list`, `memory_forget`, `memory_stats`,
`memory_flush_pending`, `memory_consolidate`, `memory_pending_synthesis`, `memory_resolve`, `memory_dismiss`. CLI: `uv run jevmem {write,recall,list,stats,flush,consolidate,pending,import-claude-memory}`.

Write memories as one literal fact with explicit entities and **absolute dates**
("on 2024-05-15", not "yesterday"): Jev reads literally and does not do date arithmetic.

### 5. Skill and hooks
This repo ships both for itself:
- `.claude/skills/jev-memory/SKILL.md`: when/how to write and recall, phrasing rules, safety.
  Copy the folder to `~/.claude/skills/` to use it in every project.
- `.claude/settings.json` hooks (fail open: any error injects nothing):
  - `SessionStart`: injects the strongest stored conventions/gotchas/decisions/preferences (no Jev call).
  - `UserPromptSubmit`: one Jev call decides whether the prompt needs memory; only then does recall run
    and inject notes. A strongly stated preference/decision/convention in the prompt is auto-stored
    (disable with `JEVMEM_AUTOCAPTURE=0`).
- Seed from existing Claude Code memory files: `uv run jevmem import-claude-memory`.

## Consolidation
Every 20 successful writes (inline, ~3-5 s, via `Service.write` / `memory_write`) Jev compares recent notes with
their 3 nearest older neighbours: redundant? contradictory? outdated? worth linking? merge or promote?
Nothing is deleted or generated. Results only annotate the graph:
- contradictions are flagged on both notes; an outdated older note is flagged `superseded_by` the newer one
  (which is older is decided in code from timestamps, not by Jev); both are down-ranked/annotated in recall;
- merge/promote proposals (selected option and probability >= 0.85, contradiction < 0.85) wait in a queue; the host
  agent writes the summary text (`memory_pending_synthesis` -> `memory_resolve`). Merged source notes are kept but rank lower.

## Eval
`uv run jevmem eval [--data evals/orbit.json] [-k 5] [--json out.json]` builds the notes in a fresh in-memory store
(live Jev, ~70 calls) and compares plain vector top-k, hybrid (vector + BM25) top-k and jevmem recall, then sweeps
the sufficiency thresholds. Dataset format: `{"scope", "notes":[{key,timestamp,content,entities}],
"questions":[{q, gold:[note keys], kind}]}` (empty `gold` = unanswerable).

Result on `evals/orbit.json` (25 notes, 30 questions: single, multi-hop, superseded, temporal, 6 unanswerable):

| method | recall | exact | precision | items returned | chars | latency | Jev calls |
|---|---|---|---|---|---|---|---|
| vector top-5 | 0.83 | 0.79 | 0.18 | 5.0 | 459 | ~0 | 0 |
| hybrid top-5 | 0.96 | 0.96 | 0.22 | 5.0 | 466 | ~0 | 0 |
| jevmem | 1.00 | 1.00 | 0.83 | 1.3 | 133 | 0.9 s | 4.1 |

On unanswerable questions the baselines return 5 items; jevmem returns 0.2 on average and reports
`sufficient=false` every time. Caveats: small dataset written by us; thresholds were tuned on the same questions
(the optimum is a wide plateau, so it is probably robust, but it is not held-out); the vector baseline uses the
dependency-free hash embedder, not a real embedding model, so treat the vector row as a weak baseline. Next step:
a held-out set from real project notes and/or a LoCoMo subset.

The paper's stop thresholds (sufficient >= 0.95, missing < 0.15) did not fit our wording; defaults are now
sufficient >= 0.5, missing < 0.6, contradiction < 0.6 (`Config`). Superseded notes are marked in the evidence Jev
sees and ignored by the contradiction check.

## Using it from your own Python agents
```python
from jevmem import Judge, Service
from jevmem.decider import JevDecider

svc = Service()                       # memory: svc.writer.write(...), svc.retriever.recall(...)
judge = Judge(svc.decider)            # cheap typed decisions, one batched Jev call each
r = judge.route(task, {"coder": "writes code", "researcher": "looks things up"})
if not r.confident: ...               # includes an "unclear" escape option -> hand to an LLM
keep = judge.filter_relevant(goal, tool_output_lines)   # prune before spending LLM tokens
ok, p = judge.screen(untrusted_text)  # injection screen before text enters context or memory
if judge.should_stop(goal, evidence).stop: ...
scores = judge.check(output, {"cites": "cites a source"})  # guardrail; thresholds stay in your code
```
Runnable demo (route / screen / filter / shared memory / stop): `uv run python examples/multi_agent.py`.
Agents share memory through scopes: a shared `project:<name>` plus private `agent:<name>`.

## Layout
```
src/jevmem/
  decider.py    Decider protocol, JevDecider (typesafe-sdk), FakeDecider
  questions.py  every Noul/Choice question template
  store.py      SQLite nodes/edges + FTS5 + embeddings
  write.py      screen -> type -> candidates -> relations
  retrieve.py   route -> anchors -> budgeted expansion -> stop
  consolidate.py periodic redundancy/contradiction/obsolescence pass + synthesis queue
  evalharness.py `jevmem eval`: baselines vs jevmem + threshold sweep
  decide.py     Judge: route/filter/stop/check/screen for agent apps
  service.py    shared wiring (db path, env)
  mcp_server.py MCP tools
  cli.py        jevmem command
  hooks.py      SessionStart / UserPromptSubmit logic
```
