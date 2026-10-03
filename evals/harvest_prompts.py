"""Collect the prompts a person typed in past Claude Code sessions, for the capture, hook and SessionStart evals.
Reads ~/.claude/projects/<dir matching PATTERN>/*.jsonl (the transcripts stay on your machine; the output goes to
evals/data/, which is gitignored).
Usage: uv run python evals/harvest_prompts.py SpaceMaker Jev-things  --out evals/data/prompts.json"""
import argparse, glob, json, os, re
from pathlib import Path


def text_of(msg: dict) -> str | None:
    c = msg.get("content")
    if isinstance(c, list):
        if any(b.get("type") == "tool_result" for b in c):
            return None
        c = "\n".join(b.get("text", "") for b in c if b.get("type") == "text")
    if not isinstance(c, str):
        return None
    m = re.search(r"<command-args>(.*?)</command-args>", c, re.S)       # a slash command: the typed part is its args
    if m:
        c = m.group(1)
    c = c.strip()
    if not c or c.startswith(("<", "Caveat:", "[Request interrupted", "This session is being continued", "Base directory for this skill")):
        return None
    return c


def harvest(patterns: list[str]) -> list[dict]:
    out = []
    root = Path.home() / ".claude" / "projects"
    for d in sorted(p for p in root.iterdir() if any(pat in p.name for pat in patterns)):
        for f in sorted(glob.glob(str(d / "*.jsonl"))):
            for line in open(f, encoding="utf-8", errors="replace"):
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("type") != "user" or o.get("isSidechain") or (o.get("origin") or {}).get("kind") not in (None, "human"):
                    continue
                t = text_of(o.get("message", {}))
                if t:
                    out.append({"session": o.get("sessionId"), "ts": o.get("timestamp"), "cwd": o.get("cwd"),
                                "branch": o.get("gitBranch"), "index": (o.get("turnPosition") or {}).get("promptIndex"),
                                "project": d.name, "text": t})
    seen, uniq = set(), []
    for r in sorted(out, key=lambda r: r["ts"] or ""):          # worktree sessions replay history: keep each prompt once
        k = (r["session"], r["text"])
        if k not in seen:
            seen.add(k); uniq.append(r)
    return uniq


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("patterns", nargs="+"); ap.add_argument("--out", default="evals/data/prompts.json")
    a = ap.parse_args()
    rows = harvest(a.patterns)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(rows, open(a.out, "w"), indent=1, ensure_ascii=False)
    print(f"{len(rows)} prompts from {len({r['session'] for r in rows})} sessions -> {a.out}")
