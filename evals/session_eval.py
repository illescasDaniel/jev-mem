"""SessionStart ranking eval. Gold: for the first prompt of each past session in a project, the notes of its scope
that Jev judges to be about the same subject (topic_questions, the hook's own filter). Then compare how many gold
notes the SessionStart ranking puts in its top 10 under different weights (git context, usage).
Usage: JEVMEM_ENV_FILE=.env JEVMEM_DB=<copy of the store> uv run python evals/session_eval.py --scope project:x \\
         --repo <checkout> --project SpaceMaker [--gold evals/data/session_gold.json]"""
import argparse, json, os
from pathlib import Path

import numpy as np

from jevmem import gitctx
from jevmem.config import Config
from jevmem.hooks import _boost, _git_vector, _TYPE_WEIGHT, _unit, rank_for_session
from jevmem.questions import topic_questions
from jevmem.service import Service


def first_prompts(project, limit):
    rows = json.load(open("evals/data/prompts.json"))
    seen, out = set(), []
    for r in rows:
        if project in (r["project"] or "") and r["session"] not in seen and len(r["text"]) >= 40:
            seen.add(r["session"]); out.append(r)
    return out[:limit]


def make_gold(svc, scope, prompts, path):
    gold = json.load(open(path)) if Path(path).exists() else {}
    notes = svc.store.nodes([scope])
    for p in prompts:
        if p["session"] in gold:
            continue
        ids = []
        for i in range(0, len(notes), 12):
            chunk = notes[i:i + 12]
            a = svc.decider.ask({"goal": p["text"], "items": [{"content": n.content} for n in chunk]},
                                topic_questions(len(chunk)))
            ids += [n.id for j, n in enumerate(chunk) if a[f"item_{j}"].p >= svc.cfg.hook_topic_min]
        gold[p["session"]] = {"ts": p["ts"], "gold": ids}
        json.dump(gold, open(path, "w"))
    return gold


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", required=True); ap.add_argument("--repo", required=True)
    ap.add_argument("--project", required=True); ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--gold", default="evals/data/session_gold.json")
    a = ap.parse_args()
    svc = Service(); svc.workdir = a.repo
    prompts = first_prompts(a.project, a.limit)
    gold = make_gold(svc, a.scope, prompts, a.gold)
    cases = [(g["ts"], set(g["gold"])) for g in gold.values() if g["gold"]]
    print(f"{len(prompts)} sessions, {len(cases)} with gold notes, "
          f"avg gold {np.mean([len(g) for _, g in cases]):.1f}, store {svc.store.count()} notes")
    vecs = {}
    for ts, _ in cases:
        until = ts.replace("T", " ")[:19]
        text = gitctx.recent_work(a.repo, until=until)
        vecs[ts] = _unit(np.asarray(svc.store.embedder.embed([text])[0], dtype=np.float32)) if text else None
    print("ctx_weight  hit@10  recall@10")
    for w in (0.0, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0):
        svc.cfg = Config(session_context_weight=w, session_usage_weight=0.0)
        hit = rec = 0.0
        for ts, g in cases:
            top = {n.id for n in rank_for_session(svc, a.scope, vecs[ts])[:10]}
            hit += bool(top & g); rec += len(top & g) / min(len(g), 10)
        print(f"{w:10.2f}  {hit / len(cases):6.3f}  {rec / len(cases):9.3f}")


if __name__ == "__main__":
    main()
