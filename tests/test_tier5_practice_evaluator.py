"""
tests/test_tier5_practice_evaluator.py
Comprehensive multi-backend test suite for Tier 5 Phase 1 (FR-5.1 - FR-5.5).
Parameterized across all standalone database backends: memory, relational, graph.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple
import pytest

from trellis.domain.models import (
    DashboardTaskItem,
    StudentProfile,
    StudentTopicState,
    TopicNode,
)
from trellis.engine.content_blueprint_solver import ContentBlueprintSolver
from trellis.engine.diagnostic_engine import DiagnosticEngine
from trellis.engine.frontier_service import FrontierService
from trellis.engine.hsrs_fire_engine import HSRSFireEngine
from trellis.engine.practice_evaluator import (
    PracticeConfig,
    PracticeEvaluator,
    PracticeSubmission,
    QuizSubmissionItem,
)
from trellis.engine.session_scheduler import SessionScheduler
from trellis.engine.topology_service import TopologyService
from trellis.ports.storage_port import get_storage_adapter


def _make_node(
    node_id: str,
    title: str,
    course_id: str,
    cluster: str,
    prereqs: list[str],
    encompassed: list[tuple[str, float]],
    key_prereq_id: str | None = None,
    skill_type: str = "PROCEDURAL",
    requires_auto: bool = False,
    auto_ceiling_ms: int | None = None,
    difficulty: float = 0.4,
    num_kps: int = 3,
) -> dict:
    kps = []
    for idx in range(num_kps):
        kps.append(
            {
                "kp_index": idx,
                "kp_id": f"{node_id}#kp_{idx}",
                "scaffolding_tier": f"KP_{idx+1}_TIER",
                "kp_focus": f"Focus {idx} for {title}",
                "novel_chunk_count": 2,
                "key_prerequisite_id": key_prereq_id,
                "rule_statement": f"Rule {idx} for {title}.",
                "canonical_worked_example": {
                    "problem": f"Evaluate {idx} + 1",
                    "subgoal_steps": [
                        {
                            "subgoal_label": "Apply rule",
                            "operation": f"Compute {idx} + 1",
                            "justification": "Arithmetic definition",
                        }
                    ],
                    "final_answer": f"{{p_1}} + {idx}",
                },
                "problem_blueprint": {
                    "parameter_constraints": "p_1 in {2..9}",
                    "stem_template": f"Compute {{p_1}} + {idx}",
                    "answer_format": "NUMERIC_EXACT",
                    "verification_solver_expr": "sympy.simplify(target_expr - student_expr) == 0",
                },
                "diagnostic_misconceptions": [
                    {
                        "error_tag": "ERR_SIGN_INVERSION",
                        "trap_answer_pattern": f"-{{p_1}} + {idx}",
                        "violated_rule": "Sign inversion rule violated.",
                        "tier1_remediation_template": "Do not invert the leading term.",
                    }
                ],
            }
        )

    return {
        "id": node_id,
        "title": title,
        "course_id": course_id,
        "canonical_domain": "03_equations_and_inequalities",
        "domain_cluster": cluster,
        "skill_type": skill_type,
        "bloom_cognitive_tier": "PROCEDURAL_EXECUTION",
        "grade_level_min": 8,
        "grade_level_max": 9,
        "is_core": True,
        "in_foundations_sequence": True,
        "requires_automaticity": requires_auto,
        "automaticity_Defining_latency_ms": auto_ceiling_ms,
        "base_difficulty": difficulty,
        "estimated_xp": 4,
        "mastery_gate_n": 3,
        "key_prerequisite_id": key_prereq_id,
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


TIER5_TEST_GRAPH = {
    "schema_version": "2.0.0-trellis",
    "graph_id": "trellis-tier5-test-dag",
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
        # Foundation Nodes
        _make_node("trl_fnd_1", "Basic Addition", "CRS_PREALG", "CL_NUM", [], []),
        _make_node("trl_fnd_2", "Signed Numbers", "CRS_PREALG", "CL_NUM", ["trl_fnd_1"], [("trl_fnd_1", 0.50)], key_prereq_id="trl_fnd_1"),
        _make_node(
            "trl_fact_1", "Math Facts Fluency", "CRS_PREALG", "CL_FACT", [], [],
            skill_type="FLUENCY_FACT", requires_auto=True, auto_ceiling_ms=3000
        ),

        # Algebra 1 Nodes
        _make_node("trl_alg_1", "Linear Equations 1", "CRS_ALG1", "CL_EQN", ["trl_fnd_2"], [("trl_fnd_2", 0.50)], key_prereq_id="trl_fnd_2"),
        _make_node("trl_alg_2", "Linear Equations 2", "CRS_ALG1", "CL_EQN", ["trl_alg_1"], [("trl_alg_1", 0.50)], key_prereq_id="trl_alg_1"),
        _make_node("trl_geo_1", "Coordinate Slope", "CRS_ALG1", "CL_GEO", ["trl_alg_1"], [("trl_alg_1", 0.50)], key_prereq_id="trl_alg_1"),
    ],
}


@pytest.fixture(params=["memory", "relational", "graph"])
def tier5_fixture(request):
    backend = request.param
    storage = get_storage_adapter(backend)
    storage.ingest_knowledge_graph(TIER5_TEST_GRAPH)
    topo = TopologyService(storage)
    solver = ContentBlueprintSolver(storage)
    diag = DiagnosticEngine(storage, topology=topo, blueprint_solver=solver)
    frontier = FrontierService(storage, topology_service=topo)
    hsrs = HSRSFireEngine(storage)
    scheduler = SessionScheduler(storage, frontier_service=frontier, hsrs_engine=hsrs)
    evaluator = PracticeEvaluator(
        storage=storage,
        topology_service=topo,
        blueprint_solver=solver,
        diagnostic_engine=diag,
        frontier_service=frontier,
        hsrs_engine=hsrs,
        scheduler=scheduler,
    )
    yield storage, evaluator, hsrs, frontier, scheduler
    if hasattr(storage, "close"):
        storage.close()


# ===========================================================================
# FR-5.1: Hyper-Scaffolded Lesson Loop & N=3 Mastery Gate
# ===========================================================================

def test_fr5_1_lesson_staircase_and_n3_mastery_gate(tier5_fixture):
    storage, evaluator, hsrs, frontier, scheduler = tier5_fixture
    now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_5_1_mastery"

    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_PREALG", xp_total=0)
    )

    # Start lesson on trl_fnd_1 (3 KPs, N=3 gate)
    step = evaluator.start_lesson(student_id, "trl_fnd_1", as_of=now)
    assert step.topic_id == "trl_fnd_1"
    assert step.kp_index == 0
    assert step.consecutive_correct == 0
    assert step.show_instruction_card is True
    assert step.instruction_card is not None

    # Step through all 3 KPs: 3 consecutive unassisted correct per KP
    for kp_idx in range(3):
        for sub_step in range(3):
            cur_step = evaluator.get_current_lesson_step(student_id, "trl_fnd_1")
            assert cur_step.kp_index == kp_idx
            p1_val = cur_step.problem_instance.parameters["p_1"]
            correct_ans = str(p1_val + kp_idx)

            sub = PracticeSubmission(
                student_id=student_id,
                topic_id="trl_fnd_1",
                kp_id=cur_step.kp_id,
                kp_index=kp_idx,
                student_answer=correct_ans,
                duration_ms=2000,
                reference_peeked=False,
                as_of=now,
            )
            outcome = evaluator.evaluate_lesson_submission(sub)
            assert outcome.evaluation.correct is True

            if sub_step < 2:
                assert outcome.evaluation.consecutive_correct == sub_step + 1
                assert outcome.evaluation.kp_completed is False
                assert outcome.evaluation.node_mastered is False
                assert outcome.evaluation.next_action == "SERVE_NEXT_STEP"
            else:
                assert outcome.evaluation.kp_completed is True
                if kp_idx < 2:
                    assert outcome.evaluation.node_mastered is False
                    assert outcome.evaluation.next_action == "SERVE_NEXT_STEP"
                else:
                    # Final KP completed -> Node Mastered!
                    assert outcome.evaluation.node_mastered is True
                    assert outcome.evaluation.next_action == "SERVE_NEXT_TASK"
                    assert outcome.evaluation.xp_awarded == 4

    # Verify state transitioned to MASTERED
    states = storage.get_student_topic_states(student_id, ["trl_fnd_1"])
    assert states["trl_fnd_1"].status == "MASTERED"

    # Verify hard dependent trl_fnd_2 is now unlocked to AVAILABLE
    frontier_nodes = frontier.compute_knowledge_frontier(student_id, "CRS_PREALG")
    assert "trl_fnd_2" in [n.id for n in frontier_nodes]


def test_fr5_1_expertise_reversal_bypass_and_fallback_on_error(tier5_fixture):
    storage, evaluator, hsrs, frontier, scheduler = tier5_fixture
    now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_5_1_exp_rev"

    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_PREALG", xp_total=0)
    )

    # Start lesson with >85% diagnostic accuracy
    step = evaluator.start_lesson(student_id, "trl_fnd_1", as_of=now, diagnostic_accuracy=0.92)
    assert step.expertise_reversal_bypass is True
    assert step.show_instruction_card is False

    # Erring on the question immediately triggers Tier 1 and reveals the instruction card
    sub = PracticeSubmission(
        student_id=student_id,
        topic_id="trl_fnd_1",
        kp_id=step.kp_id,
        kp_index=0,
        student_answer="9999",  # incorrect
        duration_ms=1500,
        reference_peeked=False,
        as_of=now,
    )
    outcome = evaluator.evaluate_lesson_submission(sub)
    assert outcome.evaluation.correct is False
    assert outcome.evaluation.tier_remediation == 1
    assert outcome.remediation_card is not None
    assert outcome.next_step is not None
    assert outcome.next_step.show_instruction_card is True


def test_fr5_1_assisted_peek_does_not_increment_mastery_gate(tier5_fixture):
    storage, evaluator, hsrs, frontier, scheduler = tier5_fixture
    now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_5_1_assisted"

    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_PREALG", xp_total=0)
    )

    step = evaluator.start_lesson(student_id, "trl_fnd_1", as_of=now)
    p1_val = step.problem_instance.parameters["p_1"]
    correct_ans = str(p1_val + 0)

    # Submit correct answer BUT with reference_peeked = True
    sub = PracticeSubmission(
        student_id=student_id,
        topic_id="trl_fnd_1",
        kp_id=step.kp_id,
        kp_index=0,
        student_answer=correct_ans,
        duration_ms=2500,
        reference_peeked=True,
        as_of=now,
    )
    outcome = evaluator.evaluate_lesson_submission(sub)
    assert outcome.evaluation.correct is True
    assert outcome.evaluation.consecutive_correct == 0  # Does NOT increment towards mastery!
    assert outcome.evaluation.xp_awarded == 0


# ===========================================================================
# FR-5.2: 3-Tier Targeted Remediation Engine
# ===========================================================================

def test_fr5_2_tier1_remediation_card_and_fresh_variant(tier5_fixture):
    storage, evaluator, hsrs, frontier, scheduler = tier5_fixture
    now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_5_2_tier1"

    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_PREALG", xp_total=0)
    )

    step = evaluator.start_lesson(student_id, "trl_fnd_1", as_of=now)
    p1 = step.problem_instance.parameters["p_1"]

    # Submit trap answer pattern matching ERR_SIGN_INVERSION
    trap_ans = f"-{p1} + 0"
    sub = PracticeSubmission(
        student_id=student_id,
        topic_id="trl_fnd_1",
        kp_id=step.kp_id,
        kp_index=0,
        student_answer=trap_ans,
        duration_ms=2000,
        parameters=step.problem_instance.parameters,
        as_of=now,
    )
    outcome = evaluator.evaluate_lesson_submission(sub)
    assert outcome.evaluation.correct is False
    assert outcome.evaluation.tier_remediation == 1
    assert outcome.evaluation.next_action == "TIER_1_REMEDIATION"
    assert outcome.remediation_card.error_tag == "ERR_SIGN_INVERSION"
    assert outcome.remediation_card.violated_rule == "Sign inversion rule violated."
    assert outcome.next_step is not None


def test_fr5_2_tier2_attempt_budget_halt_and_consolidation_cooldown(tier5_fixture):
    storage, evaluator, hsrs, frontier, scheduler = tier5_fixture
    now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_5_2_tier2"

    evaluator.config = PracticeConfig(kp_attempt_budget=3, tier2_cooldown_minutes=45)

    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_PREALG", xp_total=0)
    )

    step = evaluator.start_lesson(student_id, "trl_fnd_1", as_of=now)
    p1 = step.problem_instance.parameters["p_1"]

    # Attempt 1: Correct (consecutive=1)
    sub1 = PracticeSubmission(
        student_id=student_id, topic_id="trl_fnd_1", kp_id=step.kp_id,
        student_answer=str(p1), duration_ms=2000, as_of=now
    )
    res1 = evaluator.evaluate_lesson_submission(sub1)
    assert res1.evaluation.correct is True

    # Attempt 2: Correct (consecutive=2)
    step2 = evaluator.get_current_lesson_step(student_id, "trl_fnd_1")
    p1_2 = step2.problem_instance.parameters["p_1"]
    sub2 = PracticeSubmission(
        student_id=student_id, topic_id="trl_fnd_1", kp_id=step.kp_id,
        student_answer=str(p1_2), duration_ms=2000, as_of=now
    )
    res2 = evaluator.evaluate_lesson_submission(sub2)
    assert res2.evaluation.correct is True

    # Attempt 3 (Budget=3 exhausted on 1st error): Triggers Tier 2 Cooldown!
    sub3 = PracticeSubmission(
        student_id=student_id, topic_id="trl_fnd_1", kp_id=step.kp_id,
        student_answer="wrong", duration_ms=2000, as_of=now
    )
    res3 = evaluator.evaluate_lesson_submission(sub3)
    assert res3.evaluation.correct is False
    assert res3.evaluation.tier_remediation == 2
    assert res3.evaluation.next_action == "CONSOLIDATION_COOLDOWN"

    # Verify state has cooldown_until set
    st = storage.get_student_topic_states(student_id, ["trl_fnd_1"])["trl_fnd_1"]
    assert st.cooldown_until is not None

    # Re-starting lesson before cooldown expires raises ValueError
    with pytest.raises(ValueError, match="consolidation cooldown"):
        evaluator.start_lesson(student_id, "trl_fnd_1", as_of=now + timedelta(minutes=10))


def test_fr5_2_tier3_key_prerequisite_halt_and_prerequisite_peelback(tier5_fixture):
    storage, evaluator, hsrs, frontier, scheduler = tier5_fixture
    now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_5_2_tier3"

    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_PREALG", xp_total=0)
    )

    # Master trl_fnd_1 first
    s1 = StudentTopicState(student_id=student_id, topic_id="trl_fnd_1", status="MASTERED")
    storage.upsert_student_topic_state(s1)

    # Start trl_fnd_2 (key_prerequisite_id = trl_fnd_1)
    step = evaluator.start_lesson(student_id, "trl_fnd_2", as_of=now)

    # Error 1 on KP 0 -> Tier 1
    sub1 = PracticeSubmission(
        student_id=student_id, topic_id="trl_fnd_2", kp_id=step.kp_id,
        student_answer="wrong1", duration_ms=2000, as_of=now
    )
    res1 = evaluator.evaluate_lesson_submission(sub1)
    assert res1.evaluation.tier_remediation == 1

    # Error 2 on KP 0 -> Tier 3 HALT TO KEY PREREQUISITE!
    sub2 = PracticeSubmission(
        student_id=student_id, topic_id="trl_fnd_2", kp_id=step.kp_id,
        student_answer="wrong2", duration_ms=2000, as_of=now
    )
    res2 = evaluator.evaluate_lesson_submission(sub2)
    assert res2.evaluation.tier_remediation == 3
    assert res2.evaluation.next_action == "HALT_TO_KEY_PREREQUISITE"
    assert res2.evaluation.remedial_target_topic_id == "trl_fnd_1"

    # Verify trl_fnd_2 status is TIER_3_REMEDIATION
    st = storage.get_student_topic_states(student_id, ["trl_fnd_2"])["trl_fnd_2"]
    assert st.status == "TIER_3_REMEDIATION"

    # Verify TARGETED_REMEDIATION is injected on dashboard
    menu = storage.get_dashboard_menu(student_id)
    rem_items = [it for it in menu if it.task_type == "TARGETED_REMEDIATION"]
    assert len(rem_items) >= 1
    assert rem_items[0].topic_id == "trl_fnd_1"

    # CASE A: If student FAILS the remedial review, peel back trl_fnd_1 to a full lesson!
    rev_session = evaluator.start_review_session(
        student_id=student_id,
        topic_id="trl_fnd_1",
        task_type="TARGETED_REMEDIATION",
        blocked_dependent_topic_id="trl_fnd_2",
    )
    # Fail 3 questions
    for i in range(3):
        rev_session, outcome = evaluator.submit_review_answer(
            session=rev_session, question_index=i, student_answer="wrong", as_of=now
        )
    assert outcome is not None
    assert outcome.passed is False
    assert outcome.peeled_back_to_lesson is True

    # trl_fnd_1 state peeled back to AVAILABLE
    fnd1_state = storage.get_student_topic_states(student_id, ["trl_fnd_1"])["trl_fnd_1"]
    assert fnd1_state.status == "AVAILABLE"
    assert fnd1_state.current_kp_index == 0


# ===========================================================================
# FR-5.3: Micro-Interleaved Spaced Reviews & Spotter Mode
# ===========================================================================

def test_fr5_3_micro_interleaved_review_sequence_grading(tier5_fixture):
    """
    FR-5.3 exact sequence grading verification:
      - ✖✔✖✔✔ passes
      - ✔✔✖✔✖ fails
      - ✔✔✔ passes
    """
    storage, evaluator, hsrs, frontier, scheduler = tier5_fixture
    now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_5_3_seq"

    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_PREALG", xp_total=0)
    )
    s = StudentTopicState(student_id=student_id, topic_id="trl_fnd_1", status="MASTERED")
    storage.upsert_student_topic_state(s)

    # 1. Test ✖✔✖✔✔ -> PASS
    session1 = evaluator.start_review_session(student_id, "trl_fnd_1")
    seq_patterns = [False, True, False, True, True]
    out1 = None
    for idx, is_cor in enumerate(seq_patterns):
        q = session1.questions[idx]
        ans = q.problem_instance.target_answer if is_cor else "wrong"
        session1, out1 = evaluator.submit_review_answer(session1, idx, ans, as_of=now)

    assert out1 is not None
    assert out1.sequence_pattern == "✖✔✖✔✔"
    assert out1.passed is True
    assert out1.xp_awarded == 3

    # 2. Test ✔✔✖✔✖ -> FAIL
    session2 = evaluator.start_review_session(student_id, "trl_fnd_1")
    seq_patterns2 = [True, True, False, True, False]
    out2 = None
    for idx, is_cor in enumerate(seq_patterns2):
        q = session2.questions[idx]
        ans = q.problem_instance.target_answer if is_cor else "wrong"
        session2, out2 = evaluator.submit_review_answer(session2, idx, ans, as_of=now)

    assert out2 is not None
    assert out2.sequence_pattern == "✔✔✖✔✖"
    assert out2.passed is False


def test_fr5_3_spotter_reference_mode_input_lock_and_raw_delta_discount(tier5_fixture):
    storage, evaluator, hsrs, frontier, scheduler = tier5_fixture
    now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_5_3_spotter"

    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_PREALG", xp_total=0)
    )
    s = StudentTopicState(student_id=student_id, topic_id="trl_fnd_1", status="MASTERED")
    storage.upsert_student_topic_state(s)

    session = evaluator.start_review_session(student_id, "trl_fnd_1")

    # Attempting to open spotter without input_locked=True raises ValueError
    with pytest.raises(ValueError, match="input to be locked"):
        evaluator.open_spotter_reference(session, question_index=0, input_locked=False)

    # Valid lock unlocks Spotter card and sets reference_peeked = True
    card = evaluator.open_spotter_reference(session, question_index=0, input_locked=True)
    assert card is not None
    assert session.questions[0].reference_peeked is True

    # Complete review with ✔✔✔
    out = None
    for i in range(3):
        q = session.questions[i]
        session, out = evaluator.submit_review_answer(session, i, q.problem_instance.target_answer, as_of=now)

    assert out is not None
    assert out.passed is True
    assert out.reference_peeked_count == 1
    # raw_delta discounted from 1.0 -> 0.50
    assert out.raw_delta == 0.50
    assert out.quality_tier == "MOST_XP"


# ===========================================================================
# FR-5.4: Automaticity Latency Sensor & Fluency Drills
# ===========================================================================

def test_fr5_4_automaticity_latency_sensor_and_fluency_drill_intervention(tier5_fixture):
    storage, evaluator, hsrs, frontier, scheduler = tier5_fixture
    now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_5_4_auto"

    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_PREALG", xp_total=0)
    )

    # trl_fact_1 is FLUENCY_FACT with ceiling 3000 ms
    step = evaluator.start_lesson(student_id, "trl_fact_1", as_of=now)
    assert step.requires_automaticity is True
    assert step.automaticity_ceiling_ms == 3000

    p1 = step.problem_instance.parameters["p_1"]
    cor_ans = str(p1)

    # 1. Overtime accurate responses (e.g. 4500 ms) do not advance consecutive_correct
    sub_overtime = PracticeSubmission(
        student_id=student_id, topic_id="trl_fact_1", kp_id=step.kp_id,
        student_answer=cor_ans, duration_ms=4500, as_of=now
    )
    res1 = evaluator.evaluate_lesson_submission(sub_overtime)
    assert res1.automaticity_violation is True
    assert res1.evaluation.consecutive_correct == 0

    # 2. Three consecutive slow-accurate responses trigger plateau intervention
    evaluator.evaluate_lesson_submission(sub_overtime)
    res3 = evaluator.evaluate_lesson_submission(sub_overtime)
    assert res3.fluency_drill_injected is True

    # Dashboard menu now has sticky FACT_FLUENCY task
    menu = storage.get_dashboard_menu(student_id)
    assert any(it.task_type == "FACT_FLUENCY" and it.topic_id == "trl_fact_1" for it in menu)

    # 3. Build & pass fluency drill
    drill = evaluator.build_fluency_drill(student_id, "trl_fact_1")
    drill_subs = []
    for item in drill.items:
        ans = item.problem_instance.target_answer
        drill_subs.append((ans, 1800))  # 1800 ms <= 3000 ms automatic!

    drill_res = evaluator.evaluate_fluency_drill(student_id, drill, drill_subs, as_of=now)
    assert drill_res.passed is True
    assert drill_res.automatic_count == 5

    # FACT_FLUENCY task cleared from dashboard
    menu_after = storage.get_dashboard_menu(student_id)
    assert not any(it.task_type == "FACT_FLUENCY" and it.topic_id == "trl_fact_1" for it in menu_after)


# ===========================================================================
# FR-5.5: Timed Interleaved Quizzes, Remediation & Retakes
# ===========================================================================

def test_fr5_5_timed_quiz_80_85_calibration_remediation_and_retake(tier5_fixture):
    storage, evaluator, hsrs, frontier, scheduler = tier5_fixture
    now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_5_5_quiz"

    # Setup student with 150 XP and 2 mastered topics
    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1", xp_total=150)
    )
    s1 = StudentTopicState(student_id=student_id, topic_id="trl_alg_1", status="MASTERED")
    s2 = StudentTopicState(student_id=student_id, topic_id="trl_geo_1", status="MASTERED")
    storage.upsert_student_topic_state(s1)
    storage.upsert_student_topic_state(s2)

    # Quiz is due
    assert evaluator.is_quiz_due(student_id) is True

    # Build timed quiz
    quiz = evaluator.build_timed_quiz(student_id, as_of=now)
    assert len(quiz.questions) >= 2
    # Check 80-85% expected calibration target band
    assert 0.70 <= quiz.expected_mean_accuracy <= 0.90

    # Submit answers: 1 correct, 1 missed
    q0 = quiz.questions[0]
    q1 = quiz.questions[1]
    subs = [
        QuizSubmissionItem(
            question_index=0,
            topic_id=q0.topic_id,
            kp_id=q0.kp_id,
            student_answer=q0.problem_instance.target_answer,
            duration_ms=10000,
        ),
        QuizSubmissionItem(
            question_index=1,
            topic_id=q1.topic_id,
            kp_id=q1.kp_id,
            student_answer="wrong",
            duration_ms=12000,
        ),
    ]

    res = evaluator.evaluate_timed_quiz(quiz, subs, as_of=now)
    assert res.correct_count == 1
    assert q0.topic_id in res.passed_topic_ids
    assert q1.topic_id in res.missed_topic_ids
    assert res.retake_required is True
    assert res.retake_unlocked is False

    # Missed topic has Post-Quiz TARGETED_REMEDIATION injected on dashboard
    menu = storage.get_dashboard_menu(student_id)
    rem_items = [it for it in menu if it.task_type == "TARGETED_REMEDIATION" and it.topic_id == q1.topic_id]
    assert len(rem_items) == 1

    # Retake cannot be generated while remedial review is pending
    with pytest.raises(ValueError, match="locked"):
        evaluator.build_quiz_retake(student_id, quiz.quiz_id, as_of=now)

    # Pass remedial review on missed topic
    rev = evaluator.start_review_session(student_id, q1.topic_id, task_type="TARGETED_REMEDIATION")
    for i in range(3):
        ans = rev.questions[i].problem_instance.target_answer
        rev, out = evaluator.submit_review_answer(rev, i, ans, as_of=now)
    assert out.passed is True

    # Now retake is unlocked!
    assert evaluator.is_retake_unlocked(student_id, quiz.quiz_id) is True
    retake_quiz = evaluator.build_quiz_retake(student_id, quiz.quiz_id, as_of=now)
    assert retake_quiz.is_retake is True
    assert any(q.topic_id == q1.topic_id for q in retake_quiz.questions)


# ===========================================================================
# Cross-Backend Bit-Identical Parity Test
# ===========================================================================

def test_phase1_cross_backend_bit_identical_parity():
    """
    Executes identical student active practice lifecycles across:
      - MemoryReferenceAdapter
      - StandaloneRelationalAdapter
      - StandaloneGraphAdapter
    Asserts bit-identical topic states, XP totals, and dashboard menus.
    """
    now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_parity_5"
    backends = ["memory", "relational", "graph"]

    results: Dict[str, dict] = {}

    for b in backends:
        storage = get_storage_adapter(b)
        storage.ingest_knowledge_graph(TIER5_TEST_GRAPH)
        topo = TopologyService(storage)
        solver = ContentBlueprintSolver(storage)
        diag = DiagnosticEngine(storage, topo, solver)
        frontier = FrontierService(storage, topo)
        hsrs = HSRSFireEngine(storage)
        scheduler = SessionScheduler(storage, frontier, hsrs)
        evaluator = PracticeEvaluator(
            storage=storage,
            topology_service=topo,
            blueprint_solver=solver,
            diagnostic_engine=diag,
            frontier_service=frontier,
            hsrs_engine=hsrs,
            scheduler=scheduler,
        )

        storage.upsert_student_profile(
            StudentProfile(student_id=student_id, enrolled_course_id="CRS_PREALG", xp_total=0)
        )

        # 1. Step through lesson on trl_fnd_1
        step = evaluator.start_lesson(student_id, "trl_fnd_1", as_of=now)
        for kp_idx in range(3):
            for _ in range(3):
                cur = evaluator.get_current_lesson_step(student_id, "trl_fnd_1")
                ans = str(cur.problem_instance.parameters["p_1"] + kp_idx)
                evaluator.evaluate_lesson_submission(
                    PracticeSubmission(
                        student_id=student_id,
                        topic_id="trl_fnd_1",
                        kp_id=cur.kp_id,
                        student_answer=ans,
                        duration_ms=2000,
                        as_of=now,
                    )
                )

        prof = storage.get_student_profile(student_id)
        st = storage.get_student_topic_states(student_id, ["trl_fnd_1"])["trl_fnd_1"]
        menu = storage.get_dashboard_menu(student_id)

        results[b] = {
            "xp_total": prof.xp_total,
            "status": st.status,
            "current_kp_index": st.current_kp_index,
            "consecutive_correct": st.consecutive_correct,
            "rep_num": st.rep_num,
            "menu_topics": [it.topic_id for it in menu],
        }
        if hasattr(storage, "close"):
            storage.close()

    # Assert bit-identical equivalence across backends
    assert results["memory"] == results["relational"]
    assert results["relational"] == results["graph"]


def test_fr5_6_multi_part_synthesis_problem_and_component_remediation(tier5_fixture):
    """
    FR-5.6a: Multi-part applied synthesis problems.
    Verifies:
    1. Sequential part progression (part 0 -> part 1).
    2. Component topic failure attribution (isolating errors to component topic).
    3. 1st error triggers non-Socratic Tier 1 remediation card on component KP.
    4. 2nd error halts synthesis session, applies downward HSRS penalty, and injects TARGETED_REMEDIATION.
    5. Successful completion awards bonus XP and updates HSRS repetition across all component topics.
    """
    storage, evaluator, hsrs, frontier, scheduler = tier5_fixture
    now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_5_6_synth"

    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1", xp_total=50)
    )
    s1 = StudentTopicState(student_id=student_id, topic_id="trl_alg_1", status="MASTERED")
    s2 = StudentTopicState(student_id=student_id, topic_id="trl_geo_1", status="MASTERED")
    storage.upsert_student_topic_state(s1)
    storage.upsert_student_topic_state(s2)

    # 1. Start synthesis session decomposed into 2 parts
    session = evaluator.start_synthesis_session(
        student_id=student_id,
        title="Urban Planning Optimization",
        scenario_context="Plan city grid dimensions and zoning equations.",
        component_topic_ids=["trl_alg_1", "trl_geo_1"],
        as_of=now,
    )
    assert len(session.parts) == 2
    assert session.current_part_index == 0
    assert session.completed is False
    assert session.halted is False

    # Check active part 0
    p0 = evaluator.get_current_synthesis_part(session.session_id)
    assert p0.part_index == 0
    assert p0.component_topic_id == "trl_alg_1"

    # Answer part 0 correctly
    eval0 = evaluator.evaluate_synthesis_part(
        session_id=session.session_id,
        student_answer=p0.problem_instance.target_answer,
        duration_ms=2500,
        as_of=now,
    )
    assert eval0.correct is True
    assert eval0.next_part_index == 1
    assert eval0.is_session_complete is False
    assert eval0.halted_to_targeted_remediation is False
    assert eval0.xp_awarded == 2

    # Check active part 1
    p1 = evaluator.get_current_synthesis_part(session.session_id)
    assert p1.part_index == 1
    assert p1.component_topic_id == "trl_geo_1"

    # Part 1: First error -> Tier 1 remediation card emitted
    eval1_fail1 = evaluator.evaluate_synthesis_part(
        session_id=session.session_id,
        student_answer="wrong_ans_1",
        duration_ms=3000,
        as_of=now,
    )
    assert eval1_fail1.correct is False
    assert eval1_fail1.remediation_card is not None
    assert eval1_fail1.remediation_card.topic_id == "trl_geo_1"
    assert eval1_fail1.halted_to_targeted_remediation is False

    # Part 1: Second error -> Halts synthesis session and routes to Tier 3 TARGETED_REMEDIATION
    eval1_fail2 = evaluator.evaluate_synthesis_part(
        session_id=session.session_id,
        student_answer="wrong_ans_2",
        duration_ms=3500,
        as_of=now,
    )
    assert eval1_fail2.correct is False
    assert eval1_fail2.halted_to_targeted_remediation is True

    # Verify session halted
    assert session.halted is True
    assert session.failed_component_topic_id == "trl_geo_1"
    assert evaluator.get_current_synthesis_part(session.session_id) is None

    # Verify TARGETED_REMEDIATION task was injected onto dashboard menu for trl_geo_1
    menu = storage.get_dashboard_menu(student_id)
    remedial_tasks = [t for t in menu if t.topic_id == "trl_geo_1" and t.task_type == "TARGETED_REMEDIATION"]
    assert len(remedial_tasks) >= 1

    # 2. Test a complete successful run through all parts
    student_id_pass = "std_5_6_pass"
    storage.upsert_student_profile(
        StudentProfile(student_id=student_id_pass, enrolled_course_id="CRS_ALG1", xp_total=10)
    )
    sess_pass = evaluator.start_synthesis_session(
        student_id=student_id_pass,
        title="Flight Trajectory Calculation",
        scenario_context="Calculate launch angle and fuel cutoff.",
        component_topic_ids=["trl_alg_1", "trl_geo_1"],
        as_of=now,
    )
    # Part 0 correct
    p0_p = evaluator.get_current_synthesis_part(sess_pass.session_id)
    evaluator.evaluate_synthesis_part(
        session_id=sess_pass.session_id,
        student_answer=p0_p.problem_instance.target_answer,
        as_of=now,
    )
    # Part 1 correct
    p1_p = evaluator.get_current_synthesis_part(sess_pass.session_id)
    res_final = evaluator.evaluate_synthesis_part(
        session_id=sess_pass.session_id,
        student_answer=p1_p.problem_instance.target_answer,
        as_of=now,
    )
    assert res_final.correct is True
    assert res_final.is_session_complete is True
    assert sess_pass.completed is True
    assert res_final.xp_awarded == 7  # 2 per part + 5 bonus XP


def test_fr5_6_test_prep_mode_peak_freshness_500xp_exams_and_1_3_7_reattempts(tier5_fixture):
    """
    FR-5.6b: Cumulative Test-Prep Mode.
    Verifies:
    1. Enabling Test-Prep Mode pauses course promotion.
    2. Peak freshness sweep at M <= 0.85 marks decayed topics DUE_FOR_REVIEW.
    3. Practice exam trigger every 500 XP.
    4. Longitudinal slicing across easy-to-hard sorted topics.
    5. Missed & low-confidence problems trigger 1d -> 3d -> 7d spaced re-attempts.
    6. Incorrect answer at 3d or 7d resets cycle back to Day 1.
    """
    storage, evaluator, hsrs, frontier, scheduler = tier5_fixture
    now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
    student_id = "std_5_6_exam"

    # Profile with 0 XP initially
    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1", xp_total=0)
    )

    # Setup 2 mastered topics: 1 fresh, 1 decayed past 0.85 retrievability
    s1 = StudentTopicState(
        student_id=student_id,
        topic_id="trl_alg_1",
        status="MASTERED",
        stability_interval_days=10.0,
        last_reviewed_at=now,  # completely fresh: M ~ 1.0
    )
    s2 = StudentTopicState(
        student_id=student_id,
        topic_id="trl_geo_1",
        status="MASTERED",
        stability_interval_days=2.0,
        last_reviewed_at=now - timedelta(days=5),  # 5 days elapsed / 2.0 interval => M = 2^(-2.5) = 0.177 <= 0.85
    )
    storage.upsert_student_topic_state(s1)
    storage.upsert_student_topic_state(s2)

    # 1. Enable Test-Prep Mode
    state = evaluator.set_test_prep_mode(student_id=student_id, enabled=True, course_id="CRS_ALG1", as_of=now)
    assert state.enabled is True
    assert evaluator.is_course_promotion_paused(student_id) is True

    # 2. Peak freshness sweep
    refreshed = evaluator.sweep_test_prep_peak_freshness(student_id, as_of=now)
    assert "trl_geo_1" in refreshed
    assert "trl_alg_1" not in refreshed
    s2_updated = storage.get_student_topic_states(student_id, ["trl_geo_1"])["trl_geo_1"]
    assert s2_updated.status == "DUE_FOR_REVIEW"

    # 3. 500 XP interval check
    assert evaluator.is_practice_exam_due(student_id) is False
    profile = storage.get_student_profile(student_id)
    profile.xp_total = 520
    storage.upsert_student_profile(profile)
    assert evaluator.is_practice_exam_due(student_id) is True

    # 4. Longitudinal slicing: modulus 2
    exam_slice0 = evaluator.build_practice_exam(
        student_id=student_id,
        total_problems=4,
        slice_modulus=2,
        slice_remainder=0,
        as_of=now,
    )
    assert exam_slice0.is_sliced is True
    assert exam_slice0.slice_index == 0
    assert len(exam_slice0.problems) == 4

    # 5. Evaluate practice exam with 1 correct, 1 missed, and 1 low-confidence problem
    p0 = exam_slice0.problems[0]
    p1 = exam_slice0.problems[1]
    p2 = exam_slice0.problems[2]
    p3 = exam_slice0.problems[3]

    submissions = [
        QuizSubmissionItem(
            question_index=0,
            topic_id=p0.topic_id,
            kp_id=p0.kp_id,
            student_answer=p0.problem_instance.target_answer,
            duration_ms=10000,
        ),
        QuizSubmissionItem(
            question_index=1,
            topic_id=p1.topic_id,
            kp_id=p1.kp_id,
            student_answer="wrong",
            duration_ms=10000,
        ),
        QuizSubmissionItem(
            question_index=2,
            topic_id=p2.topic_id,
            kp_id=p2.kp_id,
            student_answer=p2.problem_instance.target_answer,  # correct answer, but low confidence!
            duration_ms=10000,
        ),
        QuizSubmissionItem(
            question_index=3,
            topic_id=p3.topic_id,
            kp_id=p3.kp_id,
            student_answer=p3.problem_instance.target_answer,
            duration_ms=10000,
        ),
    ]

    exam_res = evaluator.evaluate_practice_exam(
        student_id=student_id,
        exam_id=exam_slice0.exam_id,
        submissions=submissions,
        low_confidence_indices=[2],  # p2 flagged as guess/low-confidence
        as_of=now,
    )
    assert exam_res.correct_count == 2  # p0 and p3
    assert len(exam_res.reattempts) == 2  # p1 (missed) and p2 (low-confidence)

    # 6. Spaced Re-attempts (1d -> 3d -> 7d)
    reatt = exam_res.reattempts[0]
    assert reatt.stage_index == 0
    assert reatt.immediate_rework_completed is False

    # Attempt before due date raises ValueError
    with pytest.raises(ValueError, match="not yet due"):
        evaluator.evaluate_exam_spaced_reattempt(
            student_id=student_id,
            reattempt_id=reatt.reattempt_id,
            student_answer=reatt.target_answer,
            as_of=now + timedelta(hours=12),
        )

    # Complete immediate rework
    assert evaluator.complete_immediate_exam_rework(
        student_id=student_id,
        reattempt_id=reatt.reattempt_id,
        student_answer=reatt.target_answer,
    ) is True
    assert reatt.immediate_rework_completed is True

    # At +1 day (due): Correct answer advances to 3-day stage
    day1_time = now + timedelta(days=1, hours=1)
    stg1 = evaluator.evaluate_exam_spaced_reattempt(
        student_id=student_id,
        reattempt_id=reatt.reattempt_id,
        student_answer=reatt.target_answer,
        as_of=day1_time,
    )
    assert stg1.stage_index == 1
    assert stg1.graduated is False

    # At +4 days (+3 days from stage 1): Correct answer advances to 7-day stage
    day4_time = day1_time + timedelta(days=3, hours=1)
    stg2 = evaluator.evaluate_exam_spaced_reattempt(
        student_id=student_id,
        reattempt_id=reatt.reattempt_id,
        student_answer=reatt.target_answer,
        as_of=day4_time,
    )
    assert stg2.stage_index == 2
    assert stg2.graduated is False

    # At +11 days (+7 days from stage 2): Failure resets back to Day 1!
    day11_time = day4_time + timedelta(days=7, hours=1)
    reset_stg = evaluator.evaluate_exam_spaced_reattempt(
        student_id=student_id,
        reattempt_id=reatt.reattempt_id,
        student_answer="wrong_again",
        as_of=day11_time,
    )
    assert reset_stg.stage_index == 0
    assert reset_stg.immediate_rework_completed is False
    assert reset_stg.graduated is False

    # At +12 days (+1 day from reset): Correct answer restarts progression
    day12_time = day11_time + timedelta(days=1, hours=1)
    restarted = evaluator.evaluate_exam_spaced_reattempt(
        student_id=student_id,
        reattempt_id=reatt.reattempt_id,
        student_answer=reatt.target_answer,
        as_of=day12_time,
    )
    assert restarted.stage_index == 1

    # Progress to stage 2 and graduate
    day15_time = day12_time + timedelta(days=3, hours=1)
    evaluator.evaluate_exam_spaced_reattempt(
        student_id=student_id,
        reattempt_id=reatt.reattempt_id,
        student_answer=reatt.target_answer,
        as_of=day15_time,
    )
    day22_time = day15_time + timedelta(days=7, hours=1)
    grad = evaluator.evaluate_exam_spaced_reattempt(
        student_id=student_id,
        reattempt_id=reatt.reattempt_id,
        student_answer=reatt.target_answer,
        as_of=day22_time,
    )
    assert grad.stage_index == 3
    assert grad.graduated is True
