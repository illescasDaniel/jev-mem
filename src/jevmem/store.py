"""SQLite store: nodes, typed edges, FTS5 lexical index, embeddings (brute-force cosine)."""
from __future__ import annotations

import hashlib
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
        self.embedder = embedder or HashEmbedder()
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

    def set_type_scores(self, nid: int, scores: dict[str, float]) -> None:
        self.db.execute("UPDATE nodes SET type_scores=? WHERE id=?", (json.dumps(scores), nid))
        self.db.commit()

    def delete_node(self, nid: int) -> None:
        self.db.execute("DELETE FROM nodes WHERE id=?", (nid,))
        self.db.execute("DELETE FROM fts WHERE rowid=?", (nid,))
        self.db.execute("DELETE FROM edges WHERE src=? OR dst=?", (nid, nid))
        self.db.execute("DELETE FROM pending WHERE node_id=?", (nid,))
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
