"""Benchmark one VectorIndex at scale on synthetic clustered unit vectors (a stand-in for real embeddings: random
uniform vectors have no neighbourhood structure and make ANN look worse than it is).
Usage: python evals/bench_index.py SPEC [--n 1000000] [--dim 384] [--scopes 10] [--queries 100]
SPEC is a JEVMEM_INDEX value: matrix | sqlite-vec | qdrant:http://localhost:6333 | lancedb:/tmp/x | pgvector:<dsn>
Reports build time, scoped (1/scopes of the notes) and unscoped top-10 latency (p50/p95) and recall@10 against an
exact numpy scan, plus one incremental add. Queries are stored points plus noise."""
import argparse, json, os, sqlite3, sys, tempfile, time
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from jevmem.vectorindex import make_index  # noqa: E402


def gen(n, dim, centers=2000, seed=0):
    rng = np.random.default_rng(seed)
    c = rng.standard_normal((centers, dim)).astype(np.float32)
    out = np.empty((n, dim), np.float32)
    for s in range(0, n, 100_000):
        m = min(100_000, n - s)
        v = c[rng.integers(0, centers, m)] + 0.8 * rng.standard_normal((m, dim)).astype(np.float32)
        out[s:s + m] = v / np.linalg.norm(v, axis=1, keepdims=True)
    return out, rng


def exact(V, ids_mask, Q, k=10):
    idx = np.nonzero(ids_mask)[0]
    sims = V[idx] @ Q.T
    top = np.argpartition(-sims, k, axis=0)[:k]
    return [set(idx[top[:, j]].tolist()) for j in range(Q.shape[0])]


def wait_qdrant(ix):
    """Block until the optimizer has finished building HNSW segments (status green for 3 polls in a row)."""
    ok = 0
    for _ in range(7200):
        ok = ok + 1 if str(ix.c.get_collection(ix.col).status).lower().endswith("green") else 0
        if ok >= 3:
            return
        time.sleep(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec"); ap.add_argument("--n", type=int, default=1_000_000); ap.add_argument("--dim", type=int, default=384)
    ap.add_argument("--scopes", type=int, default=10); ap.add_argument("--queries", type=int, default=100)
    ap.add_argument("--no-build", action="store_true", help="reuse an index already built from the same --n/--dim (seeded)")
    a = ap.parse_args()
    V, rng = gen(a.n, a.dim)
    scope = rng.integers(0, a.scopes, a.n); names = [f"s{i}" for i in range(a.scopes)]
    Q = V[rng.integers(0, a.n, a.queries)] + 0.1 * rng.standard_normal((a.queries, a.dim)).astype(np.float32)
    Q /= np.linalg.norm(Q, axis=1, keepdims=True)
    t_truth = {"scoped": exact(V, scope == 3, Q), "all": exact(V, np.ones(a.n, bool), Q)}

    tmp = tempfile.mkdtemp(prefix="jevbench-", dir=os.environ.get("BENCH_TMP"))
    db_path = os.path.join(tmp, "bench.db")
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE nodes(id INTEGER PRIMARY KEY, scope TEXT, emb BLOB)")
    if not a.no_build:
        db.executemany("INSERT INTO nodes VALUES(?,?,?)", ((i, names[scope[i]], V[i].tobytes()) for i in range(a.n)))
        db.commit()
    db.row_factory = sqlite3.Row
    ix = make_index(db, a.dim, db_path, a.spec, a.n)
    rows = lambda: ((r["id"], r["scope"], np.frombuffer(r["emb"], np.float32))
                    for r in db.execute("SELECT id, scope, emb FROM nodes ORDER BY id"))
    t = time.time()
    if not a.no_build:
        ix.rebuild(rows())
    if ix.name == "qdrant":
        wait_qdrant(ix)
    build = time.time() - t
    ix.search(Q[0], ["s3"], 10)                                       # warm up (lazy loads)
    res = {"spec": a.spec.split(":")[0], "env": {k: v for k, v in os.environ.items() if k.startswith("JEVMEM_")}, "n": a.n, "dim": a.dim, "build_s": round(build, 1)}
    for label, scopes in (("scoped", ["s3"]), ("all", None)):
        lat, rec = [], []
        for j in range(a.queries):
            t = time.time(); got = ix.search(Q[j], scopes, 10); lat.append((time.time() - t) * 1000)
            rec.append(len(t_truth[label][j] & {i for i, _ in got}) / 10)
        res[label] = {"p50_ms": round(float(np.percentile(lat, 50)), 1), "p95_ms": round(float(np.percentile(lat, 95)), 1),
                      "recall@10": round(float(np.mean(rec)), 3)}
    t = time.time(); ix.add(a.n + 1, "s3", V[0]); res["add_ms"] = round((time.time() - t) * 1000, 1)
    print(json.dumps(res))
    import shutil; shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
