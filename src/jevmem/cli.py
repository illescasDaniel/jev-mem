from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .service import Service


def import_claude_memory(svc: Service, root: Path) -> int:
    """Seed from Claude Code memory files (frontmatter + body), one node per file."""
    n = 0
    for f in sorted(root.glob("**/memory/*.md")):
        if f.name == "MEMORY.md":
            continue
        text = f.read_text()
        body = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S).strip()
        proj = f.parent.parent.name  # encoded path like -home-me-code-my-app (lossy)
        base = Path.cwd().name
        # match hooks' scope (basename of cwd) when importing the current project's memory
        scope = f"project:{base}" if proj.endswith("-" + base) else f"project:{proj}"
        r, _ = svc.write(body, scope=scope, source=str(f))
        n += 0 if r.rejected else 1
        print(f"{'skip' if r.rejected else 'ok  '} {f} {r.reason or ''}")
    return n


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="jevmem")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write"); w.add_argument("content"); w.add_argument("--scope", default="global")
    w.add_argument("--entity", action="append"); w.add_argument("--timestamp")
    r = sub.add_parser("recall"); r.add_argument("query"); r.add_argument("--scope"); r.add_argument("-k", type=int, default=8)
    l = sub.add_parser("list"); l.add_argument("--scope")
    sub.add_parser("stats"); sub.add_parser("flush"); sub.add_parser("consolidate"); sub.add_parser("pending")
    e = sub.add_parser("eval"); e.add_argument("--data", default="evals/orbit.json"); e.add_argument("-k", type=int, default=5); e.add_argument("--json")
    ei = sub.add_parser("eval-injection"); ei.add_argument("--data", default="evals/injection.json")
    h = sub.add_parser("hook"); h.add_argument("event", choices=["session-start", "user-prompt"])
    i = sub.add_parser("import-claude-memory"); i.add_argument("--root", default=str(Path.home() / ".claude" / "projects"))
    a = ap.parse_args(argv)
    if a.cmd == "eval":
        from .evalharness import run
        return run(a.data, a.k, a.json)
    if a.cmd == "eval-injection":
        from .evalharness import run_injection
        return run_injection(a.data)
    if a.cmd == "hook":
        from .hooks import run
        return run(a.event)
    svc = Service()
    if a.cmd == "write":
        res, _ = svc.write(a.content, a.scope, a.entity, a.timestamp)
        print(json.dumps({"node_id": res.node_id, "rejected": res.rejected, "reason": res.reason,
                          "edges": res.edges}, indent=2))
    elif a.cmd == "recall":
        res = svc.retriever.recall(a.query, svc.scopes(a.scope), a.k)
        for e in res.evidence:
            print(f"[{e.id}] {e.score:.2f} ({e.scope}) {e.content}")
        print(f"-- sufficient={res.sufficient} stop={res.stop_reason} jev_calls={res.jev_calls} degraded={res.degraded}")
    elif a.cmd == "list":
        for n in svc.store.nodes(svc.scopes(a.scope)):
            print(f"[{n.id}] ({n.scope}) {n.content}")
    elif a.cmd == "stats":
        print(json.dumps(svc.stats(), indent=2))
    elif a.cmd == "consolidate":
        import dataclasses
        print(json.dumps(dataclasses.asdict(svc.consolidator.run()), indent=2))
    elif a.cmd == "pending":
        print(json.dumps(svc.consolidator.pending(), indent=2))
    elif a.cmd == "flush":
        print("resolved:", svc.writer.flush_pending())
    elif a.cmd == "import-claude-memory":
        print("imported:", import_claude_memory(svc, Path(a.root)))


if __name__ == "__main__":
    main()
