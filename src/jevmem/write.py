"""Write path: screen+type (1 call) -> insert -> candidates -> relations (1 batched call)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .config import Config
from .decider import Decider, DeciderUnavailable
from .questions import MEMORY_TYPES, relation_questions, typing_questions
from .screen import secret_kind
from .store import Store

UNSCREENED = "unscreened"   # hidden from retrieval until screened
RELATIONS = "relations"     # screened/typed; Jev relation pass still owed


def parse_ts(ts: float | str | None) -> float | None:
    if ts is None or isinstance(ts, (int, float)):
        return ts
    return datetime.fromisoformat(ts).timestamp()


def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts).isoformat(timespec="minutes") if ts is not None else None


@dataclass
class WriteResult:
    node_id: int | None
    rejected: bool = False
    reason: str | None = None
    type_scores: dict[str, float] = field(default_factory=dict)
    edges: list[tuple[int, int, str, float]] = field(default_factory=list)
    degraded: bool = False


class Writer:
    def __init__(self, store: Store, decider: Decider, config: Config | None = None):
        self.store, self.decider, self.cfg = store, decider, config or Config()

    def write(self, content: str, scope: str = "global", entities: list[str] | None = None,
              timestamp: float | str | None = None, source: str | None = None,
              dedupe: bool = True) -> WriteResult:
        content = content.strip()
        if not content:
            return WriteResult(None, rejected=True, reason="empty")
        kind = secret_kind(content)
        if kind:
            return WriteResult(None, rejected=True, reason=f"looks like a secret ({kind}); never store credentials")
        dup = self._duplicate(content, scope) if dedupe else None
        if dup is not None:
            return WriteResult(dup, rejected=True, reason=f"duplicate of note {dup}")
        ts = parse_ts(timestamp)
        entities = entities or []

        # 1. one batched call: type scores + injection screen
        try:
            ans = self.decider.ask({"observation": content}, typing_questions())
        except DeciderUnavailable:
            nid = self.store.add_node(content, scope, ts, entities, source)
            self.store.queue_pending(nid, UNSCREENED)
            return WriteResult(nid, degraded=True, reason="jev unavailable; stored unscreened and hidden")
        if ans["injection"].p >= self.cfg.injection_block:
            return WriteResult(None, rejected=True, reason=f"injection screen p={ans['injection'].p:.2f}")
        scores = {t: ans[t].p for t in MEMORY_TYPES}

        nid = self.store.add_node(content, scope, ts, entities, source)
        self.store.set_type_scores(nid, scores)
        res = WriteResult(nid, type_scores=scores)
        self._relate(nid, res)
        self.store.bump_writes()
        return res

    def _duplicate(self, content: str, scope: str) -> int | None:
        """Id of an existing note in this scope with the same text or a near-identical embedding."""
        hits = self.store.vector_search(content, [scope], 1)
        if not hits or hits[0][1] < self.cfg.duplicate_similarity:
            return None
        return hits[0][0] if self.store.get(hits[0][0]) is not None else None

    def _relate(self, nid: int, res: WriteResult) -> None:
        node = self.store.get(nid)
        scopes = [node.scope] if node.scope == "global" else [node.scope, "global"]
        cand_ids = self._candidates(node, scopes)
        cands = [self.store.get(i) for i in cand_ids]

        # deterministic edges: shared entity ids, timestamp order
        mine = {e.lower() for e in node.entities}
        for c in cands:
            if mine & {e.lower() for e in c.entities}:
                self.store.add_edge(nid, c.id, "entity"); res.edges.append((nid, c.id, "entity", 1.0))
            if node.timestamp is not None and c.timestamp is not None and node.timestamp != c.timestamp:
                a, b = (c.id, nid) if c.timestamp < node.timestamp else (nid, c.id)
                self.store.add_edge(a, b, "temporal"); res.edges.append((a, b, "temporal", 1.0))
        if not cands:
            return

        # 2. one batched call for every pair
        state = {"new_memory": {"content": node.content, "timestamp": iso(node.timestamp)},
                 "candidates": [{"content": c.content, "timestamp": iso(c.timestamp)} for c in cands]}
        try:
            ans = self.decider.ask(state, relation_questions(len(cands)))
        except DeciderUnavailable:
            self.store.queue_pending(nid, RELATIONS)
            res.degraded = True
            return
        th = self.cfg.relation_threshold
        for i, c in enumerate(cands):
            p = ans[f"pair_{i}_semantic"].p
            if p >= th:
                self.store.add_edge(nid, c.id, "semantic", p); res.edges.append((nid, c.id, "semantic", p))
            p = ans[f"pair_{i}_candidate_causes_new"].p
            if p >= th:
                self.store.add_edge(c.id, nid, "causal", p); res.edges.append((c.id, nid, "causal", p))
            p = ans[f"pair_{i}_new_causes_candidate"].p
            if p >= th:
                self.store.add_edge(nid, c.id, "causal", p); res.edges.append((nid, c.id, "causal", p))

    def _candidates(self, node, scopes: list[str]) -> list[int]:
        """Deterministic discovery: vector + lexical + entity + temporal proximity, top K_w."""
        k, ex = self.cfg.write_candidates, {node.id}
        score: dict[int, float] = {}
        for rank, (i, _) in enumerate(self.store.vector_search(node.content, scopes, k * 2, ex)):
            score[i] = score.get(i, 0) + 1 / (60 + rank)
        for rank, (i, _) in enumerate(self.store.lexical_search(node.content, scopes, k * 2, ex)):
            score[i] = score.get(i, 0) + 1 / (60 + rank)
        for c in self.store.by_entities(node.entities, scopes, ex):
            score[c.id] = score.get(c.id, 0) + 1 / 30
        if node.timestamp is not None:
            for n in self.store.nearest_in_time(node.timestamp, scopes, 3, node.id):
                score[n.id] = score.get(n.id, 0) + 1 / 90
        hidden = set(self.store.pending(UNSCREENED))
        return [i for i, _ in sorted(score.items(), key=lambda kv: -kv[1]) if i not in hidden][:k]

    def flush_pending(self) -> int:
        """Retry degraded writes once Jev is back. Returns how many were resolved."""
        done = 0
        for nid in self.store.pending():
            node = self.store.get(nid)
            if node is None:
                self.store.clear_pending(nid); continue
            if nid in self.store.pending(UNSCREENED):
                try:
                    ans = self.decider.ask({"observation": node.content}, typing_questions())
                except DeciderUnavailable:
                    return done
                if ans["injection"].p >= self.cfg.injection_block:
                    self.store.delete_node(nid); done += 1; continue
                self.store.set_type_scores(nid, {t: ans[t].p for t in MEMORY_TYPES})
            res = WriteResult(nid)
            self.store.clear_pending(nid)
            self._relate(nid, res)
            if not res.degraded:
                done += 1
        return done
