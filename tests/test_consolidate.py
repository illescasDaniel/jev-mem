from jevmem import Config, FakeDecider, Retriever, Service
from jevmem.decider import Answer


def make(rule, **cfg):
    d = FakeDecider(rule)
    return Service(":memory:", decider=d, config=Config(**cfg)), d


def base(state, key, q):
    if key.endswith("_representation"):
        return Answer(choice="keep_separate", probs={"keep_separate": 0.9, "merge": 0.1}, confidence=0.9)
    if key == "injection":
        return 0.02
    if key.endswith(("redundant", "contradiction", "obsolescence")):
        return 0.05
    return 0.05


def cand_text(state, key):
    return state["candidates"][int(key.split("_")[1])]["content"]


def test_merge_proposal_then_resolve_by_agent():
    def rule(state, key, q):
        if key.endswith("_representation"):
            return Answer(choice="merge", probs={"merge": 0.95, "keep_separate": 0.05}, confidence=0.9)
        return base(state, key, q)
    svc, d = make(rule)
    a, _ = svc.write("We deploy with ops/deploy.sh.", scope="project:p")
    b, _ = svc.write("Deployment is done through ops/deploy.sh.", scope="project:p")
    rep = svc.consolidator.run()
    assert rep.proposals == 1 and rep.checked == 2
    (item,) = svc.consolidator.pending()
    assert {m["id"] for m in item["memories"]} == {a.node_id, b.node_id} and item["kind"] == "merge"
    assert svc.consolidator.run().proposals == 0               # nothing new, no duplicate proposal
    out = svc.consolidator.resolve(item["id"], "We deploy with ops/deploy.sh.")
    assert out["ok"] and svc.consolidator.pending() == []
    assert svc.store.flagged("merged_into") == {a.node_id, b.node_id}   # raw notes kept, ranked lower
    assert svc.store.count() == 3


def test_contradiction_and_obsolescence_annotate_without_deleting():
    def rule(state, key, q):
        if key.endswith("contradiction"):
            return 0.95 if "Postgres" in state["new_memory"]["content"] else 0.05
        if key.endswith("obsolescence"):
            return 0.95 if "2026-10" in state["new_memory"]["content"] else 0.05
        return base(state, key, q)
    svc, _ = make(rule)
    o, _ = svc.write("Default branch is master. Recorded 2026-01-05.", scope="project:p", timestamp="2026-01-05")
    n, _ = svc.write("Default branch renamed to main on 2026-10-02.", scope="project:p", timestamp="2026-10-02")
    c, _ = svc.write("We store data in Postgres.", scope="project:p")
    rep = svc.consolidator.run()
    assert rep.superseded >= 1 and svc.store.flagged("superseded_by") == {o.node_id}   # older one, by timestamp
    assert svc.store.count() == 3


def test_superseded_notes_rank_lower_in_recall():
    def rule(state, key, q):
        if key.endswith("obsolescence"):
            return 0.95 if "main" in state["new_memory"]["content"] else 0.05
        if key.endswith("relevance") or key in ("semantic",):
            return 0.9
        if key == "evidence_sufficient":
            return 0.99
        if key == "missing_evidence":
            return 0.01
        return base(state, key, q)
    svc, _ = make(rule)
    o, _ = svc.write("Default branch is master.", scope="project:p", timestamp="2026-01-05")
    n, _ = svc.write("Default branch is main.", scope="project:p", timestamp="2026-10-02")
    svc.consolidator.run()
    r = Retriever(svc.store, svc.decider, svc.cfg).recall("what is the default branch?", ["project:p"])
    assert r.evidence[0].id == n.node_id
    assert any(f.startswith("superseded") for e in r.evidence if e.id == o.node_id for f in e.flags)


def test_auto_trigger_every_n_writes_and_degraded_retry():
    svc, d = make(base, consolidate_every=3)
    reports = [svc.write(f"Fact number {i} about topic.", scope="project:p")[1] for i in range(3)]
    assert reports[:2] == [None, None] and reports[2] is not None and reports[2].checked == 3
    svc.write("Another fact about topic.", scope="project:p")
    svc.write("Yet another fact about topic.", scope="project:p")
    d.down = True                                   # Jev drops out just for the consolidation pass
    rep = svc.consolidator.run()
    assert rep.degraded and svc.store.meta_get("last_consolidated_id") == 3   # progress not advanced past failure
    d.down = False
    assert svc.consolidator.run().checked == 2      # the unprocessed notes are retried
