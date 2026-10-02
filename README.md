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

Datasets: `evals/orbit.json` (25 notes / 30 questions, used to tune thresholds, so **in-sample**) and
`evals/harbor.json` (40 notes / 41 questions, a different domain, run once with frozen defaults before any tuning,
so **held-out**). Both include superseded facts, multi-hop, temporal and 6 unanswerable questions.
`jevmem-flat` is an ablation with graph expansion off (`max_depth=0`); `hybrid@3` is hybrid search cut to 3 items.

Held-out (`harbor`):

| method | recall | exact | precision | items | chars | latency | Jev calls | Jev input tokens |
|---|---|---|---|---|---|---|---|---|
| vector top-5 | 0.69 | 0.66 | 0.14 | 5.0 | 480 | ~0 | 0 | 0 |
| hybrid top-5 | 0.79 | 0.74 | 0.17 | 5.0 | 470 | ~0 | 0 | 0 |
| hybrid top-3 | 0.79 | 0.74 | 0.28 | 3.0 | 282 | ~0 | 0 | 0 |
| jevmem-flat | 0.87 | 0.86 | 0.68 | 1.4 | 150 | 1.0 s | 4.0 | 3.3k |
| jevmem | 0.96 | 0.94 | 0.71 | 1.7 | 179 | 1.2 s | 4.7 | 4.7k |

In-sample (`orbit`): vector 0.83, hybrid 0.96, jevmem 1.00 (flat ablation also 1.00) with ~1.2 items / 122 chars.

Reading it: on unseen data the lift over hybrid holds (0.79 -> 0.96 recall) and the injected context is about 40%
the size of hybrid top-5 (and precision is 4x). Graph expansion earns its keep on the held-out set (flat 0.87 ->
0.96), which orbit could not show. Jev cost is ~4.7k input tokens per recall, about $0.0002. On unanswerable
questions the baselines return 5 items; jevmem returns under 1 and reports `sufficient=false` every time
(6/6 on both sets). With defaults the held-out sufficiency check had 1 false positive and 1 false negative of 41.

`uv run jevmem eval-injection` checks the write-path screen with `evals/injection.json` (15 benign notes, including
imperative team conventions such as "never use pip", and 10 injection attempts). The first run **blocked 9/15 benign
notes** because the screen question treated any imperative as an injection; the question was reworded to target text
aimed at the agent itself, after which it was 0/15 false positives and 0/10 false negatives. That rewording was
tuned on this same small set, so treat it as a regression test, not a measured rate.

Caveats: small datasets written by us (the held-out notes were written after the system, by the same author);
single run each (Jev is not perfectly deterministic); the vector baseline uses the dependency-free hash embedder, not
a real embedding model, so it is a weak baseline; precision is low for baselines by construction (fixed k). Next:
a real embedding baseline and a LoCoMo subset (both need model/data downloads).

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
  evalharness.py `jevmem eval` / `eval-injection`: baselines, ablation, sweep, screen accuracy
  decide.py     Judge: route/filter/stop/check/screen for agent apps
  service.py    shared wiring (db path, env)
  mcp_server.py MCP tools
  cli.py        jevmem command
  hooks.py      SessionStart / UserPromptSubmit logic
```
