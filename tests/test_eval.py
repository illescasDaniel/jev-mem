from jevmem import Config, FakeDecider, Service
from jevmem.evalharness import build, evaluate, summarize, sweep


def test_eval_pipeline_runs_with_fake_decider():
    data = {"scope": "project:t", "notes": [
        {"key": "a", "timestamp": "2026-01-01", "content": "We deploy with ops/deploy.sh.", "entities": []},
        {"key": "b", "timestamp": "2026-01-02", "content": "Lunch is on Thursdays.", "entities": []}],
        "questions": [{"q": "how do we deploy?", "gold": ["a"], "kind": "single"},
                      {"q": "what is the cloud provider?", "gold": [], "kind": "unanswerable"}]}
    rule = lambda s, k, q: 0.9 if (k.endswith("relevance") or k in ("evidence_sufficient",)) else 0.05
    svc = Service(":memory:", decider=FakeDecider(rule), config=Config())
    rows = evaluate(svc, data, build(svc, data), k=3)
    assert "jevmem" in summarize(rows) and "sufficiency sweep" in sweep(rows["jevmem"])
