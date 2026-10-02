# jevmem

System-One agent memory for Claude Code and our own agents, based on
*Jev-Mem* (arXiv 2609.23986). [Jev](https://typesafe.ai) makes every memory-control
decision (typing, relations, routing, scoring, stopping) as batched typed
probabilities; the host agent (Claude) is System Two and does all writing/synthesis.
jevmem itself never calls a generative LLM.

> Status: working prototype. Core library, MCP server/CLI, skill, hooks, Python helpers, consolidation, eval harness
> and a pluggable vector index are in. New to the ideas? Read [docs/concepts.md](docs/concepts.md).

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
| `JEVMEM_INDEX` | vector index: `auto`, `matrix`, `sqlite-vec`, `qdrant[:path-or-url]` | `auto` |
| `JEVMEM_SCOPE` | default scope for write/recall | `global` (hooks: `project:<folder name>`) |
| `JEVMEM_AUTOCAPTURE` | auto-store strongly stated preferences/decisions/conventions from prompts; set `0` to turn off | `1` (on) |

Tools: `memory_write`, `memory_recall`, `memory_list`, `memory_forget`, `memory_stats`,
`memory_flush_pending`, `memory_consolidate`, `memory_pending_synthesis`, `memory_resolve`, `memory_dismiss`. CLI: `uv run jevmem {write,recall,list,stats,flush,consolidate,pending,reindex,import-claude-memory,eval,eval-injection}`.

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

## Evaluation (short version)
`uv run jevmem eval --data evals/harbor.json` and `uv run jevmem eval-injection` run live against Jev; details,
tables, datasets (including a LoCoMo slice) and caveats are in [docs/evaluation.md](docs/evaluation.md).
On the held-out set with real embeddings (`bge-small`): recall 0.99 vs 0.93 for hybrid top-5 while injecting about a
quarter of the context; on a LoCoMo slice 0.83 vs 0.67 (vector) with multi-hop 0.70 vs 0.20; unanswerable questions
are abstained on 16/16. One Jev relevance filter over a vector top-20 already gets most of the gain for ~$0.0001.
These are small datasets, so treat them as indications, not benchmarks.

## Choosing embeddings and a vector index
- Embeddings: default `hash` is dependency-free but weak; `uv sync --extra embed` + `JEVMEM_EMBEDDER=fastembed`
  uses a local model (call `Store.reembed()` after switching).
- Index: `JEVMEM_INDEX=auto|matrix|sqlite-vec|qdrant[:path-or-url]` (`uv sync --extra index` for the optional ones).
  SQLite stays the source of truth, the index is a rebuildable copy (`jevmem reindex`). `auto` uses an in-RAM exact
  matrix and moves to sqlite-vec past 100k notes. See [docs/concepts.md](docs/concepts.md) for what these are.

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
  vectorindex.py pluggable vector search (matrix, sqlite-vec, qdrant)
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

## Ideas to try next
- **Qdrant server (true HNSW).** The `qdrant:http://host:6333` adapter exists but was only tested in local mode,
  which is exact and slow. Run a Qdrant server (e.g. via Docker), load 1M+ notes and measure approximate-search
  recall and latency against `matrix`/`sqlite-vec`; useful if several machines should share one memory.
- A "lite" recall mode: vector top-20 plus a single Jev relevance filter (about a quarter of the cost, most of the gain).
- Held-out evaluation on more LoCoMo conversations and on real project notes; retune the sufficiency check there.
- A `VectorIndex` for LanceDB or pgvector if a team already runs one.

## Docs
- [docs/concepts.md](docs/concepts.md): Jev, memory decisions, storage, embeddings, vector indexes and databases.
- [docs/evaluation.md](docs/evaluation.md): methodology, datasets, results, caveats.
