import asyncio
import threading

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from jevmem import FakeDecider, Service, mcp_server


def test_tools_work_when_the_store_was_opened_on_another_thread(monkeypatch, tmp_path):
    # The MCP framework runs sync tools on worker threads, never on the thread that opened the store.
    box = {}
    opener = threading.Thread(target=lambda: box.update(
        svc=Service(str(tmp_path / "m.db"), decider=FakeDecider(lambda s, k, q: 0.5))))
    opener.start(); opener.join()
    nid = box["svc"].store.add_node("SpaceMaker uses tabs.", "p")
    monkeypatch.setattr(mcp_server, "_svc", box["svc"])

    async def calls():
        await mcp_server.mcp.call_tool("memory_pin", {"node_id": nid})
        await asyncio.gather(*[mcp_server.mcp.call_tool("memory_list", {"scope": "p"}) for _ in range(8)])

    asyncio.run(calls())
    assert box["svc"].store.pinned() == {nid}


def test_a_crashing_tool_reports_the_exception_to_the_client(monkeypatch):
    class Boom:
        def stats(self):
            raise RuntimeError("database is locked")
    monkeypatch.setattr(mcp_server, "_svc", Boom())

    async def call():
        return await mcp_server.mcp.call_tool("memory_stats", {})

    with pytest.raises(ToolError, match="RuntimeError: database is locked"):
        asyncio.run(call())


def test_recall_hints_at_full_mode_when_lite_says_insufficient(monkeypatch):
    from jevmem.mcp_server import _hint
    from jevmem.retrieve import RecallResult
    assert "mode='full'" in _hint(RecallResult([], sufficient=False, stop_reason="lite"))
    assert _hint(RecallResult([], sufficient=False, stop_reason="sufficient")) is None
    assert _hint(RecallResult([], sufficient=True, stop_reason="lite")) is None


def test_notes_default_to_the_project_scope_and_global_stays_explicit(monkeypatch, tmp_path):
    repo = tmp_path / "shop"
    repo.mkdir()
    monkeypatch.setenv("JEVMEM_REPO", str(repo))
    svc = Service(str(tmp_path / "m.db"), decider=FakeDecider(lambda s, k, q: 0.05 if k == "injection" else 0.5))
    monkeypatch.setattr(mcp_server, "_svc", svc)

    async def calls():
        await mcp_server.mcp.call_tool("memory_write", {"content": "The shop uses Postgres for orders."})
        await mcp_server.mcp.call_tool("memory_write", {"content": "Python files use tabs.", "scope": "global"})
        await mcp_server.mcp.call_tool("memory_write", {"content": "Billing uses Stripe.", "scope": "Project:Billing"})

    asyncio.run(calls())
    assert {n.content: n.scope for n in svc.store.nodes()} == {
        "The shop uses Postgres for orders.": "project:shop", "Python files use tabs.": "global",
        "Billing uses Stripe.": "project:billing"}
    assert mcp_server._scope(None) == "project:shop"
    monkeypatch.setenv("JEVMEM_SCOPE", "project:other")        # an explicit setting still wins
    assert mcp_server._scope(None) == "project:other" and mcp_server._scope("global") == "global"
