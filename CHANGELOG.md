# Changelog

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
