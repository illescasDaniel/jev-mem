import asyncio
import threading

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
