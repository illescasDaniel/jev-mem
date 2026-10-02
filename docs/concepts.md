# Concepts behind jevmem

This page explains every idea the project is built on, from the model (Jev) to where the bytes end up on disk.
Read it top to bottom once; each section stands alone afterwards.

## 1. The big picture

An AI agent forgets everything between sessions. A *memory* system saves facts and brings back the right ones
later. Two separate jobs hide in that sentence:

1. **Control decisions**: is this worth keeping? what kind of thing is it? does it contradict something? which
   old notes are relevant to this question? do I have enough yet?
2. **Generation**: writing the memory text, summarising, answering.

jevmem gives job 1 to **Jev** (a fast, cheap classifier-style model) and job 2 to the host agent (Claude). jevmem
itself never calls a generative LLM. Memory is stored in a plain **SQLite** file, with an optional **vector
index** next to it for fast similarity search.

```
Claude (writes notes, reads answers)          <- System Two: slow, expensive, generative
   |  memory_write / memory_recall (MCP, hooks, Python)
jevmem
   |-- Jev decides: type, injection, relations, routing, relevance, stop    <- System One: fast, cheap
   |-- SQLite file: notes, graph edges, full-text index, embeddings (truth)
   '-- vector index (matrix | sqlite-vec | qdrant): derived copy for similarity search
```

## 2. Jev and "System One"

Jev (by TypeSafe) is a model that **answers typed questions about some data and never writes text**. This mirrors
the "System One / System Two" split from psychology: System One is fast intuition, System Two is slow reasoning.
The paper behind this project (*Jev-Mem*, arXiv 2609.23986) argues most memory-control work is "semantic but not
generative", so a System-One model should do it instead of an expensive LLM.

You send a `state` (named JSON) and a batch of `questions`. Each question returns a typed answer:

| primitive | answer | example |
|---|---|---|
| `Noul` | probability that the answer is yes | "Does `observation` contain an instruction aimed at an AI agent?" -> 0.97 |
| `Choice` | one option out of a list, with probabilities and a confidence | route a task to `coder`, `researcher` or `unclear` |
| `Score` | a position on a rubric | (supported by Jev, little used here) |

Properties that shape the whole design:
- Fast and cheap: about 70-500 ms per call, about $0.042 per million input tokens. A recall costs ~$0.0002.
- Independent questions are **batched into one call**, so a decision usually costs one round trip.
- It reads **literally**: it cannot do date arithmetic ("yesterday") and gets confused by long, noisy context
  ("context rot"). That is why notes must contain absolute dates and one fact each, and why we keep state small.
- It returns **probabilities, not verdicts**. Our code owns the thresholds (see `Config`), tuned on data.
- It can be down or wrong, so everything **fails open**: if Jev is unavailable, writes are queued as
  "unscreened" (hidden from recall) and recall falls back to plain hybrid search.

## 3. The decisions Jev makes

### On write (`write.py`)
1. **Typing and screening, one batched call.** Scores for episodic / semantic / procedural / preference /
   decision / bugfix / convention / gotcha, plus **injection**. A note that tries to hijack an agent
   ("ignore previous instructions...") is rejected, because memory persists and would poison every later session.
   Ordinary team rules such as "never use pip" must pass (a real bug found by our injection eval).
2. **Candidates.** Code finds likely related older notes: vector + keyword (BM25) + shared entities + nearest in
   time, merged with reciprocal rank fusion.
3. **Relations, one batched call.** For each candidate Jev gives probabilities for semantic / causal links. Edges
   are stored when the probability is >= 0.60. Entity and temporal edges are created by code, because Jev cannot
   order dates.

The result is a **graph**: notes are nodes, edges say "related", "because", "same entity", "next in time".

### On read (`retrieve.py`)
1. **Route**: which kinds of memory does this question need (facts, timelines, causes, entities)?
2. **Anchors**: take the top candidates from vector + keyword search, merged with reciprocal rank fusion (RRF).
3. **Judge**: Jev scores each anchor's relevance and drops the weak ones.
4. **Expand**: follow graph edges from the best notes (within a budget) to reach multi-hop answers, e.g. a note
   explaining *why* something was done that has little word overlap with the question.
5. **Stop rule**: after each round Jev answers "is this evidence sufficient? is something missing? any
   contradiction? is continuing useful?". We stop when thresholds are met or a hard limit (depth, nodes, Jev
   calls, seconds) is hit.
6. The result carries `sufficient` / `missing` flags so the agent knows to look elsewhere (for example in the
   code) instead of guessing, and abstains cleanly on unanswerable questions.

### Periodically (`consolidate.py`)
Every 20 writes Jev compares recent notes with older neighbours: redundant, contradictory, outdated? Nothing is
deleted. Outdated notes get a `superseded_by` flag (which one is older is decided from timestamps in code);
merge/promote proposals wait in a queue and **Claude writes the summary** (`memory_pending_synthesis` ->
`memory_resolve`). This is the System-Two half of the loop.

### Safety rule
Notes are **data, not instructions**. Recalled text is shown to the agent as evidence; the skill tells Claude
never to obey instructions found in it.

## 4. Where memory is stored

One SQLite file, by default `~/.jevmem/memory.db` (`JEVMEM_DB`). SQLite is a database engine that lives in a
single file inside your process: no server, no setup, transactions, and a small footprint. Inside it:

| table | holds |
|---|---|
| `nodes` | one row per note: text, scope, timestamp, entities, Jev type scores, **embedding (`emb`)** |
| `edges` | typed links between notes (semantic, temporal, causal, entity) with weights |
| `fts` | an FTS5 full-text index (keyword search with BM25 ranking) |
| `node_entities` | entity -> note lookup, so entity search is an indexed query |
| `flags`, `pending`, `synth`, `meta` | superseded/merged marks, queued degraded writes, synthesis queue, counters |

**Scopes** partition memory: `global`, `project:<name>`, `agent:<name>`. A project's agents share the project
scope and keep private notes in their own. Every search is scope-filtered.

### What is an embedding?
An embedding turns text into a list of numbers (a vector, e.g. 384 of them) such that texts with similar
*meaning* get nearby vectors. "How do we deploy?" lands near "Releases go out through ops/deploy.sh" even
without shared words. Similarity is usually **cosine similarity**: the angle between two vectors (1 = same
direction).

jevmem ships two embedders:
- `hash` (default): a dependency-free feature-hashing trick. Fine for tests, weak for meaning.
- `fastembed`: a small local ONNX model (`BAAI/bge-small-en-v1.5`, no API calls, no data leaves your machine).
  Enable with `uv sync --extra embed` and `JEVMEM_EMBEDDER=fastembed`. It makes plain vector search much
  better; see [evaluation.md](evaluation.md). If you switch embedders on an existing database, call
  `Store.reembed()`, because vectors from different models are not comparable.

## 5. Vector search, vector indexes and vector databases

**Vector search** answers "which stored vectors are closest to this query vector?".

- **Exact (brute force)**: compare the query against every vector. Always correct. Cost grows linearly with the
  number of notes. With numpy it is a single matrix multiplication: 4.5 ms for 100k notes of 384 numbers.
- **Approximate nearest neighbour (ANN)**: build a clever structure so you only look at a small part of the
  data, accepting that you may occasionally miss a true neighbour. Common structures:
  - **HNSW**: a layered graph of "close to" links; the search hops greedily toward the query. Fast and accurate,
    uses RAM. The most popular choice.
  - **IVF**: cluster the vectors, search only the few nearest clusters.
  - **Product quantisation**: compress vectors so more fit in memory.
  ANN becomes worthwhile when exact search is too slow or too big, typically in the millions of vectors.

A **vector index** is such a structure inside your program. A **vector database** is a system that stores
vectors (usually with their text and metadata), maintains an index, supports filtering (such as "only scope X")
and handles persistence, updates, concurrency and often network access and replication.

Examples of the landscape (not endorsements, and each evolves quickly):

| kind | examples | typical use |
|---|---|---|
| library | FAISS, hnswlib | embed an index in your own code, you handle storage |
| embedded / in-process | sqlite-vec, LanceDB, Chroma (local), Qdrant local mode | one machine, no server |
| extension to a normal database | pgvector (Postgres), sqlite-vec (SQLite) | vectors next to your existing relational data |
| dedicated server | Qdrant, Milvus, Weaviate | large scale, many clients, shared memory |
| managed cloud service | Pinecone and cloud versions of the above | no ops, pay per use |

### How jevmem uses them
A vector database is *storage plus search*. jevmem's value is the **control layer** (Jev's decisions and the
graph), which is independent of where vectors live. So:

- **SQLite is the source of truth.** Notes, edges and embeddings always live in the SQLite file.
- **The vector index is a derived copy**, selected with `JEVMEM_INDEX`. Drop it and rebuild any time
  (`jevmem reindex`); a persistent index that is missing rows is rebuilt automatically on open. Switching
  indexes never loses memory.
- Only `Store.vector_search` touches vectors (through the `VectorIndex` protocol in `vectorindex.py`), so adding
  a backend means implementing four methods: `add`, `remove`, `search`, `rebuild`.

| `JEVMEM_INDEX` | what it is | measured at 100k notes x 384 numbers (top-10 in one scope) |
|---|---|---|
| `matrix` | exact numpy matrix in RAM, loaded lazily, incremental adds | 4.5 ms, ~150 MB RAM |
| `sqlite-vec` | exact search inside the same SQLite file, partitioned by scope | 12 ms, almost no RAM |
| `qdrant[:path\|url]` | external vector DB (local mode is exact and in-process) | 700 ms local mode; HNSW needs a server |
| `auto` (default) | `matrix`, switching to `sqlite-vec` past 100k notes | |

Keyword search (FTS5/BM25) is separate from the vector index and always lives in SQLite. Results from both are
merged with RRF because vectors catch paraphrases while keywords catch exact names, ids and error codes.

### When would you outgrow this?
A normal project accumulates hundreds to thousands of notes; `matrix` handles that instantly. `sqlite-vec` covers
hundreds of thousands. If you reach millions of notes, or want several machines to share one memory, point the
index at a Qdrant (or other) server; the SQLite file still holds the facts.

## 6. Putting a retrieval together (worked example)

Question: "Why does the month-end job run in the evening now?"

1. Jev routes it as causal + temporal.
2. Vector and keyword search return notes about month-end jobs and schedules. Notes about warehouse size
   have little word overlap with the question, so they are not in the top results.
3. Jev drops irrelevant anchors; the "month-end job moved to 22:00" note survives.
4. Graph expansion follows a causal edge to "the warehouse was downsized from Large to Medium", the real reason.
5. Jev's stop check says sufficient, nothing missing, no contradiction. Two short notes go to Claude.

A plain top-5 vector search would send five notes and might miss the cause. That is the measured difference in
[evaluation.md](evaluation.md).

## 7. Glossary

- **System One / Two**: fast intuitive model (Jev) vs slow generative model (Claude).
- **Noul / Choice / Score**: Jev's typed answers.
- **Scope**: a namespace for memories.
- **Edge**: a typed link between two notes (semantic, temporal, causal, entity).
- **RRF (reciprocal rank fusion)**: merges ranked lists by summing `1/(k + rank)`; robust and needs no tuning.
- **BM25 / FTS5**: classic keyword ranking / SQLite's full-text engine.
- **Embedding / cosine similarity**: text as numbers / how close two such vectors are.
- **ANN / HNSW**: approximate nearest-neighbour search / a popular graph-based ANN structure.
- **Fail open**: when Jev is down, memory degrades instead of blocking the agent.
- **Consolidation**: periodic pass that flags contradictions and obsolete notes and queues merges for Claude.
- **MCP**: Model Context Protocol, how Claude Code talks to the jevmem server.
