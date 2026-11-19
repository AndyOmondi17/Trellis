"""
tests/test_tier0_persistence.py
Parameterized tests verifying bit-identical parity across all standalone adapters:
  - Adapter A: StandaloneRelationalAdapter
  - Adapter B: StandaloneGraphAdapter
  - Adapter C: MemoryReferenceAdapter
"""

import json
from datetime import datetime, timezone
import pytest

from trellis.domain.models import (
    DashboardTaskItem,
    InteractionAttempt,
    KPContentShell,
    StudentProfile,
    StudentTopicState,
    XPLedgerEntry,
)
from trellis.ports.storage_port import get_storage_adapter


SAMPLE_GRAPH_PAYLOAD = {
    "schema_version": "2.0.0-trellis",
    "graph_id": "trellis-test-dag",
    "course_catalog": [
        {
            "course_id": "CRS_ALG1",
            "course_code": "alg",
            "title": "Algebra I",
            "grade_band": "8-9",
            "benchmark_xp": 1800,
            "mastery_floor_course_ids": ["CRS_PREALG"],
        },
        {
            "course_id": "CRS_PREALG",
            "course_code": "pra",
            "title": "Pre-Algebra",
            "grade_band": "6-8",
            "benchmark_xp": 1600,
            "mastery_floor_course_ids": [],
        },
    ],
    "nodes": [
        {
            "id": "trl_pra_num_001",
            "title": "Integers and Absolute Value",
            "course_id": "CRS_PREALG",
            "canonical_domain": "01_early_numeracy_and_place_value",
            "domain_cluster": "CRS_PREALG::num",
            "skill_type": "PROCEDURAL",
            "bloom_cognitive_tier": "PROCEDURAL_EXECUTION",
            "grade_level_min": 6,
            "grade_level_max": 7,
            "is_core": True,
            "in_foundations_sequence": True,
            "requires_automaticity": False,
            "base_difficulty": 0.25,
            "estimated_xp": 3,
            "mastery_gate_n": 3,
            "key_prerequisite_id": None,
            "aligned_standards": ["ccss-math:6.NS.C.5"],
            "mastery_criteria": ["Evaluate absolute values"],
            "knowledge_points": [
                {
                    "kp_index": 0,
                    "kp_id": "trl_pra_num_001#kp_0",
                    "scaffolding_tier": "KP_1_BASELINE",
                    "kp_focus": "Absolute value of positive and negative integers",
                    "novel_chunk_count": 2,
                    "rule_statement": "The absolute value represents distance from zero.",
                }
            ],
            "prerequisites": [],
            "encompassed_skills": [],
        },
        {
            "id": "trl_pra_num_002",
            "title": "Adding Signed Integers",
            "course_id": "CRS_PREALG",
            "canonical_domain": "02_arithmetic_operations",
            "domain_cluster": "CRS_PREALG::num",
            "skill_type": "PROCEDURAL",
            "bloom_cognitive_tier": "PROCEDURAL_EXECUTION",
            "grade_level_min": 6,
            "grade_level_max": 7,
            "is_core": True,
            "in_foundations_sequence": True,
            "requires_automaticity": False,
            "base_difficulty": 0.35,
            "estimated_xp": 3,
            "mastery_gate_n": 3,
            "key_prerequisite_id": "trl_pra_num_001",
            "aligned_standards": ["ccss-math:7.NS.A.1"],
            "mastery_criteria": ["Add signed numbers"],
            "knowledge_points": [
                {
                    "kp_index": 0,
                    "kp_id": "trl_pra_num_002#kp_0",
                    "scaffolding_tier": "KP_1_BASELINE",
                    "kp_focus": "Add same-sign and opposite-sign numbers",
                    "novel_chunk_count": 2,
                    "rule_statement": "Same signs add magnitudes; different signs subtract magnitudes.",
                }
            ],
            "prerequisites": [
                {
                    "prerequisite_id": "trl_pra_num_001",
                    "strength": "hard",
                    "rationale": "Understanding integer sign magnitude is needed before addition.",
                }
            ],
            "encompassed_skills": [
                {
                    "encompassed_id": "trl_pra_num_001",
                    "fire_weight": 0.5,
                    "is_non_ancestor": False,
                    "rationale": "Adding integers exercises sign evaluation.",
                }
            ],
        },
        {
            "id": "trl_alg_lin_001",
            "title": "Solving One-Step Linear Equations",
            "course_id": "CRS_ALG1",
            "canonical_domain": "06_prealgebra_and_elementary_algebra",
            "domain_cluster": "CRS_ALG1::lin",
            "skill_type": "PROCEDURAL",
            "bloom_cognitive_tier": "PROCEDURAL_EXECUTION",
            "grade_level_min": 8,
            "grade_level_max": 9,
            "is_core": True,
            "in_foundations_sequence": True,
            "requires_automaticity": False,
            "base_difficulty": 0.45,
            "estimated_xp": 4,
            "mastery_gate_n": 3,
            "key_prerequisite_id": "trl_pra_num_002",
            "aligned_standards": ["ccss-math:HSA-REI.B.3"],
            "mastery_criteria": ["Isolate variable using inverse operations"],
            "knowledge_points": [
                {
                    "kp_index": 0,
                    "kp_id": "trl_alg_lin_001#kp_0",
                    "scaffolding_tier": "KP_1_BASELINE",
                    "kp_focus": "One-step addition and subtraction equations",
                    "novel_chunk_count": 2,
                    "rule_statement": "Apply inverse operations to both sides of the equation.",
                }
            ],
            "prerequisites": [
                {
                    "prerequisite_id": "trl_pra_num_002",
                    "strength": "hard",
                    "rationale": "Signed integer arithmetic is required to isolate terms.",
                }
            ],
            "encompassed_skills": [
                {
                    "encompassed_id": "trl_pra_num_002",
                    "fire_weight": 0.6,
                    "is_non_ancestor": False,
                    "rationale": "Inverse operations exercise integer addition/subtraction.",
                }
            ],
        },
    ],
}


@pytest.fixture(params=["memory", "relational", "graph"])
def adapter(request):
    """Instantiates each adapter independently and ingests the sample graph."""
    backend = request.param
    ad = get_storage_adapter(backend)
    report = ad.ingest_knowledge_graph(SAMPLE_GRAPH_PAYLOAD)
    assert report.courses_ingested == 2
    assert report.nodes_ingested == 3
    assert report.knowledge_points_ingested == 3
    assert report.prereq_edges_ingested == 2
    assert report.encompassing_edges_ingested == 2
    yield ad
    if hasattr(ad, "close"):
        ad.close()


def test_course_and_node_retrieval(adapter):
    """Verifies course catalog and topic node retrieval across adapters."""
    catalog = adapter.get_course_catalog()
    assert len(catalog) == 2
    c_ids = {c.course_id for c in catalog}
    assert "CRS_ALG1" in c_ids
    assert "CRS_PREALG" in c_ids

    node = adapter.get_topic_node("trl_alg_lin_001")
    assert node.id == "trl_alg_lin_001"
    assert node.course_id == "CRS_ALG1"
    assert node.is_core is True
    assert node.in_foundations_sequence is True
    assert len(node.knowledge_points) == 1

    subgraph = adapter.get_course_subgraph("CRS_PREALG")
    assert len(subgraph) == 2
    subgraph_ids = {n.id for n in subgraph}
    assert subgraph_ids == {"trl_pra_num_001", "trl_pra_num_002"}


def test_graph_traversals(adapter):
    """Verifies multi-hop traversals: parents, postrequisites, and encompassings."""
    # Hard parents
    parents = adapter.get_hard_parents("trl_alg_lin_001")
    assert parents == ["trl_pra_num_002"]

    # Postrequisites (downstream dependents)
    postreqs = adapter.get_postrequisites("trl_pra_num_001")
    assert "trl_pra_num_002" in postreqs
    assert "trl_alg_lin_001" in postreqs

    # Bounded postrequisites
    postreqs_1hop = adapter.get_postrequisites("trl_pra_num_001", max_hops=1)
    assert postreqs_1hop == ["trl_pra_num_002"]

    # Encompassed ancestors (FIRe trickle-down)
    enc_paths = adapter.get_encompassed_ancestors("trl_alg_lin_001", max_hops=2)
    # Hop 1: trl_pra_num_002 (w=0.6)
    # Hop 2: trl_pra_num_001 (w=0.6 * 0.5 = 0.3)
    path_targets = {p.ancestor_id: p.compound_weight for p in enc_paths}
    assert "trl_pra_num_002" in path_targets
    assert path_targets["trl_pra_num_002"] == pytest.approx(0.6, abs=1e-4)
    assert "trl_pra_num_001" in path_targets
    assert path_targets["trl_pra_num_001"] == pytest.approx(0.3, abs=1e-4)

    # Encompassing descendants (Summer slide penalty trickle-up)
    desc_paths = adapter.get_encompassing_descendants("trl_pra_num_001", max_hops=2)
    desc_targets = {p.ancestor_id: p.compound_weight for p in desc_paths}
    assert "trl_pra_num_002" in desc_targets
    assert "trl_alg_lin_001" in desc_targets

    # Local neighborhood
    neigh = adapter.get_local_neighborhood("trl_pra_num_002")
    assert neigh.direct_prerequisites == ["trl_pra_num_001"]
    assert neigh.key_prerequisite_id == "trl_pra_num_001"
    assert "trl_pra_num_001" in neigh.encompassed_ancestors
    assert "trl_alg_lin_001" in neigh.encompassing_descendants


def test_student_profile_and_states(adapter):
    """Verifies student state CRUD operations and profile mutations."""
    student_id = "usr_test_001"

    profile = StudentProfile(
        student_id=student_id,
        enrolled_course_id="CRS_PREALG",
        xp_total=100,
        progress_pct=15.0,
        daily_xp_target=40,
        rolling_pace_xp=35.0,
        independence_stage="DAILY_CHECKIN",
    )
    adapter.upsert_student_profile(profile)

    fetched_p = adapter.get_student_profile(student_id)
    assert fetched_p.student_id == student_id
    assert fetched_p.xp_total == 100

    state1 = StudentTopicState(
        student_id=student_id,
        topic_id="trl_pra_num_001",
        status="MASTERED",
        rep_num=2.0,
        stability_interval_days=3.0,
        student_ability=1.2,
    )
    adapter.upsert_student_topic_state(state1)

    states = adapter.get_student_topic_states(student_id)
    assert "trl_pra_num_001" in states
    assert states["trl_pra_num_001"].status == "MASTERED"
    assert states["trl_pra_num_001"].rep_num == 2.0


def test_knowledge_frontier_calculation(adapter):
    """Verifies Knowledge Frontier calculation: prerequisite gating."""
    student_id = "usr_frontier_test"

    # Initially, student has no states.
    # In CRS_PREALG, trl_pra_num_001 has 0 prereqs -> AVAILABLE.
    # trl_pra_num_002 requires trl_pra_num_001 -> LOCKED.
    frontier = adapter.compute_knowledge_frontier(student_id, "CRS_PREALG")
    assert len(frontier) == 1
    assert frontier[0].id == "trl_pra_num_001"

    # Student masters trl_pra_num_001
    adapter.upsert_student_topic_state(
        StudentTopicState(
            student_id=student_id,
            topic_id="trl_pra_num_001",
            status="MASTERED",
        )
    )

    # Now trl_pra_num_002 should unlock in CRS_PREALG
    frontier_updated = adapter.compute_knowledge_frontier(student_id, "CRS_PREALG")
    assert len(frontier_updated) == 1
    assert frontier_updated[0].id == "trl_pra_num_002"


def test_atomic_submission_and_xp_ledger(adapter):
    """Verifies atomic commit of attempt, states, and XP ledger."""
    student_id = "usr_submit_test"
    adapter.upsert_student_profile(
        StudentProfile(
            student_id=student_id,
            enrolled_course_id="CRS_PREALG",
            xp_total=50,
        )
    )

    now = datetime.now(timezone.utc)
    attempt = InteractionAttempt(
        attempt_id="att_001",
        student_id=student_id,
        topic_id="trl_pra_num_001",
        kp_id="trl_pra_num_001#kp_0",
        submitted_answer="7",
        is_correct=True,
        duration_ms=2100,
        created_at=now,
    )

    updated_state = StudentTopicState(
        student_id=student_id,
        topic_id="trl_pra_num_001",
        status="IN_PROGRESS",
        consecutive_correct=1,
    )

    xp_entry = XPLedgerEntry(
        entry_id="xp_001",
        student_id=student_id,
        topic_id="trl_pra_num_001",
        task_type="FRONTIER_LESSON",
        quality_tier="FULL_XP",
        delta_xp=3,
        reason="Correct unassisted step",
        created_at=now,
    )

    adapter.apply_submission_and_propagate_fire(
        attempt=attempt,
        updated_states=[updated_state],
        xp_entry=xp_entry,
    )

    # Verify attempt recorded
    entries = adapter.get_xp_ledger_entries(student_id)
    assert len(entries) == 1
    assert entries[0].entry_id == "xp_001"
    assert entries[0].delta_xp == 3

    # Verify profile XP incremented: 50 + 3 = 53
    prof = adapter.get_student_profile(student_id)
    assert prof.xp_total == 53

    # Verify state updated
    states = adapter.get_student_topic_states(student_id, ["trl_pra_num_001"])
    assert states["trl_pra_num_001"].consecutive_correct == 1


def test_dashboard_task_menu(adapter):
    """Verifies sticky dashboard menu persistence and ordering."""
    student_id = "usr_dash_test"
    now = datetime.now(timezone.utc)

    items = [
        DashboardTaskItem(
            student_id=student_id,
            topic_id="trl_pra_num_001",
            task_type="FRONTIER_LESSON",
            rank_score=0.95,
            is_sticky=True,
            assigned_at=now,
        ),
        DashboardTaskItem(
            student_id=student_id,
            topic_id="trl_pra_num_002",
            task_type="SPACED_REVIEW",
            rank_score=0.80,
            is_sticky=True,
            assigned_at=now,
        ),
    ]

    adapter.save_dashboard_menu(student_id, items)
    menu = adapter.get_dashboard_menu(student_id)
    assert len(menu) == 2
    assert menu[0].topic_id == "trl_pra_num_001"
    assert menu[0].rank_score >= menu[1].rank_score


def test_content_shell_registry(adapter):
    """Verifies decoupled content shell retrieval and update."""
    shell = adapter.get_kp_content_shell("trl_pra_num_001#kp_0")
    assert shell.kp_id == "trl_pra_num_001#kp_0"
    assert shell.content_status == "BLUEPRINT_READY"

    # Update shell
    updated_shell = shell.model_copy(update={"content_status": "VERIFIED_PRODUCTION"})
    adapter.upsert_kp_content_shell(updated_shell)

    fetched = adapter.get_kp_content_shell("trl_pra_num_001#kp_0")
    assert fetched.content_status == "VERIFIED_PRODUCTION"
