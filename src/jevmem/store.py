"""SQLite store: nodes, typed edges, FTS5 lexical index, embeddings (brute-force cosine)."""
from __future__ import annotations

import hashlib
import os
import json
import re
import sqlite3
import time
from .vectorindex import make_index
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

EDGE_KINDS = ("semantic", "temporal", "causal", "entity")
# consolidation flags that mean "another note says this better or more recently": down-ranked in recall
STALE_FLAGS = ("superseded_by", "merged_into", "duplicate_of", "subsumed_by")
_TOKEN = re.compile(r"[a-z0-9_]+")


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashEmbedder:
    """Dependency-free feature-hashing embedder: fine for tests and as a fallback.
    Swap in a real model (e.g. fastembed MiniLM) for quality."""

    spec = "hash"

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
        self.spec = f"fastembed:{model}"

    def embed(self, texts: list[str]) -> np.ndarray:
        v = np.array(list(self.model.embed(texts)), dtype=np.float32)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


def make_embedder(spec: str | None = None) -> Embedder:
    """`hash` (default) or `fastembed[:model]`; read from JEVMEM_EMBEDDER when spec is None."""
    spec = spec or os.environ.get("JEVMEM_EMBEDDER", "hash")
    if spec.startswith("fastembed"):
        return FastEmbedEmbedder(*spec.split(":", 1)[1:])
    return HashEmbedder()


class EmbedderMismatch(RuntimeError):
    """The database was built with a different embedder than the one requested."""


_LEGACY_DIMS = {256: "hash", 384: "fastembed"}   # databases created before the embedder was recorded


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
    created: float | None = None         # when it was written (not when the fact happened)
    branch: str | None = None            # git branch and commit the note was written on (None: unknown)
    commit: str | None = None


@dataclass
class Edge:
    src: int
    dst: int
    kind: str
    weight: float


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class Store:
    def __init__(self, path: str = ":memory:", embedder: Embedder | None = None, index: str | None = None,
                 switch_embedder: bool = False):
        self.db = sqlite3.connect(path, check_same_thread=False)  # MCP runs tools on worker threads; callers serialize
        self.db.row_factory = sqlite3.Row
        self.path = path
        self._index_spec = index
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
        CREATE TABLE IF NOT EXISTS node_entities(node_id INTEGER NOT NULL, entity TEXT NOT NULL,
          PRIMARY KEY(node_id, entity));
        CREATE TABLE IF NOT EXISTS judged(a INTEGER NOT NULL, b INTEGER NOT NULL, PRIMARY KEY(a, b));
        CREATE TABLE IF NOT EXISTS usage(node_id INTEGER PRIMARY KEY, hits INTEGER NOT NULL, last_hit REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS pair_queue(a INTEGER NOT NULL, b INTEGER NOT NULL, PRIMARY KEY(a, b));
        CREATE INDEX IF NOT EXISTS node_entities_e ON node_entities(entity);
        CREATE INDEX IF NOT EXISTS nodes_scope ON nodes(scope);
        CREATE INDEX IF NOT EXISTS nodes_ts ON nodes(ts);
        """)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(nodes)")}
        for col in ("git_branch", "git_commit"):          # databases made before notes recorded where they were written
            if col not in cols:
                self.db.execute(f"ALTER TABLE nodes ADD COLUMN {col} TEXT")
        self.embedder = embedder or self._choose_embedder(switch_embedder)
        self._backfill_entities()
        dim = int(self.embedder.embed(["dim"]).shape[1])
        self.index = make_index(self.db, dim, path, index, self.count())
        self.sync_index()
        if self.meta_text("embedder") is None:
            self.meta_set_text("embedder", getattr(self.embedder, "spec", "custom"))

    def _choose_embedder(self, switch: bool) -> Embedder:
        """The database remembers its embedder: use it unless the user asks for another one explicitly."""
        stored = self.meta_text("embedder")
        if stored is None and self.count():
            r = self.db.execute("SELECT emb FROM nodes WHERE emb IS NOT NULL LIMIT 1").fetchone()
            stored = _LEGACY_DIMS.get(len(r["emb"]) // 4) if r else None
        wanted = os.environ.get("JEVMEM_EMBEDDER")
        if not wanted:
            return make_embedder(stored or "hash")
        emb = make_embedder(wanted)
        same = stored is None or stored == emb.spec or stored == wanted.split(":")[0]
        if not same and not switch:
            raise EmbedderMismatch(
                f"this database was built with embedder '{stored}' but JEVMEM_EMBEDDER='{wanted}'. "
                f"Unset JEVMEM_EMBEDDER to keep using '{stored}', or run `jevmem reembed` to switch.")
        return emb

    # -- nodes ---------------------------------------------------------------
    def add_node(self, content: str, scope: str = "global", timestamp: float | None = None,
                 entities: list[str] | None = None, source: str | None = None, branch: str | None = None,
                 commit: str | None = None) -> int:
        emb = self.embedder.embed([content])[0].astype(np.float32)
        # never reuse the id of a deleted note: consolidation watermarks and edges refer to ids
        top = self.db.execute("SELECT COALESCE(MAX(id), 0) FROM nodes").fetchone()[0]
        nid = max(top, self.meta_get("max_node_id")) + 1
        self.db.execute(
            "INSERT INTO nodes(id,content,scope,ts,entities,source,emb,created,git_branch,git_commit) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (nid, content, scope, timestamp, json.dumps(entities or []), source, emb.tobytes(), time.time(),
             branch, commit))
        self.meta_set("max_node_id", nid)
        self.db.execute("INSERT INTO fts(rowid, content) VALUES(?,?)", (nid, content))
        self._index_entities(nid, entities or [])
        self.db.commit()
        self.index.add(nid, scope, emb)
        return nid

    def _backfill_entities(self) -> None:
        if self.db.execute("SELECT 1 FROM node_entities LIMIT 1").fetchone():
            return
        for r in self.db.execute("SELECT id, entities FROM nodes").fetchall():
            self._index_entities(r["id"], json.loads(r["entities"]))
        self.db.commit()

    def _index_entities(self, nid: int, entities: list[str]) -> None:
        self.db.executemany("INSERT OR IGNORE INTO node_entities VALUES(?,?)", [(nid, e.lower()) for e in entities])

    def _embeddings(self):
        for r in self.db.execute("SELECT id, scope, emb FROM nodes WHERE emb IS NOT NULL ORDER BY id"):
            yield r["id"], r["scope"], np.frombuffer(r["emb"], dtype=np.float32)

    def sync_index(self, force: bool = False) -> int | None:
        """Rebuild a derived index from SQLite when it is missing rows (or `force`). Returns rows indexed."""
        n = self.index.size()
        if n is None or (not force and n == self.count()):
            return None
        self.index.rebuild(self._embeddings())
        return self.count()

    def reembed(self) -> int:
        """Recompute every stored embedding with the current embedder (needed after switching embedders)."""
        rows = self.db.execute("SELECT id, content FROM nodes").fetchall()
        for i in range(0, len(rows), 64):
            chunk = rows[i:i + 64]
            embs = self.embedder.embed([r["content"] for r in chunk]).astype(np.float32)
            self.db.executemany("UPDATE nodes SET emb=? WHERE id=?", [(e.tobytes(), r["id"]) for e, r in zip(embs, chunk)])
        self.db.commit()
        self.meta_set_text("embedder", getattr(self.embedder, "spec", "custom"))
        self.sync_index(force=True)
        return len(rows)

    def set_type_scores(self, nid: int, scores: dict[str, float]) -> None:
        self.db.execute("UPDATE nodes SET type_scores=? WHERE id=?", (json.dumps(scores), nid))
        self.db.commit()

    def delete_node(self, nid: int) -> None:
        self.db.execute("DELETE FROM nodes WHERE id=?", (nid,))
        self.db.execute("DELETE FROM fts WHERE rowid=?", (nid,))
        self.db.execute("DELETE FROM node_entities WHERE node_id=?", (nid,))
        self.db.execute("DELETE FROM edges WHERE src=? OR dst=?", (nid, nid))
        self.db.execute("DELETE FROM pending WHERE node_id=?", (nid,))
        self.db.execute("DELETE FROM flags WHERE node_id=? OR other_id=?", (nid, nid))
        self.db.execute("DELETE FROM usage WHERE node_id=?", (nid,))
        for t in ("judged", "pair_queue"):
            self.db.execute(f"DELETE FROM {t} WHERE a=? OR b=?", (nid, nid))
        for it in self.synth_items():       # a proposal that mentions a deleted note is void
            if nid in it["node_ids"]:
                self.db.execute("UPDATE synth SET status='dismissed' WHERE id=?", (it["id"],))
        self.db.commit()
        self.index.remove(nid)

    @staticmethod
    def _node(r: sqlite3.Row) -> Node:
        return Node(r["id"], r["content"], r["scope"], r["ts"], json.loads(r["entities"]),
                    json.loads(r["type_scores"]) if r["type_scores"] else None, r["source"],
                    np.frombuffer(r["emb"], dtype=np.float32) if r["emb"] else None, r["created"],
                    r["git_branch"], r["git_commit"])

    def get(self, nid: int) -> Node | None:
        r = self.db.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
        return self._node(r) if r else None

    def _query(self, where: str = "", args: list | None = None, scopes: list[str] | None = None,
               order: str = "", limit: int | None = None) -> list[Node]:
        args, conds = list(args or []), [where] if where else []
        if scopes:
            conds.append(f"scope IN ({','.join('?' * len(scopes))})"); args += scopes
        sql = "SELECT * FROM nodes" + (" WHERE " + " AND ".join(conds) if conds else "")
        if order:
            sql += f" ORDER BY {order}"
        if limit:
            sql += " LIMIT ?"; args.append(limit)
        return [self._node(r) for r in self.db.execute(sql, args)]

    def nodes(self, scopes: list[str] | None = None, limit: int | None = None, newest_first: bool = False) -> list[Node]:
        """Full scans are for small/admin uses; hot paths use the targeted queries below."""
        return self._query(scopes=scopes, order="id DESC" if newest_first else "", limit=limit)

    def nodes_after(self, last_id: int, limit: int) -> list[Node]:
        return self._query("id > ?", [last_id], order="id", limit=limit)

    def nearest_in_time(self, ts: float, scopes: list[str] | None, n: int, exclude_id: int) -> list[Node]:
        before = self._query("ts IS NOT NULL AND ts <= ? AND id != ?", [ts, exclude_id], scopes, "ts DESC", n)
        after = self._query("ts IS NOT NULL AND ts >= ? AND id != ?", [ts, exclude_id], scopes, "ts ASC", n)
        return sorted({x.id: x for x in before + after}.values(), key=lambda x: abs(x.timestamp - ts))[:n]

    def max_timestamp(self, scopes: list[str] | None) -> float | None:
        q = f" AND scope IN ({','.join('?' * len(scopes))})" if scopes else ""
        return self.db.execute(f"SELECT MAX(ts) FROM nodes WHERE ts IS NOT NULL{q}", scopes or []).fetchone()[0]

    def nodes_of_type(self, types: tuple[str, ...], min_score: float, scopes: list[str] | None) -> list[Node]:
        cond = " OR ".join(f"json_extract(type_scores, '$.{t}') >= ?" for t in types)
        return self._query(f"type_scores IS NOT NULL AND ({cond})", [min_score] * len(types), scopes)

    def bump_usage(self, ids) -> None:
        """Count a note as useful once more (the prompt hook injected it, or recall judged it relevant)."""
        now = time.time()
        with self.db:
            self.db.executemany("INSERT INTO usage(node_id, hits, last_hit) VALUES(?, 1, ?) "
                                "ON CONFLICT(node_id) DO UPDATE SET hits = hits + 1, last_hit = excluded.last_hit",
                                [(i, now) for i in ids])

    def usage(self) -> dict[int, int]:
        return dict(self.db.execute("SELECT node_id, hits FROM usage").fetchall())

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

    # -- search ----------------------------------------------------------------
    def vector_search(self, text: str, scopes: list[str] | None, k: int,
                      exclude: set[int] = frozenset()) -> list[tuple[int, float]]:
        return self.index.search(self.embedder.embed([text])[0], scopes, k, exclude)

    def lexical_search(self, text: str, scopes: list[str] | None, k: int,
                       exclude: set[int] = frozenset()) -> list[tuple[int, float]]:
        toks = list(dict.fromkeys(tokens(text)))
        if not toks:
            return []
        match = " OR ".join(f'"{t}"' for t in toks)
        sql = "SELECT fts.rowid AS rowid, bm25(fts) AS s FROM fts"
        args: list = [match]
        if scopes:
            sql += " JOIN nodes n ON n.id = fts.rowid"
        sql += " WHERE fts MATCH ?"
        if scopes:
            sql += f" AND n.scope IN ({','.join('?' * len(scopes))})"; args += scopes
        rows = self.db.execute(sql + " ORDER BY s LIMIT ?", args + [k + len(exclude)]).fetchall()
        allowed = None
        out = [(r["rowid"], -r["s"]) for r in rows
               if r["rowid"] not in exclude and (allowed is None or r["rowid"] in allowed)]
        return out[:k]

    def by_entities(self, entities: list[str], scopes: list[str] | None,
                    exclude: set[int] = frozenset()) -> list[Node]:
        want = list({e.lower() for e in entities})
        if not want:
            return []
        q = ",".join("?" * len(want))
        sql = f"SELECT n.* FROM nodes n WHERE n.id IN (SELECT node_id FROM node_entities WHERE entity IN ({q}))"
        args = want
        if scopes:
            sql += f" AND n.scope IN ({','.join('?' * len(scopes))})"; args = args + scopes
        return [n for n in map(self._node, self.db.execute(sql, args)) if n.id not in exclude]

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

    def meta_text(self, key: str) -> str | None:
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def meta_set_text(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, value))
        self.db.commit()

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

    def clear_flags(self, *flags: str) -> None:
        self.db.execute(f"DELETE FROM flags WHERE flag IN ({','.join('?' * len(flags))})", flags)
        self.db.commit()

    def set_pinned(self, nid: int, pinned: bool = True) -> None:
        """A pinned note is one the user marked as always relevant: SessionStart injects it first."""
        if pinned:
            self.add_flag(nid, "pinned", nid, 1.0)
        else:
            self.db.execute("DELETE FROM flags WHERE node_id=? AND flag='pinned'", (nid,)); self.db.commit()

    def pinned(self) -> set[int]:
        return self.flagged("pinned")

    def stale(self) -> set[int]:
        return self.flagged(*STALE_FLAGS)

    def flags_of(self, nid: int) -> list[tuple[str, int, float]]:
        return [(r["flag"], r["other_id"], r["p"]) for r in
                self.db.execute("SELECT * FROM flags WHERE node_id=?", (nid,))]

    # -- consolidation pairs: which pairs were judged, and which co-retrieved pairs still wait for a judgement ----
    def mark_judged(self, a: int, b: int) -> None:
        a, b = sorted((a, b))
        self.db.execute("INSERT OR IGNORE INTO judged VALUES(?,?)", (a, b))
        self.db.execute("DELETE FROM pair_queue WHERE a=? AND b=?", (a, b))
        self.db.commit()

    def queue_pairs(self, pairs: list[tuple[int, int]], max_size: int) -> int:
        """Queue pairs not judged yet (up to `max_size` waiting in total). Returns how many were added."""
        room, added = max_size - self.db.execute("SELECT COUNT(*) FROM pair_queue").fetchone()[0], 0
        for a, b in pairs:
            a, b = sorted((a, b))
            if added >= room or self.db.execute("SELECT 1 FROM judged WHERE a=? AND b=?", (a, b)).fetchone():
                continue
            added += self.db.execute("INSERT OR IGNORE INTO pair_queue VALUES(?,?)", (a, b)).rowcount
        self.db.commit()
        return added

    def queued_pairs(self, limit: int) -> list[tuple[int, int]]:
        return [(r["a"], r["b"]) for r in self.db.execute("SELECT a, b FROM pair_queue ORDER BY rowid LIMIT ?", (limit,))]

    def clear_judged(self) -> None:
        self.db.execute("DELETE FROM judged"); self.db.execute("DELETE FROM pair_queue"); self.db.commit()

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
