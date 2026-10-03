"""Optional dependencies (`jevmem[qdrant]`, `jevmem[lancedb]`, `jevmem[pgvector]`, `jevmem[all]`): fail with a message that says how to install them."""
from __future__ import annotations

from contextlib import contextmanager


class MissingExtra(RuntimeError):
    """An optional dependency is not installed; the message says which extra provides it."""

    def __init__(self, package: str, extra: str, why: str):
        self.package, self.extra = package, extra
        super().__init__(
            f"{package} is not installed ({why}). Install the '{extra}' extra: "
            f"uvx --from 'jevmem[{extra}]' ... (or pip install 'jevmem[{extra}]'; from a clone: uv sync --extra {extra})")


@contextmanager
def needs_extra(extra: str, why: str):
    """Wrap the import of an optional package; a missing one becomes a MissingExtra naming the extra to install."""
    try:
        yield
    except ModuleNotFoundError as e:
        if not e.name:
            raise
        raise MissingExtra(e.name.split(".")[0], extra, why) from e
