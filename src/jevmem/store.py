"""SQLite store: nodes, typed edges, FTS5 lexical index, embeddings (brute-force cosine)."""
from __future__ import annotations

import hashlib
import os
import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

EDGE_KINDS = ("semantic", "temporal", "causal", "entity")
_TOKEN = re.compile(r"[a-z0-9_]+")


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashEmbedder:
    """Dependency-free feature-hashing embedder: fine for tests and as a fallback.
    Swap in a real model (e.g. fastembed MiniLM) for quality."""

    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            toks = _TOKEN.findall(t.lower())
            for tok in toks + [a + "_" + b for a, b in zip(toks, toks[1:])]:
                h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:4], "little")
                out[i, h % self.dim] += 1.0 if (h >> 31) & 1 else -1.0
        n = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(n == 0, 1, n)


class FastEmbedEmbedder:
    """Local ONNX embeddings via fastembed (`uv sync --extra embed`). Downloads the model on first use."""

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5"):
        from fastembed import TextEmbedding
        self.model = TextEmbedding(model_name=model)

    def embed(self, texts: list[str]) -> np.ndarray:
        v = np.array(list(self.model.embed(texts)), dtype=np.float32)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


def make_embedder(spec: str | None = None) -> Embedder:
    """`hash` (default) or `fastembed[:model]`; read from JEVMEM_EMBEDDER when spec is None."""
    spec = spec or os.environ.get("JEVMEM_EMBEDDER", "hash")
    if spec.startswith("fastembed"):
        return FastEmbedEmbedder(*spec.split(":", 1)[1:])
    return HashEmbedder()


@dataclass
class Node:
    id: int
    content: str
    scope: str
    timestamp: float | None
    entities: list[str]
    type_scores: dict[str, float] | None
    source: str | None = None
    embedding: np.ndarray | None = field(default=None, repr=False)


@dataclass
class Edge:
    src: int
    dst: int
    kind: str
    weight: float


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class Store:
    def __init__(self, path: str = ":memory:", embedder: Embedder | None = None):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.embedder = embedder or make_embedder()
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS nodes(
          id INTEGER PRIMARY KEY, content TEXT NOT NULL, scope TEXT NOT NULL, ts REAL,
          entities TEXT NOT NULL, type_scores TEXT, source TEXT, emb BLOB, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS edges(
          src INTEGER NOT NULL, dst INTEGER NOT NULL, kind TEXT NOT NULL, weight REAL NOT NULL,
          PRIMARY KEY(src, dst, kind));
        CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst);
        CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(content);
        CREATE TABLE IF NOT EXISTS pending(node_id INTEGER PRIMARY KEY, reason TEXT);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS flags(node_id INTEGER NOT NULL, flag TEXT NOT NULL, other_id INTEGER NOT NULL,
          p REAL NOT NULL, PRIMARY KEY(node_id, flag, other_id));
        CREATE TABLE IF NOT EXISTS synth(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, node_ids TEXT NOT NULL,
          p REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL, result_id INTEGER);
        """)

    # -- nodes ---------------------------------------------------------------
    def add_node(self, content: str, scope: str = "global", timestamp: float | None = None,
                 entities: list[str] | None = None, source: str | None = None) -> int:
        emb = self.embedder.embed([content])[0].astype(np.float32)
        cur = self.db.execute(
            "INSERT INTO nodes(content,scope,ts,entities,source,emb,created) VALUES(?,?,?,?,?,?,?)",
            (content, scope, timestamp, json.dumps(entities or []), source, emb.tobytes(), time.time()))
        nid = cur.lastrowid
        self.db.execute("INSERT INTO fts(rowid, content) VALUES(?,?)", (nid, content))
        self.db.commit()
        return nid

    def reembed(self) -> int:
        """Recompute every stored embedding with the current embedder (needed after switching embedders)."""
        rows = self.db.execute("SELECT id, content FROM nodes").fetchall()
        for i in range(0, len(rows), 64):
            chunk = rows[i:i + 64]
            embs = self.embedder.embed([r["content"] for r in chunk]).astype(np.float32)
            self.db.executemany("UPDATE nodes SET emb=? WHERE id=?", [(e.tobytes(), r["id"]) for e, r in zip(embs, chunk)])
        self.db.commit()
        return len(rows)

    def set_type_scores(self, nid: int, scores: dict[str, float]) -> None:
        self.db.execute("UPDATE nodes SET type_scores=? WHERE id=?", (json.dumps(scores), nid))
        self.db.commit()

    def delete_node(self, nid: int) -> None:
        self.db.execute("DELETE FROM nodes WHERE id=?", (nid,))
        self.db.execute("DELETE FROM fts WHERE rowid=?", (nid,))
        self.db.execute("DELETE FROM edges WHERE src=? OR dst=?", (nid, nid))
        self.db.execute("DELETE FROM pending WHERE node_id=?", (nid,))
        self.db.execute("DELETE FROM flags WHERE node_id=? OR other_id=?", (nid, nid))
        self.db.commit()

    @staticmethod
    def _node(r: sqlite3.Row) -> Node:
        return Node(r["id"], r["content"], r["scope"], r["ts"], json.loads(r["entities"]),
                    json.loads(r["type_scores"]) if r["type_scores"] else None, r["source"],
                    np.frombuffer(r["emb"], dtype=np.float32) if r["emb"] else None)

    def get(self, nid: int) -> Node | None:
        r = self.db.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
        return self._node(r) if r else None

    def nodes(self, scopes: list[str] | None = None) -> list[Node]:
        if scopes:
            q = ",".join("?" * len(scopes))
            rows = self.db.execute(f"SELECT * FROM nodes WHERE scope IN ({q})", scopes).fetchall()
        else:
            rows = self.db.execute("SELECT * FROM nodes").fetchall()
        return [self._node(r) for r in rows]

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

    # -- search ----------------------------------------------------------------
    def vector_search(self, text: str, scopes: list[str] | None, k: int,
                      exclude: set[int] = frozenset()) -> list[tuple[int, float]]:
        ns = [n for n in self.nodes(scopes) if n.id not in exclude]
        if not ns:
            return []
        q = self.embedder.embed([text])[0]
        sims = np.stack([n.embedding for n in ns]) @ q
        order = np.argsort(-sims)[:k]
        return [(ns[i].id, float(sims[i])) for i in order]

    def lexical_search(self, text: str, scopes: list[str] | None, k: int,
                       exclude: set[int] = frozenset()) -> list[tuple[int, float]]:
        toks = list(dict.fromkeys(tokens(text)))
        if not toks:
            return []
        match = " OR ".join(f'"{t}"' for t in toks)
        rows = self.db.execute(
            "SELECT rowid, bm25(fts) AS s FROM fts WHERE fts MATCH ? ORDER BY s LIMIT ?",
            (match, k * 4 + len(exclude))).fetchall()
        allowed = None if not scopes else {n.id for n in self.nodes(scopes)}
        out = [(r["rowid"], -r["s"]) for r in rows
               if r["rowid"] not in exclude and (allowed is None or r["rowid"] in allowed)]
        return out[:k]

    def by_entities(self, entities: list[str], scopes: list[str] | None,
                    exclude: set[int] = frozenset()) -> list[Node]:
        want = {e.lower() for e in entities}
        return [n for n in self.nodes(scopes)
                if n.id not in exclude and want & {e.lower() for e in n.entities}]

    # -- edges -----------------------------------------------------------------
    def add_edge(self, src: int, dst: int, kind: str, weight: float = 1.0) -> None:
        assert kind in EDGE_KINDS
        self.db.execute("INSERT OR REPLACE INTO edges VALUES(?,?,?,?)", (src, dst, kind, weight))
        self.db.commit()

    def neighbors(self, nid: int, kinds: list[str]) -> list[tuple[Edge, int]]:
        """Undirected traversal; returns (edge, other_node_id)."""
        q = ",".join("?" * len(kinds))
        rows = self.db.execute(
            f"SELECT * FROM edges WHERE kind IN ({q}) AND (src=? OR dst=?)", [*kinds, nid, nid]).fetchall()
        return [(Edge(r["src"], r["dst"], r["kind"], r["weight"]),
                 r["dst"] if r["src"] == nid else r["src"]) for r in rows]

    def edges_of(self, nid: int) -> list[Edge]:
        return [e for e, _ in self.neighbors(nid, list(EDGE_KINDS))]

    # -- degraded-mode queue -----------------------------------------------------
    def queue_pending(self, nid: int, reason: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO pending VALUES(?,?)", (nid, reason))
        self.db.commit()

    def pending(self, reason: str | None = None) -> list[int]:
        if reason:
            rows = self.db.execute("SELECT node_id FROM pending WHERE reason=? ORDER BY node_id", (reason,))
        else:
            rows = self.db.execute("SELECT node_id FROM pending ORDER BY node_id")
        return [r[0] for r in rows]

    def clear_pending(self, nid: int) -> None:
        self.db.execute("DELETE FROM pending WHERE node_id=?", (nid,))
        self.db.commit()

    # -- meta / consolidation state ----------------------------------------------
    def meta_get(self, key: str, default: int = 0) -> int:
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return int(r[0]) if r else default

    def meta_set(self, key: str, value: int) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, str(value)))
        self.db.commit()

    def bump_writes(self) -> int:
        n = self.meta_get("writes_since_consolidation") + 1
        self.meta_set("writes_since_consolidation", n)
        return n

    def add_flag(self, nid: int, flag: str, other: int, p: float) -> None:
        self.db.execute("INSERT OR REPLACE INTO flags VALUES(?,?,?,?)", (nid, flag, other, p))
        self.db.commit()

    def flagged(self, *flags: str) -> set[int]:
        q = ",".join("?" * len(flags))
        return {r[0] for r in self.db.execute(f"SELECT node_id FROM flags WHERE flag IN ({q})", flags)}

    def flags_of(self, nid: int) -> list[tuple[str, int, float]]:
        return [(r["flag"], r["other_id"], r["p"]) for r in
                self.db.execute("SELECT * FROM flags WHERE node_id=?", (nid,))]

    # -- System-Two synthesis queue ----------------------------------------------
    def add_synth(self, kind: str, node_ids: list[int], p: float) -> int | None:
        ids = json.dumps(sorted(node_ids))
        if self.db.execute("SELECT 1 FROM synth WHERE kind=? AND node_ids=? AND status='pending'",
                           (kind, ids)).fetchone():
            return None
        cur = self.db.execute("INSERT INTO synth(kind,node_ids,p,created) VALUES(?,?,?,?)",
                              (kind, ids, p, time.time()))
        self.db.commit()
        return cur.lastrowid

    def synth_items(self, status: str = "pending") -> list[dict]:
        return [{"id": r["id"], "kind": r["kind"], "node_ids": json.loads(r["node_ids"]), "p": r["p"]}
                for r in self.db.execute("SELECT * FROM synth WHERE status=? ORDER BY id", (status,))]

    def synth_get(self, sid: int) -> dict | None:
        r = self.db.execute("SELECT * FROM synth WHERE id=?", (sid,)).fetchone()
        return {"id": r["id"], "kind": r["kind"], "node_ids": json.loads(r["node_ids"]),
                "status": r["status"]} if r else None

    def synth_close(self, sid: int, status: str, result_id: int | None = None) -> None:
        self.db.execute("UPDATE synth SET status=?, result_id=? WHERE id=?", (status, result_id, sid))
        self.db.commit()
