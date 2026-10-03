"""Pluggable vector search. SQLite's `nodes.emb` column stays the source of truth; an index is a derived,
rebuildable copy, so switching or losing one never loses memory (`jevmem reindex`).

Tiers (JEVMEM_INDEX, default `auto`):
  matrix      in-RAM numpy matrix, lazily loaded, exact. Best up to ~100k notes.
  sqlite-vec  on-disk exact KNN inside the same SQLite file, partitioned by scope. Low RAM, one file.
  qdrant[:target]  HNSW ANN; target = a local path, an http(s) URL or empty (<db dir>/qdrant). Million-scale/shared.
  lancedb[:path]  embedded on-disk columnar store (default <db dir>/lancedb); cosine KNN, scope pushed down as a filter.
  pgvector:<dsn>  Postgres + pgvector (HNSW) for a team that already runs Postgres, e.g. pgvector:postgresql://u:p@host/db
  auto        matrix, and switch to sqlite-vec (if installed) once the store passes AUTO_SWITCH notes.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
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
        self.c = (QdrantClient(url=target, timeout=300, prefer_grpc=os.environ.get("JEVMEM_QDRANT_GRPC") == "1") if target.startswith("http")
                  else QdrantClient(":memory:") if target == ":memory:" else QdrantClient(path=target))
        if not self.c.collection_exists(collection):
            self._create()

    def _create(self):
        self.c.create_collection(self.col, vectors_config=self.m.VectorParams(size=self.dim, distance=self.m.Distance.COSINE))
        # a keyword index lets the HNSW graph search honour the scope filter instead of post-filtering
        self.c.create_payload_index(self.col, "scope", self.m.PayloadSchemaType.KEYWORD)

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
        self._create()
        batch = []
        for nid, scope, vec in rows:
            batch.append(self.m.PointStruct(id=nid, vector=vec.astype(np.float32).tolist(), payload={"scope": scope}))
            if len(batch) >= 1000:
                self.c.upsert(self.col, batch); batch = []
        if batch:
            self.c.upsert(self.col, batch)

    def size(self):
        return self.c.count(self.col, exact=True).count


class LanceDBIndex:
    """Exact flat scan below LANCE_ANN_MIN rows (and for rows added since the last build); an IVF-PQ index is built on
    `rebuild` once the table is that big, searched with nprobes/refine_factor. Both are env-tunable."""
    name = "lancedb"

    def __init__(self, target: str, dim: int, table: str = "jevmem"):
        import lancedb
        self.dim, self.table = dim, table
        self.db = lancedb.connect(target)
        self.t = self.db.open_table(table) if table in self.db.list_tables().tables else self._create()
        self.nprobes = int(os.environ.get("JEVMEM_LANCE_NPROBES", 300))
        self.refine = int(os.environ.get("JEVMEM_LANCE_REFINE", 20))

    def _schema(self):
        import pyarrow as pa
        return pa.schema([("id", pa.int64()), ("scope", pa.string()), ("vector", pa.list_(pa.float32(), self.dim))])

    def _create(self, data=None):
        return self.db.create_table(self.table, data=data, schema=self._schema(), mode="overwrite")

    @staticmethod
    def _quote(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"

    def add(self, nid, scope, vec):
        self.t.add([{"id": nid, "scope": scope, "vector": vec.astype(np.float32).tolist()}])

    def remove(self, nid):
        self.t.delete(f"id = {int(nid)}")

    def _indexed(self) -> bool:
        return any("vector" in (getattr(i, "columns", None) or []) for i in self.t.list_indices())

    def search(self, q, scopes, k, exclude=frozenset()):
        if self.t.count_rows() == 0:
            return []
        conds = []
        if scopes:
            conds.append("scope IN (" + ", ".join(self._quote(x) for x in scopes) + ")")
        if exclude:
            conds.append("id NOT IN (" + ", ".join(str(int(x)) for x in exclude) + ")")
        qb = self.t.search(q.astype(np.float32).tolist()).metric("cosine").limit(k)
        if self._indexed():
            qb = qb.nprobes(self.nprobes).refine_factor(self.refine)
        if conds:
            qb = qb.where(" AND ".join(conds), prefilter=True)
        return [(int(r["id"]), 1.0 - float(r["_distance"])) for r in qb.to_list()]

    def rebuild(self, rows):
        import pyarrow as pa
        self.t = self._create()
        ids, scs, vecs = [], [], []
        for i, sc, v in rows:                         # consumed on this thread: `rows` reads SQLite
            ids.append(i); scs.append(sc); vecs.append(v.astype(np.float32))
            if len(ids) >= 50_000:
                self.t.add(self._batch(pa, ids, scs, vecs)); ids, scs, vecs = [], [], []
        if ids:
            self.t.add(self._batch(pa, ids, scs, vecs))
        n = self.t.count_rows()
        if n >= int(os.environ.get("JEVMEM_LANCE_ANN_MIN", 1_000_000)):
            parts = max(16, int(n ** 0.5))
            subs = next(d for d in (96, 64, 48, 32, 16, 8, 4, 2, 1) if self.dim % d == 0)
            from lancedb.index import IvfPq
            self.t.create_index("vector", config=IvfPq(distance_type="cosine", num_partitions=parts, num_sub_vectors=subs))

    def _batch(self, pa, ids, scs, vecs):
        flat = pa.array(np.concatenate(vecs), pa.float32())
        return pa.record_batch([pa.array(ids, pa.int64()), pa.array(scs, pa.string()),
                                pa.FixedSizeListArray.from_arrays(flat, self.dim)], schema=self._schema())

    def size(self):
        return self.t.count_rows()


class PgVectorIndex:
    name = "pgvector"

    def __init__(self, dsn: str, dim: int, table: str = "jevmem_vec"):
        import psycopg
        from pgvector.psycopg import register_vector
        self.dim, self.table = dim, table
        self.db = psycopg.connect(dsn, autocommit=True)
        self.db.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(self.db)
        self.db.execute(f"CREATE TABLE IF NOT EXISTS {table}(id bigint PRIMARY KEY, scope text NOT NULL, "
                        f"embedding vector({dim}) NOT NULL)")
        self._hnsw()
        self.db.execute(f"CREATE INDEX IF NOT EXISTS {table}_scope ON {table}(scope)")
        self.db.execute(f"SET hnsw.ef_search = {int(os.environ.get('JEVMEM_PG_EF_SEARCH', 400))}")   # default 40 is low
        try:                                           # pgvector >= 0.8: keep scanning so scope filters still fill k
            self.db.execute("SET hnsw.iterative_scan = relaxed_order")
        except psycopg.Error:
            pass

    def _hnsw(self):
        self.db.execute(f"CREATE INDEX IF NOT EXISTS {self.table}_hnsw ON {self.table} "
                        "USING hnsw (embedding vector_cosine_ops) "
                        f"WITH (m = {int(os.environ.get('JEVMEM_PG_M', 16))}, "
                        f"ef_construction = {int(os.environ.get('JEVMEM_PG_EF_CONSTRUCTION', 200))})")

    def add(self, nid, scope, vec):
        self.db.execute(f"INSERT INTO {self.table}(id, scope, embedding) VALUES(%s,%s,%s) ON CONFLICT (id) "
                        "DO UPDATE SET scope=EXCLUDED.scope, embedding=EXCLUDED.embedding",
                        (nid, scope, vec.astype(np.float32)))

    def remove(self, nid):
        self.db.execute(f"DELETE FROM {self.table} WHERE id=%s", (nid,))

    def search(self, q, scopes, k, exclude=frozenset()):
        qv = q.astype(np.float32)
        sql = (f"SELECT id, 1 - (embedding <=> %s) FROM {self.table} "
               "WHERE (%s::text[] IS NULL OR scope = ANY(%s)) AND NOT (id = ANY(%s::bigint[])) "
               "ORDER BY (embedding <=> %s){} LIMIT %s")
        args = (qv, scopes or None, scopes or None, list(exclude), qv, k)
        rows = self.db.execute(sql.format(""), args).fetchall()
        if len(rows) < k and (scopes or exclude):
            # HNSW filters AFTER its candidate scan (before pgvector 0.8's iterative scan), so a selective scope can
            # leave it short. `+ 0` hides the distance from the index: exact scan over the scope instead.
            rows = self.db.execute(sql.format(" + 0"), args).fetchall()
        return [(int(i), float(sim)) for i, sim in rows]

    def rebuild(self, rows):
        self.db.execute(f"DROP INDEX IF EXISTS {self.table}_hnsw")      # bulk load, then build the graph once
        self.db.execute(f"TRUNCATE {self.table}")
        with self.db.cursor() as cur, cur.copy(f"COPY {self.table}(id, scope, embedding) FROM STDIN (FORMAT BINARY)") as cp:
            cp.set_types(["int8", "text", "vector"])
            for i, sc, v in rows:
                cp.write_row((i, sc, v.astype(np.float32)))
        self._hnsw()

    def size(self):
        return self.db.execute(f"SELECT COUNT(*) FROM {self.table}").fetchone()[0]


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
    if spec.startswith("lancedb"):
        target = spec.split(":", 1)[1] if ":" in spec else ""
        if not target:
            base = os.path.dirname(os.path.abspath(path)) if path != ":memory:" else tempfile.mkdtemp(prefix="jevmem-lance-")
            target = os.path.join(base, "lancedb")
        return LanceDBIndex(target, dim)
    if spec.startswith("pgvector"):
        dsn = spec.split(":", 1)[1] if ":" in spec else os.environ.get("JEVMEM_PGVECTOR_DSN", "")
        if not dsn:
            raise ValueError("pgvector needs a DSN: JEVMEM_INDEX=pgvector:postgresql://user:pass@host/db")
        return PgVectorIndex(dsn, dim)
    return MatrixIndex(db)
