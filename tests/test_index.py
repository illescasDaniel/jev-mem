import numpy as np
import pytest

from jevmem.store import Store, HashEmbedder

pytest.importorskip("sqlite_vec"); pytest.importorskip("qdrant_client")
SPECS = ["matrix", "sqlite-vec", "qdrant::memory:"]


def fill(spec):
    s = Store(":memory:", HashEmbedder(64), index=spec)
    for i in range(40):
        s.add_node(f"note {i} about topic{i % 7} thing{i}", "a" if i % 2 else "b", entities=[f"e{i % 5}"])
    return s


@pytest.mark.parametrize("spec", SPECS)
def test_index_matches_bruteforce(spec):
    ref, s = fill("matrix"), fill(spec)
    for q in ("topic3 thing10", "note 7"):
        # hash embeddings tie often, so compare the similarity values rather than the ids
        np.testing.assert_allclose([x for _, x in s.vector_search(q, None, 5)],
                                   [x for _, x in ref.vector_search(q, None, 5)], atol=1e-4)
        got = s.vector_search(q, ["a"], 5, exclude={2})
        assert got and all(s.get(i).scope == "a" and i != 2 for i, _ in got)


@pytest.mark.parametrize("spec", SPECS)
def test_delete_and_resync(spec):
    s = fill(spec)
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
