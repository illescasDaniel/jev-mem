"""Periodic consolidation (paper 3.2, App. B.3). Jev judges; nothing is deleted or generated.

For each recently written note vs. a few older neighbours, one batched call asks order-neutral questions: same
question with a different answer? one outdates the other? does either state every fact of the other? worth
linking? repeated episodes of one pattern? Code then decides from timestamps which note is older.
Outcomes only annotate the graph: flags (superseded_by, contradicts, duplicate_of, subsumed_by), extra semantic
edges, and a queue of promote proposals that the host agent (System Two) writes up via resolve()."""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass

from .config import Config
from .decider import Decider, DeciderUnavailable
from .questions import consolidation_questions
from .store import Store
from .write import UNSCREENED, Writer, iso


CONSOLIDATION_FLAGS = ("superseded_by", "contradicts", "duplicate_of", "subsumed_by")


@dataclass
class ConsolidationReport:
    checked: int = 0
    proposals: int = 0
    contradictions: int = 0
    superseded: int = 0
    duplicates: int = 0
    subsumed: int = 0
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
        cap = max_nodes or cfg.consolidate_max_nodes
        todo = [n for n in self.store.nodes_after(last, cap + len(hidden)) if n.id not in hidden][:cap]
        for node in todo:
            scopes = [node.scope] if node.scope == "global" else [node.scope, "global"]
            ids = [i for i in self.writer._candidates(node, scopes) if i < node.id][:cfg.consolidate_candidates]
            if ids and not self._judge(node, [self.store.get(i) for i in ids], rep):
                return rep          # keep last_consolidated_id: retry these nodes next time
            rep.checked += 1
            self.store.meta_set("last_consolidated_id", node.id)
        if not self._drain_queue(rep):
            return rep
        self.store.meta_set("writes_since_consolidation", 0)
        return rep

    def _judge(self, node, cands: list, rep: ConsolidationReport) -> bool:
        """One batched Jev call comparing `node` with each candidate. False if Jev is unavailable."""
        state = {"new_memory": {"content": node.content, "timestamp": iso(node.timestamp)},
                 "candidates": [{"content": c.content, "timestamp": iso(c.timestamp)} for c in cands]}
        try:
            ans = self.decider.ask(state, consolidation_questions(len(cands)))
        except DeciderUnavailable:
            rep.degraded = True
            return False
        for i, c in enumerate(cands):
            self._apply(node, c, {k.split("_", 2)[2]: v for k, v in ans.items() if k.startswith(f"pair_{i}_")}, rep)
            self.store.mark_judged(node.id, c.id)
        return True

    def _drain_queue(self, rep: ConsolidationReport) -> bool:
        """Judge pairs that recall returned together (see `Retriever._queue_coretrieved`): the newer note of each pair
        is the "new memory", its queued partners the candidates. False if Jev is unavailable."""
        groups: dict[int, list[int]] = {}
        for a, b in self.store.queued_pairs(self.cfg.pair_queue_per_pass):
            groups.setdefault(b, []).append(a)              # b > a: the later-written note
        for newer, olds in groups.items():
            node = self.store.get(newer)
            for start in range(0, len(olds), self.cfg.consolidate_candidates):
                cands = [self.store.get(i) for i in olds[start:start + self.cfg.consolidate_candidates]]
                if node is None or any(c is None for c in cands):
                    continue
                if not self._judge(node, cands, rep):
                    return False
        return True

    def rescan(self) -> ConsolidationReport:
        """Re-judge every note from scratch, e.g. after upgrading the consolidation questions.
        Clears the flags consolidation itself set (not merged_into, which records an agent's resolve)."""
        self.store.clear_flags(*CONSOLIDATION_FLAGS)
        self.store.clear_judged()
        self.store.meta_set("last_consolidated_id", 0)
        total = ConsolidationReport()
        while True:
            rep = self.run()
            for k, v in dataclasses.asdict(rep).items():
                setattr(total, k, v if k == "degraded" else getattr(total, k) + v)
            if rep.degraded or rep.checked == 0:
                return total

    def _apply(self, new, old, a, rep: ConsolidationReport) -> None:
        cfg = self.cfg
        change = max(a["new_reports_change"].p, a["candidate_reports_change"].p)
        p_conflict = max(min(a["same_question"].p, a["different_answer"].p), a["outdated"].p, change)
        conflict = ((a["same_question"].p >= cfg.conflict_same and a["different_answer"].p >= cfg.conflict_different)
                    or a["outdated"].p >= cfg.conflict_outdated or change >= cfg.conflict_change)
        new_covers, old_covers = (a["new_covers"].p >= cfg.cover_threshold,
                                  a["candidate_covers"].p >= cfg.cover_threshold)
        if conflict:
            ordered = _older_newer(new, old)
            if ordered is None:          # nothing orders them in time: the wording may (one note reports the change)
                n_chg, o_chg = a["new_reports_change"].p, a["candidate_reports_change"].p
                if n_chg >= cfg.conflict_change and o_chg <= cfg.tie_other_max:
                    ordered = (old, new)
                elif o_chg >= cfg.conflict_change and n_chg <= cfg.tie_other_max:
                    ordered = (new, old)
            if ordered:                  # Jev only says "these clash"; which one is older is decided here
                older, newer = ordered
                self.store.add_flag(older.id, "superseded_by", newer.id, p_conflict)
                rep.superseded += 1
            else:                        # same time or unknown order: a real contradiction, keep both visible
                self.store.add_flag(new.id, "contradicts", old.id, p_conflict)
                self.store.add_flag(old.id, "contradicts", new.id, p_conflict)
                rep.contradictions += 1
                if self.store.add_synth("conflict", [new.id, old.id], p_conflict):   # the host agent can settle it
                    rep.proposals += 1
        elif new_covers and old_covers:  # same facts twice: the later-written copy ranks lower
            self.store.add_flag(new.id, "duplicate_of", old.id, min(a["new_covers"].p, a["candidate_covers"].p))
            rep.duplicates += 1
        elif new_covers or old_covers:   # one note says everything the other does and more
            short, full, p = ((old, new, a["new_covers"].p) if new_covers else (new, old, a["candidate_covers"].p))
            self.store.add_flag(short.id, "subsumed_by", full.id, p)
            rep.subsumed += 1
        elif a["pattern"].p >= cfg.promote_threshold:
            if self.store.add_synth("promote", [new.id, old.id], a["pattern"].p):
                rep.proposals += 1
        if a["link_usefulness"].p >= cfg.relation_threshold and not any(
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
                                           for n in nodes], "instruction": _INSTRUCTION[it["kind"]]})
        return out

    def resolve(self, synth_id: int, text: str, scope: str | None = None) -> dict:
        item = self.store.synth_get(synth_id)
        if not item or item["status"] != "pending":
            return {"ok": False, "error": "no such pending synthesis"}
        srcs = [self.store.get(i) for i in item["node_ids"]]
        scopes = {n.scope for n in srcs}
        entities = sorted({e for n in srcs for e in n.entities})
        res = self.writer.write(text, scope or (scopes.pop() if len(scopes) == 1 else "global"),
                                entities, source=f"consolidation:{synth_id}", dedupe=False)
        if res.rejected:
            return {"ok": False, "error": res.reason}
        for n in srcs:
            self.store.add_edge(res.node_id, n.id, "semantic", 1.0)
            if item["kind"] in ("merge", "conflict"):     # raw notes stay, but the summary now outranks them
                self.store.add_flag(n.id, "merged_into", res.node_id, 1.0)
        self.store.synth_close(synth_id, "resolved", res.node_id)
        return {"ok": True, "node_id": res.node_id}

    def dismiss(self, synth_id: int) -> dict:
        item = self.store.synth_get(synth_id)
        if not item or item["status"] != "pending":
            return {"ok": False, "error": "no such pending synthesis"}
        self.store.synth_close(synth_id, "dismissed")
        return {"ok": True}


_INSTRUCTION = {
    "merge": ("First check that the notes really state the same fact; if they are distinct facts, call "
              "memory_dismiss instead. Otherwise write ONE literal fact that combines these without losing any distinct "
              "detail. Use absolute dates and explicit entity names."),
    "promote": ("If these episodes do not really show one pattern, call memory_dismiss. Otherwise they suggest a stable "
                "pattern: write ONE general, literal statement of that pattern; the episodes are kept as evidence."),
    "conflict": ("These notes disagree and nothing says which is current. Check the code or docs, or ask the user. If "
                 "one is right, memory_forget the other (or memory_resolve with one literal, dated statement of the "
                 "current fact: both originals then rank lower). If both can be true (different contexts), call "
                 "memory_dismiss."),
}

_DATE = re.compile(r"\b(20\d\d-[01]\d-[0-3]\d)\b")


def _older_newer(a, b):
    """(older, newer) by when the fact happened (timestamp), else by when it was written; None if tied.
    Write order alone is not enough when timestamps exist: backfilled or imported notes are written late.
    Equal timestamps (notes stamped with the same day) fall back to the latest date each note mentions."""
    ta, tb = (a.timestamp, b.timestamp) if a.timestamp is not None and b.timestamp is not None else (a.created, b.created)
    if ta is not None and ta == tb:
        ta, tb = max(_DATE.findall(a.content), default=None), max(_DATE.findall(b.content), default=None)
    if ta is None or tb is None or ta == tb:
        return None
    return (a, b) if ta < tb else (b, a)
