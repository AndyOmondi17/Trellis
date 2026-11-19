"""
tests/test_tier2_diagnostic.py
Comprehensive parameterized test suite for Tier 2:
  - FR-2.1: Pre-Diagnostic Graph Compression & Max-Entropy Question Selector
  - FR-2.2: Plus-Minus Confidence Balance & Conflict Resolution Engine
  - FR-2.3: Conditional Completion, Supplemental Diagnostics & "I Already Know This" Fast-Track
Parameterized across all standalone database backends: memory, relational, graph.
"""

from __future__ import annotations

import pytest

from trellis.domain.models import (
    StudentProfile,
    StudentTopicState,
)
from trellis.engine.content_blueprint_solver import ContentBlueprintSolver
from trellis.engine.diagnostic_engine import (
    DiagnosticEngine,
    DiagnosticSubmission,
)
from trellis.engine.topology_service import TopologyService
from trellis.ports.storage_port import get_storage_adapter


def _make_node(
    node_id: str,
    title: str,
    course_id: str,
    cluster: str,
    prereqs: list[str],
    encompassed: list[tuple[str, float]],
    is_core: bool = True,
    in_foundations: bool = True,
    difficulty: float = 0.4,
    num_kps: int = 2,
) -> dict:
    """Helper to construct a clean-room TopicNode dictionary with KPs and SymPy blueprints."""
    kps = []
    for idx in range(num_kps):
        kps.append(
            {
                "kp_index": idx,
                "kp_id": f"{node_id}#kp_{idx}",
                "scaffolding_tier": "KP_1_BASELINE" if idx == 0 else "KP_2_ADVANCED",
                "kp_focus": f"Focus {idx} for {title}",
                "novel_chunk_count": 2,
                "key_prerequisite_id": prereqs[0] if prereqs else None,
                "rule_statement": f"Apply rule {idx} for {title}.",
                "canonical_worked_example": {
                    "problem": "Evaluate 3 + 4",
                    "subgoal_steps": [
                        {
                            "subgoal_label": "Combine terms",
                            "operation": "3 + 4 = 7",
                            "justification": "Addition",
                        }
                    ],
                    "final_answer": "{p_1} + {p_2}",
                },
                "problem_blueprint": {
                    "parameter_constraints": "p_1 in {2..8}, p_2 in {1..7}",
                    "stem_template": f"[{node_id}#kp_{idx}] Compute {{p_1}} + {{p_2}}",
                    "answer_format": "NUMERIC_EXACT",
                    "verification_solver_expr": "sympy.simplify(target_expr - student_expr) == 0",
                },
                "diagnostic_misconceptions": [],
            }
        )

    return {
        "id": node_id,
        "title": title,
        "course_id": course_id,
        "canonical_domain": "03_equations_and_inequalities",
        "domain_cluster": cluster,
        "skill_type": "PROCEDURAL",
        "bloom_cognitive_tier": "PROCEDURAL_EXECUTION",
        "grade_level_min": 8,
        "grade_level_max": 10,
        "is_core": is_core,
        "in_foundations_sequence": in_foundations,
        "requires_automaticity": False,
        "base_difficulty": difficulty,
        "estimated_xp": 4,
        "mastery_gate_n": 3,
        "key_prerequisite_id": prereqs[0] if prereqs else None,
        "aligned_standards": [],
        "mastery_criteria": [f"Master {title}"],
        "knowledge_points": kps,
        "prerequisites": [
            {"prerequisite_id": pid, "strength": "hard", "rationale": "Hard prerequisite"}
            for pid in prereqs
        ],
        "encompassed_skills": [
            {
                "encompassed_id": eid,
                "fire_weight": fw,
                "is_non_ancestor": False,
                "rationale": "Implicit repetition",
            }
            for eid, fw in encompassed
        ],
    }


SAMPLE_TIER2_GRAPH = {
    "schema_version": "2.0.0-trellis",
    "graph_id": "trellis-tier2-diagnostic-dag",
    "course_catalog": [
        {
            "course_id": "CRS_FND_FLOOR",
            "course_code": "flr",
            "title": "Arithmetic Floor",
            "grade_band": "3-5",
            "benchmark_xp": 1000,
            "mastery_floor_course_ids": [],
        },
        {
            "course_id": "CRS_PREALG",
            "course_code": "pra",
            "title": "Pre-Algebra Foundations",
            "grade_band": "6-7",
            "benchmark_xp": 1200,
            "mastery_floor_course_ids": ["CRS_FND_FLOOR"],
        },
        {
            "course_id": "CRS_ALG1",
            "course_code": "alg",
            "title": "Algebra I",
            "grade_band": "8-9",
            "benchmark_xp": 1500,
            "mastery_floor_course_ids": ["CRS_FND_FLOOR"],
        },
        {
            "course_id": "CRS_GEOM",
            "course_code": "geo",
            "title": "Unrelated Euclidean Geometry",
            "grade_band": "9-10",
            "benchmark_xp": 1400,
            "mastery_floor_course_ids": [],
        },
    ],
    "nodes": [
        # Floor course node (automatically credited via mastery_floor_course_ids)
        _make_node(
            "trl_flr_num_001",
            "Whole Number Addition",
            "CRS_FND_FLOOR",
            "CRS_FND_FLOOR::num",
            prereqs=[],
            encompassed=[],
            difficulty=0.1,
        ),
        # Pre-Algebra prerequisite chain: pra_001 -> pra_002 -> pra_003
        _make_node(
            "trl_pra_int_001",
            "Integer Signs",
            "CRS_PREALG",
            "CRS_PREALG::int",
            prereqs=["trl_flr_num_001"],
            encompassed=[("trl_flr_num_001", 0.5)],
            difficulty=0.2,
        ),
        _make_node(
            "trl_pra_int_002",
            "Integer Addition",
            "CRS_PREALG",
            "CRS_PREALG::int",
            prereqs=["trl_pra_int_001"],
            encompassed=[("trl_pra_int_001", 0.6)],
            difficulty=0.25,
        ),
        _make_node(
            "trl_pra_int_003",
            "Integer Multiplication",
            "CRS_PREALG",
            "CRS_PREALG::int",
            prereqs=["trl_pra_int_002"],
            encompassed=[("trl_pra_int_002", 0.6)],
            difficulty=0.3,
        ),
        # Algebra I linear chain: alg_001 -> alg_002 -> alg_003 -> alg_004 -> alg_005
        _make_node(
            "trl_alg_lin_001",
            "One-Step Equations",
            "CRS_ALG1",
            "CRS_ALG1::lin",
            prereqs=["trl_pra_int_003"],
            encompassed=[("trl_pra_int_003", 0.5)],
            difficulty=0.35,
        ),
        _make_node(
            "trl_alg_lin_002",
            "Two-Step Equations",
            "CRS_ALG1",
            "CRS_ALG1::lin",
            prereqs=["trl_alg_lin_001"],
            encompassed=[("trl_alg_lin_001", 0.6)],
            difficulty=0.45,
        ),
        _make_node(
            "trl_alg_lin_003",
            "Multi-Step Equations with Variables on Both Sides",
            "CRS_ALG1",
            "CRS_ALG1::lin",
            prereqs=["trl_alg_lin_002"],
            encompassed=[("trl_alg_lin_002", 0.6)],
            difficulty=0.55,
        ),
        _make_node(
            "trl_alg_lin_004",
            "Linear Equations with Rational Coefficients",
            "CRS_ALG1",
            "CRS_ALG1::lin",
            prereqs=["trl_alg_lin_003"],
            encompassed=[("trl_alg_lin_003", 0.6)],
            difficulty=0.65,
        ),
        # Two module leaf siblings in CRS_ALG1::lin branching from trl_alg_lin_004
        _make_node(
            "trl_alg_lin_005",
            "Literal Equations & Formula Rearrangement (Module Leaf A)",
            "CRS_ALG1",
            "CRS_ALG1::lin",
            prereqs=["trl_alg_lin_004"],
            encompassed=[("trl_alg_lin_004", 0.5)],
            difficulty=0.75,
        ),
        _make_node(
            "trl_alg_lin_006",
            "Absolute Value Linear Equations (Module Leaf B)",
            "CRS_ALG1",
            "CRS_ALG1::lin",
            prereqs=["trl_alg_lin_004"],
            encompassed=[("trl_alg_lin_004", 0.5)],
            difficulty=0.75,
        ),
        # Non-foundations supplemental node in CRS_ALG1
        _make_node(
            "trl_alg_sup_001",
            "Recreational Figurate Number Puzzles",
            "CRS_ALG1",
            "CRS_ALG1::sup",
            prereqs=["trl_alg_lin_001"],
            encompassed=[],
            is_core=False,
            in_foundations=False,
            difficulty=0.5,
        ),
        # Unrelated Geometry node (must be excluded from CRS_ALG1 diagnostic scope)
        _make_node(
            "trl_geo_tri_001",
            "Triangle Congruence Proofs",
            "CRS_GEOM",
            "CRS_GEOM::tri",
            prereqs=[],
            encompassed=[],
            is_core=True,
            in_foundations=False,
            difficulty=0.5,
        ),
    ],
}


@pytest.fixture(params=["memory", "relational", "graph"])
def tier2_fixture(request):
    """Initializes standalone adapter, ingests Tier 2 DAG, and builds DiagnosticEngine."""
    backend = request.param
    ad = get_storage_adapter(backend)
    ad.ingest_knowledge_graph(SAMPLE_TIER2_GRAPH)
    topo = TopologyService(ad)
    solver = ContentBlueprintSolver(ad)
    diag = DiagnosticEngine(ad, topology=topo, blueprint_solver=solver)
    yield ad, topo, solver, diag
    if hasattr(ad, "close"):
        ad.close()


# ---------------------------------------------------------------------------
# Test FR-2.1: Pre-Diagnostic Graph Compression & Max-Entropy Selector
# ---------------------------------------------------------------------------

def test_fr2_1_graph_compression_3hop_cover(tier2_fixture):
    """
    Verifies that compress_diagnostic_subgraph:
      1. Excludes floor courses (CRS_FND_FLOOR) and unrelated courses (CRS_GEOM).
      2. Respects foundations_only filter.
      3. Compresses the 9-node chain/branch scope into a smaller minimal cover
         where every topic has an ancestor and progeny within <= 3 prerequisite hops.
    """
    _, _, _, diag = tier2_fixture
    student_id = "usr_diag_compress"

    scope = diag.get_diagnostic_scope("CRS_ALG1", student_id=student_id)
    assert "trl_flr_num_001" not in scope  # Floor course excluded
    assert "trl_geo_tri_001" not in scope  # Unrelated course excluded
    assert "trl_pra_int_001" in scope      # Prerequisite foundation included
    assert "trl_alg_lin_005" in scope      # Course leaf included
    assert "trl_alg_sup_001" in scope      # Supplemental node included in full track

    # Foundations track filter prunes non-foundations supplemental node
    fnd_scope = diag.get_diagnostic_scope(
        "CRS_ALG1", student_id=student_id, foundations_only=True
    )
    assert "trl_alg_sup_001" not in fnd_scope
    assert len(fnd_scope) == 9

    # Compress subgraph
    cover = diag.compress_diagnostic_subgraph(
        "CRS_ALG1", student_id=student_id, foundations_only=True, max_hops=3
    )
    assert len(cover) < len(fnd_scope)
    assert diag.verify_diagnostic_cover(fnd_scope, cover, max_hops=3) is True


def test_fr2_1_max_entropy_question_selector(tier2_fixture):
    """
    Verifies maximum-entropy question selection along the ~50% accuracy boundary
    and adaptive upward/downward bisection across the DAG.
    """
    _, _, _, diag = tier2_fixture
    student_id = "usr_diag_entropy"

    # At balance 0.0, mastery probability is 0.50 and binary entropy is 1.0 bit
    assert diag.balance_to_mastery_probability(0.0) == pytest.approx(0.50, abs=1e-6)
    assert diag.binary_entropy(0.50) == pytest.approx(1.0, abs=1e-6)

    # 1st question selected should be near the middle of the DAG with prior_balance == 0.0
    q1 = diag.select_next_diagnostic_question(
        student_id=student_id,
        enrolled_course_id="CRS_ALG1",
        asked_topic_ids=set(),
        foundations_only=True,
        seed=42,
    )
    assert q1 is not None
    assert q1.prior_balance == 0.0
    assert q1.prior_mastery_probability == 0.5
    assert q1.expected_information_gain > 1.0
    # Uses highest KP (#kp_1)
    assert q1.kp_index == 1
    assert q1.blueprint_instance is not None

    # Simulate student answering q1 CORRECTLY and quickly
    p1 = q1.blueprint_instance.parameters["p_1"]
    p2 = q1.blueprint_instance.parameters["p_2"]
    diag.process_diagnostic_submission(
        DiagnosticSubmission(
            student_id=student_id,
            topic_id=q1.topic_id,
            kp_id=q1.kp_id,
            student_answer=str(p1 + p2),
            duration_ms=5000,
            expected_time_seconds=q1.expected_time_seconds,
            target_answer=q1.blueprint_instance.target_answer,
            parameters=q1.blueprint_instance.parameters,
        )
    )

    # Next question should adaptively shift toward unassessed topics near the 50% boundary
    q2 = diag.select_next_diagnostic_question(
        student_id=student_id,
        enrolled_course_id="CRS_ALG1",
        asked_topic_ids={q1.topic_id},
        foundations_only=True,
        seed=43,
    )
    assert q2 is not None
    assert q2.topic_id != q1.topic_id
    # Because ancestors of q1 now have positive balance (> 0), postrequisites still have balance == 0.0
    assert abs(q2.prior_balance) < 0.01


# ---------------------------------------------------------------------------
# Test FR-2.2: Plus-Minus Confidence Balance & Conflict Resolution
# ---------------------------------------------------------------------------

def test_fr2_2_accuracy_time_conflict_and_i_dont_know(tier2_fixture):
    """
    Verifies:
      1. Accuracy-Time Conflict discounting (+1.0 -> +0.15 floor when duration_ms > expected_time_ms).
      2. Explicit "I_DONT_KNOW" flag records -1.0 weight immediately without guessing distortion.
    """
    _, _, _, diag = tier2_fixture
    student_id = "usr_diag_weights"

    # 1. Fast correct answer -> +1.0
    w_fast, disc_fast = diag.compute_answer_weight(
        is_correct=True, duration_ms=15000, expected_time_seconds=30.0
    )
    assert w_fast == 1.0
    assert disc_fast is False

    # 2. Moderate overtime correct answer (60s on 30s expected = 2x) -> 0.575 in (0.15, 1.0)
    w_mid, disc_mid = diag.compute_answer_weight(
        is_correct=True, duration_ms=60000, expected_time_seconds=30.0
    )
    assert 0.15 < w_mid < 1.0
    assert w_mid == pytest.approx(0.575, abs=1e-3)
    assert disc_mid is True

    # 3. Severe overtime correct answer (>= 3x expected_time) -> +0.15 floor
    w_slow, disc_slow = diag.compute_answer_weight(
        is_correct=True, duration_ms=120000, expected_time_seconds=30.0
    )
    assert w_slow == pytest.approx(0.15, abs=1e-4)
    assert disc_slow is True

    # 4. Explicit "I_DONT_KNOW" submission -> records -1.0 immediately
    report_idk = diag.process_diagnostic_submission(
        DiagnosticSubmission(
            student_id=student_id,
            topic_id="trl_alg_lin_002",
            kp_id="trl_alg_lin_002#kp_1",
            student_answer="10",
            duration_ms=2000,
            expected_time_seconds=45.0,
            submission_flag="I_DONT_KNOW",
            target_answer="10",
        )
    )
    assert report_idk.is_correct is False
    assert report_idk.assigned_weight == -1.0
    assert report_idk.updated_balances["trl_alg_lin_002"] == -1.0
    # Negative weight propagates to post-requisites (trl_alg_lin_003, trl_alg_lin_004, etc.)
    assert "trl_alg_lin_003" in report_idk.propagated_postrequisites
    assert report_idk.updated_balances["trl_alg_lin_003"] < 0.0


def test_fr2_2_causal_and_correlational_propagation(tier2_fixture):
    """
    Verifies:
      - Positive weight propagates backward to causal ancestors (encompassed & prerequisite).
      - Positive weight on a module leaf topic (trl_alg_lin_005) propagates correlational
        credit (+0.50 * w) to its module leaf sibling (trl_alg_lin_006), but NOT to non-leaf nodes.
    """
    _, _, _, diag = tier2_fixture
    student_id = "usr_diag_prop"

    # Check module leaf sibling identification
    leaf_sibs = diag.get_module_leaf_siblings("trl_alg_lin_005")
    assert leaf_sibs == ["trl_alg_lin_006"]
    # Non-leaf in the same cluster has no leaf sibling correlation
    assert diag.get_module_leaf_siblings("trl_alg_lin_003") == []

    # Submit correct answer on module leaf trl_alg_lin_005
    report = diag.process_diagnostic_submission(
        DiagnosticSubmission(
            student_id=student_id,
            topic_id="trl_alg_lin_005",
            kp_id="trl_alg_lin_005#kp_1",
            student_answer="9",
            duration_ms=10000,
            expected_time_seconds=60.0,
            target_answer="9",
        )
    )
    assert report.is_correct is True
    assert report.assigned_weight == 1.0

    # Causal ancestors received positive credit
    assert "trl_alg_lin_004" in report.propagated_ancestors
    assert "trl_alg_lin_003" in report.propagated_ancestors
    assert "trl_alg_lin_002" in report.propagated_ancestors
    assert report.updated_balances["trl_alg_lin_004"] > 0.0

    # Correlational module leaf sibling (trl_alg_lin_006) received +0.50 credit
    assert report.propagated_leaf_siblings == ["trl_alg_lin_006"]
    assert report.updated_balances["trl_alg_lin_006"] == pytest.approx(0.50, abs=1e-4)


def test_fr2_2_prerequisite_postrequisite_conflict_resolution(tier2_fixture):
    """
    Verifies Prerequisite-Postrequisite Conflict Resolution:
    When a student passes a postrequisite (trl_alg_lin_004) but fails a prerequisite
    (trl_alg_lin_002), the engine resolves the conflict by revoking credit above the hole
    and placing the student at the bottom of their lowest knowledge hole (trl_alg_lin_002).
    """
    ad, _, _, diag = tier2_fixture
    student_id = "usr_diag_conflict"

    # Student passes foundations up to trl_alg_lin_001
    for tid in ["trl_pra_int_001", "trl_pra_int_002", "trl_pra_int_003", "trl_alg_lin_001"]:
        ad.upsert_student_topic_state(
            StudentTopicState(
                student_id=student_id,
                topic_id=tid,
                status="LOCKED",
                diagnostic_plus_minus_balance=1.5,
            )
        )

    # Student FAILS prerequisite trl_alg_lin_002 (knowledge hole: balance = -0.8)
    ad.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_alg_lin_002",
            status="LOCKED",
            diagnostic_plus_minus_balance=-0.8,
        )
    )

    # Yet student somehow has positive balance on postrequisites trl_alg_lin_003 and trl_alg_lin_004
    ad.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_alg_lin_003",
            status="LOCKED",
            diagnostic_plus_minus_balance=0.6,
        )
    )
    ad.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_alg_lin_004",
            status="LOCKED",
            diagnostic_plus_minus_balance=1.2,
        )
    )

    placement = diag.finalize_diagnostic_placement(
        student_id=student_id,
        enrolled_course_id="CRS_ALG1",
        foundations_only=True,
    )

    # Conflicts on trl_alg_lin_003 and trl_alg_lin_004 must be detected and resolved
    conflict_postreqs = {c.conflicting_postrequisite_id for c in placement.conflicts_resolved}
    assert "trl_alg_lin_003" in conflict_postreqs
    assert "trl_alg_lin_004" in conflict_postreqs

    # Prerequisites below the hole remain MASTERED
    assert "trl_alg_lin_001" in placement.mastered_topic_ids

    # Postrequisites above the hole are stripped of mastery and LOCKED
    assert "trl_alg_lin_003" not in placement.mastered_topic_ids
    assert "trl_alg_lin_004" not in placement.mastered_topic_ids
    assert "trl_alg_lin_003" in placement.locked_topic_ids
    assert "trl_alg_lin_004" in placement.locked_topic_ids

    # Student is placed strictly at the bottom of their lowest knowledge hole: trl_alg_lin_002
    assert placement.lowest_knowledge_hole_ids == ["trl_alg_lin_002"]
    assert placement.available_frontier_topic_ids == ["trl_alg_lin_002"]


# ---------------------------------------------------------------------------
# Test FR-2.3: Conditional Completion, Supplemental Diagnostics & Fast-Track
# ---------------------------------------------------------------------------

def test_fr2_3_conditional_completion_and_cascading_revocation(tier2_fixture):
    """
    Verifies:
      1. Topics with balance >= 1.0 become MASTERED with rep_num = floor(balance) and
         is_conditionally_completed = False.
      2. Topics with 0.0 < balance < 1.0 become MASTERED with is_conditionally_completed = True.
      3. Failing a dependent task immediately revokes credit on conditionally completed
         prerequisites and peels back the knowledge profile.
    """
    ad, _, _, diag = tier2_fixture
    student_id = "usr_diag_conditional"

    # High-confidence mastery on foundations + trl_alg_lin_001 (balance = 2.4 -> rep_num = 2.0)
    for tid in ["trl_pra_int_001", "trl_pra_int_002", "trl_pra_int_003", "trl_alg_lin_001"]:
        ad.upsert_student_topic_state(
            StudentTopicState(
                student_id=student_id,
                topic_id=tid,
                diagnostic_plus_minus_balance=2.4,
            )
        )

    # Low-confidence mastery on trl_alg_lin_002 (balance = 0.35 from slow overtime response)
    # and trl_alg_lin_003 (balance = 0.25)
    ad.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_alg_lin_002",
            diagnostic_plus_minus_balance=0.35,
        )
    )
    ad.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_alg_lin_003",
            diagnostic_plus_minus_balance=0.25,
        )
    )

    placement = diag.finalize_diagnostic_placement(
        student_id=student_id,
        enrolled_course_id="CRS_ALG1",
        foundations_only=True,
    )

    states = ad.get_student_topic_states(student_id)
    assert states["trl_alg_lin_001"].status == "MASTERED"
    assert states["trl_alg_lin_001"].rep_num == 2.0
    assert states["trl_alg_lin_001"].is_conditionally_completed is False

    assert states["trl_alg_lin_002"].status == "MASTERED"
    assert states["trl_alg_lin_002"].is_conditionally_completed is True
    assert states["trl_alg_lin_003"].status == "MASTERED"
    assert states["trl_alg_lin_003"].is_conditionally_completed is True
    assert set(placement.conditionally_completed_topic_ids) == {
        "trl_alg_lin_002",
        "trl_alg_lin_003",
    }
    # trl_alg_lin_004 is unlocked on the frontier because trl_alg_lin_003 is conditionally mastered
    assert "trl_alg_lin_004" in placement.available_frontier_topic_ids

    # Now student FAILS dependent task trl_alg_lin_004!
    revocation = diag.revoke_conditional_completion(
        student_id=student_id,
        failed_topic_id="trl_alg_lin_004",
        enrolled_course_id="CRS_ALG1",
    )

    # Credit for both trl_alg_lin_002 and trl_alg_lin_003 is immediately revoked
    assert set(revocation.revoked_topic_ids) == {"trl_alg_lin_002", "trl_alg_lin_003"}
    # Profile peels back to trl_alg_lin_002 as AVAILABLE, while 003 and 004 lock
    assert revocation.newly_available_topic_ids == ["trl_alg_lin_002"]
    assert "trl_alg_lin_003" in revocation.peeled_back_locked_topic_ids
    assert "trl_alg_lin_004" in revocation.peeled_back_locked_topic_ids

    post_states = ad.get_student_topic_states(student_id)
    assert post_states["trl_alg_lin_002"].status == "AVAILABLE"
    assert post_states["trl_alg_lin_002"].is_conditionally_completed is False
    assert post_states["trl_alg_lin_003"].status == "LOCKED"
    assert post_states["trl_alg_lin_004"].status == "LOCKED"


def test_fr2_3_supplemental_diagnostics(tier2_fixture):
    """
    Verifies 2-5 question Supplemental Diagnostics targeting newly added or rewired
    curriculum nodes with diagnostic_plus_minus_balance == 0.0.
    """
    ad, _, _, diag = tier2_fixture
    student_id = "usr_diag_supplemental"

    # Give non-zero balances to most nodes, leave 3 newly added/rewired nodes at balance == 0.0
    scope = diag.get_diagnostic_scope("CRS_ALG1", student_id=student_id, foundations_only=True)
    zero_nodes = {"trl_alg_lin_003", "trl_alg_lin_005", "trl_alg_lin_006"}
    for tid in scope:
        bal = 0.0 if tid in zero_nodes else 1.5
        ad.upsert_student_topic_state(
            StudentTopicState(
                student_id=student_id,
                topic_id=tid,
                status="LOCKED" if bal == 0.0 else "MASTERED",
                diagnostic_plus_minus_balance=bal,
            )
        )

    structural_delta = {
        "added_nodes": ["trl_alg_lin_005", "trl_alg_lin_006"],
        "rewired_edges": [{"topic_id": "trl_alg_lin_003", "added_prereqs": [], "removed_prereqs": []}],
    }

    supp_questions = diag.build_supplemental_diagnostic(
        student_id=student_id,
        enrolled_course_id="CRS_ALG1",
        structural_delta=structural_delta,
        min_questions=2,
        max_questions=5,
        seed=77,
    )

    assert 2 <= len(supp_questions) <= 5
    asked_topics = {q.topic_id for q in supp_questions}
    assert asked_topics == zero_nodes
    for q in supp_questions:
        assert q.prior_balance == 0.0


def test_fr2_3_fast_track_i_already_know_this_bypass(tier2_fixture):
    """
    Verifies the "I Already Know This" Fast-Track lesson bypass:
      - Generates 2 questions from the highest KP (#kp_1).
      - Denies bypass if overtime, assisted (reference_peeked), wrong answer, or non-highest KP.
      - Grants immediate MASTERED status and unlocks postrequisites when 2 unassisted
        highest-KP questions are answered correctly within expected time.
    """
    ad, _, _, diag = tier2_fixture
    student_id = "usr_fast_track"

    # Prerequisite trl_pra_int_003 is already MASTERED; trl_alg_lin_001 is AVAILABLE
    ad.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_pra_int_003",
            status="MASTERED",
            rep_num=2.0,
        )
    )
    ad.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_alg_lin_001",
            status="AVAILABLE",
        )
    )

    ft_questions = diag.generate_fast_track_questions("trl_alg_lin_001", count=2, base_seed=200)
    assert len(ft_questions) == 2
    assert ft_questions[0].kp_id == "trl_alg_lin_001#kp_1"
    assert ft_questions[1].kp_id == "trl_alg_lin_001#kp_1"

    # Case 1: Fail due to reference_peeked == True
    q0, q1 = ft_questions[0], ft_questions[1]
    ans0 = str(q0.blueprint_instance.parameters["p_1"] + q0.blueprint_instance.parameters["p_2"])
    ans1 = str(q1.blueprint_instance.parameters["p_1"] + q1.blueprint_instance.parameters["p_2"])

    res_peeked = diag.evaluate_fast_track_bypass(
        student_id=student_id,
        topic_id="trl_alg_lin_001",
        submissions=[
            DiagnosticSubmission(
                student_id=student_id,
                topic_id="trl_alg_lin_001",
                kp_id=q0.kp_id,
                student_answer=ans0,
                duration_ms=4000,
                expected_time_seconds=q0.expected_time_seconds,
                reference_peeked=True,
                target_answer=q0.blueprint_instance.target_answer,
                parameters=q0.blueprint_instance.parameters,
            ),
            DiagnosticSubmission(
                student_id=student_id,
                topic_id="trl_alg_lin_001",
                kp_id=q1.kp_id,
                student_answer=ans1,
                duration_ms=4000,
                expected_time_seconds=q1.expected_time_seconds,
                reference_peeked=False,
                target_answer=q1.blueprint_instance.target_answer,
                parameters=q1.blueprint_instance.parameters,
            ),
        ],
    )
    assert res_peeked.bypass_granted is False
    assert res_peeked.updated_status == "AVAILABLE"

    # Case 2: Fail due to overtime (duration_ms > expected_time_seconds * 1000)
    res_overtime = diag.evaluate_fast_track_bypass(
        student_id=student_id,
        topic_id="trl_alg_lin_001",
        submissions=[
            DiagnosticSubmission(
                student_id=student_id,
                topic_id="trl_alg_lin_001",
                kp_id=q0.kp_id,
                student_answer=ans0,
                duration_ms=120000,  # Exceeds ~51s expected limit
                expected_time_seconds=q0.expected_time_seconds,
                reference_peeked=False,
                target_answer=q0.blueprint_instance.target_answer,
                parameters=q0.blueprint_instance.parameters,
            ),
            DiagnosticSubmission(
                student_id=student_id,
                topic_id="trl_alg_lin_001",
                kp_id=q1.kp_id,
                student_answer=ans1,
                duration_ms=4000,
                expected_time_seconds=q1.expected_time_seconds,
                reference_peeked=False,
                target_answer=q1.blueprint_instance.target_answer,
                parameters=q1.blueprint_instance.parameters,
            ),
        ],
    )
    assert res_overtime.bypass_granted is False

    # Case 3: Clean pass -> 2 unassisted highest-KP correct responses within expected time
    res_pass = diag.evaluate_fast_track_bypass(
        student_id=student_id,
        topic_id="trl_alg_lin_001",
        submissions=[
            DiagnosticSubmission(
                student_id=student_id,
                topic_id="trl_alg_lin_001",
                kp_id=q0.kp_id,
                student_answer=ans0,
                duration_ms=4500,
                expected_time_seconds=q0.expected_time_seconds,
                reference_peeked=False,
                target_answer=q0.blueprint_instance.target_answer,
                parameters=q0.blueprint_instance.parameters,
            ),
            DiagnosticSubmission(
                student_id=student_id,
                topic_id="trl_alg_lin_001",
                kp_id=q1.kp_id,
                student_answer=ans1,
                duration_ms=5200,
                expected_time_seconds=q1.expected_time_seconds,
                reference_peeked=False,
                target_answer=q1.blueprint_instance.target_answer,
                parameters=q1.blueprint_instance.parameters,
            ),
        ],
    )
    assert res_pass.bypass_granted is True
    assert res_pass.updated_status == "MASTERED"
    assert res_pass.unassisted_correct_within_time == 2
    assert "trl_alg_lin_002" in res_pass.unlocked_postrequisite_ids

    states = ad.get_student_topic_states(student_id)
    assert states["trl_alg_lin_001"].status == "MASTERED"
    assert states["trl_alg_lin_002"].status == "AVAILABLE"


def test_tier2_cross_backend_bit_identical_parity():
    """
    Executes an identical multi-step adaptive diagnostic trajectory across
    Standalone Relational, Standalone Graph, and In-Memory Reference adapters
    and verifies 100% bit-identical mathematical parity.
    """
    results = {}

    for backend in ["memory", "relational", "graph"]:
        ad = get_storage_adapter(backend)
        ad.ingest_knowledge_graph(SAMPLE_TIER2_GRAPH)
        diag = DiagnosticEngine(ad)
        student_id = "usr_parity_student"

        cover = diag.compress_diagnostic_subgraph(
            "CRS_ALG1", student_id=student_id, foundations_only=True
        )
        asked: set[str] = set()
        selected_sequence: list[str] = []

        # Simulate 4 adaptive diagnostic steps:
        # Pass Pre-Algebra & early Algebra, fail middle Algebra
        for step in range(4):
            q = diag.select_next_diagnostic_question(
                student_id=student_id,
                enrolled_course_id="CRS_ALG1",
                asked_topic_ids=asked,
                foundations_only=True,
                seed=500 + step,
            )
            assert q is not None
            asked.add(q.topic_id)
            selected_sequence.append(q.topic_id)

            # Student knows Pre-Algebra and alg_lin_001..002, doesn't know alg_lin_003+
            knows_topic = q.topic_id in {
                "trl_pra_int_001",
                "trl_pra_int_002",
                "trl_pra_int_003",
                "trl_alg_lin_001",
                "trl_alg_lin_002",
            }
            if knows_topic:
                p1 = q.blueprint_instance.parameters["p_1"]
                p2 = q.blueprint_instance.parameters["p_2"]
                sub = DiagnosticSubmission(
                    student_id=student_id,
                    topic_id=q.topic_id,
                    kp_id=q.kp_id,
                    student_answer=str(p1 + p2),
                    duration_ms=8000,
                    expected_time_seconds=q.expected_time_seconds,
                    target_answer=q.blueprint_instance.target_answer,
                    parameters=q.blueprint_instance.parameters,
                )
            else:
                sub = DiagnosticSubmission(
                    student_id=student_id,
                    topic_id=q.topic_id,
                    kp_id=q.kp_id,
                    submission_flag="I_DONT_KNOW",
                    duration_ms=3000,
                    expected_time_seconds=q.expected_time_seconds,
                )
            diag.process_diagnostic_submission(sub)

        placement = diag.finalize_diagnostic_placement(
            student_id=student_id,
            enrolled_course_id="CRS_ALG1",
            foundations_only=True,
        )
        final_states = ad.get_student_topic_states(student_id)
        balance_snapshot = {
            tid: st.diagnostic_plus_minus_balance
            for tid, st in sorted(final_states.items())
        }

        results[backend] = {
            "cover": cover,
            "selected_sequence": selected_sequence,
            "placement": placement.model_dump(),
            "balances": balance_snapshot,
        }
        if hasattr(ad, "close"):
            ad.close()

    assert results["memory"] == results["relational"]
    assert results["memory"] == results["graph"]
