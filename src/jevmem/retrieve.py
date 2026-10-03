"""Adaptive retrieval: route -> anchors (RRF) -> budgeted expansion -> assess -> stop."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from .config import Config
from .decide import MAX_ITEM_CHARS
from .decider import Decider, DeciderUnavailable
from .questions import anchor_questions, lite_escalation_questions, relevance_questions, candidate_questions, needs_memory_questions, routing_questions, stop_questions
from .store import EDGE_KINDS, Store
from .write import UNSCREENED, iso

DAY = 86400.0


@dataclass
class Evidence:
    id: int
    content: str
    score: float
    timestamp: str | None
    scope: str
    via: str = "anchor"
    flags: list[str] = field(default_factory=list)   # e.g. "superseded", "contradicts:12"


@dataclass
class RecallResult:
    evidence: list[Evidence]
    sufficient: bool | None = None        # None = not assessed (degraded or skipped)
    missing: float | None = None
    depth: int = 0
    jev_calls: int = 0
    degraded: bool = False
    stop_reason: str = ""
    routing: dict[str, float] = field(default_factory=dict)
    assess: dict[str, float] = field(default_factory=dict)   # last stop-check values (for threshold tuning)


def largest_remainder(total: float, weights: dict[str, float]) -> dict[str, int]:
    raw = {k: total * w for k, w in weights.items()}
    out = {k: int(v) for k, v in raw.items()}
    left = int(round(total)) - sum(out.values())
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[:max(left, 0)]:
        out[k] += 1
    return out


def escalation(lite: RecallResult, cfg: Config) -> str | None:
    """Why "auto" mode should rerun a lite result in full mode, or None to keep it."""
    if lite.degraded:
        return None
    if not lite.evidence:
        return "nothing_found"
    if lite.routing.get("multi_hop", 0.0) >= cfg.escalate_multi_hop:
        return "multi_hop"
    if lite.routing.get("temporal", 0.0) >= cfg.escalate_temporal:
        return "temporal"
    if cfg.escalate_insufficient and lite.sufficient is False:
        return "insufficient"
    return None


class Retriever:
    def __init__(self, store: Store, decider: Decider, config: Config | None = None):
        self.store, self.decider, self.cfg = store, decider, config or Config()

    def needs_memory(self, query: str) -> float:
        """Cheap gate for hooks: P(query depends on prior knowledge). Fails open (1.0)."""
        try:
            return self.decider.ask({"query": query}, needs_memory_questions())["needs_memory"].p
        except DeciderUnavailable:
            return 1.0

    # ------------------------------------------------------------------
    def recall(self, query: str, scopes: list[str] | None = None, k: int | None = None,
               mode: str | None = None) -> RecallResult:
        """mode (default `Config.recall_mode`, "auto"): "full" = route, graph expansion, stop rule; "lite": see recall_lite;
        "auto" = lite, rerun in full when `escalation` says so."""
        mode = mode or self.cfg.recall_mode
        if mode == "lite":
            return self.recall_lite(query, scopes, k)
        if mode == "auto":
            lite = self.recall_lite(query, scopes, k)
            hop, tmp = lite.routing.get("multi_hop", 0.0), lite.routing.get("temporal", 0.0)
            why = escalation(lite, self.cfg)
            if why is None:
                lite.stop_reason = "lite"
                return lite
            res = self.recall(query, scopes, k, mode="full")
            res.jev_calls += lite.jev_calls
            res.stop_reason = f"escalated:{why}>{res.stop_reason}"
            return res
        cfg, k = self.cfg, k or self.cfg.top_k
        scopes = scopes and list(dict.fromkeys([*scopes, "global"]))
        hidden = set(self.store.pending(UNSCREENED))
        t0, calls = time.time(), 0
        anchors = self._anchors(query, scopes, hidden)
        sims = {i: s for i, s in self._vec(query, scopes, hidden)}
        score = {i: sims.get(i, 0.0) for i, _ in anchors}
        via = {i: "anchor" for i in score}
        beam = [i for i, _ in anchors[:cfg.beam_width]]
        res = RecallResult([], routing={})

        old = self.store.stale()

        def finish(reason: str) -> RecallResult:
            for i in score:
                if i in old:
                    score[i] *= cfg.superseded_penalty
            top = sorted(score, key=lambda i: -score[i])[:k]
            res.evidence = [self._ev(i, score[i], via[i]) for i in top]
            res.jev_calls, res.stop_reason = calls, reason
            return res

        try:
            # route
            p = self.decider.ask({"query": query}, routing_questions()); calls += 1
            graphs = {g: p[g].p for g in EDGE_KINDS}
            res.routing = {**graphs, "multi_hop": p["multi_hop"].p, "recency": p["recency_importance"].p}
            active = {g: v for g, v in graphs.items() if v >= cfg.activation_threshold} or {"semantic": 1.0}
            tot = sum(v ** cfg.gamma for v in active.values())
            w = {g: v ** cfg.gamma / tot for g, v in active.items()}
            spare = max(cfg.graph_budget - cfg.min_graph_budget * len(active), 0)
            extra = largest_remainder(spare, w)
            budget = {g: cfg.min_graph_budget + extra[g] for g in active}
            max_depth = min(cfg.max_depth, max(1, math.ceil(cfg.max_depth * res.routing["multi_hop"])))
            recency = res.routing["recency"]

            # judge anchors: drop weak matches, blend judged relevance into their score
            judged = [i for i, _ in anchors[:cfg.beam_width * 2]]
            for start in range(0, len(judged), cfg.score_chunk):
                chunk = judged[start:start + cfg.score_chunk]
                a = self.decider.ask(
                    {"query": query, "candidates": [{"content": self.store.get(i).content} for i in chunk]},
                    anchor_questions(len(chunk))); calls += 1
                for j, i in enumerate(chunk):
                    rel = a[f"anchor_{j}_relevance"].p
                    if rel < cfg.min_relevance:
                        score.pop(i); via.pop(i)
                    else:
                        score[i] = (score[i] + rel) / 2
            for i in [i for i in score if i not in judged]:
                score.pop(i); via.pop(i)
            beam = [i for i in beam if i in score]

            visited, edges_seen, depth = set(beam), 0, 0
            now = self.store.max_timestamp(scopes)
            while True:
                # assess
                if (calls >= cfg.max_jev_calls or time.time() - t0 > cfg.time_budget_s):
                    return finish("limit:calls/time")
                a = self.decider.ask(
                    {"query": query, "depth": depth, "evidence": self._ev_state(score, via, k)},
                    stop_questions()); calls += 1
                s, u, m, c = (a[x].p for x in ("evidence_sufficient", "continue_useful",
                                               "missing_evidence", "contradiction"))
                res.assess = {"sufficient": s, "continue_useful": u, "missing": m, "contradiction": c}
                res.sufficient = s >= cfg.sufficient and m < cfg.missing_max and c < cfg.contradiction_max
                res.missing, res.depth = m, depth
                if res.sufficient:
                    return finish("sufficient")
                if u < cfg.cont_threshold:
                    return finish("no_expected_gain")
                if depth >= max_depth or len(visited) >= cfg.max_nodes or edges_seen >= cfg.max_edges:
                    return finish("limit:depth/nodes/edges")

                # expand
                cands: dict[int, tuple[float, str, int]] = {}   # id -> (edge weight, kind, via)
                for g in active:
                    used = 0
                    for nid in beam:
                        for e, other in self.store.neighbors(nid, [g]):
                            if used >= budget[g]:
                                break
                            used += 1; edges_seen += 1
                            if other in visited or other in hidden:
                                continue
                            if scopes and self.store.get(other).scope not in scopes:
                                continue
                            if other not in cands or e.weight > cands[other][0]:
                                cands[other] = (e.weight, g, nid)
                if not cands:
                    return finish("frontier_exhausted")
                scored = self._score_candidates(query, cands, score, via, k, graphs, now, recency)
                calls += scored[1]
                new = sorted(scored[0].items(), key=lambda kv: -kv[1])[:cfg.beam_width]
                for i, sc in new:
                    score[i] = sc; via[i] = cands[i][1]; visited.add(i)
                beam = [i for i, _ in new]
                depth += 1
        except DeciderUnavailable:
            res.degraded = True
            return finish("degraded")

    def recall_lite(self, query: str, scopes: list[str] | None = None, k: int | None = None) -> RecallResult:
        """Vector top-N, then ONE batched Jev relevance filter that also judges whether the candidates answer the
        query (`sufficient`). No routing, graph or multi-hop. Stale notes rank lower. Degrades to plain vector top-k
        (`sufficient` None) if Jev is down."""
        cfg, k = self.cfg, k or self.cfg.top_k
        scopes = scopes and list(dict.fromkeys([*scopes, "global"]))
        hidden = set(self.store.pending(UNSCREENED))
        cand = self.store.vector_search(query, scopes, cfg.lite_candidates, hidden)
        res = RecallResult([], stop_reason="lite")
        if not cand:
            return res
        judge, calls, hop, tmp, suff, miss = [], 0, 0.0, 0.0, 0.0, 1.0
        try:
            for start in range(0, len(cand), cfg.score_chunk * 3):      # 36 per call: one call at the default N=20
                chunk = cand[start:start + cfg.score_chunk * 3]
                a = self.decider.ask(
                    {"goal": query, "items": [{"content": self.store.get(i).content[:MAX_ITEM_CHARS]} for i, _ in chunk]},
                    {**relevance_questions(len(chunk)), **lite_escalation_questions()}); calls += 1
                hop, tmp = max(hop, a["multi_hop"].p), max(tmp, a["temporal"].p)
                suff, miss = max(suff, a["evidence_sufficient"].p), min(miss, a["missing_evidence"].p)
                judge += [(i, sim, a[f"item_{j}"].p) for j, (i, sim) in enumerate(chunk)]
        except DeciderUnavailable:
            res.degraded, res.stop_reason = True, "degraded"
            judge = [(i, sim, sim) for i, sim in cand]          # no judgement: rank by similarity alone
            rel_min = -1.0
        else:
            rel_min = cfg.min_relevance
        old = self.store.stale()
        scored = [(i, rel * (cfg.superseded_penalty if i in old else 1.0)) for i, _, rel in judge if rel >= rel_min]
        top = sorted(scored, key=lambda t: -t[1])[:k]
        res.evidence = [self._ev(i, s, "vector") for i, s in top]
        res.jev_calls, res.routing = calls, {"multi_hop": hop, "temporal": tmp}
        if not res.degraded:
            res.assess = {"sufficient": suff, "missing": miss, "multi_hop": hop, "temporal": tmp}
            res.sufficient = bool(res.evidence) and suff >= cfg.lite_sufficient and miss < cfg.lite_missing_max
            res.missing = miss
        return res

    # ------------------------------------------------------------------
    def _vec(self, query, scopes, hidden):
        return self.store.vector_search(query, scopes, self.cfg.anchor_count, hidden)

    def _anchors(self, query, scopes, hidden) -> list[tuple[int, float]]:
        rrf: dict[int, float] = {}
        lists = [self._vec(query, scopes, hidden),
                 self.store.lexical_search(query, scopes, self.cfg.anchor_count, hidden)]
        for lst in lists:
            for rank, (i, _) in enumerate(lst):
                rrf[i] = rrf.get(i, 0.0) + 1 / (self.cfg.rrf_k + rank + 1)
        return sorted(rrf.items(), key=lambda kv: -kv[1])[:self.cfg.anchor_count]

    def _ev_state(self, score, via, k):
        top = sorted(score, key=lambda i: -score[i])[:k]
        old = self.store.stale()
        out = []
        for i in top:
            n = self.store.get(i)
            item = {"content": n.content, "timestamp": iso(n.timestamp)}
            if i in old:
                item["status"] = "superseded"   # consolidation already resolved this conflict
            out.append(item)
        return out

    def _ev(self, i, sc, via) -> Evidence:
        n = self.store.get(i)
        names = {"superseded_by": "superseded", "merged_into": "merged", "duplicate_of": "duplicate",
                 "subsumed_by": "subsumed", "contradicts": "contradicts"}
        flags = [f"{names[f]}:{o}" for f, o, _ in self.store.flags_of(i) if f in names]
        return Evidence(i, n.content, sc, iso(n.timestamp), n.scope, via, flags)

    def _score_candidates(self, query, cands, score, via, k, graphs, now, recency):
        """Eq. 23 (+ recency eq. 24-25). Returns ({id: score}, jev_calls)."""
        q = self.store.embedder.embed([query])[0]
        ev = self._ev_state(score, via, k)
        ids, out, calls = list(cands), {}, 0
        for start in range(0, len(ids), self.cfg.score_chunk):
            chunk = ids[start:start + self.cfg.score_chunk]
            nodes = [self.store.get(i) for i in chunk]
            state = {"query": query, "evidence": ev, "candidates": [
                {"content": n.content, "timestamp": iso(n.timestamp), "relation": cands[i][1],
                 "via": self.store.get(cands[i][2]).content} for i, n in zip(chunk, nodes)]}
            a = self.decider.ask(state, candidate_questions(len(chunk))); calls += 1
            for j, (i, n) in enumerate(zip(chunk, nodes)):
                w, g, _ = cands[i]
                z = max(0.0, float(n.embedding @ q))
                s = (z + a[f"candidate_{j}_relevance"].p
                     + graphs[g] * a[f"candidate_{j}_usefulness"].p
                     + a[f"candidate_{j}_new_information"].p
                     + (w + a[f"candidate_{j}_supports"].p) / 2) / 5
                if now is not None and n.timestamp is not None:
                    rho = 1 / (1 + max(0.0, now - n.timestamp) / DAY)
                    s = (s + 0.1 * recency * rho) / (1 + 0.1 * recency)
                out[i] = s
        return out, calls
