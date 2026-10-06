"""MCP stdio server (MCPServer, mcp 2.x): memory tools only. Generic guardrails live in jev-mcp."""
from __future__ import annotations

import dataclasses
import functools
import threading

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .service import Service

mcp = MCPServer("jevmem")
_svc: Service | None = None
_lock = threading.Lock()   # sync tools run on any worker thread: one SQLite connection, one call at a time


def tool(fn):
    """`mcp.tool()` that serializes calls (concurrent tool calls never interleave on the shared connection) and
    reports a crash as a `ToolError`: the SDK hides any other exception from the client ("Error executing tool X")."""
    @functools.wraps(fn)
    def locked(*args, **kwargs):
        with _lock:
            try:
                return fn(*args, **kwargs)
            except ToolError:
                raise
            except Exception as e:
                raise ToolError(f"{type(e).__name__}: {e}") from e
    return mcp.tool()(locked)


def svc() -> Service:
    global _svc
    if _svc is None:
        _svc = Service()
    return _svc


def _scope(scope: str | None) -> str:
    """The given scope, else `JEVMEM_SCOPE`, else the project the server runs in (`project:<repository name>`, shared
    by all worktrees; the same default the hooks use). Say `global` explicitly for notes that apply everywhere."""
    from .hooks import default_scope
    return scope or default_scope(None)


@tool
def memory_write(content: str, scope: str | None = None, entities: list[str] | None = None,
                 timestamp: str | None = None, source: str | None = None, pinned: bool = False) -> dict:
    """Store ONE literal fact (decision, bugfix, convention, gotcha, preference, event).
    Use explicit entity names and ABSOLUTE dates (never 'yesterday'): the memory model reads literally.
    Content that looks like instructions to an agent is rejected. scope: 'global' (applies to every project: personal preferences, conventions) or
    e.g. 'project:<name>'; omitted = this project.
    pinned=True only when the user says this must always be remembered: pinned notes open every session."""
    r, rep = svc().write(content, _scope(scope), entities, timestamp, source)
    if pinned and r.node_id is not None and not r.rejected:
        svc().store.set_pinned(r.node_id)
    top = sorted(r.type_scores.items(), key=lambda kv: -kv[1])[:3]
    return {"node_id": r.node_id, "rejected": r.rejected, "reason": r.reason, "degraded": r.degraded,
            "top_types": top, "edges": [{"src": a, "dst": b, "kind": k, "p": round(p, 2)}
                                        for a, b, k, p in r.edges],
            "consolidation": dataclasses.asdict(rep) if rep else None}


def _hint(r) -> str | None:
    """Lite answered and says the evidence is incomplete: a missing link between facts is something only graph
    expansion (full mode) can find, and lite cannot tell which kind of gap it has."""
    if r.sufficient is False and r.stop_reason == "lite":
        return ("memory may be missing part of the answer. If the answer joins several facts, rerun with "
                "mode='full'; otherwise check the code/docs")
    return None


@tool
def memory_recall(query: str, scope: str | None = None, max_items: int = 8, mode: str | None = None) -> dict:
    """Adaptive recall. Check `sufficient`/`missing`: if sufficient is false, the memory may lack the answer,
    so verify in code/docs rather than assuming. Searches `scope` (default: this project) plus global; scope='all' searches every project.
    mode: "auto" (default: lite first, escalates to full when lite finds nothing or the question looks
    multi-hop/time-related; `stop_reason` says which ran), "full" (always multi-hop graph expansion: use it when you
    know the answer joins several facts) or "lite" (vector top-20 + one Jev call that filters them and judges
    sufficiency: ~4x cheaper than full, no multi-hop)."""
    r = svc().retriever.recall(query, svc().scopes(_scope(scope)), max_items, mode)
    return {"evidence": [{"id": e.id, "content": e.content, "timestamp": e.timestamp, "scope": e.scope,
                          "score": round(e.score, 2), "flags": e.flags} for e in r.evidence],
            "sufficient": r.sufficient, "missing": r.missing, "degraded": r.degraded,
            "stop_reason": r.stop_reason, "jev_calls": r.jev_calls, "hint": _hint(r)}


@tool
def memory_pin(node_id: int, pinned: bool = True) -> dict:
    """Pin (or unpin) a note the user wants in every session; SessionStart injects pinned notes first."""
    if svc().store.get(node_id) is None:
        return {"ok": False, "error": "no such note"}
    svc().store.set_pinned(node_id, pinned)
    return {"ok": True}


@tool
def memory_list(scope: str | None = None, limit: int = 30) -> list[dict]:
    """List stored memories (newest first) with their top memory types. scope: omitted = every scope."""
    nodes = svc().store.nodes(svc().scopes(scope), limit, newest_first=True)
    return [{"id": n.id, "scope": n.scope, "content": n.content, "timestamp": n.timestamp,
             "top_types": sorted((n.type_scores or {}).items(), key=lambda kv: -kv[1])[:2]} for n in nodes]


@tool
def memory_forget(node_id: int) -> dict:
    """Delete a memory and its edges."""
    existed = svc().store.get(node_id) is not None
    svc().store.delete_node(node_id)
    return {"deleted": existed}


@tool
def memory_pending_synthesis() -> list[dict]:
    """Merge/promote proposals from consolidation. YOU are the writer: for each item follow `instruction`,
    then call memory_resolve(synthesis_id, text), or memory_dismiss if the proposal is wrong."""
    return svc().consolidator.pending()


@tool
def memory_resolve(synthesis_id: int, text: str) -> dict:
    """Store your synthesized fact for a pending proposal. Source notes are kept (merged ones rank lower)."""
    return svc().consolidator.resolve(synthesis_id, text)


@tool
def memory_dismiss(synthesis_id: int) -> dict:
    """Reject a merge/promote proposal."""
    return svc().consolidator.dismiss(synthesis_id)


@tool
def memory_consolidate(all_notes: bool = False) -> dict:
    """Run a consolidation pass now (also runs automatically every 20 writes). all_notes=True re-judges every
    stored note from scratch (slow: one Jev call per note); use it once after upgrading jevmem."""
    c = svc().consolidator
    return dataclasses.asdict(c.rescan() if all_notes else c.run())


@tool
def memory_stats() -> dict:
    """Node counts, queued (degraded) writes and Jev usage for this server process."""
    return svc().stats()


@tool
def memory_flush_pending() -> dict:
    """Retry writes queued while Jev was unreachable."""
    return {"resolved": svc().writer.flush_pending()}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
