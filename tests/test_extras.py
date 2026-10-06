import json
import sys

import pytest

from jevmem.extras import MissingExtra
from jevmem.store import EmbedderUnavailable, make_embedder
from jevmem.vectorindex import QdrantIndex


@pytest.fixture
def no_fastembed(monkeypatch):
    monkeypatch.setitem(sys.modules, "fastembed", None)   # makes `import fastembed` raise ModuleNotFoundError


def test_missing_backend_names_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "qdrant_client", None)
    with pytest.raises(MissingExtra) as e:
        QdrantIndex(":memory:", 8)
    assert "jevmem[qdrant]" in str(e.value) and e.value.extra == "qdrant"


def test_unavailable_fastembed_suggests_warmup_and_hash(no_fastembed):
    with pytest.raises(EmbedderUnavailable) as e:
        make_embedder("fastembed")
    assert "jevmem warmup" in str(e.value) and "JEVMEM_EMBEDDER=hash" in str(e.value)


def test_default_embedder_is_fastembed(monkeypatch, no_fastembed):
    monkeypatch.delenv("JEVMEM_EMBEDDER")
    with pytest.raises(EmbedderUnavailable):
        make_embedder()


def test_hook_reports_the_embedder_problem(no_fastembed, monkeypatch, capsys):
    from jevmem import hooks
    monkeypatch.setenv("JEVMEM_EMBEDDER", "fastembed")
    monkeypatch.setenv("JEVMEM_DB", ":memory:")
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("{}"))
    hooks.run("session-start")
    assert "jevmem warmup" in json.loads(capsys.readouterr().out)["systemMessage"]


def test_cli_exits_with_the_embedder_problem(no_fastembed, monkeypatch):
    from jevmem import cli
    monkeypatch.setenv("JEVMEM_EMBEDDER", "fastembed")
    monkeypatch.setenv("JEVMEM_DB", ":memory:")
    monkeypatch.setattr(sys, "argv", ["jevmem", "stats"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert "jevmem warmup" in str(e.value)


def test_auto_index_warns_when_sqlite_vec_is_unavailable(monkeypatch):
    import sqlite3
    from jevmem import vectorindex
    monkeypatch.setitem(sys.modules, "sqlite_vec", None)
    db = sqlite3.connect(":memory:")
    with pytest.warns(UserWarning, match="sqlite-vec"):
        idx = vectorindex.make_index(db, 8, ":memory:", "auto", n_nodes=vectorindex.AUTO_SWITCH)
    assert idx.name == "matrix"


def test_db_path_rejects_an_unexpanded_variable(monkeypatch):
    from jevmem.service import db_path
    monkeypatch.setenv("JEVMEM_DB", "${HOME}/.jevmem/x.db")
    with pytest.raises(ValueError, match="USERPROFILE"):
        db_path()
