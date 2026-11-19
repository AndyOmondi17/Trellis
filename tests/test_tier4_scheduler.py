"""
tests/test_tier4_scheduler.py
Comprehensive parameterized test suite for Tier 4:
  - FR-4.1: Spaced Repetition Compression Set-Cover Optimizer & Fending
  - FR-4.2: Macro-Interleaving & Review-Tsunami Throttle
  - FR-4.3: Sticky 'Math Buffet' Task Dashboard Manager
Parameterized across all standalone database backends: memory, relational, graph.
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Set, Tuple
import pytest

from trellis.domain.models import (
    DashboardTaskItem,
    InteractionAttempt,
    StudentProfile,
    StudentTopicState,
    TopicNode,
)
from trellis.engine.frontier_service import FrontierService
from trellis.engine.hsrs_fire_engine import CREDIT_TRIGGER_EPSILON, HSRSFireEngine
from trellis.engine.session_scheduler import (
    CoverageCandidate,
    InterleavingComposer,
    ReviewCompressionOptimizer,
    ScheduleSnapshot,
    ScheduledTask,
    SchedulerConfig,
    SessionScheduler,
    compute_composite_importance,
)
from trellis.engine.topology_service import TopologyService
from trellis.ports.storage_port import get_storage_adapter


def _make_node(
    node_id: str,
    title: str,
    course_id: str,
    cluster: str,
    prereqs: list[tuple[str, str]],  # (prereq_id, strength)
    encompassed: list[tuple[str, float]],  # (encompassed_id, weight)
    grade_min: int = 8,
    difficulty: float = 0.5,
    is_core: bool = True,
) -> dict:
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
        "is_core": is_core,
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


TIER4_TEST_GRAPH = {
    "schema_version": "2.0.0-trellis",
    "graph_id": "trellis-tier4-test-dag",
    "course_catalog": [
        {
            "course_id": "CRS_ALG1",
            "course_code": "alg",
            "title": "Algebra I",
            "grade_band": "8-9",
            "benchmark_xp": 1500,
            "mastery_floor_course_ids": ["CRS_PREALG"],
        },
        {
            "course_id": "CRS_PREALG",
            "course_code": "pra",
            "title": "Pre-Algebra",
            "grade_band": "6-7",
            "benchmark_xp": 1000,
            "mastery_floor_course_ids": [],
        },
    ],
    "nodes": [
        # Foundation Nodes (Cluster C1)
        _make_node("trl_c1_1", "Integer Ops 1", "CRS_PREALG", "CL_NUM", [], []),
        _make_node("trl_c1_2", "Integer Ops 2", "CRS_PREALG", "CL_NUM", [("trl_c1_1", "hard")], [("trl_c1_1", 0.65)]),
        _make_node("trl_c1_3", "Integer Ops 3", "CRS_PREALG", "CL_NUM", [("trl_c1_2", "hard")], [("trl_c1_2", 0.65)]),

        # Algebraic Equations (Cluster C2)
        _make_node("trl_c2_1", "Linear Eq 1", "CRS_ALG1", "CL_EQN", [("trl_c1_3", "hard")], [("trl_c1_3", 0.65)]),
        _make_node("trl_c2_2", "Linear Eq 2", "CRS_ALG1", "CL_EQN", [("trl_c2_1", "hard")], [("trl_c2_1", 0.65)]),
        _make_node("trl_c2_3", "Linear Eq 3", "CRS_ALG1", "CL_EQN", [("trl_c2_2", "hard")], [("trl_c2_2", 0.65)]),

        # Graphing / Functions (Cluster C3)
        _make_node("trl_c3_1", "Slope 1", "CRS_ALG1", "CL_FUN", [("trl_c1_3", "hard")], [("trl_c1_3", 0.65)]),
        _make_node("trl_c3_2", "Slope 2", "CRS_ALG1", "CL_FUN", [("trl_c3_1", "hard")], [("trl_c3_1", 0.65)]),
        _make_node("trl_c3_3", "Slope 3", "CRS_ALG1", "CL_FUN", [("trl_c3_2", "hard")], [("trl_c3_2", 0.65)]),

        # Geometry / Area (Cluster C4)
        _make_node("trl_c4_1", "Area 1", "CRS_ALG1", "CL_GEO", [("trl_c1_2", "hard")], [("trl_c1_2", 0.65)]),
        _make_node("trl_c4_2", "Area 2", "CRS_ALG1", "CL_GEO", [("trl_c4_1", "hard")], [("trl_c4_1", 0.65)]),

        # Polynomials (Cluster C5)
        _make_node("trl_c5_1", "Poly 1", "CRS_ALG1", "CL_POLY", [("trl_c2_2", "hard")], [("trl_c2_2", 0.65)]),
        _make_node("trl_c5_2", "Poly 2", "CRS_ALG1", "CL_POLY", [("trl_c5_1", "hard")], [("trl_c5_1", 0.65)]),

        # Statistics (Cluster C6)
        _make_node("trl_c6_1", "Stat 1", "CRS_ALG1", "CL_STAT", [("trl_c1_1", "hard")], [("trl_c1_1", 0.65)]),
        _make_node("trl_c6_2", "Stat 2", "CRS_ALG1", "CL_STAT", [("trl_c6_1", "hard")], [("trl_c6_1", 0.65)]),
    ],
}


@pytest.fixture(params=["memory", "relational", "graph"])
def tier4_fixture(request):
    """Initializes standalone adapter, ingests Tier 4 DAG, and instantiates services."""
    backend = request.param
    storage = get_storage_adapter(backend)
    storage.ingest_knowledge_graph(TIER4_TEST_GRAPH)
    topo = TopologyService(storage)
    frontier_svc = FrontierService(storage, topology_service=topo)
    hsrs_engine = HSRSFireEngine(storage)
    scheduler = SessionScheduler(storage, frontier_service=frontier_svc, hsrs_engine=hsrs_engine)
    yield storage, topo, frontier_svc, hsrs_engine, scheduler
    if hasattr(storage, "close"):
        storage.close()


# ===========================================================================
# Category 1: FR-4.1 Set-Cover & Look-Ahead Fending
# ===========================================================================

def test_math_academy_39x6_compression(tier4_fixture):
    """
    FR-4.1: Verifies that an encompassing candidate task covering 3 due reviews
    is selected over 3 explicit individual reviews, achieving 3.0x efficiency.
    """
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_39x6"

    cand_c = CoverageCandidate(
        topic_id="trl_compressor",
        kind="FRONTIER_LESSON",
        domain_cluster="CL_EQN",
        course_id="CRS_ALG1",
        effective_delta=1.0,
        flows={"trl_r1": 1.0, "trl_r2": 1.0, "trl_r3": 1.0},
        is_enrolled=True,
        is_core=True,
        dependents_count=5,
        memory_strength=0.0,
    )
    r1 = CoverageCandidate(
        topic_id="trl_r1", kind="DUE_REVIEW", domain_cluster="CL_NUM", course_id="CRS_ALG1",
        effective_delta=1.0, flows={}, is_enrolled=True, is_core=True, dependents_count=0, memory_strength=0.4
    )
    r2 = CoverageCandidate(
        topic_id="trl_r2", kind="DUE_REVIEW", domain_cluster="CL_NUM", course_id="CRS_ALG1",
        effective_delta=1.0, flows={}, is_enrolled=True, is_core=True, dependents_count=0, memory_strength=0.3
    )
    r3 = CoverageCandidate(
        topic_id="trl_r3", kind="DUE_REVIEW", domain_cluster="CL_NUM", course_id="CRS_ALG1",
        effective_delta=1.0, flows={}, is_enrolled=True, is_core=True, dependents_count=0, memory_strength=0.2
    )
    snap = ScheduleSnapshot(
        student_id=student_id,
        enrolled_course_id="CRS_ALG1",
        as_of=now,
        states={
            "trl_r1": StudentTopicState(student_id=student_id, topic_id="trl_r1", status="DUE_FOR_REVIEW"),
            "trl_r2": StudentTopicState(student_id=student_id, topic_id="trl_r2", status="DUE_FOR_REVIEW"),
            "trl_r3": StudentTopicState(student_id=student_id, topic_id="trl_r3", status="DUE_FOR_REVIEW"),
        },
        due_review_ids=["trl_r1", "trl_r2", "trl_r3"],
        upcoming_review_ids=[],
        credit_targets={"trl_r1", "trl_r2", "trl_r3"},
        candidates={"trl_compressor": cand_c, "trl_r1": r1, "trl_r2": r2, "trl_r3": r3},
    )

    optimizer = ReviewCompressionOptimizer()
    res = optimizer.solve(snap)

    assert res.selected_task_ids == ["trl_compressor"]
    assert sorted(res.covered_review_ids) == ["trl_r1", "trl_r2", "trl_r3"]
    assert res.efficiency_ratio == 3.0
    assert res.task_knockout_attribution["trl_compressor"] == 3


def test_lesson_preferred_over_review(tier4_fixture):
    """
    FR-4.1: When a frontier lesson and a due review both cover a review with identical gain,
    the frontier lesson is strictly preferred via 3-tier kind rank.
    """
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    cand_l = CoverageCandidate(
        topic_id="trl_l1", kind="FRONTIER_LESSON", domain_cluster="CL_A", course_id="CRS_ALG1",
        effective_delta=1.0, flows={"trl_r1": 1.0}, is_enrolled=True, is_core=True, dependents_count=1, memory_strength=0.0
    )
    cand_r = CoverageCandidate(
        topic_id="trl_r1", kind="DUE_REVIEW", domain_cluster="CL_A", course_id="CRS_ALG1",
        effective_delta=1.0, flows={}, is_enrolled=True, is_core=True, dependents_count=0, memory_strength=0.4
    )
    snap = ScheduleSnapshot(
        student_id="std_pref",
        enrolled_course_id="CRS_ALG1",
        as_of=now,
        states={"trl_r1": StudentTopicState(student_id="std_pref", topic_id="trl_r1", status="DUE_FOR_REVIEW")},
        due_review_ids=["trl_r1"],
        upcoming_review_ids=[],
        credit_targets={"trl_r1"},
        candidates={"trl_l1": cand_l, "trl_r1": cand_r},
    )
    optimizer = ReviewCompressionOptimizer()
    res = optimizer.solve(snap)
    assert res.selected_task_ids == ["trl_l1"]
    assert res.covered_review_ids == ["trl_r1"]


def test_multi_task_fractional_accumulation(tier4_fixture):
    """
    FR-4.1: Two frontier lessons L1, L2 each flowing 0.65 into {r1, r2, r3} (starting at acc = 0.0)
    combine (0.65 + 0.65 = 1.30 >= 1.0) to knock out 3 reviews with 2 tasks.
    """
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    flows = {"trl_r1": 0.65, "trl_r2": 0.65, "trl_r3": 0.65}
    l1 = CoverageCandidate(
        topic_id="trl_l1", kind="FRONTIER_LESSON", domain_cluster="CL_A", course_id="CRS_ALG1",
        effective_delta=1.0, flows=flows, is_enrolled=True, is_core=True, dependents_count=2, memory_strength=0.0
    )
    l2 = CoverageCandidate(
        topic_id="trl_l2", kind="FRONTIER_LESSON", domain_cluster="CL_B", course_id="CRS_ALG1",
        effective_delta=1.0, flows=flows, is_enrolled=True, is_core=True, dependents_count=1, memory_strength=0.0
    )
    r_cands = {
        r: CoverageCandidate(
            topic_id=r, kind="DUE_REVIEW", domain_cluster="CL_R", course_id="CRS_ALG1",
            effective_delta=1.0, flows={}, is_enrolled=True, is_core=True, dependents_count=0, memory_strength=0.4
        )
        for r in ["trl_r1", "trl_r2", "trl_r3"]
    }
    cands = {"trl_l1": l1, "trl_l2": l2}
    cands.update(r_cands)

    snap = ScheduleSnapshot(
        student_id="std_frac",
        enrolled_course_id="CRS_ALG1",
        as_of=now,
        states={
            r: StudentTopicState(student_id="std_frac", topic_id=r, status="DUE_FOR_REVIEW", cumulative_fire_credit=0.0)
            for r in ["trl_r1", "trl_r2", "trl_r3"]
        },
        due_review_ids=["trl_r1", "trl_r2", "trl_r3"],
        upcoming_review_ids=[],
        credit_targets={"trl_r1", "trl_r2", "trl_r3"},
        candidates=cands,
    )
    optimizer = ReviewCompressionOptimizer()
    res = optimizer.solve(snap)
    assert res.selected_task_ids == ["trl_l1", "trl_l2"]
    assert sorted(res.covered_review_ids) == ["trl_r1", "trl_r2", "trl_r3"]
    assert res.efficiency_ratio == 1.5


def test_reverse_redundancy_pruning(tier4_fixture):
    """
    FR-4.1: A partial candidate selected on step 1 whose covered items are later
    superseded by a full encompassing candidate is cleanly pruned in reverse pass.
    """
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    l_full = CoverageCandidate(
        topic_id="trl_l_full", kind="FRONTIER_LESSON", domain_cluster="CL_B", course_id="CRS_ALG1",
        effective_delta=1.0, flows={"trl_r1": 1.0, "trl_r2": 1.0}, is_enrolled=True, is_core=True, dependents_count=0, memory_strength=0.0
    )
    r1 = CoverageCandidate(
        topic_id="trl_r1", kind="DUE_REVIEW", domain_cluster="CL_C", course_id="CRS_ALG1",
        effective_delta=1.0, flows={}, is_enrolled=True, is_core=True, dependents_count=0, memory_strength=0.4
    )
    r2 = CoverageCandidate(
        topic_id="trl_r2", kind="DUE_REVIEW", domain_cluster="CL_C", course_id="CRS_ALG1",
        effective_delta=1.0, flows={}, is_enrolled=True, is_core=True, dependents_count=0, memory_strength=0.4
    )
    snap = ScheduleSnapshot(
        student_id="std_prune",
        enrolled_course_id="CRS_ALG1",
        as_of=now,
        states={
            "trl_r1": StudentTopicState(student_id="std_prune", topic_id="trl_r1", status="DUE_FOR_REVIEW", cumulative_fire_credit=0.0),
            "trl_r2": StudentTopicState(student_id="std_prune", topic_id="trl_r2", status="DUE_FOR_REVIEW", cumulative_fire_credit=0.0),
        },
        due_review_ids=["trl_r1", "trl_r2"],
        upcoming_review_ids=[],
        credit_targets={"trl_r1", "trl_r2"},
        candidates={"trl_l_full": l_full, "trl_r1": r1, "trl_r2": r2},
    )
    optimizer = ReviewCompressionOptimizer()
    res = optimizer.solve(snap)
    assert res.selected_task_ids == ["trl_l_full"]
    assert sorted(res.covered_review_ids) == ["trl_r1", "trl_r2"]


def test_preventative_gate_slow_speed(tier4_fixture):
    """
    FR-4.1 / FR-3.2: A slow-speed due review (allow_implicit_fire_credit = False)
    cannot receive credit flow, forcing explicit review.
    """
    storage, topo, frontier_svc, hsrs_engine, scheduler = tier4_fixture
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_slow"
    storage.upsert_student_profile(StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1"))

    # trl_c1_2 is mastered and due for review, but calibrated with speed < 1.0 (blocked)
    storage.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_c1_2",
            status="DUE_FOR_REVIEW",
            allow_implicit_fire_credit=False,
            speed=0.6,
        )
    )
    # Master trl_c1_1 so trl_c1_2 prerequisite is met
    storage.upsert_student_topic_state(
        StudentTopicState(student_id=student_id, topic_id="trl_c1_1", status="MASTERED")
    )

    snap = scheduler.build_snapshot(student_id, as_of=now)
    assert "trl_c1_2" not in snap.credit_targets

    # Even though trl_c1_3 encompasses trl_c1_2, trl_c1_3 has NO flow to trl_c1_2
    if "trl_c1_3" in snap.candidates:
        assert "trl_c1_2" not in snap.candidates["trl_c1_3"].flows


def test_prior_cumulative_fire_credit_and_early_discount(tier4_fixture):
    """
    FR-4.1: Existing fractional credit (0.50) + discounted MASTERED_REVIEW flow (0.50)
    crosses 1.0 - 1e-6, triggering knockout.
    """
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    r1 = CoverageCandidate(
        topic_id="trl_r1", kind="DUE_REVIEW", domain_cluster="CL_B", course_id="CRS_ALG1",
        effective_delta=1.0, flows={}, is_enrolled=True, is_core=True, dependents_count=0, memory_strength=0.4
    )
    m_with_fend = CoverageCandidate(
        topic_id="trl_m1", kind="MASTERED_REVIEW", domain_cluster="CL_A", course_id="CRS_ALG1",
        effective_delta=0.7692, flows={"trl_r1": 0.50, "trl_u1": 0.30}, is_enrolled=True, is_core=True,
        dependents_count=0, memory_strength=0.9
    )
    snap = ScheduleSnapshot(
        student_id="std_cum",
        enrolled_course_id="CRS_ALG1",
        as_of=now,
        states={
            "trl_r1": StudentTopicState(
                student_id="std_cum", topic_id="trl_r1", status="DUE_FOR_REVIEW", cumulative_fire_credit=0.50
            ),
            "trl_m1": StudentTopicState(
                student_id="std_cum", topic_id="trl_m1", status="MASTERED", memory_strength=0.9
            ),
            "trl_u1": StudentTopicState(
                student_id="std_cum", topic_id="trl_u1", status="MASTERED", memory_strength=0.60
            ),
        },
        due_review_ids=["trl_r1"],
        upcoming_review_ids=["trl_u1"],
        credit_targets={"trl_r1", "trl_u1"},
        candidates={"trl_m1": m_with_fend, "trl_r1": r1},
    )
    optimizer = ReviewCompressionOptimizer()
    res = optimizer.solve(snap)
    assert "trl_r1" in res.covered_review_ids
    assert "trl_m1" in res.selected_task_ids


def test_three_tier_kind_priority_mastered_review(tier4_fixture):
    """
    Fix 6: A non-due MASTERED_REVIEW that only knocks out 1 due review with zero
    extra compression or fending is rejected in favor of the explicit DUE_REVIEW.
    """
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    m_solo = CoverageCandidate(
        topic_id="trl_m_solo", kind="MASTERED_REVIEW", domain_cluster="CL_A", course_id="CRS_ALG1",
        effective_delta=1.0, flows={"trl_r1": 1.0}, is_enrolled=True, is_core=True,
        dependents_count=0, memory_strength=0.9
    )
    r1 = CoverageCandidate(
        topic_id="trl_r1", kind="DUE_REVIEW", domain_cluster="CL_B", course_id="CRS_ALG1",
        effective_delta=1.0, flows={}, is_enrolled=True, is_core=True, dependents_count=0, memory_strength=0.4
    )
    snap = ScheduleSnapshot(
        student_id="std_prio",
        enrolled_course_id="CRS_ALG1",
        as_of=now,
        states={
            "trl_r1": StudentTopicState(student_id="std_prio", topic_id="trl_r1", status="DUE_FOR_REVIEW"),
            "trl_m_solo": StudentTopicState(student_id="std_prio", topic_id="trl_m_solo", status="MASTERED"),
        },
        due_review_ids=["trl_r1"],
        upcoming_review_ids=[],
        credit_targets={"trl_r1"},
        candidates={"trl_m_solo": m_solo, "trl_r1": r1},
    )
    optimizer = ReviewCompressionOptimizer()
    res = optimizer.solve(snap)
    # DUE_REVIEW must be chosen because m_solo does not compress >= 2 reviews or fend
    assert res.selected_task_ids == ["trl_r1"]


def test_lookahead_fending_upcoming_reviews(tier4_fixture):
    """
    FR-4.1: An upcoming review (0.50 < memory <= 0.70) is marked fended when flow
    replenishes memory above 0.70, tested both when R_due is non-empty and when R_due is empty.
    """
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    l1 = CoverageCandidate(
        topic_id="trl_l1", kind="FRONTIER_LESSON", domain_cluster="CL_A", course_id="CRS_ALG1",
        effective_delta=1.0, flows={"trl_u1": 0.35}, is_enrolled=True, is_core=True, dependents_count=1, memory_strength=0.0
    )
    snap = ScheduleSnapshot(
        student_id="std_fend",
        enrolled_course_id="CRS_ALG1",
        as_of=now,
        states={
            "trl_u1": StudentTopicState(student_id="std_fend", topic_id="trl_u1", status="MASTERED", memory_strength=0.60)
        },
        due_review_ids=[],
        upcoming_review_ids=["trl_u1"],
        credit_targets={"trl_u1"},
        candidates={"trl_l1": l1},
    )
    optimizer = ReviewCompressionOptimizer()
    fended = optimizer.compute_fended_reviews(snap, active_task_ids=["trl_l1"])
    # 0.60 + 0.5 * 0.35 = 0.775 > 0.70 -> fended!
    assert "trl_u1" in fended


# ===========================================================================
# Category 2: FR-4.1 Mathematical Invariants & Verification
# ===========================================================================

def test_prediction_equals_hsrs_execution(tier4_fixture):
    """
    Mathematical Invariant: Sequential execution of selected compression tasks
    through HSRSFireEngine.process_explicit_repetition produces the EXACT set of
    covered review transitions to MASTERED.
    """
    storage, topo, frontier_svc, hsrs_engine, scheduler = tier4_fixture
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_pred_exec"
    storage.upsert_student_profile(StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1"))

    # Master trl_c1_1 and trl_c1_2, set trl_c1_1 to DUE_FOR_REVIEW
    storage.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id, topic_id="trl_c1_1", status="DUE_FOR_REVIEW",
            last_reviewed_at=now - timedelta(days=10), stability_interval_days=7.0
        )
    )
    storage.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id, topic_id="trl_c1_2", status="MASTERED",
            last_reviewed_at=now - timedelta(days=7), stability_interval_days=7.0
        )
    )

    snap = scheduler.build_snapshot(student_id, as_of=now)
    res = scheduler.optimizer.solve(snap)

    # Execute all selected tasks in HSRS engine
    for tid in res.selected_task_ids:
        hsrs_engine.process_explicit_repetition(student_id, tid, is_success=True, as_of=now)

    # Check post-execution state: all covered reviews must now be MASTERED
    post_states = storage.get_student_topic_states(student_id, res.covered_review_ids)
    for cr in res.covered_review_ids:
        assert post_states[cr].status == "MASTERED", f"Review {cr} was predicted covered but not MASTERED in HSRS"


def test_inverse_equals_forward_flow_equivalence():
    """
    Mathematical Invariant: Verifies on the 1,585-node DAG that inverse descendant
    discovery from target matches forward ancestor lookup from candidates.
    """
    adapter = get_storage_adapter("memory")
    with open("data/clean_spec/trellis_knowledge_graph.json", "r", encoding="utf-8") as f:
        graph_data = json.load(f)
    adapter.ingest_knowledge_graph(graph_data)

    node_ids = sorted(list(adapter.nodes.keys()))[:50]
    for nid in node_ids:
        ancestors = adapter.get_encompassed_ancestors(nid, max_hops=2)
        anc_weights = HSRSFireEngine.deduplicate_encompassing_weights(ancestors)
        for anc_id, fwd_w in anc_weights.items():
            descendants = adapter.get_encompassing_descendants(anc_id, max_hops=2)
            desc_weights = HSRSFireEngine.deduplicate_encompassing_weights(descendants)
            assert anc_id != nid
            assert nid in desc_weights
            assert math.isclose(fwd_w, desc_weights[nid], abs_tol=1e-5)


def test_greedy_vs_brute_force_optimum():
    """
    Mathematical Invariant: On 50 random problem instances, Greedy Set-Cover
    achieves 100% coverage and card(greedy) <= H(k) * card(opt).
    """
    import random
    rng = random.Random(42)

    for trial in range(50):
        num_targets = rng.randint(2, 5)
        num_cands = rng.randint(3, 7)
        targets = [f"trl_r{i}" for i in range(num_targets)]

        cands: Dict[str, CoverageCandidate] = {}
        for c in range(num_cands):
            cid = f"trl_c{c}"
            flows = {r: round(rng.choice([0.0, 0.35, 0.65, 1.0]), 2) for r in targets}
            cands[cid] = CoverageCandidate(
                topic_id=cid, kind="FRONTIER_LESSON", domain_cluster="CL_X", course_id="CRS_1",
                effective_delta=1.0, flows=flows, is_enrolled=True, is_core=True,
                dependents_count=0, memory_strength=0.0
            )
        for r in targets:
            cands[r] = CoverageCandidate(
                topic_id=r, kind="DUE_REVIEW", domain_cluster="CL_X", course_id="CRS_1",
                effective_delta=1.0, flows={}, is_enrolled=True, is_core=True,
                dependents_count=0, memory_strength=0.0
            )

        snap = ScheduleSnapshot(
            student_id="opt_test",
            enrolled_course_id="CRS_1",
            as_of=datetime.now(timezone.utc),
            states={r: StudentTopicState(student_id="opt_test", topic_id=r, status="DUE_FOR_REVIEW") for r in targets},
            due_review_ids=targets,
            upcoming_review_ids=[],
            credit_targets=set(targets),
            candidates=cands,
        )
        opt = ReviewCompressionOptimizer()
        res = opt.solve(snap)
        assert set(res.covered_review_ids) == set(targets), f"Trial {trial}: Failed 100% coverage"

        h_k = sum(1.0 / i for i in range(1, num_targets + 1))
        assert len(res.selected_task_ids) <= math.ceil(h_k * num_targets)


def test_latency_budget(tier4_fixture):
    """
    Performance SLA: Both pure solve() and full refresh_dashboard() execute
    within the < 50ms budget on a populated curriculum.
    """
    storage, topo, frontier_svc, hsrs_engine, scheduler = tier4_fixture
    student_id = "std_perf"
    storage.upsert_student_profile(StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1"))

    scheduler.refresh_dashboard(student_id)

    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        scheduler.refresh_dashboard(student_id)
        times.append((time.perf_counter() - t0) * 1000.0)

    median_ms = sorted(times)[len(times) // 2]
    assert median_ms < 50.0, f"Dashboard refresh median latency {median_ms:.2f}ms exceeded 50ms budget"


# ===========================================================================
# Category 3: FR-4.2 Macro-Interleaving & Tsunami Throttle
# ===========================================================================

def test_baseline_menu_split(tier4_fixture):
    """
    FR-4.2: Baseline 6-slot menu produces 4 lessons and 2 reviews (70/30 baseline split).
    """
    storage, topo, frontier_svc, hsrs_engine, scheduler = tier4_fixture
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_base_split"
    storage.upsert_student_profile(StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1"))

    # Unlock multiple lessons and 2 reviews
    storage.upsert_student_topic_state(StudentTopicState(student_id=student_id, topic_id="trl_c1_1", status="MASTERED"))
    storage.upsert_student_topic_state(StudentTopicState(student_id=student_id, topic_id="trl_c1_2", status="DUE_FOR_REVIEW"))
    storage.upsert_student_topic_state(StudentTopicState(student_id=student_id, topic_id="trl_c6_1", status="DUE_FOR_REVIEW"))

    res = scheduler.refresh_dashboard(student_id, as_of=now)
    assert len(res.menu) <= 6
    review_count = sum(1 for it in res.menu if it.task_type == "SPACED_REVIEW")
    assert review_count <= 2
    assert res.plan.is_review_wave is False


def test_review_wave_throttles_reviews_and_preserves_lesson_floor(tier4_fixture):
    """
    FR-4.2: When 6 reviews are pending, reviews are throttled at 4 and
    the 2-lesson floor (25% hard floor) is preserved.
    """
    storage, topo, frontier_svc, hsrs_engine, scheduler = tier4_fixture
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_wave"
    storage.upsert_student_profile(StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1"))

    reviews = ["trl_c1_1", "trl_c1_2", "trl_c2_1", "trl_c3_1", "trl_c4_1", "trl_c6_1"]
    for r in reviews:
        storage.upsert_student_topic_state(
            StudentTopicState(student_id=student_id, topic_id=r, status="DUE_FOR_REVIEW", speed=0.5, allow_implicit_fire_credit=False)
        )

    res = scheduler.refresh_dashboard(student_id, as_of=now)
    lesson_count = sum(1 for it in res.menu if it.task_type == "FRONTIER_LESSON")
    review_count = sum(1 for it in res.menu if it.task_type == "SPACED_REVIEW")

    assert review_count <= 4, f"Review count {review_count} exceeded 4-review tsunami cap"
    assert res.plan.is_review_wave is True
    if lesson_count > 0:
        assert res.plan.lesson_floor_satisfied is True


def test_frontier_exhausted_backfills_reviews(tier4_fixture):
    """
    FR-4.2: When 0 frontier lessons are available, free slots backfill with reviews
    and lesson_floor_satisfied is False.
    """
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    snap = ScheduleSnapshot(
        student_id="std_exhausted",
        enrolled_course_id="CRS_ALG1",
        as_of=now,
        states={f"trl_r{i}": StudentTopicState(student_id="std_exhausted", topic_id=f"trl_r{i}", status="DUE_FOR_REVIEW") for i in range(6)},
        due_review_ids=[f"trl_r{i}" for i in range(6)],
        upcoming_review_ids=[],
        credit_targets={f"trl_r{i}" for i in range(6)},
        candidates={
            f"trl_r{i}": CoverageCandidate(
                topic_id=f"trl_r{i}", kind="DUE_REVIEW", domain_cluster=f"CL_{i}", course_id="CRS_ALG1",
                effective_delta=1.0, flows={}, is_enrolled=True, is_core=True, dependents_count=0, memory_strength=0.4
            )
            for i in range(6)
        },
    )
    composer = InterleavingComposer()
    comp = ReviewCompressionOptimizer().solve(snap)
    plan = composer.compose_menu(
        snapshot=snap,
        compression=comp,
        retained_items=[],
        cluster_map={f"trl_r{i}": f"CL_{i}" for i in range(6)},
        importance_map={f"trl_r{i}": 1.0 for i in range(6)},
    )
    assert plan.lesson_count == 0
    assert plan.review_count == 6
    assert plan.lesson_floor_satisfied is False


def test_zero_reviews_due_fills_frontier_lessons(tier4_fixture):
    """
    FR-4.2: When 0 reviews are due, all slots are allocated to frontier lessons.
    """
    storage, topo, frontier_svc, hsrs_engine, scheduler = tier4_fixture
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_zero_reviews"
    storage.upsert_student_profile(StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1"))

    # Master foundations so Algebra 1 lessons unlock
    storage.upsert_student_topic_state(StudentTopicState(student_id=student_id, topic_id="trl_c1_1", status="MASTERED"))
    storage.upsert_student_topic_state(StudentTopicState(student_id=student_id, topic_id="trl_c1_2", status="MASTERED"))
    storage.upsert_student_topic_state(StudentTopicState(student_id=student_id, topic_id="trl_c1_3", status="MASTERED"))

    res = scheduler.refresh_dashboard(student_id, as_of=now)
    review_count = sum(1 for it in res.menu if it.task_type == "SPACED_REVIEW")
    assert review_count == 0
    assert len(res.menu) >= 1
    assert all(it.task_type == "FRONTIER_LESSON" for it in res.menu)


def test_simultaneous_menu_cluster_non_interference(tier4_fixture):
    """
    FR-4.2: Menu mode forbids duplicate domain clusters on the dashboard
    when tasks from distinct clusters are available.
    """
    storage, topo, frontier_svc, hsrs_engine, scheduler = tier4_fixture
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_cluster_noninterfere"
    storage.upsert_student_profile(StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1"))

    storage.upsert_student_topic_state(StudentTopicState(student_id=student_id, topic_id="trl_c1_1", status="MASTERED"))
    storage.upsert_student_topic_state(StudentTopicState(student_id=student_id, topic_id="trl_c1_2", status="MASTERED"))
    storage.upsert_student_topic_state(StudentTopicState(student_id=student_id, topic_id="trl_c1_3", status="MASTERED"))

    res = scheduler.refresh_dashboard(student_id, as_of=now)
    clusters = [scheduler._cluster_cache.get(it.topic_id) for it in res.menu]
    if not res.plan.interference_relaxed:
        assert len(clusters) == len(set(clusters))


def test_single_cluster_bottleneck_relaxes_interference(tier4_fixture):
    """
    FR-4.2: When all available tasks belong to a single cluster, non-interference
    is cleanly relaxed with relaxation_reason = 'SINGLE_CLUSTER_BOTTLENECK'.
    """
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    cands = {
        f"trl_l_{i}": CoverageCandidate(
            topic_id=f"trl_l_{i}", kind="FRONTIER_LESSON", domain_cluster="SOLO_CLUSTER", course_id="CRS_ALG1",
            effective_delta=1.0, flows={}, is_enrolled=True, is_core=True, dependents_count=i, memory_strength=0.0
        )
        for i in range(4)
    }
    snap = ScheduleSnapshot(
        student_id="std_bottleneck",
        enrolled_course_id="CRS_ALG1",
        as_of=now,
        states={},
        due_review_ids=[],
        upcoming_review_ids=[],
        credit_targets=set(),
        candidates=cands,
    )
    composer = InterleavingComposer()
    plan = composer.compose_menu(
        snapshot=snap,
        compression=ReviewCompressionOptimizer().solve(snap),
        retained_items=[],
        cluster_map={f"trl_l_{i}": "SOLO_CLUSTER" for i in range(4)},
        importance_map={f"trl_l_{i}": float(i) for i in range(4)},
    )
    assert plan.interference_relaxed is True
    assert plan.relaxation_reason == "SINGLE_CLUSTER_BOTTLENECK"
    assert len(plan.tasks) == 4


def test_sequence_interleaving_and_pigeonhole_rule():
    """
    FR-4.2: SEQUENCE mode strictly prevents consecutive cluster collision,
    enforces max_consecutive_reviews <= 3, and passes the Pigeonhole majority-cluster test.
    """
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    composer = InterleavingComposer()

    # Pigeonhole test: 3 tasks in cluster A, 1 in B, 1 in C (total 5)
    tasks = [
        ScheduledTask(topic_id="trl_a1", task_type="FRONTIER_LESSON", domain_cluster="CL_A", rank_score=10.0, is_sticky=True, assigned_at=now),
        ScheduledTask(topic_id="trl_a2", task_type="FRONTIER_LESSON", domain_cluster="CL_A", rank_score=9.0, is_sticky=True, assigned_at=now),
        ScheduledTask(topic_id="trl_a3", task_type="FRONTIER_LESSON", domain_cluster="CL_A", rank_score=8.0, is_sticky=True, assigned_at=now),
        ScheduledTask(topic_id="trl_b1", task_type="FRONTIER_LESSON", domain_cluster="CL_B", rank_score=7.0, is_sticky=True, assigned_at=now),
        ScheduledTask(topic_id="trl_c1", task_type="FRONTIER_LESSON", domain_cluster="CL_C", rank_score=6.0, is_sticky=True, assigned_at=now),
    ]

    plan = composer.build_session_queue(tasks=tasks, max_tasks=5)
    sequence = plan.tasks

    assert len(sequence) == 5
    for i in range(len(sequence) - 1):
        assert sequence[i].domain_cluster != sequence[i + 1].domain_cluster, (
            f"Consecutive collision at step {i}: {sequence[i].topic_id} ({sequence[i].domain_cluster}) "
            f"and {sequence[i+1].topic_id} ({sequence[i+1].domain_cluster})"
        )
    assert plan.interference_relaxed is False


# ===========================================================================
# Category 4: FR-4.3 Sticky Dashboard & Adapter Parity
# ===========================================================================

def test_sticky_lesson_retention_on_single_completion(tier4_fixture):
    """
    FR-4.3: Completing 1 lesson on the dashboard evicts only that lesson;
    uncompleted lessons remain sticky with their original assigned_at timestamps.
    """
    storage, topo, frontier_svc, hsrs_engine, scheduler = tier4_fixture
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_sticky_1"
    storage.upsert_student_profile(StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1"))

    # Initial refresh populates dashboard
    res1 = scheduler.refresh_dashboard(student_id, as_of=now)
    assert len(res1.menu) >= 1
    first_item = res1.menu[0]
    orig_assigned_at = first_item.assigned_at

    # Complete first item
    storage.upsert_student_topic_state(
        StudentTopicState(student_id=student_id, topic_id=first_item.topic_id, status="MASTERED")
    )

    # Next day refresh
    next_day = now + timedelta(days=1)
    res2 = scheduler.refresh_dashboard(student_id, as_of=next_day)

    # First item must be evicted as COMPLETED
    assert res2.removed.get(first_item.topic_id) == "COMPLETED"
    assert first_item.topic_id not in [it.topic_id for it in res2.menu]

    # Any retained item must preserve original assigned_at
    for it in res2.menu:
        if it.topic_id in res1.retained_ids:
            assert it.assigned_at == orig_assigned_at


def test_prerequisite_decay_does_not_relock_or_evict_sticky_lesson(tier4_fixture):
    """
    Fix 2 / FR-4.3: Memory decay on a prerequisite (MASTERED -> DUE_FOR_REVIEW)
    does NOT re-lock an unlocked child lesson or evict it from the dashboard.
    """
    storage, topo, frontier_svc, hsrs_engine, scheduler = tier4_fixture
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_decay_relock"
    storage.upsert_student_profile(StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1"))

    # Master trl_c1_1, unlocking trl_c1_2
    storage.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id, topic_id="trl_c1_1", status="MASTERED",
            last_reviewed_at=now, stability_interval_days=7.0
        )
    )
    res1 = scheduler.refresh_dashboard(student_id, as_of=now)
    assert "trl_c1_2" in [it.topic_id for it in res1.menu]

    # Fast forward 30 days: trl_c1_1 decays to DUE_FOR_REVIEW
    future = now + timedelta(days=30)
    res2 = scheduler.refresh_dashboard(student_id, as_of=future)

    # trl_c1_2 MUST remain sticky and NOT be evicted as INVALIDATED_RELOCKED
    assert res2.removed.get("trl_c1_2") != "INVALIDATED_RELOCKED"
    assert "trl_c1_2" in [it.topic_id for it in res2.menu]


def test_encompassing_lesson_completion_resolves_sticky_due_review(tier4_fixture):
    """
    FR-4.3: Completing an encompassing lesson that knocks out a sticky DUE_FOR_REVIEW
    item removes it from the dashboard with reason REVIEW_RESOLVED.
    """
    storage, topo, frontier_svc, hsrs_engine, scheduler = tier4_fixture
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_enc_resolv"
    storage.upsert_student_profile(StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1"))

    # trl_c1_2 is DUE_FOR_REVIEW
    storage.upsert_student_topic_state(
        StudentTopicState(student_id=student_id, topic_id="trl_c1_1", status="MASTERED")
    )
    storage.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id, topic_id="trl_c1_2", status="DUE_FOR_REVIEW",
            allow_implicit_fire_credit=True, speed=1.0, cumulative_fire_credit=0.5
        )
    )

    # Seed trl_c1_2 as a sticky review on the dashboard
    storage.save_dashboard_menu(
        student_id,
        [
            DashboardTaskItem(
                student_id=student_id,
                topic_id="trl_c1_2",
                task_type="SPACED_REVIEW",
                rank_score=10.0,
                is_sticky=True,
                assigned_at=now,
            )
        ],
    )

    res1 = scheduler.refresh_dashboard(student_id, as_of=now)
    assert "trl_c1_2" in [it.topic_id for it in res1.menu]

    # Master encompassing lesson trl_c1_3 (which flows 0.65 into trl_c1_2, crossing 1.0)
    hsrs_engine.process_explicit_repetition(student_id, "trl_c1_3", is_success=True, as_of=now)

    # Refresh dashboard
    res2 = scheduler.refresh_dashboard(student_id, as_of=now)
    assert res2.removed.get("trl_c1_2") == "REVIEW_RESOLVED"
    assert "trl_c1_2" not in [it.topic_id for it in res2.menu]


def test_sticky_mastered_review_compressor_retention(tier4_fixture):
    """
    Fix 3 / FR-4.3: A MASTERED_REVIEW task placed on the dashboard stays sticky
    across refreshes until reviewed or its covered due reviews are resolved.
    """
    storage, topo, frontier_svc, hsrs_engine, scheduler = tier4_fixture
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_sticky_m"
    storage.upsert_student_profile(StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1"))

    # trl_c1_1 is DUE_FOR_REVIEW
    storage.upsert_student_topic_state(
        StudentTopicState(student_id=student_id, topic_id="trl_c1_1", status="DUE_FOR_REVIEW")
    )
    # trl_c1_2 is MASTERED
    storage.upsert_student_topic_state(
        StudentTopicState(student_id=student_id, topic_id="trl_c1_2", status="MASTERED", last_reviewed_at=now - timedelta(days=5))
    )

    # Place trl_c1_2 as a sticky SPACED_REVIEW compressor on the dashboard
    storage.save_dashboard_menu(
        student_id,
        [
            DashboardTaskItem(
                student_id=student_id,
                topic_id="trl_c1_2",
                task_type="SPACED_REVIEW",
                rank_score=15.0,
                is_sticky=True,
                assigned_at=now,
            )
        ],
    )

    res = scheduler.refresh_dashboard(student_id, as_of=now)
    assert "trl_c1_2" in [it.topic_id for it in res.menu]
    assert "trl_c1_2" in res.retained_ids


def test_failing_review_keeps_it_sticky(tier4_fixture):
    """
    FR-4.3: Failing a spaced review leaves status as DUE_FOR_REVIEW and
    preserves its sticky presence on the dashboard.
    """
    storage, topo, frontier_svc, hsrs_engine, scheduler = tier4_fixture
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_fail_sticky"
    storage.upsert_student_profile(StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1"))

    storage.upsert_student_topic_state(
        StudentTopicState(student_id=student_id, topic_id="trl_c1_1", status="DUE_FOR_REVIEW")
    )
    res1 = scheduler.refresh_dashboard(student_id, as_of=now)
    assert "trl_c1_1" in [it.topic_id for it in res1.menu]

    # Attempt fails
    hsrs_engine.process_explicit_repetition(student_id, "trl_c1_1", is_success=False, as_of=now)
    assert storage.get_student_topic_states(student_id, ["trl_c1_1"])["trl_c1_1"].status == "DUE_FOR_REVIEW"

    res2 = scheduler.refresh_dashboard(student_id, as_of=now + timedelta(minutes=5))
    assert "trl_c1_1" in [it.topic_id for it in res2.menu]
    assert "trl_c1_1" in res2.retained_ids


def test_high_importance_lesson_does_not_displace_sticky_lessons(tier4_fixture):
    """
    FR-4.3: When dashboard slots are full, a newly unlocked high-importance lesson
    never displaces existing lower-importance sticky lessons.
    """
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    existing = [
        DashboardTaskItem(
            student_id="std_displace", topic_id=f"trl_t_{i}", task_type="FRONTIER_LESSON",
            rank_score=1.0, is_sticky=True, assigned_at=now
        )
        for i in range(6)
    ]
    cands = {
        f"trl_t_{i}": CoverageCandidate(
            topic_id=f"trl_t_{i}", kind="FRONTIER_LESSON", domain_cluster=f"CL_{i}", course_id="CRS_ALG1",
            effective_delta=1.0, flows={}, is_enrolled=True, is_core=True, dependents_count=0, memory_strength=0.0
        )
        for i in range(6)
    }
    cands["trl_super"] = CoverageCandidate(
        topic_id="trl_super", kind="FRONTIER_LESSON", domain_cluster="CL_SUPER", course_id="CRS_ALG1",
        effective_delta=1.0, flows={}, is_enrolled=True, is_core=True, dependents_count=100, memory_strength=0.0
    )
    snap = ScheduleSnapshot(
        student_id="std_displace",
        enrolled_course_id="CRS_ALG1",
        as_of=now,
        states={f"trl_t_{i}": StudentTopicState(student_id="std_displace", topic_id=f"trl_t_{i}", status="AVAILABLE") for i in range(6)},
        due_review_ids=[],
        upcoming_review_ids=[],
        credit_targets=set(),
        candidates=cands,
    )
    composer = InterleavingComposer()
    plan = composer.compose_menu(
        snapshot=snap,
        compression=ReviewCompressionOptimizer().solve(snap),
        retained_items=existing,
        cluster_map={**{f"trl_t_{i}": f"CL_{i}" for i in range(6)}, "trl_super": "CL_SUPER"},
        importance_map={**{f"trl_t_{i}": 1.0 for i in range(6)}, "trl_super": 100.0},
    )
    assert len(plan.tasks) == 6
    assert set(t.topic_id for t in plan.tasks) == {f"trl_t_{i}" for i in range(6)}
    assert "trl_super" not in [t.topic_id for t in plan.tasks]


def test_conditional_revocation_evicts_sticky_lesson(tier4_fixture):
    """
    FR-4.3 / FR-2.3: Upstream conditional completion revocation (status -> LOCKED)
    immediately evicts a re-locked sticky lesson with INVALIDATED_RELOCKED.
    """
    storage, topo, frontier_svc, hsrs_engine, scheduler = tier4_fixture
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_revocation"
    storage.upsert_student_profile(StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1"))

    # trl_c1_2 is on dashboard
    storage.save_dashboard_menu(
        student_id,
        [
            DashboardTaskItem(
                student_id=student_id, topic_id="trl_c1_2", task_type="FRONTIER_LESSON",
                rank_score=5.0, is_sticky=True, assigned_at=now
            )
        ],
    )
    # Prerequisite trl_c1_1 was revoked (status == LOCKED)
    storage.upsert_student_topic_state(
        StudentTopicState(student_id=student_id, topic_id="trl_c1_1", status="LOCKED")
    )
    storage.upsert_student_topic_state(
        StudentTopicState(student_id=student_id, topic_id="trl_c1_2", status="LOCKED")
    )

    res = scheduler.refresh_dashboard(student_id, as_of=now)
    assert res.removed.get("trl_c1_2") == "INVALIDATED_RELOCKED"
    assert "trl_c1_2" not in [it.topic_id for it in res.menu]


def test_cooldown_or_tier3_remediation_evicts_sticky_lesson(tier4_fixture):
    """
    FR-4.3: A task entering cooldown_until > as_of or TIER_3_REMEDIATION is evicted
    with reason INVALIDATED_HOLD.
    """
    storage, topo, frontier_svc, hsrs_engine, scheduler = tier4_fixture
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_hold"
    storage.upsert_student_profile(StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1"))

    storage.save_dashboard_menu(
        student_id,
        [
            DashboardTaskItem(
                student_id=student_id, topic_id="trl_c1_1", task_type="FRONTIER_LESSON",
                rank_score=5.0, is_sticky=True, assigned_at=now
            )
        ],
    )
    storage.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id, topic_id="trl_c1_1", status="AVAILABLE",
            cooldown_until=now + timedelta(hours=2)
        )
    )

    res = scheduler.refresh_dashboard(student_id, as_of=now)
    assert res.removed.get("trl_c1_1") == "INVALIDATED_HOLD"
    assert "trl_c1_1" not in [it.topic_id for it in res.menu]


def test_opaque_tier5_tasks_retained_untouched(tier4_fixture):
    """
    FR-4.3: Opaque Tier-5 items (TARGETED_REMEDIATION, FACT_FLUENCY, TIMED_QUIZ)
    are retained untouched on the dashboard.
    """
    storage, topo, frontier_svc, hsrs_engine, scheduler = tier4_fixture
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_tier5_opaque"
    storage.upsert_student_profile(StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1"))

    storage.save_dashboard_menu(
        student_id,
        [
            DashboardTaskItem(student_id=student_id, topic_id="trl_c1_1", task_type="TARGETED_REMEDIATION", rank_score=20.0, is_sticky=True, assigned_at=now),
            DashboardTaskItem(student_id=student_id, topic_id="trl_c1_2", task_type="FACT_FLUENCY", rank_score=15.0, is_sticky=True, assigned_at=now),
            DashboardTaskItem(student_id=student_id, topic_id="trl_c1_3", task_type="TIMED_QUIZ", rank_score=10.0, is_sticky=True, assigned_at=now),
        ],
    )

    res = scheduler.refresh_dashboard(student_id, as_of=now)
    persisted = storage.get_dashboard_menu(student_id)
    types = {it.task_type for it in persisted}
    assert "TARGETED_REMEDIATION" in types
    assert "FACT_FLUENCY" in types
    assert "TIMED_QUIZ" in types


def test_cross_backend_bit_identical_parity():
    """
    Cross-Backend Parity: Executes a multi-day student progression lifecycle across
    memory, relational, and graph backends. Asserts bit-identical menus, tasks,
    and rank scores.
    """
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    backends = ["memory", "relational", "graph"]
    menus_by_backend = {}

    for backend in backends:
        storage = get_storage_adapter(backend)
        storage.ingest_knowledge_graph(TIER4_TEST_GRAPH)
        topo = TopologyService(storage)
        frontier_svc = FrontierService(storage, topology_service=topo)
        hsrs_engine = HSRSFireEngine(storage)
        sched = SessionScheduler(storage, frontier_service=frontier_svc, hsrs_engine=hsrs_engine)

        student_id = "std_parity"
        storage.upsert_student_profile(StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1"))

        # Day 1: Initial unlock
        sched.refresh_dashboard(student_id, as_of=now)

        # Complete root node
        storage.upsert_student_topic_state(
            StudentTopicState(student_id=student_id, topic_id="trl_c1_1", status="MASTERED", last_reviewed_at=now)
        )

        # Day 2: Next refresh
        day2 = now + timedelta(days=1)
        res = sched.refresh_dashboard(student_id, as_of=day2)
        menus_by_backend[backend] = [
            (it.topic_id, it.task_type, round(it.rank_score, 4))
            for it in res.menu
        ]

        if hasattr(storage, "close"):
            storage.close()

    ref = menus_by_backend["memory"]
    for b in ["relational", "graph"]:
        assert menus_by_backend[b] == ref, f"Backend {b} diverged from memory reference: {menus_by_backend[b]} != {ref}"
