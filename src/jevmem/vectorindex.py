"""Pluggable vector search. SQLite's `nodes.emb` column stays the source of truth; an index is a derived,
rebuildable copy, so switching or losing one never loses memory (`jevmem reindex`).

Tiers (JEVMEM_INDEX, default `auto`):
  matrix      in-RAM numpy matrix, lazily loaded, exact. Best up to ~100k notes.
  sqlite-vec  on-disk exact KNN inside the same SQLite file, partitioned by scope. Low RAM, one file.
  qdrant[:target]  HNSW ANN; target = a local path, an http(s) URL or empty (<db dir>/qdrant). Million-scale/shared.
  auto        matrix, and switch to sqlite-vec (if installed) once the store passes AUTO_SWITCH notes.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Iterable, Protocol

import numpy as np

AUTO_SWITCH = 100_000


class VectorIndex(Protocol):
    name: str

    def add(self, nid: int, scope: str, vec: np.ndarray) -> None: ...
    def remove(self, nid: int) -> None: ...
    def search(self, q: np.ndarray, scopes: list[str] | None, k: int,
               exclude: set[int] = frozenset()) -> list[tuple[int, float]]: ...
    def rebuild(self, rows: Iterable[tuple[int, str, np.ndarray]]) -> None: ...
    def size(self) -> int | None: ...   # None = "always in sync" (derived lazily from SQLite)


class MatrixIndex:
    name = "matrix"

    def __init__(self, db: sqlite3.Connection):
        self.db, self._loaded = db, False
        self.ids = np.zeros(0, dtype=np.int64); self.codes = np.zeros(0, dtype=np.int32); self.scope_code: dict[str, int] = {}
        self.mat = np.zeros((0, 0), dtype=np.float32)
        self._n = 0

    def _load(self) -> None:
        rows = self.db.execute("SELECT id, scope, emb FROM nodes WHERE emb IS NOT NULL ORDER BY id").fetchall()
        self.ids = np.array([r[0] for r in rows], dtype=np.int64)
        self.scope_code = {}
        self.codes = np.array([self.scope_code.setdefault(r[1], len(self.scope_code)) for r in rows], dtype=np.int32)
        self.mat = np.stack([np.frombuffer(r[2], dtype=np.float32) for r in rows]) if rows else np.zeros((0, 0), np.float32)
        self._n, self._loaded = len(rows), True

    def add(self, nid, scope, vec):
        if not self._loaded:
            return                                    # will be read from SQLite on first search
        vec = vec.astype(np.float32)
        if self._n == 0 or self.mat.shape[1] == 0:
            self.mat = np.zeros((16, len(vec)), np.float32); self.ids = np.zeros(16, np.int64)
            self.codes = np.zeros(16, np.int32)
        elif self._n == len(self.mat):                # amortized doubling
            self.mat = np.vstack([self.mat, np.zeros_like(self.mat)])
            self.ids = np.concatenate([self.ids, np.zeros_like(self.ids)])
            self.codes = np.concatenate([self.codes, np.zeros_like(self.codes)])
        self.mat[self._n], self.ids[self._n] = vec, nid
        self.codes[self._n] = self.scope_code.setdefault(scope, len(self.scope_code))
        self._n += 1

    def remove(self, nid):
        self._loaded = False                          # rare; cheapest correct thing is to reload lazily

    def search(self, q, scopes, k, exclude=frozenset()):
        if not self._loaded:
            self._load()
        if self._n == 0:
            return []
        sims = self.mat[:self._n] @ q.astype(np.float32)
        mask = np.ones(self._n, bool)
        if scopes:
            mask &= np.isin(self.codes[:self._n], [self.scope_code[x] for x in scopes if x in self.scope_code])
        if exclude:
            mask &= ~np.isin(self.ids[:self._n], list(exclude))
        sims = np.where(mask, sims, -np.inf)
        top = np.argpartition(-sims, min(k, self._n) - 1)[:k] if self._n > k else np.arange(self._n)
        top = top[np.argsort(-sims[top])]
        return [(int(self.ids[i]), float(sims[i])) for i in top if np.isfinite(sims[i])]

    def rebuild(self, rows):
        self._loaded = False

    def size(self):
        return None


class SqliteVecIndex:
    name = "sqlite-vec"

    def __init__(self, db: sqlite3.Connection, dim: int):
        import sqlite_vec
        db.enable_load_extension(True); sqlite_vec.load(db); db.enable_load_extension(False)
        self.db, self.dim = db, dim
        db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec USING vec0(scope text partition key, "
                   f"embedding float[{dim}] distance_metric=cosine)")

    def add(self, nid, scope, vec):
        self.db.execute("INSERT OR REPLACE INTO vec(rowid, scope, embedding) VALUES(?,?,?)",
                        (nid, scope, vec.astype(np.float32).tobytes()))

    def remove(self, nid):
        self.db.execute("DELETE FROM vec WHERE rowid=?", (nid,))

    def search(self, q, scopes, k, exclude=frozenset()):
        qb = q.astype(np.float32).tobytes()
        scopes = scopes or [r[0] for r in self.db.execute("SELECT DISTINCT scope FROM nodes")]
        out: list[tuple[int, float]] = []
        for sc in scopes:                             # partition keys filter on equality
            rows = self.db.execute("SELECT rowid, distance FROM vec WHERE embedding MATCH ? AND k=? AND scope=?",
                                   (qb, k + len(exclude), sc)).fetchall()
            out += [(r[0], 1.0 - r[1]) for r in rows if r[0] not in exclude]
        return sorted(out, key=lambda x: -x[1])[:k]

    def rebuild(self, rows):
        self.db.execute("DELETE FROM vec")
        for nid, scope, vec in rows:
            self.add(nid, scope, vec)
        self.db.commit()

    def size(self):
        return self.db.execute("SELECT COUNT(*) FROM vec_rowids").fetchone()[0]


class QdrantIndex:
    name = "qdrant"

    def __init__(self, target: str, dim: int, collection: str = "jevmem"):
        from qdrant_client import QdrantClient, models
        self.m, self.col, self.dim = models, collection, dim
        self.c = (QdrantClient(url=target) if target.startswith("http")
                  else QdrantClient(":memory:") if target == ":memory:" else QdrantClient(path=target))
        if not self.c.collection_exists(collection):
            self.c.create_collection(collection, vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE))

    def add(self, nid, scope, vec):
        self.c.upsert(self.col, [self.m.PointStruct(id=nid, vector=vec.astype(np.float32).tolist(), payload={"scope": scope})])

    def remove(self, nid):
        self.c.delete(self.col, points_selector=self.m.PointIdsList(points=[nid]))

    def search(self, q, scopes, k, exclude=frozenset()):
        flt = self.m.Filter(must=[self.m.FieldCondition(key="scope", match=self.m.MatchAny(any=scopes))]) if scopes else None
        r = self.c.query_points(self.col, query=q.astype(np.float32).tolist(), query_filter=flt,
                                limit=k + len(exclude)).points
        return [(int(p.id), float(p.score)) for p in r if p.id not in exclude][:k]

    def rebuild(self, rows):
        self.c.delete_collection(self.col)
        self.c.create_collection(self.col, vectors_config=self.m.VectorParams(size=self.dim, distance=self.m.Distance.COSINE))
        batch = []
        for nid, scope, vec in rows:
            batch.append(self.m.PointStruct(id=nid, vector=vec.astype(np.float32).tolist(), payload={"scope": scope}))
            if len(batch) >= 256:
                self.c.upsert(self.col, batch); batch = []
        if batch:
            self.c.upsert(self.col, batch)

    def size(self):
        return self.c.count(self.col, exact=True).count


def make_index(db: sqlite3.Connection, dim: int, path: str, spec: str | None = None, n_nodes: int = 0) -> VectorIndex:
    spec = spec or os.environ.get("JEVMEM_INDEX", "auto")
    if spec == "auto":
        if n_nodes >= AUTO_SWITCH:
            try:
                return SqliteVecIndex(db, dim)
            except Exception:
                pass
        return MatrixIndex(db)
    if spec == "sqlite-vec":
        return SqliteVecIndex(db, dim)
    if spec.startswith("qdrant"):
        target = spec.split(":", 1)[1] if ":" in spec else ""
        if not target:
            base = os.path.dirname(os.path.abspath(path)) if path != ":memory:" else None
            target = os.path.join(base, "qdrant") if base else ":memory:"
        return QdrantIndex(target, dim)
    return MatrixIndex(db)
