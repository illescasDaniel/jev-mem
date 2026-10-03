import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Tests must not depend on the developer's own jevmem configuration."""
    import os
    for k in [k for k in os.environ if k.startswith("JEVMEM_") and k != "JEVMEM_TEST_PG_DSN"]:
        monkeypatch.delenv(k)
