"""Shared wiring for the CLI and MCP server."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from .config import Config
from .decider import JevDecider
from .retrieve import Retriever
from .store import Store
from .write import Writer


def db_path() -> str:
    p = Path(os.environ.get("JEVMEM_DB", Path.home() / ".jevmem" / "memory.db"))
    p.parent.mkdir(parents=True, exist_ok=True)
    return str(p)


class Service:
    def __init__(self, path: str | None = None, decider=None, config: Config | None = None):
        load_dotenv(os.environ.get("JEVMEM_ENV_FILE") or Path.home() / ".jevmem" / ".env")
        load_dotenv()
        self.cfg = config or Config()
        self.store = Store(path or db_path())
        self.decider = decider or JevDecider()
        self.writer = Writer(self.store, self.decider, self.cfg)
        self.retriever = Retriever(self.store, self.decider, self.cfg)

    def scopes(self, scope: str | None) -> list[str] | None:
        return [scope] if scope and scope != "all" else None

    def stats(self) -> dict:
        d = self.decider
        return {"nodes": self.store.count(),
                "pending_unscreened": len(self.store.pending("unscreened")),
                "pending_relations": len(self.store.pending("relations")),
                "jev_calls_this_process": getattr(d, "calls", 0),
                "input_tokens": getattr(d, "input_tokens", 0)}
