"""
tests/test_tier6_pedagogy.py
Comprehensive multi-backend test suite for Tier 6 (FR-6.1, FR-6.2, FR-6.3).
Tests:
  - FR-6.1: "I Do" Direct Instruction Generator & Deterministic Validator (Skill B.1)
  - FR-6.2: Non-Socratic Tier-1 Precision Remediation Engine (Skill A.5 Tier 1)
  - FR-6.3: John Wooden 5-to-1 Process Coach & Paper-and-Pencil Prompts (Skill C.2)
  - End-to-End Integration with PracticeEvaluator Active Learning Loops
Parameterized across all standalone database backends: memory, relational, graph.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Dict, List, Tuple
import pytest

from trellis.domain.models import (
    CanonicalWorkedExample,
    CanonicalWorkedExampleStep,
    DiagnosticMisconception,
    KPContentShell,
    KnowledgePoint,
    ProblemBlueprint,
    StudentProfile,
    StudentTopicState,
    TopicNode,
)
from trellis.engine.content_blueprint_solver import ContentBlueprintSolver
from trellis.engine.diagnostic_engine import DiagnosticEngine
from trellis.engine.frontier_service import FrontierService
from trellis.engine.hsrs_fire_engine import HSRSFireEngine
from trellis.engine.practice_evaluator import (
    InstructionCardPayload,
    PracticeConfig,
    PracticeEvaluator,
    PracticeSubmission,
    RemediationCardPayload,
)
from trellis.engine.session_scheduler import SessionScheduler
from trellis.engine.topology_service import TopologyService
from trellis.pedagogy.instruction_coach_service import InstructionCoachService
from trellis.pedagogy.llm_client import (
    DeterministicMockLLMClient,
    PedagogyLLMClient,
)
from trellis.pedagogy.models import (
    GenerateInstructionRequest,
    RemediationRequest,
)
from trellis.pedagogy.prompt_templates import (
    PAPER_PENCIL_PROMPT_MESSAGE,
    contains_hollow_praise,
    contains_slip_dismissal,
    contains_socratic_inquiry,
    split_into_sentences,
)
from trellis.ports.storage_port import get_storage_adapter


def _make_pedagogy_node(
    node_id: str,
    title: str,
    course_id: str,
    cluster: str,
    prereqs: list[str],
    encompassed: list[tuple[str, float]],
    key_prereq_id: str | None = None,
    num_kps: int = 2,
) -> dict:
    kps = []
    for idx in range(num_kps):
        scaff = "KP_1_BASELINE" if idx == 0 else "KP_3_MULTI_STEP_INVERSION"
        chunks = 2 if idx == 0 else 3
        kps.append(
            {
                "kp_index": idx,
                "kp_id": f"{node_id}#kp_{idx}",
                "scaffolding_tier": scaff,
                "kp_focus": f"Focus rule {idx} for {title}",
                "novel_chunk_count": chunks,
                "key_prerequisite_id": key_prereq_id,
                "rule_statement": f"Rule {idx} for {title}: Apply the property directly.",
                "canonical_worked_example": {
                    "problem": f"Evaluate {idx} + 1",
                    "subgoal_steps": [
                        {
                            "subgoal_label": "Identify operation",
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
                        "violated_rule": "An odd power preserves negative factor sign.",
                        "tier1_remediation_template": "Do not invert the sign of the leading factor.",
                    }
                ],
            }
        )

    return {
        "id": node_id,
        "title": title,
        "course_id": course_id,
        "canonical_domain": "ALGEBRA",
        "domain_cluster": cluster,
        "grade_level_min": 8,
        "grade_level_max": 9,
        "is_core": True,
        "base_difficulty": 0.4,
        "estimated_xp": 3,
        "mastery_gate_n": 3,
        "key_prerequisite_id": key_prereq_id,
        "mastery_criteria": [f"Solve {title} accurately"],
        "knowledge_points": kps,
        "prerequisites": [
            {"prerequisite_id": pid, "strength": "hard", "rationale": "Required"}
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


TIER6_TEST_GRAPH = {
    "schema_version": "2.0.0-trellis",
    "graph_id": "trellis-tier6-test-dag",
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
        _make_pedagogy_node("trl_fnd_1", "Signed Operations", "CRS_PREALG", "CL_NUM", [], []),
        _make_pedagogy_node("trl_alg_1", "Linear Equations", "CRS_ALG1", "CL_EQN", ["trl_fnd_1"], [("trl_fnd_1", 0.50)], key_prereq_id="trl_fnd_1"),
    ],
}


@pytest.fixture(params=["memory", "relational", "graph"])
def tier6_fixture(request):
    """Initializes TrellisStoragePort, ContentBlueprintSolver, and InstructionCoachService."""
    storage = get_storage_adapter(request.param)
    storage.ingest_knowledge_graph(TIER6_TEST_GRAPH)
    solver = ContentBlueprintSolver(storage)
    coach = InstructionCoachService(storage=storage, solver=solver)
    evaluator = PracticeEvaluator(
        storage=storage,
        blueprint_solver=solver,
        coach_service=coach,
    )
    return storage, solver, coach, evaluator


# ===========================================================================
# 1. FR-6.1: "I Do" Direct Instruction Generator & Validator Tests
# ===========================================================================

def test_fr6_1_generate_and_cache_instruction_card(tier6_fixture):
    """Verifies generation, Catrambone subgoal step validation, and shell caching."""
    storage, solver, coach, _ = tier6_fixture
    kp_id = "trl_alg_1#kp_0"

    # Shell initially in BLUEPRINT_READY
    shell_before = storage.get_kp_content_shell(kp_id)
    assert shell_before.content_status == "BLUEPRINT_READY"

    # Generate card with dual-coding SVG asset
    svg_uri = "assets/svg/linear_equations_schema.svg"
    card = coach.generate_instruction_card(kp_id, force_refresh=True, svg_asset_uri=svg_uri)

    assert isinstance(card, InstructionCardPayload)
    assert card.kp_id == kp_id
    assert card.svg_asset_uri == svg_uri

    # Invariants: <= 2 sentences, <= 4 novel chunks
    sentences = split_into_sentences(card.rule_statement)
    assert 1 <= len(sentences) <= 2
    assert card.novel_chunk_count <= 4

    # Worked example invariants: >= 1 Catrambone subgoal step
    assert card.worked_example is not None
    assert len(card.worked_example.subgoal_steps) >= 1
    for step in card.worked_example.subgoal_steps:
        assert step.subgoal_label != ""
        assert step.operation != ""
        assert step.justification != ""
    assert card.worked_example.final_answer != ""

    # Zero Socratic inquiry
    assert not contains_socratic_inquiry(card.rule_statement)

    # Shell cached in storage with status advanced to LLM_DRAFTED
    shell_after = storage.get_kp_content_shell(kp_id)
    assert shell_after.content_status == "LLM_DRAFTED"
    assert shell_after.rule_statement == card.rule_statement
    assert shell_after.worked_example == card.worked_example
    assert shell_after.svg_asset_uri == svg_uri

    # Subsequent call without force_refresh returns cached card immediately
    cached_card = coach.generate_instruction_card(kp_id, force_refresh=False)
    assert cached_card.rule_statement == card.rule_statement


def test_fr6_1_deterministic_validator_rejection(tier6_fixture):
    """Verifies deterministic validator strictly catches and rejects prohibited structures."""
    storage, solver, _, _ = tier6_fixture

    # Test A: Socratic inquiry rejection
    mock_socratic = DeterministicMockLLMClient(inject_socratic_error=True)
    coach_socratic = InstructionCoachService(storage=storage, solver=solver, llm_client=mock_socratic)
    with pytest.raises(ValueError, match="failed deterministic validation"):
        coach_socratic.generate_instruction_card("trl_alg_1#kp_0", force_refresh=True)

    # Test B: Excessive sentences (> 2 sentences) rejection
    mock_sentences = DeterministicMockLLMClient(inject_excess_sentences=True)
    coach_sentences = InstructionCoachService(storage=storage, solver=solver, llm_client=mock_sentences)
    with pytest.raises(ValueError, match="exceeds 2-sentence ceiling"):
        coach_sentences.generate_instruction_card("trl_alg_1#kp_0", force_refresh=True)

    # Test C: Excessive cognitive load (> 4 novel chunks) rejection
    mock_chunks = DeterministicMockLLMClient(inject_excess_chunks=True)
    coach_chunks = InstructionCoachService(storage=storage, solver=solver, llm_client=mock_chunks)
    with pytest.raises(ValueError, match="exceeds cognitive load limit of 4 chunks"):
        coach_chunks.generate_instruction_card("trl_alg_1#kp_0", force_refresh=True)

    # Test D: Direct validator inspection on invalid worked example
    invalid_card = InstructionCardPayload(
        topic_id="trl_alg_1",
        kp_id="trl_alg_1#kp_0",
        kp_index=0,
        scaffolding_tier="KP_1",
        kp_focus="Focus",
        novel_chunk_count=2,
        rule_statement="A valid single sentence rule.",
        worked_example=CanonicalWorkedExample(
            problem="",
            subgoal_steps=[],
            final_answer="",
        ),
    )
    val_res = coach_chunks.validate_instruction_card(invalid_card)
    assert val_res.is_valid is False
    assert any("Worked example problem stem is empty" in e for e in val_res.errors)
    assert any("Worked example final boxed answer is empty" in e for e in val_res.errors)
    assert any("must contain at least 1 Catrambone subgoal step" in e for e in val_res.errors)


# ===========================================================================
# 2. FR-6.2: Non-Socratic Tier-1 Precision Remediation Tests
# ===========================================================================

def test_fr6_2_fast_path_misconception_matching(tier6_fixture):
    """Verifies sub-5ms fast path retrieval for cataloged diagnostic misconceptions."""
    storage, solver, coach, _ = tier6_fixture
    kp_id = "trl_alg_1#kp_0"

    t0 = time.perf_counter()
    card = coach.generate_tier1_remediation(
        topic_id="trl_alg_1",
        kp_id=kp_id,
        student_answer="-5 + 0",  # Matches ERR_SIGN_INVERSION: -{p_1} + {idx}
        target_answer="5 + 0",
        problem_stem="Compute 5 + 0",
        parameters={"p_1": 5},
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert elapsed_ms < 50.0  # Generous upper bound for test execution (fast path is < 5 ms in memory/relational)
    assert card.error_tag == "ERR_SIGN_INVERSION"
    assert card.violated_rule == "An odd power preserves negative factor sign."
    assert "Do not invert the sign" in card.identified_misstep
    assert card.boxed_answer == "5 + 0"
    assert len(card.corrected_steps) >= 2

    # Deterministic validation pass
    val = coach.validate_remediation_card(card)
    assert val.is_valid is True
    assert len(val.socratic_violations) == 0
    assert len(val.hollow_praise_violations) == 0


def test_fr6_2_fallback_and_validator_rejection(tier6_fixture):
    """Verifies fallback SymPy/LLM path and strict ban on Socratic/dismissive phrasing."""
    storage, solver, coach, _ = tier6_fixture
    kp_id = "trl_alg_1#kp_0"

    # Arbitrary incorrect answer not matching any documented misconception
    card = coach.generate_tier1_remediation(
        topic_id="trl_alg_1",
        kp_id=kp_id,
        student_answer="999",
        target_answer="5 + 0",
        problem_stem="Compute 5 + 0",
        parameters={"p_1": 5},
    )
    assert card.error_tag == "ERR_PROCEDURAL_MISSTEP"
    assert card.boxed_answer == "5 + 0"
    assert len(card.corrected_steps) >= 1

    # Test rejection of Socratic questioning in remediation
    socratic_rem = RemediationCardPayload(
        topic_id="trl_alg_1",
        kp_id=kp_id,
        error_tag="ERR_SOCRATIC",
        violated_rule="Why did you make that choice?",
        identified_misstep="Notice how the factors combine. What do you think happens?",
        corrected_steps=["Check step 1"],
        boxed_answer="5",
    )
    val_soc = coach.validate_remediation_card(socratic_rem)
    assert val_soc.is_valid is False
    assert len(val_soc.socratic_violations) > 0

    # Test rejection of "silly mistake" dismissals in remediation
    slip_rem = RemediationCardPayload(
        topic_id="trl_alg_1",
        kp_id=kp_id,
        error_tag="ERR_SLIP",
        violated_rule="Sign rules.",
        identified_misstep="Don't worry, that was just a silly mistake on the arithmetic.",
        corrected_steps=["Check step 1"],
        boxed_answer="5",
    )
    val_slip = coach.validate_remediation_card(slip_rem)
    assert val_slip.is_valid is False
    assert len(val_slip.slip_dismissal_violations) > 0


# ===========================================================================
# 3. FR-6.3: John Wooden 5-to-1 Process Coach & Paper-and-Pencil Tests
# ===========================================================================

def test_fr6_3_five_to_one_ratio_tracking_and_profile_telemetry(tier6_fixture):
    """Verifies 5-to-1 confirmation-to-correction ratio tracking and profile persistence."""
    storage, solver, coach, _ = tier6_fixture
    student_id = "std_wooden_ratio"

    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1", xp_total=0)
    )

    # 1. First error -> Corrective Intervention
    fb_err = coach.evaluate_coaching_feedback(
        student_id=student_id,
        topic_id="trl_alg_1",
        is_correct=False,
        kp_index=0,
        duration_ms=4000,
        student_answer="wrong",
        target_answer="right",
    )
    assert fb_err.feedback_type == "CORRECTIVE_INTERVENTION"
    assert fb_err.corrective_interventions_count == 1
    assert fb_err.process_confirmations_count == 0
    assert fb_err.prompt_paper_and_pencil is True  # Triggered immediately on error recovery

    # 2. 5 consecutive process confirmations
    for i in range(1, 6):
        fb_succ = coach.evaluate_coaching_feedback(
            student_id=student_id,
            topic_id="trl_alg_1",
            is_correct=True,
            kp_index=0,
            duration_ms=3000,
            student_answer="right",
            target_answer="right",
        )
        assert fb_succ.feedback_type == "PROCESS_CONFIRMATION"
        assert fb_succ.process_confirmations_count == i
        assert fb_succ.corrective_interventions_count == 1

    # Ratio is now exactly 5.0 (5 confirmations : 1 correction)
    assert fb_succ.current_praise_ratio == 5.0

    # Telemetry persisted in StudentProfile
    prof = storage.get_student_profile(student_id)
    assert prof.process_praise_counter == 5


def test_fr6_3_rejection_of_ability_praise(tier6_fixture):
    """Verifies that ability praise is sanitized into process-grounded confirmations."""
    storage, solver, _, _ = tier6_fixture
    student_id = "std_wooden_praise"

    # Inject ability praise into mock
    mock_praise = DeterministicMockLLMClient(inject_hollow_praise=True)
    coach = InstructionCoachService(storage=storage, solver=solver, llm_client=mock_praise)

    fb = coach.evaluate_coaching_feedback(
        student_id=student_id,
        topic_id="trl_alg_1",
        is_correct=True,
        kp_index=0,
        duration_ms=2500,
    )

    # Prohibited ability praise ("math genius") must NOT appear in output message
    assert not contains_hollow_praise(fb.message)
    assert "Disciplined execution" in fb.message


def test_fr6_3_paper_and_pencil_heuristics(tier6_fixture):
    """Verifies cognitive load heuristics for prompting 'When in doubt, write it out'."""
    storage, solver, coach, _ = tier6_fixture
    student_id = "std_paper_pencil"

    # Case A: Elevated solving latency (> 2.5x expected 15s = 37.5s)
    fb_slow = coach.evaluate_coaching_feedback(
        student_id=student_id,
        topic_id="trl_alg_1",
        is_correct=True,
        kp_index=0,
        duration_ms=45000,  # 45 seconds
        expected_duration_ms=15000,
    )
    assert fb_slow.prompt_paper_and_pencil is True
    assert "Working memory juggling" in (fb_slow.paper_and_pencil_rationale or "")

    # Case B: Multi-step structural inversion KP (kp_index = 1, scaffolding_tier = KP_3_MULTI_STEP_INVERSION)
    fb_multistep = coach.evaluate_coaching_feedback(
        student_id=student_id,
        topic_id="trl_alg_1",
        is_correct=True,
        kp_index=1,
        duration_ms=10000,
        expected_duration_ms=15000,
    )
    assert fb_multistep.prompt_paper_and_pencil is True
    assert "Multi-step manipulation" in (fb_multistep.paper_and_pencil_rationale or "")


# ===========================================================================
# 4. End-to-End Active Practice Evaluator Integration Tests
# ===========================================================================

def test_fr6_integration_lesson_loop_with_pedagogy_and_wooden_coach(tier6_fixture):
    """Verifies complete loop: instruction card presentation, Tier-1 precision card, and Wooden coaching."""
    storage, solver, coach, evaluator = tier6_fixture
    student_id = "std_e2e_pedagogy"
    now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)

    # Master prerequisite first
    storage.upsert_student_profile(
        StudentProfile(student_id=student_id, enrolled_course_id="CRS_ALG1", xp_total=0)
    )
    s_fnd = StudentTopicState(student_id=student_id, topic_id="trl_fnd_1", status="MASTERED")
    storage.upsert_student_topic_state(s_fnd)

    # 1. Start lesson on trl_alg_1 -> Instruction card hydrated with Catrambone steps
    step = evaluator.start_lesson(student_id=student_id, topic_id="trl_alg_1", as_of=now)
    assert step.show_instruction_card is True
    assert step.instruction_card is not None
    assert len(step.instruction_card.worked_example.subgoal_steps) >= 1

    # 2. Student errs on question with sign inversion misconception
    sub_err = PracticeSubmission(
        student_id=student_id,
        topic_id="trl_alg_1",
        kp_id=step.kp_id,
        student_answer="-2 + 0",
        duration_ms=2500,
        as_of=now,
        parameters={"p_1": 2},
    )
    outcome_err = evaluator.evaluate_lesson_submission(sub_err)

    assert outcome_err.evaluation.correct is False
    assert outcome_err.evaluation.tier_remediation == 1
    assert outcome_err.remediation_card is not None
    assert outcome_err.remediation_card.error_tag == "ERR_SIGN_INVERSION"
    assert outcome_err.coaching_feedback is not None
    assert outcome_err.coaching_feedback.feedback_type == "CORRECTIVE_INTERVENTION"
    assert outcome_err.coaching_feedback.prompt_paper_and_pencil is True

    # 3. Student achieves N=3 consecutive unassisted correct answers
    for rep in range(3):
        # Extract correct target answer from problem instance
        active_inst = evaluator._active_problem_instances.get((student_id, "trl_alg_1"))
        correct_ans = active_inst.target_answer if active_inst else "2 + 0"

        sub_corr = PracticeSubmission(
            student_id=student_id,
            topic_id="trl_alg_1",
            kp_id=step.kp_id,
            student_answer=correct_ans,
            duration_ms=2000,
            as_of=now,
        )
        outcome_corr = evaluator.evaluate_lesson_submission(sub_corr)
        assert outcome_corr.evaluation.correct is True
        assert outcome_corr.coaching_feedback is not None
        assert outcome_corr.coaching_feedback.feedback_type == "PROCESS_CONFIRMATION"

    # Step advanced to KP 1
    assert outcome_corr.evaluation.kp_completed is True
