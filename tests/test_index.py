import numpy as np
import pytest

from jevmem.store import Store, HashEmbedder

pytest.importorskip("sqlite_vec"); pytest.importorskip("qdrant_client")
pytest.importorskip("lancedb"); pytest.importorskip("pgvector")
SPECS = ["matrix", "sqlite-vec", "qdrant::memory:", "lancedb", "pgvector"]
_pg = None


def resolve(spec, tmp_path):
    """Concrete JEVMEM_INDEX string. pgvector runs against an embedded Postgres (pgserver) or JEVMEM_TEST_PG_DSN."""
    global _pg
    if spec == "lancedb":
        return f"lancedb:{tmp_path / 'lance'}"
    if spec == "pgvector":
        import os
        if os.environ.get("JEVMEM_TEST_PG_DSN"):
            dsn = os.environ["JEVMEM_TEST_PG_DSN"]
        else:
            pgserver = pytest.importorskip("pgserver")
            _pg = _pg or pgserver.get_server(tmp_path.parent / "pgdata")
            dsn = _pg.get_uri()
        import psycopg
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute("DROP TABLE IF EXISTS jevmem_vec")
        return f"pgvector:{dsn}"
    return spec


def fill(spec, tmp_path=None):
    spec = resolve(spec, tmp_path)
    s = Store(":memory:", HashEmbedder(64), index=spec)
    for i in range(40):
        s.add_node(f"note {i} about topic{i % 7} thing{i}", "a" if i % 2 else "b", entities=[f"e{i % 5}"])
    return s


@pytest.mark.parametrize("spec", SPECS)
def test_index_matches_bruteforce(spec, tmp_path):
    ref, s = fill("matrix"), fill(spec, tmp_path)
    for q in ("topic3 thing10", "note 7"):
        # hash embeddings tie often, so compare the similarity values rather than the ids
        np.testing.assert_allclose([x for _, x in s.vector_search(q, None, 5)],
                                   [x for _, x in ref.vector_search(q, None, 5)], atol=1e-4)
        got = s.vector_search(q, ["a"], 5, exclude={2})
        assert got and all(s.get(i).scope == "a" and i != 2 for i, _ in got)
        # k larger than the scope: must return every note in it (pgvector falls back to an exact scan here)
        every = s.vector_search(q, ["a"], 30)
        assert sorted(i for i, _ in every) == sorted(n.id for n in s.nodes(["a"]))


@pytest.mark.parametrize("spec", SPECS)
def test_delete_and_resync(spec, tmp_path):
    s = fill(spec, tmp_path)
    top = s.vector_search("topic3 thing10", None, 1)[0][0]
    s.delete_node(top)
    assert top not in [i for i, _ in s.vector_search("topic3 thing10", None, 5)]
    if s.index.size() is not None:                 # derived index lost rows -> resync from SQLite
        s.index.rebuild([])
        assert s.sync_index() == s.count() and s.index.size() == s.count()


def test_targeted_queries():
    s = fill("matrix")
    assert {n.entities[0] for n in s.by_entities(["E1"], ["a"])} == {"e1"}
    assert s.lexical_search("topic3", ["a"], 5) and all(s.get(i).scope == "a" for i, _ in s.lexical_search("topic3", ["a"], 5))


def test_lancedb_builds_ann_index_when_large(tmp_path, monkeypatch):
    from jevmem.vectorindex import LanceDBIndex
    monkeypatch.setenv("JEVMEM_LANCE_ANN_MIN", "500")
    rng = np.random.default_rng(1)
    centers = rng.standard_normal((20, 64))
    V = centers[rng.integers(0, 20, 2000)] + 0.3 * rng.standard_normal((2000, 64))
    V = (V / np.linalg.norm(V, axis=1, keepdims=True)).astype(np.float32)
    ix = LanceDBIndex(str(tmp_path / "l"), 64)
    ix.rebuild((i, "a" if i % 2 else "b", V[i]) for i in range(2000))
    assert ix._indexed() and ix.size() == 2000
    hits = []
    for j in (3, 50, 700, 1500):
        q = V[j] + 0.05 * rng.standard_normal(64).astype(np.float32)
        truth = np.nonzero(np.arange(2000) % 2 == 1)[0]
        truth = set(truth[np.argsort(-(V[truth] @ q))[:10]].tolist())
        hits.append(len(truth & {i for i, _ in ix.search(q, ["a"], 10)}) / 10)
    assert np.mean(hits) >= 0.8                      # approximate, but finds most true neighbours
