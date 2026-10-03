from jevmem import FakeDecider, Retriever, Store
from jevmem.config import Config


def rule(state, key, q):
    """Relevance oracle: items mentioning the goal's first word are relevant."""
    if key.startswith("item_"):
        i = int(key.split("_")[1])
        return 0.9 if state["goal"].split()[0] in state["items"][i]["content"] else 0.05
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
    assert res.sufficient is None


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
    full = Retriever(s, d, Config())                      # default config is "full"
    assert full.recall("bicycle", ["project:x"], mode="lite").stop_reason == "lite"
    assert full.recall("bicycle", ["project:x"]).stop_reason != "lite"


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
