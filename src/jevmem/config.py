from dataclasses import dataclass


@dataclass
class Config:
    """Defaults follow the paper's active profile (App. B)."""
    relation_threshold: float = 0.60     # theta_rel
    write_candidates: int = 10           # K_w
    injection_block: float = 0.50
    duplicate_similarity: float = 0.97   # cosine at/above which a write is rejected as a duplicate of an existing note
    activation_threshold: float = 0.30   # theta_act
    anchor_count: int = 30
    rrf_k: int = 60
    graph_budget: int = 80               # B
    min_graph_budget: int = 1            # m
    gamma: float = 1.0
    max_depth: int = 8
    beam_width: int = 10                 # W
    top_k: int = 8                       # K handed to System Two
    recall_mode: str = "auto"            # "auto" (lite first, escalate to full: nothing found or looks multi-hop/temporal;
                                         # pooled: full's recall for ~60% of its tokens), "full" (route/expand/stop) or
                                         # "lite" (vector top-N + one relevance filter)
    escalate_multi_hop: float = 0.60     # auto mode: P(multi-hop) at/above this escalates lite -> full
    escalate_temporal: float = 0.80      # auto mode: same for P(needs time relations); lite has no temporal graph
    # lite: sufficient >= x and missing < y. Pooled over 14 eval sets (466 answers, 126 unanswerable): 0.893 accurate,
    # same leave-one-set-out; abstains on 94% of unanswerable questions, flags 4% of answerable ones insufficient
    lite_sufficient: float = 0.30
    lite_missing_max: float = 0.70
    # auto mode: also escalate when lite says "memory does not know". Off: +0.002 recall for +24% tokens (same sets)
    escalate_insufficient: bool = False
    lite_candidates: int = 20            # vector candidates the lite filter judges (one Jev call)
    # stop rule: sufficient >= x, missing < y, contradiction < z. The paper's 0.95/0.15 did not fit our question
    # wording; these come from `jevmem eval` (clear gap: unanswerable <=0.24 sufficient, answerable >=0.51)
    sufficient: float = 0.50
    missing_max: float = 0.60
    contradiction_max: float = 0.60
    cont_threshold: float = 0.15
    max_nodes: int = 60
    max_edges: int = 2400
    max_jev_calls: int = 16
    time_budget_s: float = 15.0
    score_chunk: int = 12                # candidates per scoring call
    min_relevance: float = 0.30          # anchors judged below this are dropped from results
    consolidate_every: int = 20          # successful writes between consolidation passes
    consolidate_candidates: int = 3      # neighbours judged per new node
    consolidate_max_nodes: int = 20      # nodes per pass (= one Jev call each, ~3-5 s inline)
    # conflict = (same question AND different answer) OR outdated. Picked on evals/consolidation.json
    # (34/36 conflict cases in both note orders, 0 false positives), checked on evals/consolidation_holdout.json
    conflict_same: float = 0.70
    conflict_different: float = 0.85
    conflict_outdated: float = 0.80
    # ... OR one note reports that what the other says has changed (asked both ways, max). Catches renames and
    # replacements whose stale note was written last; superseded pairs scored 0.81-0.96, others <= 0.65
    conflict_change: float = 0.80
    cover_threshold: float = 0.75        # "every fact of A is in B": both ways = duplicate, one way = subsumed
    promote_threshold: float = 0.75      # repeated-episode pairs scored 0.81-0.90, everything else <= 0.59
    # UserPromptSubmit hook: inject only notes scoring >= x on the stricter same-subject question. On 32 labelled
    # SpaceMaker prompts (evals/hook_prompts.json): off-topic notes <= 0.58 (one 0.65), useful notes >= 0.67
    hook_topic_min: float = 0.60
    # SessionStart skips notes this similar (cosine) to a sentence of CLAUDE.md/AGENTS.md. Calibrated for bge-small
    # on SpaceMaker: 19/23 notes restating AGENTS.md skipped, none of the 59 others (highest other: 0.818)
    instructions_similarity: float = 0.82
    superseded_penalty: float = 0.6      # score multiplier for stale notes (store.STALE_FLAGS) in recall
