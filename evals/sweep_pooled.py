"""Pooled sufficiency sweep over several `jevmem eval --json` outputs.
Usage: python evals/sweep_pooled.py out1.json out2.json ...  (uses the `jevmem` rows)."""
import itertools, json, sys

SUFF = [0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.95]
MISS = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 1.01]
CONTR = [0.3, 0.4, 0.5, 0.6, 0.8, 1.01]


def load(paths):
    sets = {}
    for p in paths:
        rows = [r for r in json.load(open(p))["jevmem"] if r["assess"]]
        sets[p.rsplit("/", 1)[-1][:-5]] = rows
    return sets


def score(rows, ts, tm, tc):
    fp = fn = 0
    for r in rows:
        truth = r["recall"] is not None and r["recall"] == 1
        a = r["assess"]
        pred = a["sufficient"] >= ts and a["missing"] < tm and a["contradiction"] < tc
        fp += pred and not truth; fn += truth and not pred
    return 1 - (fp + fn) / len(rows), fp, fn


def main(paths):
    sets = load(paths)
    pool = [r for rs in sets.values() for r in rs]
    unans = [r for r in pool if r["recall"] is None]
    print(f"pooled {len(pool)} questions from {len(sets)} sets; {sum(r['recall'] == 1 for r in pool)} fully retrieved, {len(unans)} unanswerable")
    res = sorted(((*score(pool, ts, tm, tc), ts, tm, tc) for ts, tm, tc in itertools.product(SUFF, MISS, CONTR)),
                 key=lambda x: (-x[0], x[1], x[2]))
    for acc, fp, fn, ts, tm, tc in res[:8]:
        print(f"  suff>={ts} miss<{tm} contr<{tc}: acc={acc:.3f} fp={fp} fn={fn}")
    for ts, tm, tc in [(0.5, 0.6, 0.6)]:
        print("current", ts, tm, tc, "->", score(pool, ts, tm, tc))
        for n, rs in sets.items():
            print(f"    {n}: {score(rs, ts, tm, tc)}")
    best = res[0]
    print("best per set:", {n: tuple(round(x, 2) for x in score(rs, *best[3:])) for n, rs in sets.items()})
    # leave-one-set-out: fit on others, test on held-out
    tot = 0
    for n in sets:
        rest = [r for m, rs in sets.items() if m != n for r in rs]
        b = max(itertools.product(SUFF, MISS, CONTR), key=lambda t: (score(rest, *t)[0], -t[1]))
        a, fp, fn = score(sets[n], *b); tot += (fp + fn)
    print(f"leave-one-set-out errors: {tot}/{len(pool)} (acc {1 - tot / len(pool):.3f})")


if __name__ == "__main__":
    main(sys.argv[1:])
