# Changelog

## Unreleased (becomes 0.1.0, the first published version)

### Known-limitations pass
- MCP tool failures reach the agent as `<ExceptionType>: <message>` instead of "Error executing tool X";
  `memory_recall` items carry `flags` (superseded, contradicts, ...) and a `hint` when lite says memory may be missing
  a link.
- Auto-capture is much stricter: at most 300 characters, one paragraph, two sentences, no question, plus a Jev
  `standing` question (`Config.capture_standing` 0.70) and today's date stamped on the note. 0 false captures on 123
  real prompts (`evals/capture_eval.py`).
- Work-status notes ("next step is X") are rejected at write time (`status` question in the typing call,
  `Config.status_block` 0.85; `evals/status.json`, `jevmem eval-status`).
- Fixed in the live trial: the MCP server's in-memory vector index now notices notes written or deleted by other
  processes (hooks, CLI) via `PRAGMA data_version`; an unknown recall `mode` is an error instead of a silent full run;
  the Jev client is created on first use, so `stats`/`list`/`forget` work without an API key.
- Recall budget: `max_jev_calls` (default 19, what the old code actually spent) is a true cap and includes lite's call when `auto` escalates; escalated queries reuse
  lite's relevance judgments.
- Consolidation: same-time conflicts are ordered by which note reports the change (32 of 36 resolved, 0 true
  contradictions lost); remaining conflicts become `conflict` proposals for the agent; pairs returned together by
  recall are queued and judged (`evals/consolidation_ties*.json`).
- SessionStart ranks by type confidence boosted by similarity to the repository's recent work and by usage
  (`evals/session_eval.py`: hit@10 0.39 -> 0.52 on 31 sessions), and uses one cached Jev call to skip borderline notes
  that restate `AGENTS.md` (22 of 23 restatements skipped).
- Git awareness (`gitctx.py`): notes record the branch and commit they were written on; notes from unmerged branches
  rank x0.8 lower (`Config.unmerged_penalty`) and the prompt hook skips them; the default scope is the repository
  name, shared by all worktrees; `jevmem forget --branch`; `JEVMEM_REPO`. Run the MCP server with
  `uv run --project <jevmem>` (not `--directory`) so it keeps the agent's working directory.
- `JEVMEM_HOOK_LOG` writes hashed prompt/injection records for tuning.

### Earlier in this release: recall modes and hooks
- `auto` is the default `recall_mode` (MCP tool, CLI, library; was `full`): pooled over 14 sets it matches full
  recall (0.911 vs 0.910) for ~40% fewer Jev tokens. The prompt hook stays on `lite`; `jevmem eval` still reports
  full explicitly.
- README and skill: "What goes where" (current state in git-tracked markdown, dated facts that stay true in jevmem,
  rules in `AGENTS.md`/`CLAUDE.md`), and new known limitations (no per-branch scope, status notes go stale silently,
  the instructions-file filter misses some restatements, MCP errors hide the exception).
- Consolidation: a bidirectional "reports a change" question catches renames/replacements worded differently when
  the stale note is written last (`Config.conflict_change`); equal timestamps fall back to dates in the text.
  Held-out 44/44, fresh held-out 32/32 (`evals/consolidation_holdout2.json`).
- Lite recall judges sufficiency in its single call: `sufficient`/`missing` are set (abstains on 94% of unanswerable
  questions). `Config.lite_sufficient` 0.3, `lite_missing_max` 0.7; `escalate_insufficient` (off by default).
- SessionStart: pinned notes first (`jevmem pin`, `memory_pin`, `memory_write(pinned=True)`); skips notes already in
  `CLAUDE.md`/`AGENTS.md` (`Config.instructions_similarity`, embeddings cached by content hash) and near-copies.
- UserPromptSubmit: lite recall plus a same-subject filter (`Config.hook_topic_min`), needs-memory gate 0.20, and
  stale notes are never injected. New `evals/hook_eval.py` / `evals/hook_prompts.json`, `evals/sweep_lite.py`.
- MCP server: every tool failed with "SQLite objects created in a thread" whenever the framework ran a call on a
  different worker thread than the one that opened the store. The connection is now shared across threads and tool
  calls are serialized by a lock.

### Earlier in this release: consolidation rework
- Consolidation questions are order-neutral; code orders notes by timestamp (then write time). Stale notes written
  after their replacement (imports, backfills) are now caught, and updates are no longer filed as contradictions.
- New `duplicate_of` / `subsumed_by` flags from coverage questions replace merge proposals; only repeated episodes
  are proposed (as `promote`). Stale flags are skipped by SessionStart and down-ranked in recall (`Store.STALE_FLAGS`).
- `jevmem consolidate --all` / `memory_consolidate(all_notes=True)` re-judges every note after an upgrade.
- `Config`: `consolidate_threshold` and `merge_threshold` replaced by `conflict_*`, `cover_threshold`, `promote_threshold`.
- Consolidation eval (`evals/consolidation_eval.py`, tuning and held-out pair sets): held-out 43/44 vs 22/44.

## 0.1.0 (beta), field-test fixes
Found by running it on a real project (SpaceMaker, 82 notes, hooks and recall):
- The database records its embedder; mismatches fail loudly (`jevmem reembed` to switch). Hook errors surface as a `systemMessage`.
- CLI defaults to `JEVMEM_SCOPE`; new `forget`, `resolve`, `dismiss`, `reembed`, `import-markdown`; `recall` shows dates and flags.
- Secret screen, duplicate screen, stricter auto-capture (no relative dates, no `curl | sh`).
- "Needs memory" gate reworded (chit-chat no longer injects notes) and threshold recalibrated.
- Node ids are never reused (consolidation watermark bug); stale merge proposals are closed on delete; merge threshold 0.95.
- Injected notes are cut at word boundaries; SessionStart ranks conventions and gotchas first.

## 0.1.0 (beta)
- Jev-based memory: SQLite store, typed writes, consolidation, `full` / `lite` / `auto` recall.
- MCP server, CLI, Claude Code skill and hooks (hooks use `auto`).
- Pluggable vector index: matrix, sqlite-vec, Qdrant, LanceDB, pgvector (benchmarked at 1M notes).
- Eval harness (LoCoMo, real project notes, injection) and calibrated thresholds.
