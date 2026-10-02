"""MCP stdio server (MCPServer, mcp 2.x): memory tools only. Generic guardrails live in jev-mcp."""
from __future__ import annotations

import dataclasses
import os

from mcp.server.mcpserver import MCPServer

from .service import Service

mcp = MCPServer("jevmem")
_svc: Service | None = None


def svc() -> Service:
    global _svc
    if _svc is None:
        _svc = Service()
    return _svc


def _scope(scope: str | None) -> str:
    return scope or os.environ.get("JEVMEM_SCOPE", "global")


@mcp.tool()
def memory_write(content: str, scope: str | None = None, entities: list[str] | None = None,
                 timestamp: str | None = None, source: str | None = None) -> dict:
    """Store ONE literal fact (decision, bugfix, convention, gotcha, preference, event).
    Use explicit entity names and ABSOLUTE dates (never 'yesterday'): the memory model reads literally.
    Content that looks like instructions to an agent is rejected. scope: 'global' or e.g. 'project:<name>'."""
    r = svc().writer.write(content, _scope(scope), entities, timestamp, source)
    top = sorted(r.type_scores.items(), key=lambda kv: -kv[1])[:3]
    return {"node_id": r.node_id, "rejected": r.rejected, "reason": r.reason, "degraded": r.degraded,
            "top_types": top, "edges": [{"src": a, "dst": b, "kind": k, "p": round(p, 2)}
                                        for a, b, k, p in r.edges]}


@mcp.tool()
def memory_recall(query: str, scope: str | None = None, max_items: int = 8) -> dict:
    """Adaptive recall. Check `sufficient`/`missing`: if sufficient is false, the memory may lack the answer,
    so verify in code/docs rather than assuming. Searches `scope` plus global."""
    r = svc().retriever.recall(query, [_scope(scope)] if (scope or os.environ.get("JEVMEM_SCOPE")) else None,
                               max_items)
    return {"evidence": [{"id": e.id, "content": e.content, "timestamp": e.timestamp, "scope": e.scope,
                          "score": round(e.score, 2)} for e in r.evidence],
            "sufficient": r.sufficient, "missing": r.missing, "degraded": r.degraded,
            "stop_reason": r.stop_reason, "jev_calls": r.jev_calls}


@mcp.tool()
def memory_list(scope: str | None = None, limit: int = 30) -> list[dict]:
    """List stored memories (newest first) with their top memory types."""
    nodes = sorted(svc().store.nodes(svc().scopes(scope)), key=lambda n: -n.id)[:limit]
    return [{"id": n.id, "scope": n.scope, "content": n.content, "timestamp": n.timestamp,
             "top_types": sorted((n.type_scores or {}).items(), key=lambda kv: -kv[1])[:2]} for n in nodes]


@mcp.tool()
def memory_forget(node_id: int) -> dict:
    """Delete a memory and its edges."""
    existed = svc().store.get(node_id) is not None
    svc().store.delete_node(node_id)
    return {"deleted": existed}


@mcp.tool()
def memory_stats() -> dict:
    """Node counts, queued (degraded) writes and Jev usage for this server process."""
    return svc().stats()


@mcp.tool()
def memory_flush_pending() -> dict:
    """Retry writes queued while Jev was unreachable."""
    return {"resolved": svc().writer.flush_pending()}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
