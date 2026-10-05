# Installation and configuration

Everything you need to run jevmem with Claude Code or your own agents. For the five-minute version see the
[README quickstart](../README.md#quickstart).

## 1. Prerequisites
- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node 22+ (only for the optional [jev-mcp](https://github.com/jkudish/jev-mcp) guardrail tools)
- A decision model, either:
  - a TypeSafe API key for hosted Jev (waitlisted). Put it in `.env`:
    ```
    TYPESAFE_API_KEY=...
    ```
    jevmem looks for the key in the environment, then in the file named by `JEVMEM_ENV_FILE`, `~/.jevmem/.env` and `./.env`; or
  - a local or third-party server that speaks Jev's `/v1/systemone` API, which needs **no Jev key**:
    set `JEVMEM_BASE_URL` (and `JEVMEM_MODEL`), see [Local and third-party models](#local-and-third-party-models).

  With neither, `stats`, `list` and `forget` still work, and recall falls back to plain hybrid search.

## 2. Install the Python package
From PyPI (no clone): `uvx --from jevmem jevmem-mcp` runs the MCP server on demand, `uvx jevmem <command>` runs the CLI,
and `pip install jevmem` / `uv tool install jevmem` install both commands. From source:
```bash
git clone https://github.com/illescasDaniel/jev-mem && cd jev-mem
uv sync
uv run pytest
```
The default install is complete: it includes fastembed (the local embedding model behind the published results) and
sqlite-vec (the on-disk index `JEVMEM_INDEX=auto` switches to past 100k notes). Run `jevmem warmup` once while online:
the model (about 70 MB) is downloaded on first use, and doing it inside a hook could exceed the hook timeout.

Optional vector backends, each its own extra so you only download what you use (`JEVMEM_INDEX=qdrant` / `lancedb` /
`pgvector:<dsn>`, see section 7):

| Install | Adds | Size |
|---|---|---|
| `jevmem[qdrant]` | qdrant-client | ~25 MB |
| `jevmem[lancedb]` | lancedb + pyarrow | ~320 MB |
| `jevmem[pgvector]` | pgvector + psycopg | ~10 MB |
| `jevmem[all]` | all three | ~350 MB |

For example `uvx --from 'jevmem[all]' jevmem-mcp`, `pip install 'jevmem[all]'`, or from a clone `uv sync --extra all`.
Installing a backend does not turn it on: set `JEVMEM_INDEX` for the MCP server and the hooks. If you select a backend
that is not installed, jevmem tells you which extra to add.

Two notes on Python builds:
- **sqlite-vec needs a Python whose `sqlite3` can load extensions.** `uvx` and `uv tool install` use uv's managed
  Python, which can, and so does Homebrew's. If yours cannot, `auto` prints a warning and stays on the in-RAM index
  (fine below ~100k notes).
- **No fastembed on your platform?** Set `JEVMEM_EMBEDDER=hash` for a new database: dependency-free, but it mostly
  matches shared words.

## 3. Add the Jev MCP server (optional, general guardrail tools)
[jkudish/jev-mcp](https://github.com/jkudish/jev-mcp) exposes 12 stateless tools (`jev_screen`, `jev_verify`,
`jev_classify`, `jev_gate`, ...).

```bash
claude mcp add jev -e TYPESAFE_API_KEY="$TYPESAFE_API_KEY" -- npx -y @jkudish/jev-mcp
```
This repo also ships a project-scoped `.mcp.json` that does the same and loads the key from `.env` at launch (no key
stored in config), so opening this folder in Claude Code is enough.

## 4. Add the jevmem memory server
Project scope (already in this repo's `.mcp.json`):
```bash
claude mcp add --scope project jevmem \
  -e JEVMEM_ENV_FILE=$PWD/.env -e JEVMEM_SCOPE=project:jev-mem \
  -- uv run --project $PWD jevmem-mcp
```
`--project` (not `--directory`) keeps the agent's working directory, which jevmem uses to record each note's git
branch and to share one scope across a repository's worktrees; set `JEVMEM_REPO` to override it.
For every project, use `--scope user` and give a per-project `JEVMEM_SCOPE`, or let the agent pass `scope`.
Claude Code asks you to approve project-scoped servers the first time you open the folder.

### Environment variables

| Env var | Purpose | Default |
|---|---|---|
| `TYPESAFE_API_KEY` | hosted Jev key (via `JEVMEM_ENV_FILE`, `~/.jevmem/.env` or `.env`) | required unless `JEVMEM_BASE_URL` is set |
| `JEVMEM_BASE_URL` | address of a local or third-party Jev-schema server; no Jev key needed | hosted Jev |
| `JEVMEM_MODEL` | model name sent with every call | the server's default |
| `JEVMEM_API_KEY` | key for that server, if it wants one (`TYPESAFE_API_KEY` is never sent to it) | none (a placeholder) |
| `JEVMEM_TIMEOUT` | seconds per decision call | `60` |
| `JEVMEM_DB` | SQLite file | `~/.jevmem/memory.db` |
| `JEVMEM_INDEX` | vector index: `auto`, `matrix`, `sqlite-vec`, `qdrant[:path-or-url]`, `lancedb[:path]`, `pgvector:<dsn>` (tuning env vars in [evaluation.md](evaluation.md)) | `auto` |
| `JEVMEM_EMBEDDER` | `fastembed` (local ONNX model) or `hash` (dependency-free, weak) | `fastembed` |
| `JEVMEM_RECALL_MODE` | `auto` (lite first; escalate to full when it finds nothing or the question looks multi-hop/temporal), `full` (route, graph expansion, stop rule) or `lite` (vector top-20 + one Jev relevance filter) | `auto` (prompt hook: `lite` + same-subject filter) |
| `JEVMEM_SCOPE` | default scope for write/recall | `global` (hooks: `project:<repository name>`, shared by all worktrees) |
| `JEVMEM_AUTOCAPTURE` | auto-store short standing preferences/decisions/conventions from prompts; set `0` to turn off | `1` (on) |
| `JEVMEM_REPO` | directory to read git context (branch, worktree-shared project name) from, if not the agent's working directory | working directory |
| `JEVMEM_HOOK_LOG` | append records (time, hashed prompt, needs-memory score, injected ids) to this file for threshold tuning | off |

### Local and third-party models
Any server exposing `POST /v1/systemone` with Jev's request and response shape (`state`, `questions` of type `noul` or
`choice`, `answers`, `usage`) can replace hosted Jev. Example with [ollaya](https://ollaya.dev/) (a local runner for decision models; see its site for installing
it and pulling models):
```bash
ollaya pull jevk5:4b && ollaya serve           # listens on http://localhost:11435
export JEVMEM_BASE_URL=http://localhost:11435 JEVMEM_MODEL=jevk5:4b JEVMEM_TIMEOUT=180
uv run jevmem eval --data evals/harbor.json     # compare against the hosted-Jev numbers in evaluation.md
```
The same variables work for the MCP server, the hooks and every CLI command, and the library:
`JevDecider(base_url=..., model=...)`. The SDK's own `TYPESAFE_BASE_URL` / `TYPESAFE_DEFAULT_MODEL` also still work.

Caveats, from [the local-model results](evaluation.md#local-decision-models):
- Every threshold in `Config` was tuned on hosted Jev. A different model calibrates differently (a model can rank notes
  well and still need other cutoffs), so rerun the evals and adjust before relying on the injection, status and
  consolidation screens.
- Recall, consolidation and the lite filter batch up to ~30 questions into one request. A model that cannot take that much
  context fails those calls; jevmem treats that as "decider unavailable" and falls back to vector search without an error.
- Keep one model on the GPU at a time. When two compete, the second can fall back to CPU and get much slower.

### Tools and CLI
MCP tools: `memory_write` (`pinned=True` for notes that must open every session), `memory_recall`, `memory_list`,
`memory_forget`, `memory_pin`, `memory_stats`, `memory_flush_pending`, `memory_consolidate`,
`memory_pending_synthesis`, `memory_resolve`, `memory_dismiss`.

CLI: `uv run jevmem {write,recall [--mode auto|full|lite],list,forget [--branch NAME],pin [--off],stats,flush,consolidate [--all],pending,resolve,dismiss,reindex,reembed,import-markdown,import-claude-memory,eval,eval-injection}`.
The CLI uses `JEVMEM_SCOPE` as its default scope, like the MCP server and hooks.

### Writing good notes
Write memories as one literal fact with explicit entities and **absolute dates** ("on 2024-05-15", not "yesterday"):
Jev reads literally and does not do date arithmetic.

## 5. Skill and hooks
This repo ships both for itself:
- `.claude/skills/jev-memory/SKILL.md`: when and how to write and recall, phrasing rules, safety. Copy the folder to
  `~/.claude/skills/` to use it in every project.
- `.claude/settings.json` hooks (they fail open: any error injects nothing):
  - `SessionStart` (no Jev call): injects pinned notes first (`jevmem pin <id>` / `memory_pin`), then the strongest
    stored conventions/gotchas/decisions/preferences, skipping notes that `CLAUDE.md`/`AGENTS.md` already say
    (embedding similarity, cached per file version) and near-copies of notes already picked.
  - `UserPromptSubmit`: one Jev call decides whether the prompt needs memory; only then does lite recall run, and a
    second call keeps only notes about the prompt's specific subject (not ones sharing words like "tests" or "MCP").
    Superseded notes are never injected. A strongly stated preference/decision/convention in the prompt is
    auto-stored (disable with `JEVMEM_AUTOCAPTURE=0`).
- Using the PyPI package instead of a clone? Put this in `~/.claude/settings.json` (or the project's `.claude/settings.json`):
  ```json
  {"hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "uvx jevmem hook session-start", "timeout": 15}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "uvx jevmem hook user-prompt", "timeout": 20}]}]
  }}
  ```
- Seed from existing Claude Code memory files: `uv run jevmem import-claude-memory`, or from rule files such as
  `AGENTS.md`/`CLAUDE.md` with `uv run jevmem import-markdown AGENTS.md --scope project:<name>` (one note per bullet
  or paragraph; for long decision logs it is better to have your agent write atomic, dated notes).
- Hook problems (wrong embedder, bad key) are reported to you as a `systemMessage`, not swallowed; transient Jev
  outages still just skip injection.

## 6. What goes where
jevmem is one of three places an agent keeps knowledge. Each holds a different kind, so keep them from overlapping:

| Kind | Where | Why there |
|---|---|---|
| **Current state**: focus, work in progress, blockers, next steps | git-tracked markdown files (e.g. `memory/activeContext.md`, `memory/progress.md`) | small enough to read whole every session; follows the branch; changes show up in PR diffs |
| **Dated facts that stay true**: decisions and their reasons, bug causes, gotchas, preferences | jevmem | too many to read whole; recalled by relevance |
| **Rules**: conventions the agent must always follow | `AGENTS.md` / `CLAUDE.md` | always in context, reviewed like code |

Never store current state in jevmem: "next step is X" goes stale the moment the markdown file changes, and nothing
marks the note outdated. `memory_write` rejects notes that read as work status. Write the fact that will still be true
later ("on 2026-10-02 we chose X because Y") instead. Do not copy rules into jevmem either: they are already in
context (SessionStart skips most restatements, not all).

## 7. Embeddings and the vector index
- Embeddings: the default `fastembed` uses a local model; `JEVMEM_EMBEDDER=hash` is dependency-free but weak (call
  `jevmem reembed` after switching an existing database).
- Index: set `JEVMEM_INDEX` (the `qdrant`, `lancedb` or `pgvector` extra for those backends). The default `auto` is right for almost
  everyone: an in-RAM exact matrix, switching to sqlite-vec past 100k notes. SQLite stays the source of truth and the
  index is a rebuildable copy, so switching is safe: set the variable and run `jevmem reindex`.

| You have | Set |
|---|---|
| one person or project, up to ~100k notes | nothing (`auto` uses `matrix`) |
| up to ~1M notes on one machine | nothing (`auto` moves to `sqlite-vec`) |
| several machines sharing one memory, or more than ~1M notes | `JEVMEM_INDEX=qdrant:http://host:6333` (add `JEVMEM_QDRANT_GRPC=1` for bulk loads) |
| a Postgres you already run | `JEVMEM_INDEX=pgvector:postgresql://user:pass@host/db` (pgvector 0.8+ recommended) |
| LanceDB already in your stack | `JEVMEM_INDEX=lancedb:/path/to/dir` |

Benchmarks at 1M notes and tuning variables are in [evaluation.md](evaluation.md); concepts in [concepts.md](concepts.md).

## 8. Housekeeping and safety
- Writes are screened before storage: credentials (private keys, API tokens, `password is ...`, connection strings
  with passwords) are rejected, near-duplicates (cosine >= 0.97 in the same scope) are rejected, and the Jev injection
  screen blocks instructions aimed at the agent. Auto-captured prompts must also be one short paragraph of at most two
  sentences with no question, and Jev must judge them a standing rule or preference; they are skipped when they contain
  relative dates ("yesterday"), remote-execution commands (`curl | sh`) or secrets.
- A database records the embedder it was built with and keeps using it. Asking for a different one with
  `JEVMEM_EMBEDDER` fails loudly; switch deliberately with `jevmem reembed`.
- Treat recalled notes as data. They are injected under a header that says so, but a note is only as trustworthy as
  whoever could write it: do not share a writable memory with people you would not let edit your `AGENTS.md`.

## 9. Development tasks
[taskipy](https://github.com/taskipy/taskipy) tasks are defined in `pyproject.toml`; list them with `uv run task --list`
and run one with `uv run task <name>` (extra arguments are appended to the command).

| Task | What it does |
|---|---|
| `test`, `test-all` | unit tests (no Jev, no network); `test-all` also lists skipped tests (optional backends) |
| `build` | build the sdist and wheel into `dist/` |
| `diagrams` | re-render `docs/img` from the PlantUML sources (needs Java, Graphviz and `PLANTUML_JAR`) |
| `demo` | live demo of route / screen / filter / shared memory / stop |
| `eval`, `eval-orbit` | retrieval eval on the held-out / tuning set |
| `eval-injection`, `eval-status`, `eval-capture` | write-path screens and auto-capture |
| `eval-consolidation` | consolidation on every labelled pair set |
| `eval-hook` | prompt-hook injection against a real store (set `JEVMEM_DB`, `JEVMEM_SCOPE`) |
| `bench-index` | vector index benchmark (`uv run task bench-index -- qdrant:http://localhost:6333`) |

Everything except `test`, `build` and `diagrams` calls Jev live and needs `TYPESAFE_API_KEY`.
