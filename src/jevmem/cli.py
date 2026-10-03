from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from .service import Service
from .store import EmbedderMismatch


def env_scope(default: str | None = "global") -> str | None:
    """CLI default scope: JEVMEM_SCOPE when set (same as the MCP server and hooks), else `default`."""
    return os.environ.get("JEVMEM_SCOPE") or default


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


def import_markdown(svc: Service, files: list[Path], scope: str, max_chars: int = 500) -> int:
    """Seed from markdown files: one note per bullet or paragraph (text only; headings give context).
    jevmem never rewrites text, so for decision logs it is better to have your agent write notes; this is a
    quick bootstrap for short rule/convention files such as AGENTS.md or CLAUDE.md."""
    n = 0
    for f in files:
        heading, blocks, cur = "", [], []
        for line in f.read_text().splitlines():
            if line.startswith("#"):
                blocks.append((heading, cur)); cur = []; heading = line.lstrip("# ").strip()
            elif re.match(r"\s*([-*]|\d+\.)\s", line):
                blocks.append((heading, cur)); cur = [re.sub(r"^\s*([-*]|\d+\.)\s+", "", line)]
            elif line.strip():
                cur.append(line.strip())
            else:
                blocks.append((heading, cur)); cur = []
        blocks.append((heading, cur))
        for head, parts in blocks:
            text = " ".join(parts).strip()
            if len(text) < 25 or text.startswith("|") or text.startswith("```"):
                continue
            text = f"{head}: {text}" if head else text
            r, _ = svc.write(text[:max_chars], scope=scope, source=str(f))
            n += 0 if r.rejected else 1
            if r.rejected:
                print(f"skip {f.name}: {r.reason}: {text[:60]}")
    return n


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="jevmem")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write"); w.add_argument("content"); w.add_argument("--scope", default=env_scope())
    w.add_argument("--entity", action="append"); w.add_argument("--timestamp")
    r = sub.add_parser("recall"); r.add_argument("query"); r.add_argument("--scope", default=env_scope(None)); r.add_argument("-k", type=int, default=8); r.add_argument("--mode", choices=["full", "lite", "auto"])
    l = sub.add_parser("list"); l.add_argument("--scope", default=env_scope(None))
    sub.add_parser("stats"); sub.add_parser("reindex"); sub.add_parser("reembed"); sub.add_parser("flush"); sub.add_parser("consolidate"); sub.add_parser("pending")
    e = sub.add_parser("eval"); e.add_argument("--data", default="evals/orbit.json"); e.add_argument("-k", type=int, default=5); e.add_argument("--json"); e.add_argument("--embedder", help="hash | fastembed[:model]")
    ei = sub.add_parser("eval-injection"); ei.add_argument("--data", default="evals/injection.json")
    h = sub.add_parser("hook"); h.add_argument("event", choices=["session-start", "user-prompt"])
    f = sub.add_parser("forget"); f.add_argument("node_id", type=int)
    rs = sub.add_parser("resolve"); rs.add_argument("synthesis_id", type=int); rs.add_argument("text")
    ds = sub.add_parser("dismiss"); ds.add_argument("synthesis_id", type=int)
    im = sub.add_parser("import-markdown"); im.add_argument("files", nargs="+", type=Path); im.add_argument("--scope", default=env_scope())
    i = sub.add_parser("import-claude-memory"); i.add_argument("--root", default=str(Path.home() / ".claude" / "projects"))
    a = ap.parse_args(argv)
    if a.cmd == "eval":
        from .evalharness import run
        if a.embedder:
            os.environ["JEVMEM_EMBEDDER"] = a.embedder
        return run(a.data, a.k, a.json)
    if a.cmd == "eval-injection":
        from .evalharness import run_injection
        return run_injection(a.data)
    if a.cmd == "hook":
        from .hooks import run
        return run(a.event)
    try:
        svc = Service(switch_embedder=a.cmd == "reembed")
    except EmbedderMismatch as e:
        raise SystemExit(f"jevmem: {e}")
    if a.cmd == "write":
        res, _ = svc.write(a.content, a.scope, a.entity, a.timestamp)
        print(json.dumps({"node_id": res.node_id, "rejected": res.rejected, "reason": res.reason,
                          "edges": res.edges}, indent=2))
    elif a.cmd == "recall":
        res = svc.retriever.recall(a.query, svc.scopes(a.scope), a.k, a.mode)
        for e in res.evidence:
            when = f" [{e.timestamp[:10]}]" if e.timestamp else ""
            flags = f" <{' '.join(e.flags)}>" if e.flags else ""
            print(f"[{e.id}] {e.score:.2f} ({e.scope}){when}{flags} {e.content}")
        print(f"-- sufficient={res.sufficient} stop={res.stop_reason} jev_calls={res.jev_calls} degraded={res.degraded}")
    elif a.cmd == "list":
        for n in svc.store.nodes(svc.scopes(a.scope)):
            print(f"[{n.id}] ({n.scope}) {n.content}")
    elif a.cmd == "stats":
        print(json.dumps(svc.stats(), indent=2))
    elif a.cmd == "reembed":
        print(f"re-embedded {svc.store.reembed()} notes with {getattr(svc.store.embedder, 'spec', '?')}")
    elif a.cmd == "forget":
        existed = svc.store.get(a.node_id) is not None
        svc.store.delete_node(a.node_id)
        print("deleted" if existed else "no such note")
    elif a.cmd == "resolve":
        print(json.dumps(svc.consolidator.resolve(a.synthesis_id, a.text)))
    elif a.cmd == "dismiss":
        print(json.dumps(svc.consolidator.dismiss(a.synthesis_id)))
    elif a.cmd == "import-markdown":
        print("imported:", import_markdown(svc, a.files, a.scope))
    elif a.cmd == "reindex":
        n = svc.store.sync_index(force=True)
        print(f"index={svc.store.index.name} rows={'n/a (derived lazily)' if n is None else n}")
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
