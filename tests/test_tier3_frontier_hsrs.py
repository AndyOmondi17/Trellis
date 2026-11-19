"""
tests/test_tier3_frontier_hsrs.py
Comprehensive parameterized test suite for Tier 3:
  - FR-3.1: Knowledge Frontier Calculation & Foundational Momentum Sequencer
  - FR-3.2: Student-Topic Learning Speed Calibration & Preventative Remediation Gate
Parameterized across all standalone database backends: memory, relational, graph.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from trellis.domain.models import StudentProfile, StudentTopicState
from trellis.engine.frontier_service import FrontierService
from trellis.engine.hsrs_fire_engine import HSRSFireEngine
from trellis.engine.topology_service import TopologyService
from trellis.ports.storage_port import get_storage_adapter


def _make_test_node(
    node_id: str,
    title: str,
    course_id: str,
    cluster: str,
    prereqs: list[tuple[str, str]],  # (prereq_id, strength)
    encompassed: list[tuple[str, float]],  # (encompassed_id, weight)
    grade_min: int = 8,
    difficulty: float = 0.5,
) -> dict:
    """Constructs a clean-room TopicNode dictionary for testing."""
    return {
        "id": node_id,
        "title": title,
        "course_id": course_id,
        "canonical_domain": "03_equations_and_inequalities",
        "domain_cluster": cluster,
        "skill_type": "PROCEDURAL",
        "bloom_cognitive_tier": "PROCEDURAL_EXECUTION",
        "grade_level_min": grade_min,
        "grade_level_max": grade_min + 1,
        "is_core": True,
        "in_foundations_sequence": True,
        "requires_automaticity": False,
        "base_difficulty": difficulty,
        "estimated_xp": 3,
        "mastery_gate_n": 3,
        "key_prerequisite_id": prereqs[0][0] if prereqs else None,
        "aligned_standards": [],
        "mastery_criteria": [f"Master {title}"],
        "knowledge_points": [
            {
                "kp_index": 0,
                "kp_id": f"{node_id}#kp_0",
                "scaffolding_tier": "KP_1_BASELINE",
                "kp_focus": f"Baseline for {title}",
                "novel_chunk_count": 2,
                "key_prerequisite_id": prereqs[0][0] if prereqs else None,
                "rule_statement": f"Rule for {title}.",
                "diagnostic_misconceptions": [],
            }
        ],
        "prerequisites": [
            {"prerequisite_id": pid, "strength": strength, "rationale": "Prerequisite"}
            for pid, strength in prereqs
        ],
        "encompassed_skills": [
            {
                "encompassed_id": eid,
                "fire_weight": fw,
                "is_non_ancestor": False,
                "rationale": "Encompassed skill",
            }
            for eid, fw in encompassed
        ],
    }


TIER3_TEST_GRAPH = {
    "schema_version": "2.0.0-trellis",
    "graph_id": "trellis-tier3-dag",
    "course_catalog": [
        {
            "course_id": "CRS_PREALG",
            "course_code": "pra",
            "title": "Pre-Algebra Foundations",
            "grade_band": "6-7",
            "benchmark_xp": 1000,
            "mastery_floor_course_ids": [],
        },
        {
            "course_id": "CRS_ALG1",
            "course_code": "alg",
            "title": "Algebra I",
            "grade_band": "8-9",
            "benchmark_xp": 1500,
            "mastery_floor_course_ids": [],
        },
    ],
    "nodes": [
        # Pre-Algebra foundational nodes
        _make_test_node(
            node_id="trl_pra_001",
            title="Integers and Absolute Value",
            course_id="CRS_PREALG",
            cluster="CRS_PREALG::num",
            prereqs=[],
            encompassed=[],
            grade_min=6,
            difficulty=0.3,
        ),
        _make_test_node(
            node_id="trl_pra_002",
            title="Fraction Addition and Subtraction",
            course_id="CRS_PREALG",
            cluster="CRS_PREALG::num",
            prereqs=[("trl_pra_001", "hard")],
            encompassed=[("trl_pra_001", 0.5)],
            grade_min=7,
            difficulty=0.4,
        ),
        # Algebra 1 enrolled nodes
        _make_test_node(
            node_id="trl_alg_001",
            title="One-Step Linear Equations",
            course_id="CRS_ALG1",
            cluster="CRS_ALG1::lin",
            prereqs=[],  # Independent starter node in Alg 1
            encompassed=[],
            grade_min=8,
            difficulty=0.4,
        ),
        _make_test_node(
            node_id="trl_alg_002",
            title="Two-Step Linear Equations",
            course_id="CRS_ALG1",
            cluster="CRS_ALG1::lin",
            prereqs=[("trl_alg_001", "hard")],
            encompassed=[("trl_alg_001", 0.5)],
            grade_min=8,
            difficulty=0.5,
        ),
        _make_test_node(
            node_id="trl_alg_003",
            title="Rational Coefficient Linear Equations",
            course_id="CRS_ALG1",
            cluster="CRS_ALG1::lin",
            # Requires both an Alg1 topic and a foundational Prealg topic!
            prereqs=[("trl_alg_002", "hard"), ("trl_pra_002", "hard")],
            encompassed=[("trl_alg_002", 0.5), ("trl_pra_002", 0.5)],
            grade_min=9,
            difficulty=0.8,
        ),
    ],
}


@pytest.fixture(params=["memory", "relational", "graph"])
def tier3_fixture(request):
    """Initializes standalone adapter, ingests Tier 3 DAG, and instantiates services."""
    backend = request.param
    storage = get_storage_adapter(backend)
    storage.ingest_knowledge_graph(TIER3_TEST_GRAPH)
    topo = TopologyService(storage)
    frontier_svc = FrontierService(storage, topology_service=topo)
    hsrs_engine = HSRSFireEngine(storage)
    yield storage, topo, frontier_svc, hsrs_engine
    if hasattr(storage, "close"):
        storage.close()


# ===========================================================================
# FR-3.1 Tests: Knowledge Frontier Calculation & Foundational Momentum
# ===========================================================================

def test_frontier_initial_unlock_of_root_nodes(tier3_fixture):
    """Verifies that nodes with 0 hard prerequisites are unlocked to AVAILABLE."""
    storage, topo, frontier_svc, _ = tier3_fixture
    student_id = "usr_t3_root_01"
    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1")
    )

    frontier = frontier_svc.compute_knowledge_frontier(student_id, "CRS_ALG1")
    frontier_ids = [n.id for n in frontier]

    # trl_alg_001 has 0 hard prerequisites -> must be AVAILABLE
    assert "trl_alg_001" in frontier_ids
    # trl_alg_002 requires trl_alg_001 (unmastered) -> must NOT be in frontier
    assert "trl_alg_002" not in frontier_ids
    assert "trl_alg_003" not in frontier_ids

    # Verify state transition was persisted
    states = storage.get_student_topic_states(student_id, ["trl_alg_001", "trl_alg_002"])
    assert states["trl_alg_001"].status == "AVAILABLE"
    assert states.get("trl_alg_002") is None or states["trl_alg_002"].status == "LOCKED"


def test_frontier_strict_hard_prerequisite_gating(tier3_fixture):
    """
    Verifies that a node unlocks strictly when 100% of incoming hard parents
    are MASTERED or MASTERED_FLOOR.
    """
    storage, topo, frontier_svc, _ = tier3_fixture
    student_id = "usr_t3_gating_01"
    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1")
    )

    # Initially trl_alg_002 is locked
    assert not frontier_svc.is_topic_unlocked(student_id, "trl_alg_002")

    # Master trl_alg_001
    storage.upsert_student_topic_state(
        StudentTopicState(student_id=student_id, topic_id="trl_alg_001", status="MASTERED")
    )

    # Now trl_alg_002 should unlock
    assert frontier_svc.is_topic_unlocked(student_id, "trl_alg_002")

    frontier = frontier_svc.compute_knowledge_frontier(student_id, "CRS_ALG1")
    frontier_ids = [n.id for n in frontier]

    assert "trl_alg_002" in frontier_ids
    # trl_alg_001 is already MASTERED, so it must no longer be on the learning frontier
    assert "trl_alg_001" not in frontier_ids


def test_frontier_multi_parent_gating(tier3_fixture):
    """
    trl_alg_003 requires both trl_alg_002 (Alg 1) and trl_pra_002 (Prealg).
    It unlocks if and only if BOTH are MASTERED.
    """
    storage, topo, frontier_svc, _ = tier3_fixture
    student_id = "usr_t3_multi_parent_01"
    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1")
    )

    # Master trl_alg_002 only
    storage.upsert_student_topic_state(
        StudentTopicState(student_id=student_id, topic_id="trl_alg_002", status="MASTERED")
    )
    assert not frontier_svc.is_topic_unlocked(student_id, "trl_alg_003")

    # Master trl_pra_002 as well
    storage.upsert_student_topic_state(
        StudentTopicState(student_id=student_id, topic_id="trl_pra_002", status="MASTERED")
    )
    assert frontier_svc.is_topic_unlocked(student_id, "trl_alg_003")


def test_foundational_momentum_sequencer(tier3_fixture):
    """
    Verifies that the Foundational Momentum Sequencer prioritizes available
    enrolled course topics before dropping down to lower-grade foundational gaps.
    """
    storage, topo, frontier_svc, _ = tier3_fixture
    student_id = "usr_t3_momentum_01"
    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1")
    )

    # Initially:
    # - In CRS_ALG1: trl_alg_001 is available (momentum!).
    # - In foundational CRS_PREALG: trl_pra_001 is available (has 0 prereqs).
    result = frontier_svc.get_sequenced_frontier_result(student_id, "CRS_ALG1")

    assert result.has_enrolled_momentum is True
    assert [n.id for n in result.enrolled_frontier_nodes] == ["trl_alg_001"]
    assert [n.id for n in result.foundational_gap_nodes] == ["trl_pra_001"]

    # Enrolled topic must lead the sequence!
    sequence_ids = [n.id for n in result.prioritized_sequence]
    assert sequence_ids[0] == "trl_alg_001"
    assert sequence_ids[1] == "trl_pra_001"


def test_momentum_fallback_when_enrolled_course_blocked(tier3_fixture):
    """
    If all enrolled course topics are blocked behind foundational gaps,
    the sequencer automatically surfaces the foundational gap topics to unblock the student.
    """
    storage, topo, frontier_svc, _ = tier3_fixture
    student_id = "usr_t3_blocked_01"
    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1")
    )

    # Suppose student already mastered trl_alg_001 and trl_alg_002
    storage.upsert_student_topic_state(
        StudentTopicState(student_id=student_id, topic_id="trl_alg_001", status="MASTERED")
    )
    storage.upsert_student_topic_state(
        StudentTopicState(student_id=student_id, topic_id="trl_alg_002", status="MASTERED")
    )
    # But trl_alg_003 is still locked because trl_pra_002 is not mastered!
    # And trl_pra_002 requires trl_pra_001 (not mastered).

    result = frontier_svc.get_sequenced_frontier_result(student_id, "CRS_ALG1")

    assert result.has_enrolled_momentum is False
    assert len(result.enrolled_frontier_nodes) == 0
    # Available foundational gap is trl_pra_001 (root of foundational gap)
    assert [n.id for n in result.foundational_gap_nodes] == ["trl_pra_001"]
    assert [n.id for n in result.prioritized_sequence] == ["trl_pra_001"]


# ===========================================================================
# FR-3.2 Tests: Learning Speed Calibration & Preventative Gating
# ===========================================================================

def test_calibrate_learning_speed_bounds(tier3_fixture):
    """Verifies speed calculation clamping to [0.25, 2.50]."""
    _, _, _, hsrs = tier3_fixture

    # Very high ability, low difficulty -> capped at 2.50
    assert hsrs.calibrate_learning_speed(student_ability=2.5, topic_difficulty=0.2) == 2.50

    # Very low ability, high difficulty -> floor at 0.25
    assert hsrs.calibrate_learning_speed(student_ability=0.1, topic_difficulty=0.9) == 0.25

    # Nominal calculation: 0.6 / 0.5 = 1.2
    assert hsrs.calibrate_learning_speed(student_ability=0.6, topic_difficulty=0.5) == 1.20


def test_preventative_remediation_gate(tier3_fixture):
    """
    Verifies that speed < 1.0 sets allow_implicit_fire_credit = False (forcing explicit review),
    and speed >= 1.0 sets allow_implicit_fire_credit = True.
    """
    _, _, _, hsrs = tier3_fixture

    assert hsrs.evaluate_preventative_gate(speed=1.5) is True
    assert hsrs.evaluate_preventative_gate(speed=1.0) is True
    assert hsrs.evaluate_preventative_gate(speed=0.99) is False
    assert hsrs.evaluate_preventative_gate(speed=0.5) is False


def test_initialize_student_ability_from_local_neighborhood(tier3_fixture):
    """Verifies student_ability initialization from the mean of neighbor states."""
    storage, _, _, hsrs = tier3_fixture
    student_id = "usr_t3_ability_init_01"
    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1")
    )

    # Set up neighbor state on trl_alg_001 (prerequisite of trl_alg_002)
    storage.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_alg_001",
            status="MASTERED",
            student_ability=1.4,
            topic_difficulty=0.4,
            speed=2.5,
        )
    )

    # Initialize ability for trl_alg_002
    init_ability = hsrs.initialize_student_ability(student_id, "trl_alg_002")
    # Should draw from trl_alg_001's ability (1.4)
    assert init_ability == 1.4


def test_ewma_ability_update_on_correct_and_incorrect(tier3_fixture):
    """
    Verifies EWMA ability updates:
    new_ability = (1 - alpha) * old_ability + alpha * obs
    """
    storage, _, _, hsrs = tier3_fixture
    student_id = "usr_t3_ewma_01"
    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1")
    )
    topic_id = "trl_alg_001"  # difficulty = 0.4

    # 1. First correct attempt (starts from baseline 1.0, alpha=0.2, obs=1.0)
    # new_ability = 0.8 * 1.0 + 0.2 * 1.0 = 1.0
    res1 = hsrs.record_attempt(student_id, topic_id, is_correct=True, alpha=0.2, propagate=False)
    assert res1.new_ability == 1.0
    assert res1.new_speed == 2.5  # 1.0 / 0.4 = 2.5 (clamped)
    assert res1.allow_implicit_fire_credit is True

    # 2. Incorrect attempt (alpha=0.2, obs=0.0)
    # new_ability = 0.8 * 1.0 + 0.2 * 0.0 = 0.8
    res2 = hsrs.record_attempt(student_id, topic_id, is_correct=False, alpha=0.2, propagate=False)
    assert res2.new_ability == 0.8
    assert res2.new_speed == 2.0  # 0.8 / 0.4 = 2.0
    assert res2.allow_implicit_fire_credit is True

    # 3. Multiple incorrect attempts driving ability down and tripping the preventative gate
    # diff = 0.8 on trl_alg_003
    t3_state = StudentTopicState(
        student_id=student_id,
        topic_id="trl_alg_003",
        status="IN_PROGRESS",
        student_ability=0.7,
        topic_difficulty=0.8,
        speed=0.875,
        allow_implicit_fire_credit=False,
    )
    storage.upsert_student_topic_state(t3_state)

    res3 = hsrs.record_attempt(student_id, "trl_alg_003", is_correct=False, alpha=0.2, propagate=False)
    # new_ability = 0.8 * 0.7 + 0.2 * 0.0 = 0.56
    assert res3.new_ability == 0.56
    # speed = 0.56 / 0.8 = 0.70 < 1.0 -> preventative gate trips!
    assert res3.new_speed == 0.7
    assert res3.allow_implicit_fire_credit is False


def test_bidirectional_ewma_propagation(tier3_fixture):
    """
    Verifies that:
      - Correct attempt on trl_alg_002 propagates downward to encompassed trl_alg_001.
      - Incorrect attempt on trl_alg_001 propagates upward to encompassing trl_alg_002.
    """
    storage, _, _, hsrs = tier3_fixture
    student_id = "usr_t3_bidirectional_01"
    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1")
    )

    # Set up initial states for both nodes
    storage.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_alg_001",
            status="MASTERED",
            student_ability=1.0,
            topic_difficulty=0.4,
            speed=2.5,
            allow_implicit_fire_credit=True,
        )
    )
    storage.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_alg_002",
            status="IN_PROGRESS",
            student_ability=1.0,
            topic_difficulty=0.5,
            speed=2.0,
            allow_implicit_fire_credit=True,
        )
    )

    # 1. Correct attempt on advanced node trl_alg_002 -> downward trickle to trl_alg_001
    res_down = hsrs.record_attempt(
        student_id=student_id,
        topic_id="trl_alg_002",
        is_correct=True,
        alpha=0.2,
        propagate=True,
    )
    assert len(res_down.propagated_updates) >= 1
    down_delta = next(d for d in res_down.propagated_updates if d.topic_id == "trl_alg_001")
    assert down_delta.direction == "DOWNWARD_ENCOMPASSED"
    assert down_delta.compound_weight == 0.5

    # 2. Incorrect attempt on simpler node trl_alg_001 -> upward penalty to trl_alg_002
    res_up = hsrs.record_attempt(
        student_id=student_id,
        topic_id="trl_alg_001",
        is_correct=False,
        alpha=0.2,
        propagate=True,
    )
    assert len(res_up.propagated_updates) >= 1
    up_delta = next(d for d in res_up.propagated_updates if d.topic_id == "trl_alg_002")
    assert up_delta.direction == "UPWARD_ENCOMPASSED" or up_delta.direction == "UPWARD_ENCOMPASSING"
    assert up_delta.new_ability < up_delta.prior_ability


# ===========================================================================
# FR-3.3 Tests: Memory Decay, Downward FIRe & Upward Penalty
# ===========================================================================

def test_memory_decay_half_life(tier3_fixture):
    """
    Verifies that memory strength follows half-life decay:
    M = (0.5)^(days_elapsed / stability_interval_days)
    """
    _, _, _, hsrs = tier3_fixture

    interval = 10.0

    # At 0 days elapsed -> M = 1.0
    assert hsrs.calculate_memory_strength(0.0, interval) == 1.0

    # At exactly 1 interval elapsed -> M = 0.50
    assert hsrs.calculate_memory_strength(10.0, interval) == 0.50

    # At 2 intervals elapsed -> M = 0.25
    assert hsrs.calculate_memory_strength(20.0, interval) == 0.25

    # At half interval elapsed -> M = (0.5)^0.5 ≈ 0.7071
    assert abs(hsrs.calculate_memory_strength(5.0, interval) - 0.7071) < 0.001


def test_memory_decay_sweep_transition(tier3_fixture):
    """
    Verifies that sweep_and_update_memory_decay transitions MASTERED -> DUE_FOR_REVIEW
    strictly when memory <= 0.50.
    """
    from datetime import datetime, timedelta, timezone

    storage, _, _, hsrs = tier3_fixture
    student_id = "usr_t3_sweep_01"
    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1")
    )

    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    # Topic A: reviewed 15 days ago, interval 10.0 -> M = (0.5)^1.5 = 0.3536 <= 0.50 -> DUE_FOR_REVIEW
    state_a = StudentTopicState(
        student_id=student_id,
        topic_id="trl_alg_001",
        status="MASTERED",
        stability_interval_days=10.0,
        last_reviewed_at=now - timedelta(days=15),
        student_ability=1.0,
        topic_difficulty=0.4,
        speed=2.5,
    )
    # Topic B: reviewed 3 days ago, interval 10.0 -> M = (0.5)^0.3 = 0.8122 > 0.50 -> stays MASTERED
    state_b = StudentTopicState(
        student_id=student_id,
        topic_id="trl_alg_002",
        status="MASTERED",
        stability_interval_days=10.0,
        last_reviewed_at=now - timedelta(days=3),
        student_ability=1.0,
        topic_difficulty=0.5,
        speed=2.0,
    )
    storage.upsert_student_topic_state(state_a)
    storage.upsert_student_topic_state(state_b)

    transitioned = hsrs.sweep_and_update_memory_decay(student_id, as_of=now)
    transitioned_ids = [s.topic_id for s in transitioned]

    assert "trl_alg_001" in transitioned_ids
    assert "trl_alg_002" not in transitioned_ids

    # Check updated records in storage
    states = storage.get_student_topic_states(student_id, ["trl_alg_001", "trl_alg_002"])
    assert states["trl_alg_001"].status == "DUE_FOR_REVIEW"
    assert states["trl_alg_002"].status == "MASTERED"


def test_early_repetition_discount(tier3_fixture):
    """
    Verifies that early repetitions (days_elapsed < interval) discount rawDelta.
    """
    _, _, _, hsrs = tier3_fixture

    interval = 10.0

    # Completed at 5 days (half interval) -> discount = 0.50
    assert hsrs.calculate_early_review_discount(5.0, interval) == 0.50

    # Completed at or after interval -> discount = 1.0 (no discount)
    assert hsrs.calculate_early_review_discount(10.0, interval) == 1.0
    assert hsrs.calculate_early_review_discount(12.0, interval) == 1.0


def test_downward_fire_credit_trickle_and_interval_expansion(tier3_fixture):
    """
    Verifies that passing advanced node trl_alg_002:
      1. Trickles discounted credit Delta_C = rawDelta * prod(w_i) down to trl_alg_001.
      2. Expands interval I_{k+1} = I_k * (1.0 + 0.5 * speed) when cumulative credit >= 1.0.
    """
    from datetime import datetime, timezone

    storage, _, _, hsrs = tier3_fixture
    student_id = "usr_t3_trickle_01"
    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1")
    )

    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    # trl_alg_001: simpler node (interval=2.0, speed=2.5, cumulative_credit=0.6)
    storage.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_alg_001",
            status="MASTERED",
            stability_interval_days=2.0,
            cumulative_fire_credit=0.6,
            student_ability=1.0,
            topic_difficulty=0.4,
            speed=2.5,
            allow_implicit_fire_credit=True,
            last_reviewed_at=now,
        )
    )
    # trl_alg_002: advanced node encompassing trl_alg_001 (w=0.5), reviewed 4 days ago
    storage.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_alg_002",
            status="IN_PROGRESS",
            stability_interval_days=4.0,
            student_ability=1.0,
            topic_difficulty=0.5,
            speed=2.0,
            allow_implicit_fire_credit=True,
            last_reviewed_at=now - timedelta(days=4),
        )
    )

    # Pass advanced node trl_alg_002 on time (raw_delta=1.0, days_elapsed=4.0 >= 4.0)
    res = hsrs.process_explicit_repetition(
        student_id=student_id,
        topic_id="trl_alg_002",
        is_success=True,
        raw_delta=1.0,
        as_of=now,
    )

    # Downward transfer to trl_alg_001: Delta_C = 1.0 * 0.5 = 0.5
    # Total credit on trl_alg_001 = 0.6 + 0.5 = 1.1 >= 1.0 -> triggers interval expansion!
    assert len(res.downward_fire_transfers) >= 1
    transfer = next(t for t in res.downward_fire_transfers if t.ancestor_id == "trl_alg_001")
    assert transfer.credit_delta == 0.5
    assert transfer.interval_expanded is True
    # Mult = 1.0 + 0.5 * 2.5 = 2.25 -> new interval = 2.0 * 2.25 = 4.5
    assert transfer.new_interval == 4.5
    # Consumes 1.0 credit -> remainder = 1.1 - 1.0 = 0.1
    assert abs(transfer.cumulative_credit_after - 0.1) < 0.001


def test_preventative_gate_blocks_downward_fire_credit(tier3_fixture):
    """
    Verifies that when an ancestor has allow_implicit_fire_credit == False (speed < 1.0),
    it is blocked from receiving implicit FIRe credit.
    """
    from datetime import datetime, timezone

    storage, _, _, hsrs = tier3_fixture
    student_id = "usr_t3_blocked_fire_01"
    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1")
    )

    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    # trl_alg_001 has speed 0.5 < 1.0 -> allow_implicit_fire_credit = False
    storage.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_alg_001",
            status="MASTERED",
            stability_interval_days=2.0,
            cumulative_fire_credit=0.0,
            student_ability=0.2,
            topic_difficulty=0.4,
            speed=0.5,
            allow_implicit_fire_credit=False,
            last_reviewed_at=now,
        )
    )
    storage.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_alg_002",
            status="IN_PROGRESS",
            stability_interval_days=4.0,
            student_ability=1.0,
            topic_difficulty=0.5,
            speed=2.0,
            allow_implicit_fire_credit=True,
            last_reviewed_at=now,
        )
    )

    res = hsrs.process_explicit_repetition(
        student_id=student_id,
        topic_id="trl_alg_002",
        is_success=True,
        raw_delta=1.0,
        as_of=now,
    )

    # Must NOT transfer credit to trl_alg_001
    assert not any(t.ancestor_id == "trl_alg_001" for t in res.downward_fire_transfers)
    st1 = storage.get_student_topic_states(student_id, ["trl_alg_001"])["trl_alg_001"]
    assert st1.cumulative_fire_credit == 0.0


def test_implicit_credit_knocks_out_due_for_review(tier3_fixture):
    """
    Verifies that when implicit credit reaches >= 1.0 on a DUE_FOR_REVIEW topic,
    it expands the interval and restores status to MASTERED.
    """
    from datetime import datetime, timezone

    storage, _, _, hsrs = tier3_fixture
    student_id = "usr_t3_knockout_01"
    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1")
    )

    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    # trl_alg_001 is DUE_FOR_REVIEW with cumulative_credit=0.7
    storage.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_alg_001",
            status="DUE_FOR_REVIEW",
            stability_interval_days=3.0,
            cumulative_fire_credit=0.7,
            student_ability=1.0,
            topic_difficulty=0.4,
            speed=2.5,
            allow_implicit_fire_credit=True,
            last_reviewed_at=now,
        )
    )
    storage.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_alg_002",
            status="MASTERED",
            stability_interval_days=5.0,
            student_ability=1.0,
            topic_difficulty=0.5,
            speed=2.0,
            allow_implicit_fire_credit=True,
            last_reviewed_at=now - timedelta(days=5),
        )
    )

    # Pass trl_alg_002 (w=0.5) -> Delta_C = 0.5 -> total = 1.2 >= 1.0
    res = hsrs.process_explicit_repetition(
        student_id=student_id,
        topic_id="trl_alg_002",
        is_success=True,
        raw_delta=1.0,
        as_of=now,
    )

    transfer = next(t for t in res.downward_fire_transfers if t.ancestor_id == "trl_alg_001")
    assert transfer.status_transitioned == "MASTERED"

    st1 = storage.get_student_topic_states(student_id, ["trl_alg_001"])["trl_alg_001"]
    assert st1.status == "MASTERED"
    assert st1.memory_strength == 1.0


def test_upward_penalty_and_summer_slide(tier3_fixture):
    """
    Verifies that failing simpler node trl_alg_001:
      1. Applies overdue summer slide decay (decay >= 1.0).
      2. Propagates penalty upward to encompassing trl_alg_002.
      3. Decays trl_alg_002 memory and flags DUE_FOR_REVIEW if memory <= 0.50.
    """
    from datetime import datetime, timedelta, timezone

    storage, _, _, hsrs = tier3_fixture
    student_id = "usr_t3_penalty_01"
    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1")
    )

    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    # trl_alg_001: reviewed 6 days ago with interval=2.0 -> overdue ratio = 3.0 -> decay = 3.0
    storage.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_alg_001",
            status="DUE_FOR_REVIEW",
            stability_interval_days=2.0,
            rep_num=3.0,
            student_ability=1.0,
            topic_difficulty=0.4,
            speed=2.5,
            allow_implicit_fire_credit=True,
            last_reviewed_at=now - timedelta(days=6),
        )
    )
    # trl_alg_002: encompassing descendant (rep_num=4.0, interval=6.0, memory=0.60)
    storage.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_alg_002",
            status="MASTERED",
            stability_interval_days=6.0,
            rep_num=4.0,
            memory_strength=0.60,
            student_ability=1.0,
            topic_difficulty=0.5,
            speed=2.0,
            allow_implicit_fire_credit=True,
            last_reviewed_at=now,
        )
    )

    # Fail repetition on trl_alg_001
    res = hsrs.process_explicit_repetition(
        student_id=student_id,
        topic_id="trl_alg_001",
        is_success=False,
        raw_delta=1.0,
        as_of=now,
    )

    assert res.is_success is False
    assert res.decay_factor == 3.0  # 6.0 / 2.0 = 3.0 overdue summer slide!

    # Verify upward penalty on trl_alg_002
    assert len(res.upward_penalty_transfers) >= 1
    pen_transfer = next(t for t in res.upward_penalty_transfers if t.descendant_id == "trl_alg_002")
    assert pen_transfer.penalty_delta > 0
    assert pen_transfer.new_rep_num < pen_transfer.prior_rep_num
    # Prior memory was 0.60, penalty deduction = 0.25 * 0.5 = 0.125 -> 0.475 <= 0.50 -> DUE_FOR_REVIEW
    assert pen_transfer.status_transitioned == "DUE_FOR_REVIEW"

    st2 = storage.get_student_topic_states(student_id, ["trl_alg_002"])["trl_alg_002"]
    assert st2.status == "DUE_FOR_REVIEW"



# ===========================================================================
# Argon Audit Tests: Multi-Path Deduplication, Cramming Defense & Parity
# ===========================================================================

def test_early_review_prevents_interval_cramming_inflation(tier3_fixture):
    """
    Verifies that premature review cramming (days_elapsed == 0 -> discount == 0)
    does NOT expand stability_interval_days, whereas partial elapsed time scales
    interval expansion proportionally.
    """
    storage, _, _, hsrs = tier3_fixture
    student_id = "usr_t3_cram_01"
    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1")
    )

    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
    storage.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_alg_001",
            status="MASTERED",
            stability_interval_days=4.0,
            rep_num=2.0,
            student_ability=0.8,
            topic_difficulty=0.4,
            speed=2.0,
            last_reviewed_at=now,
        )
    )

    # 1. Immediate review at 0 seconds elapsed -> discount = 0.0 -> zero interval inflation
    res_zero = hsrs.process_explicit_repetition(
        student_id=student_id,
        topic_id="trl_alg_001",
        is_success=True,
        raw_delta=1.0,
        as_of=now,
    )
    assert res_zero.discount_factor == 0.0
    assert res_zero.effective_delta == 0.0
    assert res_zero.target_interval_after == 4.0
    assert res_zero.target_rep_num_after == 2.0

    # 2. Review at 2.0 days elapsed (half of 4.0d interval) -> discount = 0.5
    # Expansion mult = 1.0 + 0.5 * 2.0 * 0.5 = 1.5 -> new interval = 4.0 * 1.5 = 6.0
    res_half = hsrs.process_explicit_repetition(
        student_id=student_id,
        topic_id="trl_alg_001",
        is_success=True,
        raw_delta=1.0,
        as_of=now + timedelta(days=2),
    )
    assert res_half.discount_factor == 0.5
    assert res_half.effective_delta == 0.5
    assert res_half.target_interval_after == 6.0


def test_interval_contraction_floor_and_overdue_decay_bounds(tier3_fixture):
    """
    Verifies that extreme overdue failures clamp decay to [1.0, 5.0],
    never contract stability_interval_days below the 0.5-day floor,
    never drop rep_num below 0.0, and reset stale cumulative_fire_credit to 0.0.
    """
    storage, _, _, hsrs = tier3_fixture
    student_id = "usr_t3_bounds_01"
    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1")
    )

    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
    storage.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_alg_001",
            status="MASTERED",
            stability_interval_days=0.6,
            rep_num=1.0,
            cumulative_fire_credit=0.85,
            student_ability=1.0,
            topic_difficulty=0.4,
            speed=2.5,
            last_reviewed_at=now - timedelta(days=90),  # 150x overdue!
        )
    )

    # Also test negative raw_delta routing automatically to failure branch
    res = hsrs.process_explicit_repetition(
        student_id=student_id,
        topic_id="trl_alg_001",
        is_success=True,
        raw_delta=-1.0,
        as_of=now,
    )
    assert res.is_success is False
    assert res.decay_factor == 5.0  # Clamped at ceiling 5.0
    assert res.target_interval_after == 0.5  # Clamped at floor 0.5 days
    assert res.target_rep_num_after == 0.0  # Clamped at floor 0.0

    st = storage.get_student_topic_states(student_id, ["trl_alg_001"])["trl_alg_001"]
    assert st.cumulative_fire_credit == 0.0
    assert st.status == "DUE_FOR_REVIEW"


def test_frontier_service_ability_and_due_review_integration(tier3_fixture):
    """
    Verifies the FR-3.1 <-> FR-3.2/3.3 integration seam:
      1. Unlocking a node via FrontierService initializes student_ability from
         the Local Neighborhood mean and sets speed & allow_implicit_fire_credit.
      2. DUE_FOR_REVIEW nodes are excluded from unmastered frontier lesson lists.
    """
    storage, _, frontier_svc, _ = tier3_fixture
    student_id = "usr_t3_seam_01"
    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1")
    )

    # Master trl_alg_001 with low student_ability = 0.35
    storage.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_alg_001",
            status="MASTERED",
            student_ability=0.35,
            topic_difficulty=0.4,
            speed=0.875,
            allow_implicit_fire_credit=False,
        )
    )

    # Unlock trl_alg_002 (difficulty = 0.5) via FrontierService
    frontier = frontier_svc.compute_knowledge_frontier(student_id, "CRS_ALG1")
    assert [n.id for n in frontier] == ["trl_alg_002"]

    st2 = storage.get_student_topic_states(student_id, ["trl_alg_002"])["trl_alg_002"]
    assert st2.status == "AVAILABLE"
    assert st2.student_ability == 0.35  # Initialized from neighbor trl_alg_001!
    assert st2.speed == 0.70  # 0.35 / 0.50 = 0.70 < 1.0
    assert st2.allow_implicit_fire_credit is False

    # Now flip trl_alg_001 to DUE_FOR_REVIEW and verify it is NOT returned as a frontier lesson
    st1 = storage.get_student_topic_states(student_id, ["trl_alg_001"])["trl_alg_001"]
    st1.status = "DUE_FOR_REVIEW"
    storage.upsert_student_topic_state(st1)

    # Note: because trl_alg_001 is now DUE_FOR_REVIEW (not MASTERED), trl_alg_002 also re-locks!
    frontier_after = frontier_svc.compute_knowledge_frontier(student_id, "CRS_ALG1")
    assert "trl_alg_001" not in [n.id for n in frontier_after]


def test_tier3_cross_backend_bit_identical_parity():
    """
    Executes an identical multi-step workflow (including a diamond encompassing DAG)
    across memory, relational, and graph backends and verifies bit-identical state parity.
    """
    diamond_graph = {
        "schema_version": "2.0.0-trellis",
        "graph_id": "trellis-tier3-diamond-parity",
        "course_catalog": TIER3_TEST_GRAPH["course_catalog"],
        "nodes": [
            _make_test_node(
                node_id="trl_alg_001",
                title="Root Skill",
                course_id="CRS_ALG1",
                cluster="CRS_ALG1::lin",
                prereqs=[],
                encompassed=[],
                difficulty=0.4,
            ),
            _make_test_node(
                node_id="trl_alg_002",
                title="Branch A",
                course_id="CRS_ALG1",
                cluster="CRS_ALG1::lin",
                prereqs=[("trl_alg_001", "hard")],
                encompassed=[("trl_alg_001", 0.5)],
                difficulty=0.5,
            ),
            _make_test_node(
                node_id="trl_alg_003",
                title="Branch B",
                course_id="CRS_ALG1",
                cluster="CRS_ALG1::lin",
                prereqs=[("trl_alg_001", "hard")],
                encompassed=[("trl_alg_001", 0.8)],
                difficulty=0.5,
            ),
            _make_test_node(
                node_id="trl_alg_004",
                title="Diamond Apex (2 paths to trl_alg_001: 0.5*0.5=0.25 vs 0.5*0.8=0.40)",
                course_id="CRS_ALG1",
                cluster="CRS_ALG1::lin",
                prereqs=[("trl_alg_002", "hard"), ("trl_alg_003", "hard")],
                encompassed=[("trl_alg_002", 0.5), ("trl_alg_003", 0.5)],
                difficulty=0.6,
            ),
        ],
    }

    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
    snapshots = {}

    for backend in ("memory", "relational", "graph"):
        ad = get_storage_adapter(backend)
        ad.ingest_knowledge_graph(diamond_graph)
        topo = TopologyService(ad)
        f_svc = FrontierService(ad, topology_service=topo)
        hsrs = HSRSFireEngine(ad)

        sid = "usr_parity_diamond"
        ad.upsert_student_profile(StudentProfile(student_id=sid, enrolled_course_id="CRS_ALG1"))

        # Master root and branches
        for tid in ("trl_alg_001", "trl_alg_002", "trl_alg_003"):
            ad.upsert_student_topic_state(
                StudentTopicState(
                    student_id=sid,
                    topic_id=tid,
                    status="MASTERED",
                    stability_interval_days=2.0,
                    rep_num=1.0,
                    student_ability=1.0,
                    topic_difficulty=0.5,
                    speed=2.0,
                    last_reviewed_at=now - timedelta(days=2),
                )
            )

        # Unlock apex
        frontier = f_svc.compute_knowledge_frontier(sid, "CRS_ALG1")
        assert [n.id for n in frontier] == ["trl_alg_004"]

        # Record attempt & explicit pass on apex (diamond downward propagation)
        att_res = hsrs.record_attempt(sid, "trl_alg_004", is_correct=True, alpha=0.2, propagate=True)
        # Ensure trl_alg_001 appears only ONCE in propagated_updates with max weight 0.40!
        alg1_deltas = [d for d in att_res.propagated_updates if d.topic_id == "trl_alg_001"]
        assert len(alg1_deltas) == 1
        assert alg1_deltas[0].compound_weight == 0.40

        rep_res = hsrs.process_explicit_repetition(
            sid, "trl_alg_004", is_success=True, raw_delta=1.0, as_of=now
        )
        alg1_fire = [t for t in rep_res.downward_fire_transfers if t.ancestor_id == "trl_alg_001"]
        assert len(alg1_fire) == 1
        assert alg1_fire[0].compound_weight == 0.40

        states = ad.get_student_topic_states(sid)
        snapshots[backend] = {
            tid: (
                s.status,
                s.rep_num,
                s.stability_interval_days,
                s.memory_strength,
                s.cumulative_fire_credit,
                s.student_ability,
                s.speed,
                s.allow_implicit_fire_credit,
            )
            for tid, s in sorted(states.items())
        }
        if hasattr(ad, "close"):
            ad.close()

    assert snapshots["memory"] == snapshots["relational"]
    assert snapshots["relational"] == snapshots["graph"]
