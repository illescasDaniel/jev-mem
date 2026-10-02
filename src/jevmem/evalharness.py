"""Eval: vector top-k vs hybrid (vector+BM25) top-k vs jevmem recall on a note/question dataset.
Also sweeps the sufficiency thresholds against ground truth (answerable & fully retrieved)."""
from __future__ import annotations

import itertools
import json
import time
from dataclasses import dataclass, field
from statistics import mean

from .config import Config
from .retrieve import Retriever
from .service import Service

SWEEP_SUFF = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95)
SWEEP_MISS = (0.15, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)


@dataclass
class Row:
    q: str
    kind: str
    gold: set[int]
    returned: list[int]
    chars: int
    latency: float
    calls: int = 0
    assess: dict = field(default_factory=dict)
    sufficient: bool | None = None

    @property
    def recall(self) -> float:
        return len(self.gold & set(self.returned)) / len(self.gold) if self.gold else float("nan")

    @property
    def precision(self) -> float:
        return len(self.gold & set(self.returned)) / len(self.returned) if self.returned else 0.0


def build(svc: Service, data: dict) -> dict[str, int]:
    ids = {}
    for n in sorted(data["notes"], key=lambda n: n["timestamp"]):
        r, _ = svc.write(n["content"], data["scope"], n.get("entities"), n.get("timestamp"))
        if r.rejected or r.node_id is None:
            print(f"  note {n['key']} rejected: {r.reason}")
        else:
            ids[n["key"]] = r.node_id
    return ids


def evaluate(svc: Service, data: dict, ids: dict[str, int], k: int = 5) -> dict[str, list[Row]]:
    scopes = [data["scope"], "global"]
    ret: Retriever = svc.retriever
    out: dict[str, list[Row]] = {"vector": [], "hybrid": [], "jevmem": []}
    text = lambda i: svc.store.get(i).content
    for item in data["questions"]:
        q, gold = item["q"], {ids[g] for g in item["gold"] if g in ids}
        for name in ("vector", "hybrid"):
            t = time.time()
            got = ([i for i, _ in svc.store.vector_search(q, scopes, k)] if name == "vector"
                   else [i for i, _ in ret._anchors(q, scopes, set())[:k]])
            out[name].append(Row(q, item["kind"], gold, got, sum(len(text(i)) for i in got), time.time() - t))
        t, c0 = time.time(), svc.decider.calls
        r = ret.recall(q, [data["scope"]], k)
        got = [e.id for e in r.evidence]
        out["jevmem"].append(Row(q, item["kind"], gold, got, sum(len(e.content) for e in r.evidence),
                                 time.time() - t, svc.decider.calls - c0, r.assess, r.sufficient))
    return out


def summarize(rows: dict[str, list[Row]]) -> str:
    lines = [f"{'method':8} {'recall':>7} {'exact':>6} {'prec':>6} {'items':>6} {'chars':>6} {'latency':>8} {'calls':>6}"]
    for name, rs in rows.items():
        a = [r for r in rs if r.gold]
        lines.append(f"{name:8} {mean(r.recall for r in a):7.2f} {mean(r.recall == 1 for r in a):6.2f} "
                     f"{mean(r.precision for r in a):6.2f} {mean(len(r.returned) for r in rs):6.1f} "
                     f"{mean(r.chars for r in rs):6.0f} {mean(r.latency for r in rs):7.2f}s {mean(r.calls for r in rs):6.1f}")
    un = [r for r in rows["jevmem"] if not r.gold]
    lines.append(f"\nunanswerable ({len(un)}): items returned avg  vector={mean(len(r.returned) for r in rows['vector'] if not r.gold):.1f}"
                 f"  hybrid={mean(len(r.returned) for r in rows['hybrid'] if not r.gold):.1f}"
                 f"  jevmem={mean(len(r.returned) for r in un):.1f};  jevmem abstained (sufficient!=True): "
                 f"{mean(r.sufficient is not True for r in un):.2f}")
    by = {}
    for r in rows["jevmem"]:
        by.setdefault(r.kind, []).append(r)
    lines.append("jevmem by kind (recall): " + ", ".join(
        f"{kd}={mean(x.recall for x in rs):.2f}" for kd, rs in by.items() if kd != "unanswerable"))
    return "\n".join(lines)


def sweep(rows: list[Row]) -> str:
    """Ground truth: sufficient should be True iff the question is answerable AND jevmem retrieved all gold."""
    rs = [r for r in rows if r.assess]
    truth = [bool(r.gold) and r.recall == 1 for r in rs]
    cfg, res = Config(), []
    for ts, tm in itertools.product(SWEEP_SUFF, SWEEP_MISS):
        pred = [r.assess["sufficient"] >= ts and r.assess["missing"] < tm
                and r.assess["contradiction"] < cfg.contradiction_max for r in rs]
        acc = mean(p == t for p, t in zip(pred, truth))
        fp = sum(p and not t for p, t in zip(pred, truth)); fn = sum(t and not p for p, t in zip(pred, truth))
        res.append((acc, ts, tm, fp, fn))
    res.sort(key=lambda x: (-x[0], x[3], -x[2]))
    cur = next((x for x in res if x[1] == cfg.sufficient and x[2] == cfg.missing_max), res[0])
    lines = [f"\nsufficiency sweep over {len(rs)} questions (positives={sum(truth)}); contradiction<{cfg.contradiction_max} fixed; "
             "fp = said sufficient but shouldn't, fn = missed a sufficient one",
             f"  current defaults  suff>={cfg.sufficient} missing<{cfg.missing_max}: acc={cur[0]:.2f} fp={cur[3]} fn={cur[4]}"]
    lines += [f"  top: suff>={ts} missing<{tm}: acc={acc:.2f} fp={fp} fn={fn}" for acc, ts, tm, fp, fn in res[:4]]
    return "\n".join(lines)


def run(path: str, k: int = 5, out_json: str | None = None) -> None:
    data = json.load(open(path))
    svc = Service(":memory:")
    t = time.time()
    ids = build(svc, data)
    print(f"built {len(ids)} notes in {time.time() - t:.1f}s ({svc.decider.calls} Jev calls incl. consolidation)\n")
    rows = evaluate(svc, data, ids, k)
    print(summarize(rows)); print(sweep(rows["jevmem"]))
    if out_json:
        json.dump({n: [{"q": r.q, "kind": r.kind, "recall": None if not r.gold else r.recall,
                        "returned": r.returned, "assess": r.assess, "sufficient": r.sufficient,
                        "latency": r.latency, "calls": r.calls} for r in rs] for n, rs in rows.items()},
                  open(out_json, "w"), indent=1)
