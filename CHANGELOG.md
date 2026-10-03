# Changelog

## Unreleased: known-limitations pass
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

## Unreleased: consolidation rework
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
