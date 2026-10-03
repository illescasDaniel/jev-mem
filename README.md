# jevmem

System-One agent memory for Claude Code and our own agents, based on
*Jev-Mem* (arXiv 2609.23986). [Jev](https://typesafe.ai) makes every memory-control
decision (typing, relations, routing, scoring, stopping) as batched typed
probabilities; the host agent (Claude) is System Two and does all writing/synthesis.
jevmem itself never calls a generative LLM.

> Status: beta (v0.1.0). Core library, MCP server/CLI, skill, hooks, Python helpers, consolidation, eval harness
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
  -- uv run --project $PWD jevmem-mcp
```
`--project` (not `--directory`) keeps the agent's working directory, which jevmem uses to record each note's git branch and to share one scope across a repository's worktrees; set `JEVMEM_REPO` to override it.
For every project, use `--scope user` and give a per-project `JEVMEM_SCOPE`, or let the agent pass `scope`.
Claude Code asks you to approve project-scoped servers the first time you open the folder.

| Env var | Purpose | Default |
|---|---|---|
| `TYPESAFE_API_KEY` | Jev key (via `JEVMEM_ENV_FILE`, `~/.jevmem/.env` or `.env`) | required |
| `JEVMEM_DB` | SQLite file | `~/.jevmem/memory.db` |
| `JEVMEM_INDEX` | vector index: `auto`, `matrix`, `sqlite-vec`, `qdrant[:path-or-url]`, `lancedb[:path]`, `pgvector:<dsn>` (tuning env vars in [docs/evaluation.md](docs/evaluation.md)) | `auto` |
| `JEVMEM_RECALL_MODE` | `auto` (lite first; escalate to full when it finds nothing or the question looks multi-hop/temporal), `full` (route, graph expansion, stop rule) or `lite` (vector top-20 + one Jev relevance filter) | `auto` (prompt hook: `lite` + same-subject filter) |
| `JEVMEM_SCOPE` | default scope for write/recall | `global` (hooks: `project:<repository name>`, shared by all worktrees) |
| `JEVMEM_AUTOCAPTURE` | auto-store short standing preferences/decisions/conventions from prompts; set `0` to turn off | `1` (on) |
| `JEVMEM_REPO` | directory to read git context (branch, worktree-shared project name) from, if not the agent's working directory | working directory |
| `JEVMEM_HOOK_LOG` | append records (time, hashed prompt, needs-memory score, injected ids) to this file for threshold tuning | off |

Tools: `memory_write` (`pinned=True` for notes that must open every session), `memory_recall`, `memory_list`,
`memory_forget`, `memory_pin`, `memory_stats`,
`memory_flush_pending`, `memory_consolidate`, `memory_pending_synthesis`, `memory_resolve`, `memory_dismiss`. CLI: `uv run jevmem {write,recall [--mode auto|full|lite],list,forget [--branch NAME],pin [--off],stats,flush,consolidate [--all],pending,resolve,dismiss,reindex,reembed,import-markdown,import-claude-memory,eval,eval-injection}`. The CLI uses `JEVMEM_SCOPE` as its default scope, like the MCP server and hooks.

Write memories as one literal fact with explicit entities and **absolute dates**
("on 2024-05-15", not "yesterday"): Jev reads literally and does not do date arithmetic.

### 5. Skill and hooks
This repo ships both for itself:
- `.claude/skills/jev-memory/SKILL.md`: when/how to write and recall, phrasing rules, safety.
  Copy the folder to `~/.claude/skills/` to use it in every project.
- `.claude/settings.json` hooks (fail open: any error injects nothing):
  - `SessionStart` (no Jev call): injects pinned notes first (`jevmem pin <id>` / `memory_pin`), then the strongest
    stored conventions/gotchas/decisions/preferences, skipping notes that `CLAUDE.md`/`AGENTS.md` already say
    (embedding similarity, cached per file version) and near-copies of notes already picked.
  - `UserPromptSubmit`: one Jev call decides whether the prompt needs memory; only then does lite recall run, and a
    second call keeps only notes about the prompt's specific subject (not ones sharing words like "tests" or "MCP").
    Superseded notes are never injected. A strongly stated preference/decision/convention in the prompt is
    auto-stored (disable with `JEVMEM_AUTOCAPTURE=0`).
- Seed from existing Claude Code memory files: `uv run jevmem import-claude-memory`, or from rule files such as
  `AGENTS.md`/`CLAUDE.md` with `uv run jevmem import-markdown AGENTS.md --scope project:<name>` (one note per bullet or
  paragraph; for long decision logs it is better to have your agent write atomic, dated notes).
- Hook problems (wrong embedder, bad key) are reported to you as a `systemMessage`, not swallowed; transient Jev
  outages still just skip injection.

### 6. What goes where
jevmem is one of three places an agent keeps knowledge. Each holds a different kind, so keep them from overlapping:

| Kind | Where | Why there |
|---|---|---|
| **Current state**: focus, work in progress, blockers, next steps | git-tracked markdown files (e.g. `memory/activeContext.md`, `memory/progress.md`) | small enough to read whole every session; follows the branch; changes show up in PR diffs |
| **Dated facts that stay true**: decisions and their reasons, bug causes, gotchas, preferences | jevmem | too many to read whole; recalled by relevance |
| **Rules**: conventions the agent must always follow | `AGENTS.md` / `CLAUDE.md` | always in context, reviewed like code |

Never store current state in jevmem: "next step is X" goes stale the moment the markdown file changes, and nothing
marks the note outdated. `memory_write` rejects notes that read as work status. Write the fact that will still be
true later ("on 2026-10-02 we chose X because Y") instead.
Do not copy rules into jevmem either: they are already in context (SessionStart skips most restatements, not all).

## Safety and housekeeping
- Writes are screened before storage: credentials (private keys, API tokens, `password is ...`, connection strings
  with passwords) are rejected, near-duplicates (cosine >= 0.97 in the same scope) are rejected, and the Jev injection
  screen blocks instructions aimed at the agent. Auto-captured prompts must also be one short paragraph of at most two
  sentences with no question, and Jev must judge them a standing rule or preference; they are skipped when they
  contain relative dates ("yesterday"), remote-execution commands (`curl | sh`) or secrets.
- A database records the embedder it was built with and keeps using it. Asking for a different one with
  `JEVMEM_EMBEDDER` fails loudly; switch deliberately with `jevmem reembed`.
- Treat recalled notes as data. They are injected under a header that says so, but a note is only as trustworthy as
  whoever could write it: do not share a writable memory with people you would not let edit your `AGENTS.md`.

## Known limitations
- Consolidation compares each new note with its 3 nearest neighbours, plus the pairs that recall returned together
  (queued after each recall and judged on the next pass). A stale note that never surfaces with its replacement is
  still not compared. Two conflicting notes with the same timestamp and no dates are ordered by which one reports the
  change (32 of 36 such pairs in the tie eval); the rest stay `contradicts` and become a `conflict` proposal for the
  agent (`memory_pending_synthesis`), with `jevmem forget <id>` as the manual fix.
- `auto` recall (the default) reruns a question in full mode only when lite's single Jev call judges it multi-hop or
  time-related. A multi-hop question misjudged as simple keeps lite's answer: lite cannot notice a missing link that
  only graph expansion would find. `memory_recall` then returns `sufficient: false` with a hint to rerun with
  `mode="full"`; escalating on every insufficient answer costs about 25% more Jev calls for +0.002 recall, so it stays
  off. Lite's "memory does not know" is right on 94% of unanswerable questions (full: 98%).
- Notes are tagged with the git branch and commit they were written on. Notes from a branch that is neither current,
  default nor merged rank x0.8 lower and the prompt hook skips them, but a squash-merged branch looks the same as an
  abandoned one, so such notes are only demoted, never hidden. Use `jevmem forget --branch <name>` for dead branches.
  Notes written before this existed, or outside a git repository, are never penalized.
- SessionStart ranks pinned notes first, then by type confidence boosted by similarity to the repository's recent
  work (branch, last commits, touched paths) and by how often the prompt hook found a note useful. The weight was
  chosen on 31 past SpaceMaker sessions (hit@10 0.39 -> 0.52, no held-out split): an indication, not a guarantee.
- The prompt hook's relevance filter was tuned on 44 prompts against one store (about 18 of 22 off-topic prompts
  inject nothing). Expect the occasional off-topic note and tell your agent to ignore it. Set `JEVMEM_HOOK_LOG=<file>`
  to collect hashed prompt/injection records for tuning on your own data.
- Status notes are rejected at write time by a classifier (0 misses and 0 false rejections on the 36-note tune set
  and the 24-note held-out set), but a status can be phrased as a dated event ("On 2026-10-03 the migration was
  started") and pass. Keep current state in markdown ([What goes where](#6-what-goes-where)).
- SessionStart skips notes that restate `CLAUDE.md`/`AGENTS.md`: by embedding similarity >= 0.82, and for the
  borderline band 0.70-0.82 by one cached Jev coverage call. On SpaceMaker that skips 22 of 23 restating notes and
  none of the other 59; a paraphrase can still slip through. Tuned on one project.
  A live SpaceMaker session still injected 3 AGENTS.md paraphrases: their coverage score was 0.30-0.43, below the 0.50
  threshold, while a note that is not a restatement scored 0.49, so no threshold separates them there and it was not
  retuned on a few points. If a restating note keeps appearing, `jevmem forget <id>` it.
- Auto-capture stores only a short, single-paragraph, non-question statement that Jev judges to be a standing rule or
  preference. It is deliberately conservative: 0 false captures on 123 real prompts, but it also stores only 11 of 12
  synthetic standing preferences. Write the rest with `memory_write`.
- A multi-hop `auto` recall still stops at the call/time limit (`max_jev_calls` 19, which now includes lite's call and
  can no longer be overshot) and then reports `stop_reason` `limit:calls/time`.

- The call cap used to be overshot silently (the old code made up to 19 calls while documenting 16). Enforcing it at 16
  cost 0.5-0.9 points of full/auto recall on multi-hop and temporal questions; the default is now 19, which restores
  full recall (0.906 vs 0.909 old on the affected sets) at the old average cost (5.6 calls). Lower `max_jev_calls` to
  trade recall for latency. Details in [docs/evaluation.md](docs/evaluation.md).

## Release checklist (before publishing 0.1.0)
Must try:
- [x] Old-vs-new retrieval comparison repeated: the gap was the call cap, not a regression (see Known limitations).
- [x] Live trial in SpaceMaker (the long pasted chat message was not captured): SessionStart (3 restatements leaked,
      see above), recall auto/full right, tool errors readable, worktree scope/branch tag/unmerged penalty (0.97 -> 0.78)
      confirmed. It found two bugs, fixed: the MCP server's vector index never saw notes written by hooks or the CLI
      (now detects other-process writes), and `mode="bogus"` was silently accepted (now an error).
- [x] `consolidate --all` on a copy of the real store: 82 notes, same 4 correct supersessions as the live DB, 0 new proposals.
- [ ] Fresh-install test: `uv tool install` / `uvx` from a clean machine or container, `claude mcp add` per README, hooks.
- [ ] Run the hooks on a second project (not SpaceMaker) and collect `JEVMEM_HOOK_LOG` data for a few days.
- [ ] CI green on Linux, macOS and Windows (git subprocess calls, paths, sqlite-vec fallback).
- [ ] Tag `v0.1.0`, build and upload to PyPI, verify `pip install jevmem` and the entry points.

Next steps after release:
- Grow the prompt-hook eval beyond 44 prompts and a second store, using the opt-in hook log.
- Held-out split for the SessionStart ranking weights; evaluate the usage boost once real usage accumulates.
- Widen the status and capture sets with prompts from other projects and languages.

## Consolidation
Every 20 successful writes (inline, ~3-5 s, via `Service.write` / `memory_write`) Jev compares recent notes with
their 3 nearest older neighbours. The questions are order-neutral (the note written last is not always the newer
fact: imports and backfilled notes arrive late): same question with a different answer? does one outdate the
other? does either report that what the other says has changed (renamed, replaced, moved)? does either state every
fact of the other? repeated episodes of one pattern? worth linking?
Nothing is deleted or generated. Results only annotate the graph:
- a conflict between notes with different timestamps flags the older one `superseded_by` the newer (the order is
  decided in code from timestamps, falling back to write time, and for equal timestamps to the latest date each note
  mentions); a conflict with nothing to order it by flags both `contradicts`;
- a note that says nothing the other does not is flagged `duplicate_of` / `subsumed_by` it, with no agent work;
- superseded, duplicate and subsumed notes stay searchable but rank lower and are skipped by the SessionStart hook;
- only repeated episodes become proposals: the host agent writes the general pattern (`memory_pending_synthesis` ->
  `memory_resolve`). There are no merge proposals any more: on real notes they mostly paired distinct facts.

After upgrading, run `jevmem consolidate --all` (or `memory_consolidate(all_notes=True)`) once to re-judge notes
consolidated with the old questions (one Jev call per note). Measured on labelled pairs in both write orders
(`evals/consolidation_eval.py`, [docs/evaluation.md](docs/evaluation.md#consolidation)): 44/44 held-out cases right
versus 22/44 before, 32/32 on a second held-out set of reworded renames, with no false flags.

## Evaluation (short version)
`uv run jevmem eval --data evals/harbor.json` and `uv run jevmem eval-injection` run live against Jev; details,
tables, datasets (including a LoCoMo slice) and caveats are in [docs/evaluation.md](docs/evaluation.md).
On the held-out set with real embeddings (`bge-small`): recall 0.99 vs 0.93 for hybrid top-5 while injecting about a
quarter of the context; on a LoCoMo slice 0.83 vs 0.67 (vector) with multi-hop 0.70 vs 0.20; unanswerable questions
are abstained on 16/16. Across 9 more LoCoMo conversations (272 questions, never used for tuning) full recall scores 0.89 vs 0.70 for vector
top-5 and `lite` mode (one Jev relevance filter over a vector top-20, `JEVMEM_RECALL_MODE=lite`) scores 0.84 for about
a fifth of the Jev tokens; on 57 real commit-history notes full recall scores 1.00 vs 0.90. These are small datasets,
so treat them as indications, not benchmarks.

**Which recall mode?** `auto` (the default) runs lite and escalates to full when lite finds nothing or the same Jev
call says the question is multi-hop or time-related (`Config.escalate_multi_hop` 0.6 / `escalate_temporal` 0.8).
Pooled over 14 sets (466 questions) it matches full recall (0.911 vs 0.910) for about 40% fewer Jev tokens (6.3k vs
10.4k per question, 44% of questions escalate) and 0.8-1.1 s instead of 1.3-1.5 s. Auto keeps lite's answer when lite
says insufficient (escalating as well added 0.002 recall for 24% more tokens; `Config.escalate_insufficient` turns it
on). `lite`: 1 Jev call, ~0.25 s, no multi-hop; the same call judges sufficiency, so it can say "memory does not know"
(abstains on 94% of unanswerable questions). `full`: ~6 Jev calls, ~1.4 s, always expands the graph, abstains on 98%.
The `UserPromptSubmit` hook uses `lite` plus a same-subject filter (override with `JEVMEM_RECALL_MODE`); the MCP tool,
the CLI and the library default to `auto` (`mode="full"`/`"lite"` force one).

## Choosing embeddings and a vector index
- Embeddings: default `hash` is dependency-free but weak; `uv sync --extra embed` + `JEVMEM_EMBEDDER=fastembed`
  uses a local model (call `Store.reembed()` after switching).
- Index: set `JEVMEM_INDEX` (`uv sync --extra index` for the optional backends). The default `auto` is right for almost
  everyone: an in-RAM exact matrix, switching to sqlite-vec past 100k notes. Change it only for the cases below.
  SQLite stays the source of truth and the index is a rebuildable copy, so switching is safe: set the variable and
  run `jevmem reindex`.

  | You have | Set |
  |---|---|
  | one person or project, up to ~100k notes | nothing (`auto` uses `matrix`) |
  | up to ~1M notes on one machine | nothing (`auto` moves to `sqlite-vec`) |
  | several machines sharing one memory, or more than ~1M notes | `JEVMEM_INDEX=qdrant:http://host:6333` (add `JEVMEM_QDRANT_GRPC=1` for bulk loads) |
  | a Postgres you already run | `JEVMEM_INDEX=pgvector:postgresql://user:pass@host/db` (pgvector 0.8+ recommended) |
  | LanceDB already in your stack | `JEVMEM_INDEX=lancedb:/path/to/dir` |

  Benchmarks at 1M notes and tuning variables are in [docs/evaluation.md](docs/evaluation.md); concepts in
  [docs/concepts.md](docs/concepts.md).

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
  vectorindex.py pluggable vector search (matrix, sqlite-vec, qdrant, lancedb, pgvector)
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
- Re-run the 1M-note index benchmark on real embeddings (it used synthetic clustered vectors) and with several machines
  writing to one Qdrant/Postgres at once.
- Questions written by someone other than the system's author: all our "real notes" sets use questions we wrote.
- A learned (not prompted) escalation and sufficiency signal; both prompted probabilities plateau at ~0.88 accuracy.

## Docs
- [docs/concepts.md](docs/concepts.md): Jev, memory decisions, storage, embeddings, vector indexes and databases.
- [docs/evaluation.md](docs/evaluation.md): methodology, datasets, results, caveats.

## License
MIT, see [LICENSE](LICENSE).
