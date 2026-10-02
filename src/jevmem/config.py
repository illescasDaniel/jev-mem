from dataclasses import dataclass


@dataclass
class Config:
    """Defaults follow the paper's active profile (App. B)."""
    relation_threshold: float = 0.60     # theta_rel
    write_candidates: int = 10           # K_w
    injection_block: float = 0.50
    activation_threshold: float = 0.30   # theta_act
    anchor_count: int = 30
    rrf_k: int = 60
    graph_budget: int = 80               # B
    min_graph_budget: int = 1            # m
    gamma: float = 1.0
    max_depth: int = 8
    beam_width: int = 10                 # W
    top_k: int = 8                       # K handed to System Two
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
    consolidate_threshold: float = 0.85  # merge/promote/contradiction/obsolescence cut-off
    superseded_penalty: float = 0.6      # score multiplier for superseded/merged notes in recall
