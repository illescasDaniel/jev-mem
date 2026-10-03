# Known limitations

We would rather tell you where jevmem is weak than have you find out. Every number here is measured on small datasets
we built ourselves ([evaluation.md](evaluation.md)): indications, not benchmarks.

## Recall
- `auto` recall (the default) reruns a question in full mode only when lite's single Jev call judges it multi-hop or
  time-related. A multi-hop question misjudged as simple keeps lite's answer: lite cannot notice a missing link that
  only graph expansion would find. `memory_recall` then returns `sufficient: false` with a hint to rerun with
  `mode="full"`. Escalating on every insufficient answer costs about 25% more Jev calls for +0.002 recall, so it stays
  off (`Config.escalate_insufficient`). Lite's "memory does not know" is right on 94% of unanswerable questions
  (full: 98%).
- A multi-hop `auto` recall can still stop at the call/time limit (`max_jev_calls` 19, which includes lite's call and
  cannot be overshot) and then reports `stop_reason` `limit:calls/time`. Enforcing a lower cap (16) cost 0.5-0.9
  points of full/auto recall on multi-hop and temporal questions; 19 restores full recall at the old average cost
  (5.6 calls). Lower `max_jev_calls` to trade recall for latency.

## Consolidation
- Each new note is compared with its 3 nearest neighbours, plus the pairs that recall returned together (queued after
  each recall and judged on the next pass). A stale note that never surfaces with its replacement is still not
  compared.
- Two conflicting notes with the same timestamp and no dates are ordered by which one reports the change (32 of 36
  such pairs in the tie eval); the rest stay `contradicts` and become a `conflict` proposal for the agent
  (`memory_pending_synthesis`), with `jevmem forget <id>` as the manual fix.

## Git awareness
- Notes from a branch that is neither current, default nor merged rank x0.8 lower and the prompt hook skips them, but a
  squash-merged branch looks the same as an abandoned one, so such notes are only demoted, never hidden. Use
  `jevmem forget --branch <name>` for dead branches. Notes written before this existed, or outside a git repository,
  are never penalized.

## Hooks
- SessionStart ranking weights were chosen on 31 past sessions of one project (hit@10 0.39 -> 0.52, no held-out
  split).
- The prompt hook's relevance filter was tuned on 44 prompts against one store (about 18 of 22 off-topic prompts inject
  nothing). Expect the occasional off-topic note and tell your agent to ignore it. Set `JEVMEM_HOOK_LOG=<file>` to
  collect hashed prompt/injection records for tuning on your own data.
- SessionStart skips notes that restate `CLAUDE.md`/`AGENTS.md`: by embedding similarity >= 0.82, and for the
  borderline band 0.70-0.82 by one cached Jev coverage call. On the project it was tuned on that skips 22 of 23
  restating notes and none of the other 59; a paraphrase can still slip through. A live session still injected 3
  paraphrases: their coverage scores (0.30-0.43) were below the 0.50 threshold while a non-restating note scored 0.49,
  so no threshold separated them and it was not retuned on a few points. If a restating note keeps appearing,
  `jevmem forget <id>` it.

## Writes
- Status notes are rejected at write time by a classifier (0 misses and 0 false rejections on the 36-note tune set
  and the 24-note held-out set), but a status can be phrased as a dated event ("On 2026-10-03 the migration was
  started") and pass. Keep current state in markdown ([What goes where](installation.md#6-what-goes-where)).
- Auto-capture is deliberately conservative: 0 false captures on 123 real prompts, but it also stores only 11 of 12
  synthetic standing preferences. Write the rest with `memory_write`.
- Jev reads literally: write one fact per note with absolute dates.

## Trust
- A note is only as trustworthy as whoever could write it. Do not share a writable memory with people you would not let
  edit your `AGENTS.md`.
- Jev is a hosted service (TypeSafe) and currently waitlisted. When it is unreachable jevmem degrades to hybrid search
  and queues writes; it does not block your agent.

## Ideas to try next
- Re-run the 1M-note index benchmark on real embeddings (it used synthetic clustered vectors) and with several machines
  writing to one Qdrant/Postgres at once.
- Questions written by someone other than the system's author: all our "real notes" sets use questions we wrote.
- A learned (not prompted) escalation and sufficiency signal; both prompted probabilities plateau at ~0.88 accuracy.
- Grow the prompt-hook eval beyond 44 prompts and a second store, using the opt-in hook log.
- Held-out split for the SessionStart ranking weights; evaluate the usage boost once real usage accumulates.
- Widen the status and capture sets with prompts from other projects and languages.
