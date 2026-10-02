"""Adaptive retrieval: route -> anchors (RRF) -> budgeted expansion -> assess -> stop."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from .config import Config
from .decider import Decider, DeciderUnavailable
from .questions import anchor_questions, candidate_questions, needs_memory_questions, routing_questions, stop_questions
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


def largest_remainder(total: float, weights: dict[str, float]) -> dict[str, int]:
    raw = {k: total * w for k, w in weights.items()}
    out = {k: int(v) for k, v in raw.items()}
    left = int(round(total)) - sum(out.values())
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[:max(left, 0)]:
        out[k] += 1
    return out


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
    def recall(self, query: str, scopes: list[str] | None = None, k: int | None = None) -> RecallResult:
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

        def finish(reason: str) -> RecallResult:
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
            now = max((n.timestamp for n in self.store.nodes(scopes) if n.timestamp), default=None)
            while True:
                # assess
                if (calls >= cfg.max_jev_calls or time.time() - t0 > cfg.time_budget_s):
                    return finish("limit:calls/time")
                a = self.decider.ask(
                    {"query": query, "depth": depth, "evidence": self._ev_state(score, via, k)},
                    stop_questions()); calls += 1
                s, u, m, c = (a[x].p for x in ("evidence_sufficient", "continue_useful",
                                               "missing_evidence", "contradiction"))
                res.sufficient = s >= cfg.sufficient and m < cfg.cont_threshold and c < cfg.cont_threshold
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
        out = []
        for i in top:
            n = self.store.get(i)
            out.append({"content": n.content, "timestamp": iso(n.timestamp)})
        return out

    def _ev(self, i, sc, via) -> Evidence:
        n = self.store.get(i)
        return Evidence(i, n.content, sc, iso(n.timestamp), n.scope, via)

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
