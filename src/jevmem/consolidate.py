"""Periodic consolidation (paper 3.2, App. B.3). Jev judges; nothing is deleted or generated.

For each recently written note vs. a few older neighbours, one batched call asks: redundant? contradictory?
obsolete? worth linking? and which representation fits (keep_separate/merge/promote/uncertain).
Outcomes only annotate the graph: flags (contradicts, superseded_by), extra semantic edges, and a queue of
merge/promote proposals that the host agent (System Two) writes up via resolve()."""
from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .decider import Decider, DeciderUnavailable
from .questions import consolidation_questions
from .store import Store
from .write import UNSCREENED, Writer, iso


@dataclass
class ConsolidationReport:
    checked: int = 0
    proposals: int = 0
    contradictions: int = 0
    superseded: int = 0
    links: int = 0
    degraded: bool = False


class Consolidator:
    def __init__(self, store: Store, decider: Decider, writer: Writer, config: Config | None = None):
        self.store, self.decider, self.writer, self.cfg = store, decider, writer, config or Config()

    def due(self) -> bool:
        return self.store.meta_get("writes_since_consolidation") >= self.cfg.consolidate_every

    def run(self, max_nodes: int | None = None) -> ConsolidationReport:
        cfg, rep = self.cfg, ConsolidationReport()
        last = self.store.meta_get("last_consolidated_id")
        hidden = set(self.store.pending(UNSCREENED))
        todo = sorted((n for n in self.store.nodes() if n.id > last and n.id not in hidden),
                      key=lambda n: n.id)[:max_nodes or cfg.consolidate_max_nodes]
        for node in todo:
            scopes = [node.scope] if node.scope == "global" else [node.scope, "global"]
            ids = [i for i in self.writer._candidates(node, scopes) if i < node.id][:cfg.consolidate_candidates]
            cands = [self.store.get(i) for i in ids]
            if cands:
                state = {"new_memory": {"content": node.content, "timestamp": iso(node.timestamp)},
                         "candidates": [{"content": c.content, "timestamp": iso(c.timestamp)} for c in cands]}
                try:
                    ans = self.decider.ask(state, consolidation_questions(len(cands)))
                except DeciderUnavailable:
                    rep.degraded = True
                    return rep          # keep last_consolidated_id: retry these nodes next time
                for i, c in enumerate(cands):
                    self._apply(node, c, {k.split("_", 2)[2]: v for k, v in ans.items()
                                          if k.startswith(f"pair_{i}_")}, rep)
            rep.checked += 1
            self.store.meta_set("last_consolidated_id", node.id)
        self.store.meta_set("writes_since_consolidation", 0)
        return rep

    def _apply(self, new, old, a, rep: ConsolidationReport) -> None:
        th = self.cfg.consolidate_threshold
        contradiction = a["contradiction"].p >= th
        if contradiction:
            self.store.add_flag(new.id, "contradicts", old.id, a["contradiction"].p)
            self.store.add_flag(old.id, "contradicts", new.id, a["contradiction"].p)
            rep.contradictions += 1
        elif a["obsolescence"].p >= th:
            # Jev says "one outdates the other"; WHICH is older is decided in code, not by Jev
            both = new.timestamp is not None and old.timestamp is not None
            older, newer = (old, new) if (old.timestamp <= new.timestamp if both else old.id < new.id) else (new, old)
            self.store.add_flag(older.id, "superseded_by", newer.id, a["obsolescence"].p)
            rep.superseded += 1
        rep_ans = a["representation"]
        if (not contradiction and rep_ans.choice in ("merge", "promote")
                and rep_ans.probs.get(rep_ans.choice, 0.0) >= th):
            if self.store.add_synth(rep_ans.choice, [new.id, old.id], rep_ans.probs[rep_ans.choice]):
                rep.proposals += 1
        if a["link_usefulness"].p >= self.cfg.relation_threshold and not any(
                o == old.id for e, o in self.store.neighbors(new.id, ["semantic"])):
            self.store.add_edge(new.id, old.id, "semantic", a["link_usefulness"].p)
            rep.links += 1

    # -- System Two hand-off ------------------------------------------------------
    def pending(self) -> list[dict]:
        out = []
        for it in self.store.synth_items():
            nodes = [self.store.get(i) for i in it["node_ids"]]
            if any(n is None for n in nodes):
                self.store.synth_close(it["id"], "dismissed"); continue
            out.append({**it, "memories": [{"id": n.id, "content": n.content, "timestamp": iso(n.timestamp)}
                                           for n in nodes],
                        "instruction": ("Write ONE literal fact that combines these without losing any distinct "
                                        "detail. Use absolute dates and explicit entity names.") if it["kind"] == "merge"
                        else ("These repeated episodes suggest a stable pattern. Write ONE general, literal "
                              "statement of that pattern; the episodes are kept as evidence.")})
        return out

    def resolve(self, synth_id: int, text: str, scope: str | None = None) -> dict:
        item = self.store.synth_get(synth_id)
        if not item or item["status"] != "pending":
            return {"ok": False, "error": "no such pending synthesis"}
        srcs = [self.store.get(i) for i in item["node_ids"]]
        scopes = {n.scope for n in srcs}
        entities = sorted({e for n in srcs for e in n.entities})
        res = self.writer.write(text, scope or (scopes.pop() if len(scopes) == 1 else "global"),
                                entities, source=f"consolidation:{synth_id}")
        if res.rejected:
            return {"ok": False, "error": res.reason}
        for n in srcs:
            self.store.add_edge(res.node_id, n.id, "semantic", 1.0)
            if item["kind"] == "merge":     # raw notes stay, but the summary now outranks them
                self.store.add_flag(n.id, "merged_into", res.node_id, 1.0)
        self.store.synth_close(synth_id, "resolved", res.node_id)
        return {"ok": True, "node_id": res.node_id}

    def dismiss(self, synth_id: int) -> dict:
        item = self.store.synth_get(synth_id)
        if not item or item["status"] != "pending":
            return {"ok": False, "error": "no such pending synthesis"}
        self.store.synth_close(synth_id, "dismissed")
        return {"ok": True}
