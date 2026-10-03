from jevmem import Config, FakeDecider, Retriever, Service


def make(rule, **cfg):
    d = FakeDecider(rule)
    return Service(":memory:", decider=d, config=Config(**cfg)), d


def base(state, key, q):
    if key == "injection":
        return 0.02
    return 0.05


def texts(state, key):
    """(new_memory, candidate) contents for a pair_<i>_* key."""
    return state["new_memory"]["content"], state["candidates"][int(key.split("_")[1])]["content"]


def clash(words):
    """Rule: pairs whose two notes contain the two given words clash (same question, different answer)."""
    def rule(state, key, q):
        if key.endswith(("_same_question", "_different_answer")):
            pair = " ".join(texts(state, key))
            return 0.95 if all(w in pair for w in words) else 0.05
        return base(state, key, q)
    return rule


def test_conflict_with_different_dates_supersedes_the_older_note_even_when_written_last():
    svc, _ = make(clash(("master", "main")))
    n, _ = svc.write("Default branch renamed to main on 2026-10-02.", scope="project:p", timestamp="2026-10-02")
    o, _ = svc.write("Default branch is master.", scope="project:p", timestamp="2026-01-05")   # backfilled
    rep = svc.consolidator.run()
    assert rep.superseded == 1 and rep.contradictions == 0
    assert svc.store.flagged("superseded_by") == {o.node_id}      # older by timestamp, not by write order
    assert svc.store.count() == 2                                  # nothing deleted


def test_conflict_at_the_same_time_is_a_contradiction_on_both_notes():
    svc, _ = make(clash(("Postgres", "MySQL")))
    a, _ = svc.write("The shop uses Postgres.", scope="project:p", timestamp="2026-05-05")
    b, _ = svc.write("The shop uses MySQL.", scope="project:p", timestamp="2026-05-05")
    rep = svc.consolidator.run()
    assert rep.contradictions == 1 and rep.superseded == 0
    assert svc.store.flagged("contradicts") == {a.node_id, b.node_id} and svc.store.stale() == set()


def test_same_timestamp_falls_back_to_the_latest_date_in_the_notes():
    svc, _ = make(clash(("Netlify", "Pages")))
    n, _ = svc.write("On 2026-07-01 the docs moved to GitHub Pages.", scope="project:p", timestamp="2026-07-02")
    o, _ = svc.write("Since 2026-02-01 the docs are hosted on Netlify.", scope="project:p", timestamp="2026-07-02")
    assert svc.consolidator.run().superseded == 1
    assert svc.store.flagged("superseded_by") == {o.node_id}


def test_outdated_alone_is_enough_and_untimed_notes_fall_back_to_write_order():
    def rule(state, key, q):
        if key.endswith("_outdated"):
            return 0.9 if "LaunchDarkly" in " ".join(texts(state, key)) and "removed" in " ".join(texts(state, key)) else 0.05
        return base(state, key, q)
    svc, _ = make(rule)
    o, _ = svc.write("Feature flags are stored in LaunchDarkly.", scope="project:p")
    n, _ = svc.write("The team removed LaunchDarkly; flags live in config/flags.yaml.", scope="project:p")
    assert svc.consolidator.run().superseded == 1
    assert svc.store.flagged("superseded_by") == {o.node_id}


def test_a_note_reporting_a_rename_supersedes_whichever_slot_it_is_in():
    def rule(state, key, q):
        if key.endswith("_reports_change"):                  # only the "renamed" note reports the change
            new, cand = texts(state, key)
            return 0.9 if "renamed" in (new if key.endswith("_new_reports_change") else cand) else 0.05
        return base(state, key, q)
    svc, _ = make(rule)
    n, _ = svc.write("On 2026-09-27 the library folder converted/ was renamed to processed/.", scope="project:p",
                     timestamp="2026-09-27")
    o, _ = svc.write("The gallery index folder is named converted/.", scope="project:p", timestamp="2026-09-01")
    assert svc.consolidator.run().superseded == 1
    assert svc.store.flagged("superseded_by") == {o.node_id}


def test_duplicate_and_subsumed_notes_are_flagged_without_agent_work():
    def rule(state, key, q):
        if key.endswith(("_new_covers", "_candidate_covers")):
            new, cand = texts(state, key)
            covered, cover = (cand, new) if key.endswith("_new_covers") else (new, cand)
            return 0.95 if all(w in cover for w in ("deploy", "ops/deploy.sh")) and "deploy" in covered \
                and len(cover) >= len(covered) - 10 else 0.05
        return base(state, key, q)
    svc, _ = make(rule)
    short, _ = svc.write("We deploy with ops/deploy.sh.", scope="project:p")
    full, _ = svc.write("We deploy with ops/deploy.sh, which needs AWS_PROFILE=prod.", scope="project:p")
    rep = svc.consolidator.run()
    assert rep.subsumed == 1 and rep.proposals == 0 and svc.consolidator.pending() == []
    assert svc.store.flagged("subsumed_by") == {short.node_id}
    copy, _ = svc.write("We deploy with ops/deploy.sh!", scope="project:p", dedupe=False)
    assert svc.consolidator.run().duplicates >= 1
    assert copy.node_id in svc.store.flagged("duplicate_of")


def test_pattern_becomes_a_promote_proposal_the_agent_resolves():
    def rule(state, key, q):
        if key.endswith("_pattern"):
            return 0.9 if all("failed" in t for t in texts(state, key)) else 0.05
        return base(state, key, q)
    svc, _ = make(rule)
    a, _ = svc.write("On 2026-03-02 the deploy failed: AWS_PROFILE unset.", scope="project:p")
    b, _ = svc.write("On 2026-05-11 the deploy failed again: AWS_PROFILE missing.", scope="project:p")
    rep = svc.consolidator.run()
    assert rep.proposals == 1 and rep.checked == 2
    (item,) = svc.consolidator.pending()
    assert {m["id"] for m in item["memories"]} == {a.node_id, b.node_id} and item["kind"] == "promote"
    assert svc.consolidator.run().proposals == 0               # nothing new, no duplicate proposal
    out = svc.consolidator.resolve(item["id"], "Deploys fail when AWS_PROFILE is not set.")
    assert out["ok"] and svc.consolidator.pending() == []
    assert svc.store.stale() == set()                          # promote keeps the episodes at full rank
    assert svc.store.count() == 3


def test_stale_notes_rank_lower_in_recall():
    def rule(state, key, q):
        if key.endswith("_outdated"):
            return 0.95 if "branch" in state["new_memory"]["content"] else 0.05
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


def test_rescan_rejudges_old_notes_and_replaces_stale_flags():
    svc, d = make(base)
    o, _ = svc.write("Default branch is master.", scope="project:p", timestamp="2026-01-05")
    n, _ = svc.write("Default branch is main.", scope="project:p", timestamp="2026-10-02")
    svc.store.add_flag(n.node_id, "contradicts", o.node_id, 0.9)    # left over from older questions
    assert svc.consolidator.run().checked == 2 and svc.consolidator.run().checked == 0
    d.rule = clash(("master", "main"))
    rep = svc.consolidator.rescan()
    assert rep.checked == 2 and rep.superseded == 1
    assert svc.store.flagged("superseded_by") == {o.node_id} and svc.store.flagged("contradicts") == set()


def test_same_time_conflict_is_ordered_by_which_note_reports_the_change():
    def rule(state, key, q):
        if key.endswith("_reports_change"):
            new, cand = texts(state, key)
            return 0.9 if "moved to" in (new if key.endswith("_new_reports_change") else cand) else 0.05
        return clash(("Memcached", "Redis"))(state, key, q)
    for order in (0, 1):                                      # whichever note was written last
        svc, _ = make(rule)
        notes = [("The cache is Memcached.", "old"), ("The cache moved to Redis.", "new")]
        ids = {}
        for text, name in notes[::-1] if order else notes:
            ids[name] = svc.write(text, scope="project:p", timestamp="2026-05-05")[0].node_id
        rep = svc.consolidator.run()
        assert rep.superseded == 1 and rep.contradictions == 0
        assert svc.store.flagged("superseded_by") == {ids["old"]}


def test_a_real_contradiction_becomes_a_proposal_the_agent_settles():
    svc, _ = make(clash(("Postgres", "MySQL")))
    a, _ = svc.write("The shop uses Postgres.", scope="project:p", timestamp="2026-05-05")
    b, _ = svc.write("The shop uses MySQL.", scope="project:p", timestamp="2026-05-05")
    assert svc.consolidator.run().proposals == 1
    item, = svc.consolidator.pending()
    assert item["kind"] == "conflict" and {m["id"] for m in item["memories"]} == {a.node_id, b.node_id}
    assert "memory_forget" in item["instruction"]
    res = svc.consolidator.resolve(item["id"], "As of 2026-10-03 the shop uses Postgres.")
    assert res["ok"] and svc.store.flagged("merged_into") == {a.node_id, b.node_id}
    assert svc.store.stale() == {a.node_id, b.node_id}        # both originals now rank below the settled fact


def test_forgetting_one_side_closes_the_conflict_proposal():
    svc, _ = make(clash(("Postgres", "MySQL")))
    a, _ = svc.write("The shop uses Postgres.", scope="project:p", timestamp="2026-05-05")
    svc.write("The shop uses MySQL.", scope="project:p", timestamp="2026-05-05")
    svc.consolidator.run()
    svc.store.delete_node(a.node_id)
    assert svc.consolidator.pending() == []


def test_notes_recall_returns_together_are_compared_even_if_never_neighbours():
    def rule(state, key, q):
        if key.startswith("item_"):
            return 0.9
        return clash(("Postgres", "MySQL"))(state, key, q)
    svc, d = make(rule, coretrieved_similarity=0.0)
    a = svc.store.add_node("The shop uses Postgres.", "project:p", 1.0)
    b = svc.store.add_node("The shop uses MySQL.", "project:p", 2.0)
    svc.store.meta_set("last_consolidated_id", b)            # as if write-time consolidation never paired them
    svc.retriever.recall("which database does the shop use", ["project:p"], mode="lite")
    assert svc.store.queued_pairs(10) == [(a, b)]
    rep = svc.consolidator.run()
    assert rep.superseded == 1 and svc.store.flagged("superseded_by") == {a}
    assert svc.store.queued_pairs(10) == []                  # judged once, not again
    svc.retriever.recall("which database does the shop use", ["project:p"], mode="lite")
    assert svc.store.queued_pairs(10) == []


def test_write_order_still_decides_when_the_clock_cannot_tell_two_writes_apart(monkeypatch):
    """Windows' clock ticks every ~15 ms, so quick writes can share `created`; the note id then orders them."""
    monkeypatch.setattr("jevmem.store.time.time", lambda: 1_700_000_000.0)
    svc, _ = make(clash(("LaunchDarkly", "flags.yaml")))
    o, _ = svc.write("Feature flags are stored in LaunchDarkly.", scope="project:p")
    n, _ = svc.write("Feature flags are now stored in flags.yaml.", scope="project:p")
    assert svc.consolidator.run().superseded == 1
    assert svc.store.flagged("superseded_by") == {o.node_id}
