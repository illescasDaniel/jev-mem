"""Pooled sweep of the lite-mode sufficiency thresholds and of auto's escalate-on-insufficient, over several
`jevmem eval --json` outputs (uses the `jevmem-lite` and `jevmem` rows; no Jev calls).
Usage: python evals/sweep_lite.py out1.json out2.json ..."""
import itertools, json, sys
from statistics import mean

SUFF = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
MISS = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01]
MH, TEMP = 0.60, 0.80        # Config.escalate_multi_hop / escalate_temporal


def load(paths):
    return {p.rsplit("/", 1)[-1][:-5]: json.load(open(p)) for p in paths}


def pred(r, ts, tm):
    a = r["assess"]
    return bool(r["returned"]) and a is not None and a["sufficient"] >= ts and a["missing"] < tm


def score(rows, ts, tm):
    """fp: says sufficient but the gold is not all in its items (or there is no answer); fn: says insufficient
    though everything needed was returned."""
    fp = fn = 0
    for r in rows:
        truth = r["recall"] is not None and r["recall"] == 1
        p = pred(r, ts, tm)
        fp += p and not truth; fn += truth and not p
    return 1 - (fp + fn) / len(rows), fp, fn


def auto(sets, ts, tm, insufficient, x_lo=None):
    rec, calls, tok = [], [], []
    for d in sets.values():
        for lt, fu in zip(d["jevmem-lite"], d["jevmem"]):
            a = lt["assess"] or {}
            esc = not lt["returned"] or a.get("multi_hop", 0) >= MH or a.get("temporal", 0) >= TEMP \
                or (insufficient and lt["assess"] is not None and not pred(lt, ts, tm)) \
                or (x_lo is not None and lt["assess"] is not None and not pred(lt, ts, tm) and a.get("multi_hop", 0) >= x_lo)
            r = fu if esc else lt
            if r["recall"] is not None:
                rec.append(r["recall"])
            calls.append(lt["calls"] + (fu["calls"] if esc else 0)); tok.append(lt["tokens"] + (fu["tokens"] if esc else 0))
    return mean(rec), mean(calls), mean(tok)


def main(paths):
    sets = load(paths)
    pool = [r for d in sets.values() for r in d["jevmem-lite"] if r["assess"]]
    un = [r for r in pool if r["recall"] is None]
    print(f"pooled {len(pool)} lite answers from {len(sets)} sets; {sum(r['recall'] == 1 for r in pool)} fully "
          f"retrieved, {len(un)} unanswerable")
    res = sorted(((*score(pool, ts, tm), ts, tm) for ts, tm in itertools.product(SUFF, MISS)), key=lambda x: -x[0])
    for acc, fp, fn, ts, tm in res[:6]:
        print(f"  suff>={ts} miss<{tm}: acc={acc:.3f} fp={fp} fn={fn}  abstains on unanswerable "
              f"{mean(not pred(r, ts, tm) for r in un):.2f}")
    tot = 0
    for n in sets:
        rest = [r for m, d in sets.items() if m != n for r in d["jevmem-lite"] if r["assess"]]
        b = max(itertools.product(SUFF, MISS), key=lambda t: score(rest, *t)[0])
        tot += sum(score([r for r in sets[n]["jevmem-lite"] if r["assess"]], *b)[1:])
    print(f"leave-one-set-out errors: {tot}/{len(pool)} (acc {1 - tot / len(pool):.3f})")
    full = [r for d in sets.values() for r in d["jevmem"]]
    print(f"\nfull: recall {mean(r['recall'] for r in full if r['recall'] is not None):.3f} "
          f"calls {mean(r['calls'] for r in full):.2f} tokens {mean(r['tokens'] for r in full):.0f}")
    print(f"lite: recall {mean(r['recall'] for d in sets.values() for r in d['jevmem-lite'] if r['recall'] is not None):.3f}")
    for ts, tm in [(res[0][3], res[0][4]), (0.5, 0.6)]:
        for ins in (False, True):
            rc, c, t = auto(sets, ts, tm, ins)
            print(f"auto suff>={ts} miss<{tm} escalate_insufficient={ins}: recall {rc:.3f} calls {c:.2f} tokens {t:.0f}")
    print("combined rule: escalate if lite says insufficient AND multi_hop >= x_lo")
    for x_lo in (0.05, 0.1, 0.2, 0.3, 0.4, 0.5):
        rc, c, t = auto(sets, 0.5, 0.6, False, x_lo)
        print(f"  x_lo={x_lo}: recall {rc:.3f} calls {c:.2f} tokens {t:.0f}")


if __name__ == "__main__":
    main(sys.argv[1:])
