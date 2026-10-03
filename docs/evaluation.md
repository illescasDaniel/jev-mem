# Evaluation

Results, datasets and caveats for jevmem. Concepts are explained in [concepts.md](concepts.md).

## Running it
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
single run each (Jev is not perfectly deterministic); precision is low for baselines by construction (fixed k).

## Real embeddings and LoCoMo
`uv sync --extra embed` adds fastembed (`BAAI/bge-small-en-v1.5`, downloaded on first use); select it with
`JEVMEM_EMBEDDER=fastembed` (or `jevmem eval --embedder fastembed`). After switching embedders on an existing
database call `Store.reembed()`. With real embeddings the baselines get much stronger, so the honest numbers are:

| recall (items returned) | harbor (held-out) | LoCoMo conv-26, sessions 1-4 |
|---|---|---|
| vector top-5 (bge-small) | 0.86 (5.0) | 0.67 (5.0) |
| hybrid top-5 | 0.93 (5.0) | 0.57 (5.0) |
| `jevmem-lite` (vector top-20 + one Jev relevance filter) | 0.99 (2.6) | 0.76 (3.5) |
| jevmem, graph expansion off | 0.99 (1.4) | 0.79 (2.1) |
| jevmem | 0.99 (1.4) | 0.83 (2.5) |

The LoCoMo subset is 76 turns (one note per turn, stamped with the session date) and 45 questions: 35 answerable
(19 single-hop, 9 temporal, 5 multi-hop, 2 inference) plus 10 adversarial questions whose evidence is in later,
unloaded sessions (so they are unanswerable here; jevmem abstained on 10/10). Regenerate it with
`curl -L -o evals/data/locomo10.json https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json`
then `python evals/make_locomo.py 0 4 > evals/locomo_c0.json`. The data is CC BY-NC 4.0, so it is gitignored
rather than committed. This is a small slice, not a reproduction of the paper's LoCoMo numbers.

(The `jevmem-lite` row was measured before lite mode shipped as `recall_mode="lite"`; it ranked by vector order instead of by
judged relevance, which the re-run below does better: 0.81 on conv-26.)

What this says: a single Jev call that filters a vector DB's top-20 already delivers most of the benefit (it
roughly halves the context at higher recall for ~$0.0001 and ~0.25 s). The routing, graph expansion and stop rule
cost ~1 s and 4-5 Jev calls and add the multi-hop recall (0.20 -> 0.70 on LoCoMo), cleaner abstention and the
smallest context. Whether that is worth it depends on how multi-hop your questions are.

## Lite mode, more LoCoMo conversations and real project notes
`recall_mode="lite"` (`JEVMEM_RECALL_MODE=lite`, `jevmem recall --mode lite`, MCP `mode="lite"`) is vector top-20 plus one
batched Jev relevance filter: no routing, graph, multi-hop or stop rule, so `sufficient` stays `null`. If Jev is down it
returns the plain vector top-k. `jevmem eval` now reports it as `jevmem-lite`.

**More LoCoMo.** `evals/make_locomo.py N 4` for conversations 1-9 (same recipe as conv-26/index 0, `bge-small`, frozen
defaults, nothing tuned on them): 9 conversations, 272 questions (182 answerable, 90 unanswerable), pooled:

| method | recall | exact | items | items on unanswerable | Jev calls | Jev input tokens | latency |
|---|---|---|---|---|---|---|---|
| vector top-5 | 0.70 | 0.66 | 5.0 | 5.0 | 0 | 0 | ~0 |
| hybrid top-5 | 0.65 | 0.62 | 5.0 | 5.0 | 0 | 0 | ~0 |
| jevmem-lite | 0.84 | 0.82 | 3.0 | 1.3 | 1.0 | 2.1k | 0.25 s |
| jevmem-flat (no graph) | 0.85 | 0.83 | 1.6 | 0.4 | 4.0 | 3.8k | 0.94 s |
| jevmem (full) | 0.89 | 0.87 | 2.3 | 1.0 | 5.9 | 9.5k | 1.44 s |

By question kind (vector / lite / full): single 0.70 / 0.87 / 0.92 (n=102), temporal 0.81 / 0.90 / 0.94 (48), multi-hop
0.60 / 0.76 / 0.82 (19), inference 0.38 / 0.54 / 0.60 (13). Full recall abstained (`sufficient != True`) on 85 of 90
unanswerable questions (94%). Re-running conv-26 gave lite 0.81 and full 0.81 (the earlier run said 0.83 for full: Jev
is not perfectly deterministic, so differences under ~0.03 are noise). So lite keeps about three quarters of the gain over vector
search for about a fifth of the Jev tokens and a sixth of the latency; the full pipeline's remaining edge is multi-hop,
inference and knowing when memory has no answer, which lite cannot do.

**Real project notes.** `evals/make_gitnotes.py <repo> <since> <questions.json>` turns a git history into notes (one per
commit subject, stamped with the author date). We used 57 commit subjects from a real project of ours (SpaceMaker,
2026-09-29 to 10-02) with 31 questions we wrote against it (19 single, 4 multi-hop, 2 temporal, 6 unanswerable). The
data is private, so it is gitignored (`evals/real_*.json`):

| method | recall | exact | items | Jev calls | Jev input tokens |
|---|---|---|---|---|---|
| vector top-5 / hybrid top-5 | 0.90 / 0.90 | 0.88 / 0.88 | 5.0 | 0 | 0 |
| jevmem-lite | 0.92 | 0.92 | 2.8 | 1.0 | 2.1k |
| jevmem-flat | 1.00 | 1.00 | 1.7 | 4.0 | 3.7k |
| jevmem (full) | 1.00 | 1.00 | 2.1 | 5.8 | 7.6k |

All 6 unanswerable questions were abstained by full recall (lite returned 0.2 items on average, no abstain signal). The
pattern matches LoCoMo (graph/judging beats plain search on temporal and multi-hop questions) but the margin is smaller
because commit subjects are short and distinctive, so even vector search finds 90%. Caveat: the questions were written
by the same author as the system, and 31 questions is little.

**Sufficiency retune.** The stop check (`sufficient`, `missing`) was swept on these sets (truth = answerable and fully
retrieved):

| thresholds | LoCoMo conv-26 (fp / fn) | LoCoMo 1-9 (fp / fn) | real notes (fp / fn) | unanswerable called sufficient |
|---|---|---|---|---|
| defaults: sufficient >= 0.5, missing < 0.6 | 6 / 1 | 20 / 24 | 0 / 6 | 5 of 106 |
| sufficient >= 0.3, missing < 0.8 (best pooled) | 7 / 0 | 22 / 13 | 0 / 3 | 8 of 106 |

The looser setting roughly halves the missed-sufficient cases (so recall stops earlier and costs less) but lets 3 more
unanswerable questions through as "sufficient". We **kept the defaults**: a wrongly confident "memory has it" is the
costlier error for an agent. If you care more about cost than abstention, set `Config(sufficient=0.3, missing_max=0.8)`.
The sweep on the two sets agreed on direction (lower `sufficient`, higher `missing_max`), which suggests the question
wording, not the data, limits the check.

## Auto mode (lite first, escalate to full)
`recall_mode="auto"` runs lite; its single Jev call also carries two extra questions (does answering need several facts
joined? does it need dates or ordering?). It keeps the lite answer unless lite found nothing or either probability is at or
above 0.6 (`escalate_multi_hop`, `escalate_temporal`), then runs full recall and pays for both. A first version with only
the multi-hop question lost temporal recall on srxy (0.58 vs 0.83), which is why the temporal question exists. `jevmem eval`
reports `jevmem-auto` by replaying lite and full per question (no extra Jev calls). Threshold sweep (recall / escalation rate
/ Jev input tokens including the lite call; `thr 0` is "always full", `thr 1.01` is "lite, escalating only on empty"):

| set (questions) | thr 0 (always full) | thr 0.6 (default) | thr 0.8 | thr 1.01 (lite) |
|---|---|---|---|---|
| LoCoMo conv-26 (45) | 0.81 / 100% / 9.8k | 0.81 / 38% / 5.1k | 0.83 / 27% / 3.9k | 0.81 / 2% / 2.5k |
| LoCoMo 1-9 (272) | 0.89 / 100% / 11.7k | 0.88 / 58% / 8.1k | 0.86 / 49% / 6.8k | 0.84 / 18% / 3.1k |
| SpaceMaker commits (31) | 1.00 / 100% / 9.6k | 1.00 / 61% / 7.9k | 1.00 / 48% / 7.4k | 0.92 / 16% / 2.7k |
| srxy commits + docs (47) | 0.92 / 100% / 11.8k | 0.92 / 53% / 7.9k | 0.90 / 30% / 5.5k | 0.87 / 9% / 2.8k |

The default keeps full-mode recall on every set. The saving against running full recall alone (about 9.5k tokens, 5.9
calls on LoCoMo) is only 15-25% in tokens, because over half the questions look multi-hop or temporal and then pay for both
calls; latency drops from 1.3-1.5 s to 0.8-1.1 s on average. Lowering the trigger (0.8) saves more but starts losing recall.
Note that when auto does not escalate it has no `sufficient` signal, so it cannot abstain on those questions (unanswerable
questions got 1.1-2.2 items on average in auto vs 0.9-1.7 in full).

## Final calibration (pooled, 14 sets, 466 questions)
`evals/sweep_pooled.py` pools the `jevmem eval --json` output of every set (LoCoMo 0-9, SpaceMaker, srxy, orbit, harbor,
fastembed): 300 questions fully retrieved, 126 unanswerable.

**Sufficiency (`sufficient`, `missing`, `contradiction`).** The accuracy-best setting (suff >= 0.2-0.3, missing < 0.85) scores 0.89
against 0.85 for the defaults (>= 0.5, < 0.6, < 0.6), but only by calling nearly everything sufficient: it lets 9% of
unanswerable questions through versus 5% with the defaults. Almost all false positives come from answerable questions where
retrieval missed a gold note, which the prompt cannot detect. Leave-one-set-out accuracy is 0.88 either way. We kept the
defaults: they are the best at what the flag is for (abstaining on unanswerable questions, 95%), and no threshold
combination does much better, so the signal itself is the limit, not the calibration.

**Auto escalation (`escalate_multi_hop`, `escalate_temporal`).** Pooled recall of answerable questions / escalation rate / Jev
input tokens per question (full = 0.910 / 100% / 10.4k, lite = 0.876 / 14% / 2.8k):

| multi-hop \ temporal | 0.6 | 0.8 | 1.01 (never) |
|---|---|---|---|
| 0.5 | 0.912 / 57% / 7.6k | 0.912 / 49% / 7.1k | 0.891 / 33% / 5.4k |
| 0.6 | 0.911 / 52% / 7.0k | **0.911 / 44% / 6.3k** | 0.890 / 26% / 4.4k |
| 0.8 | 0.905 / 49% / 6.5k | 0.905 / 41% / 5.9k | 0.884 / 22% / 3.9k |

Raising the temporal trigger from 0.6 to 0.8 keeps full-mode recall at 40% fewer tokens than always-full (6.3k vs 10.4k); lowering the
multi-hop trigger below 0.6 buys nothing. Defaults are now 0.6 / 0.8. The temporal question must stay in the lite call:
dropping it cost temporal recall on srxy (0.58 vs 0.83).

## Third real-notes set: srxy (commits plus docs)
`evals/real_srxy.json` (gitignored) = 88 commit subjects (2026-08-15 onward) plus 50 hand-written single-fact notes taken from the
repo's docs/README/AGENTS.md (dated with each file's last commit), and 47 questions: 27 single (3 on superseded
facts), 8 multi-hop, 4 temporal, 8 unanswerable (verified absent by grep). Built by a helper agent from
`evals/make_gitnotes.py <repo> <since> <questions.json> <extra-notes.json>`; gold items were checked against the sources.

| method | recall | exact | items | Jev calls | Jev input tokens |
|---|---|---|---|---|---|
| vector top-5 / hybrid top-5 | 0.75 / 0.75 | 0.69 / 0.67 | 5.0 | 0 | 0 |
| jevmem-lite | 0.87 | 0.77 | 2.9 | 1.0 | 2.4k |
| jevmem-flat | 0.93 | 0.85 | 2.1 | 4.0 | 4.2k |
| jevmem (full) | 0.92 | 0.82 | 2.6 | 5.8 | 9.4k |
| jevmem-auto | 0.92 | 0.82 | 2.7 | 4.2 | 7.9k |

By kind (vector / lite / full): single 0.89 / 0.96 / 0.96, multi-hop 0.65 / 0.82 / 0.82, temporal 0.04 / 0.38 / 0.83. Full
recall abstained on 7 of 8 unanswerable questions (0.88) and returned 2.5 items on average for them, noticeably worse than on
the other sets: unanswerable questions about "plugins" or "shell completion" match topically similar notes. Graph expansion
(`flat` vs full) did not help here (0.93 vs 0.92). Same caveats: questions written by us, 47 is small.

## Scaling: pluggable vector index
SQLite (`nodes.emb`) stays the source of truth; the vector index is a derived copy you can drop and rebuild
(`jevmem reindex`, also automatic on open if a persistent index is missing rows). Pick one with `JEVMEM_INDEX`
(`uv sync --extra index` installs the optional ones):

| index | what it is | 100k notes x 384-d, scope-filtered top-10 |
|---|---|---|
| `matrix` (default) | exact, in-RAM numpy matrix, lazily loaded, incremental adds | 4.5 ms, ~150 MB RAM |
| `sqlite-vec` | exact KNN in the same SQLite file, partitioned by scope, no extra RAM | 12 ms, 3 s to build, 0.7 ms per add |
| `qdrant[:path or http://host:6333]` | external/shared vector DB | local mode 700 ms (exact, in-process, not HNSW) |
| `lancedb[:path]` | embedded on-disk columnar store, exact scan (IVF-PQ from 1M notes) | 55-80 ms, 2.7 s to build, 2 ms per add, recall 1.00 |
| `pgvector:<dsn>` | Postgres + pgvector HNSW | see the 1M table below |

`auto` uses `matrix` and switches to `sqlite-vec` past 100k notes. Qdrant only becomes an ANN (HNSW) with a Qdrant
*server* URL (benchmarked below at 1M notes), so use it for multi-million notes or when
several machines share one memory. Tests check that all five agree with brute force, honour scope and exclusion
filters, and recover from a lost index. Other scale fixes: scope, entity and time-neighbour lookups now run as
indexed SQL instead of scanning every note (previously each write scanned its whole scope), and the full-text
search filters by scope inside SQLite. Jev cost does not grow with store size; it is set by `max_jev_calls`.

The paper's stop thresholds (sufficient >= 0.95, missing < 0.15) did not fit our wording; defaults are now
sufficient >= 0.5, missing < 0.6, contradiction < 0.6 (`Config`). Superseded notes are marked in the evidence Jev
sees and ignored by the contradiction check.



## 1M-note benchmark (`evals/bench_index.py`)
One machine (32 cores, 31 GB RAM), 1,000,000 synthetic unit vectors x 384 dims (2000 Gaussian clusters with heavy noise, so
neighbours are not trivially separable), 10 scopes, 100 queries (a stored point plus noise), top-10, recall against an exact
numpy scan. "scoped" = one scope (10% of the notes); "all" = no scope filter. Servers ran in podman containers
(`qdrant/qdrant:latest` over gRPC, `pgvector/pgvector:pg17` with pgvector 0.8.7), so these are local loopback numbers, not a
networked deployment. Other jobs (live evals) were running on the same machine, so treat latency as +-30%.

| index | build | scoped p50 / p95 | scoped recall | all p50 | all recall | add |
|---|---|---|---|---|---|---|
| `matrix` (exact, RAM) | lazy load | 33 / 54 ms | 1.00 | 31 ms | 1.00 | 3.2 s first add (reload), then ~ms |
| `sqlite-vec` (exact) | 64 s | 44 / 50 ms | 1.00 | 795 ms | 1.00 | 0.3 ms |
| `lancedb` exact scan | 3.6 s | 407 / 461 ms | 1.00 | 431 ms | 1.00 | 1.8 ms |
| `lancedb` IVF-PQ, nprobes 20, refine 10 | 62 s | 22 / 31 ms | 0.45 | 6 ms | 0.45 | 1.9 ms |
| `lancedb` IVF-PQ, nprobes 100, refine 20 | (same) | 63 / 78 ms | 0.81 | 17 ms | 0.80 | |
| `lancedb` IVF-PQ, nprobes 300, refine 20 (default) | (same) | 67 / 87 ms | 0.98 | 55 ms | 0.98 | |
| `lancedb` IVF-PQ, nprobes 1000, refine 50 | (same) | 129 / 143 ms | 1.00 | 196 ms | 1.00 | |
| `qdrant` server HNSW (defaults, keyword index on `scope`) | 381 s (incl. optimizer) | 3.5 / 4.2 ms | 0.996 | 4.3 ms | 0.973 | 2.5 ms |
| `pgvector` HNSW m=16, ef_construction=64, ef_search=100 | 72 s | 2.1 / 3.7 ms | 0.40 | 1.4 ms | 0.39 | 11 ms |
| `pgvector` same graph, ef_search=400 | (same) | 4.3 / 16 ms | 0.65 | 3.3 ms | 0.65 | |
| `pgvector` m=16, ef_construction=200, ef_search=100 | 193 s | 2.4 / 4.5 ms | 0.79 | 1.3 ms | 0.78 | 14 ms |
| `pgvector` ef_construction=200, ef_search=200 | (same) | 3.3 / 6.4 ms | 0.91 | 2.5 ms | 0.91 | |
| `pgvector` ef_construction=200, ef_search=400 (default) | (same) | 3.3 / 6.6 ms | 0.975 | 2.4 ms | 0.973 | 15 ms |

What this says:
- **Qdrant** is the best fit at this size out of the box (about 3.5 ms at 0.97-0.99 recall) and also what to pick if several
  machines share a memory. Its first adapter version failed against a real server: REST requests were reset on bulk upload (the
  adapter now supports gRPC via `JEVMEM_QDRANT_GRPC=1`), and a payload index on `scope` was added so filtered searches
  traverse the graph instead of post-filtering. Note `localhost` resolved to IPv6 in our rootless podman setup and hung; use
  `127.0.0.1`.
- **pgvector** with default build parameters had poor recall here (0.4 at ef_search 100). Raising `ef_construction` to 200 and
  `ef_search` to 400 gives 0.975 at ~3 ms, at the cost of a 2.7x slower build (193 s). The adapter now defaults to those and
  reads `JEVMEM_PG_M`, `JEVMEM_PG_EF_CONSTRUCTION`, `JEVMEM_PG_EF_SEARCH`. Iterative scan (pgvector 0.8) is on, so scoped
  queries do not come back short; on older pgvector the adapter falls back to an exact scope scan. Our data is harder than
  typical embeddings, so your recall at lower settings may be better: measure with your own vectors.
- **LanceDB** IVF-PQ is 6x faster than its exact scan at recall 0.98 but only with `nprobes` around 30% of the 1000
  partitions; the cheap setting (nprobes 20) loses more than half the neighbours on this data. It builds automatically once
  `rebuild` sees >= 1M rows (`JEVMEM_LANCE_ANN_MIN`); tune with `JEVMEM_LANCE_NPROBES` / `JEVMEM_LANCE_REFINE`. Below ~1M the
  exact scan is under 0.4 s and needs no tuning.
- `matrix` and `sqlite-vec` stay exact; at 1M, `matrix` needs ~1.5 GB RAM and `sqlite-vec` is slow only for unscoped searches.
Caveat: synthetic vectors, one machine, single run each.
