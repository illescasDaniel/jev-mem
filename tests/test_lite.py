from jevmem import FakeDecider, Retriever, Store
from jevmem.config import Config


def rule(state, key, q):
    """Relevance oracle: items mentioning the goal's first word are relevant."""
    if key.startswith("item_"):
        i = int(key.split("_")[1])
        return 0.9 if state["goal"].split()[0] in state["items"][i]["content"] else 0.05
    if key in ("evidence_sufficient", "missing_evidence") and "items" in state:   # answerable iff an item matches
        hit = any(state["goal"].split()[0] in it["content"] for it in state["items"])
        return (0.9 if hit else 0.1) if key == "evidence_sufficient" else (0.1 if hit else 0.9)
    return 0.0


def build(**cfg):
    s, d = Store(), FakeDecider(rule)
    for t in ("bicycle repair shop opens at nine", "bicycle tyres were replaced", "the cat sleeps all day",
              "dinner is at eight"):
        s.add_node(t, "project:x")
    return s, d, Retriever(s, d, Config(recall_mode="lite", **cfg))


def test_lite_is_one_call_and_filters_by_relevance():
    s, d, r = build()
    res = r.recall("bicycle tyres", ["project:x"])
    assert d.calls == 1 and res.jev_calls == 1 and res.stop_reason == "lite"
    assert {e.content for e in res.evidence} == {"bicycle repair shop opens at nine", "bicycle tyres were replaced"}
    assert res.sufficient is True and res.assess["sufficient"] == 0.9


def test_lite_says_memory_does_not_know_in_the_same_call():
    s, d, r = build()
    s.add_node("tyres on the car were rotated", "project:x")   # topical but does not answer
    res = r.recall("unicycle tyres", ["project:x"])
    assert d.calls == 1 and res.sufficient is False and res.missing == 0.9


def test_auto_escalates_when_lite_finds_items_but_says_insufficient():
    def r2(state, key, q):
        if key == "evidence_sufficient" and "items" in state:
            return 0.1
        return rule(state, key, q)
    s, _, _ = build()
    r = Retriever(s, FakeDecider(r2), Config(recall_mode="auto", escalate_insufficient=True))
    assert r.recall("bicycle tyres", ["project:x"]).stop_reason.startswith("escalated:insufficient")
    r = Retriever(s, FakeDecider(r2), Config(recall_mode="auto"))           # default: keep lite, report insufficient
    res = r.recall("bicycle tyres", ["project:x"])
    assert res.stop_reason == "lite" and res.sufficient is False


def test_lite_ranks_superseded_lower_and_respects_k():
    s, d, r = build()
    old = s.add_node("bicycle price is 100 euro", "project:x"); new = s.add_node("bicycle price is 120 euro", "project:x")
    s.add_flag(old, "superseded_by", new, 0.9)
    res = r.recall("bicycle price", ["project:x"], k=10)
    ids = [e.id for e in res.evidence]
    assert ids.index(new) < ids.index(old)
    assert len(r.recall("bicycle price", ["project:x"], k=2).evidence) == 2


def test_lite_degrades_to_vector_top_k_when_jev_down():
    s, d, r = build()
    d.down = True
    res = r.recall("bicycle tyres", ["project:x"], k=2)
    assert res.degraded and len(res.evidence) == 2


def test_mode_argument_overrides_config():
    s, d, _ = build()
    full = Retriever(s, d, Config(recall_mode="full"))
    assert full.recall("bicycle", ["project:x"], mode="lite").stop_reason == "lite"
    assert full.recall("bicycle", ["project:x"]).stop_reason != "lite"


def test_default_mode_is_auto():
    assert Config().recall_mode == "auto"


def test_auto_stays_lite_for_single_fact_and_escalates_when_multi_hop_or_empty():
    def auto_rule(state, key, q):
        if key in ("multi_hop", "temporal") and "goal" in state:
            return (0.9 if "why" in state["goal"] else 0.1) if key == "multi_hop" else (0.9 if "when" in state["goal"] else 0.1)
        return rule(state, key, q)
    s, _, _ = build()
    d = FakeDecider(auto_rule)
    r = Retriever(s, d, Config(recall_mode="auto"))
    one = r.recall("bicycle tyres", ["project:x"])
    assert one.stop_reason == "lite" and one.jev_calls == 1
    hop = r.recall("bicycle why", ["project:x"])
    assert hop.stop_reason.startswith("escalated:multi_hop") and hop.jev_calls > 1
    none = r.recall("zebra", ["project:x"])
    assert none.stop_reason.startswith("escalated:nothing_found")


def test_auto_escalates_temporal_questions():
    def t_rule(state, key, q):
        if key in ("multi_hop", "temporal") and "goal" in state:
            return 0.9 if key == "temporal" and "when" in state["goal"] else 0.1
        return rule(state, key, q)
    s, _, _ = build()
    r = Retriever(s, FakeDecider(t_rule), Config(recall_mode="auto"))
    assert r.recall("bicycle when", ["project:x"]).stop_reason.startswith("escalated:temporal")


def _many_linked(rule_fn, **cfg):
    s, d = Store(), FakeDecider(rule_fn)
    ids = [s.add_node(f"bicycle part {n} is stored in bin {n}", "project:x") for n in range(40)]
    for a, b in zip(ids, ids[1:]):
        s.add_edge(a, b, "semantic", 0.9)
        s.add_edge(b, a, "semantic", 0.9)
    return s, d, Retriever(s, d, Config(**cfg))


def _never_sufficient(state, key, q):
    if key in ("multi_hop", "temporal"):
        return 0.9
    if key in ("evidence_sufficient", "missing_evidence", "contradiction"):
        return 0.0 if key != "missing_evidence" else 0.9
    if key == "continue_useful" or key == "semantic":
        return 0.9
    return 0.8      # every relevance/usefulness question


def test_jev_call_cap_holds_for_full_and_for_escalated_auto():
    for mode in ("full", "auto"):
        s, d, r = _many_linked(_never_sufficient, max_jev_calls=6)
        res = r.recall("bicycle part 3", ["project:x"], mode=mode)
        assert res.jev_calls <= 6 and d.calls <= 6, (mode, res.jev_calls, d.calls)
        # near-tied scores make the expansion path platform-dependent: it may run out of neighbours before the cap
        assert res.stop_reason.endswith(("limit:calls/time", "frontier_exhausted")), res.stop_reason


def test_escalation_reuses_lites_relevance_instead_of_judging_anchors_again():
    def hop_rule(state, key, q):
        return 0.9 if key == "multi_hop" and "goal" in state else rule(state, key, q)
    s, _, _ = build()
    d = FakeDecider(hop_rule)
    res = Retriever(s, d, Config(recall_mode="auto")).recall("bicycle tyres", ["project:x"])
    assert res.stop_reason.startswith("escalated:multi_hop")
    asked = [k for _, qs in d.log for k in qs]
    assert not any(k.startswith("anchor_") for k in asked)       # lite already judged every anchor
    assert any(k == "multi_hop" for k in asked)
