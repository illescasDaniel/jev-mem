"""Live consolidation eval: write each labelled pair into a fresh store and run the real Consolidator.
Each pair runs twice, older fact written first ("fwd") and last ("rev"), because the note written last is not
always the newer fact (imports, backfilled notes).
Usage: uv run python evals/consolidation_eval.py [evals/consolidation.json ...] [--out rows.json]"""
import argparse, json, sys
from collections import Counter

from jevmem import Service
from jevmem.decider import JevDecider
from jevmem.write import parse_ts

EXPECT = {"superseded": {"superseded"}, "contradiction": {"contradiction"}, "duplicate": {"duplicate", "subsumed"},
          "subsumed": {"subsumed"}, "merge": {"none"}, "distinct": {"none"}, "pattern": {"promote"}}


def outcome(svc, old_id, new_id, p):
    """What consolidation did to the pair, from the old/new labels' point of view."""
    f = {(n, fl) for n in (old_id, new_id) for fl, _, _ in svc.store.flags_of(n)}
    if (old_id, "superseded_by") in f:
        return "superseded"
    if (new_id, "superseded_by") in f:
        return "superseded_wrong_way"
    if any(fl == "contradicts" for _, fl in f):
        return "contradiction"
    if any(fl == "duplicate_of" for _, fl in f):
        return "duplicate"
    if any(fl == "subsumed_by" for _, fl in f):
        short = old_id if len(p["old"]) <= len(p["new"]) else new_id
        return "subsumed" if (short, "subsumed_by") in f else "subsumed_wrong_way"
    if svc.consolidator.pending():
        return "promote"
    return "none"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data", nargs="*", default=["evals/consolidation.json"])
    ap.add_argument("--out")
    a = ap.parse_args()
    d = JevDecider()
    rows = []
    for path in a.data:
        for p in json.load(open(path))["pairs"]:
            for order in ("fwd", "rev"):
                svc = Service(":memory:", decider=d)
                notes = [("old", p["old"], p["old_ts"]), ("new", p["new"], p["new_ts"])]
                ids = {}
                for name, text, ts in (notes if order == "fwd" else notes[::-1]):
                    ids[name] = svc.store.add_node(text, "project:eval", parse_ts(ts))   # no write-time Jev calls
                svc.consolidator.run()
                got = outcome(svc, ids["old"], ids["new"], p)
                ok = got in EXPECT[p["label"]]
                rows.append({"set": path.rsplit("/", 1)[-1], "label": p["label"], "order": order, "got": got,
                             "ok": ok, "old": p["old"][:50], "new": p["new"][:50]})
                if not ok:
                    print(f"MISS {rows[-1]['set']:28} {p['label']:13} {order} got={got:22} {p['old'][:40]} | {p['new'][:40]}")
    for s in dict.fromkeys(r["set"] for r in rows):
        rs = [r for r in rows if r["set"] == s]
        print(f"\n{s}: {sum(r['ok'] for r in rs)}/{len(rs)} correct")
        for lab in EXPECT:
            lr = [r for r in rs if r["label"] == lab]
            if lr:
                print(f"  {lab:13} {sum(r['ok'] for r in lr)}/{len(lr)}  {dict(Counter(r['got'] for r in lr))}")
        fp = [r for r in rs if r["label"] in ("distinct", "merge") and r["got"] != "none"]
        print(f"  false flags on distinct/merge pairs: {len(fp)}")
    print(f"calls={d.calls} in={d.input_tokens} out={d.output_tokens} {d.latency_s:.1f}s", file=sys.stderr)
    if a.out:
        json.dump(rows, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
