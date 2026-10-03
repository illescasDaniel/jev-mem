"""UserPromptSubmit injection eval: run the real hook on labelled prompts against a real store (autocapture off)
and score what it injects. on prompts: hit = a gold note injected; off prompts: anything injected is noise.
Usage: JEVMEM_DB=... JEVMEM_SCOPE=... uv run python evals/hook_eval.py evals/hook_prompts.json"""
import argparse, json, os

from jevmem.hooks import user_prompt
from jevmem.service import Service


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data"); ap.add_argument("--out")
    a = ap.parse_args()
    os.environ["JEVMEM_AUTOCAPTURE"] = "0"
    svc, rows = Service(), []
    notes = [(r["id"], " ".join(r["content"].split())) for r in svc.store.db.execute("SELECT id, content FROM nodes")]
    for p in json.load(open(a.data))["prompts"]:
        out = user_prompt(svc, {"prompt": p["prompt"]}) or ""
        lines = [ln[2:] for ln in out.splitlines() if ln.startswith("- ")]
        ids = [next((i for i, c in notes if c.startswith(ln[:60])), None) for ln in lines]
        ok = (not lines) if p["label"] == "off" else any(g in ids for g in p["gold"])
        rows.append({**p, "injected": ids, "n": len(lines), "ok": ok})
        print(f"{p['split']:7} {p['label']:3} {'ok ' if ok else 'BAD'} n={len(lines)} {ids}  {p['prompt'][:60]}")
    for split in ("tune", "holdout"):
        rs = [r for r in rows if r["split"] == split]
        on, off = [r for r in rs if r["label"] == "on"], [r for r in rs if r["label"] == "off"]
        print(f"{split}: on hit {sum(r['ok'] for r in on)}/{len(on)}, off clean {sum(r['ok'] for r in off)}/{len(off)}, "
              f"notes per on-prompt {sum(r['n'] for r in on) / max(len(on), 1):.1f}")
    print(f"calls={svc.decider.calls}")
    if a.out:
        json.dump(rows, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
