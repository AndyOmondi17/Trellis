"""
src/trellis/engine/practice_evaluator.py
Tier 5: Active Learning Execution Loops (FR-5.1 - FR-5.6).

Phase 1 Implements:
  - FR-5.1: Hyper-Scaffolded Lesson Loop & N=3 Mastery Gate (Skill A.5)
    * Sequential KP staircase (#kp_0..#kp_K-1).
    * "I Do" InstructionCard presentation with Catrambone subgoal steps.
    * Expertise Reversal bypass (>85% diagnostic accuracy bypasses initial worked example).
    * Mastery gate: N=3 consecutive unassisted correct answers per KP.
  - FR-5.2: 3-Tier Targeted Remediation Engine
    * Tier 1: 1st error resets counter, triggers non-Socratic RemediationCard, serves fresh variant.
    * Tier 2: Attempt budget exhaustion triggers Consolidation Cooldown with cooldown_until.
    * Tier 3: 2nd error at identical KP_j halts lesson (TIER_3_REMEDIATION) to key_prerequisite_id.
    * Remedial Unblocking & Prerequisite Peel-Back: Passing remedial review unblocks lesson;
      failing remedial review peels back key_prerequisite_id to a full lesson.
  - FR-5.3: Micro-Interleaved Spaced Review Loop, Sequence Grading & "Spotter" Mode
    * 3-5 questions micro-interleaved across KPs with worked examples hidden.
    * Exact sequence grading: ✖✔✖✔✔ passes, ✔✔✖✔✖ fails.
    * Read-only Spotter mode requires problem input to be locked, discounts rawDelta.
  - FR-5.4: Automaticity Latency Sensor (<= 3.0s) & Math Facts Fluency Drills (Skill A.6)
    * Latency ceiling (<= 3000 ms) on FLUENCY_FACT / requires_automaticity nodes.
    * Recalculation plateau sensor injects FACT_FLUENCY rapid-fire drill.
  - FR-5.5: Timed Interleaved Quizzes (80-85% Calibration), Remediation & Retakes
    * Quizzes scheduled every 100-150 XP across all mastered topics.
    * Expected accuracy calibrated to 80-85% with cluster non-interference.
    * Missed items trigger upward penalty flow and post-quiz remedial reviews.
    * Retake unlocked strictly after 100% of missed topics are remediated.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field

from trellis.domain.models import (
    CanonicalWorkedExample,
    DashboardTaskItem,
    InteractionAttempt,
    KnowledgePoint,
    QualityTier,
    StudentProfile,
    StudentTopicState,
    SubmissionEvaluationResult,
    TaskType,
    TopicMasteryStatus,
    TopicNode,
    XPLedgerEntry,
)
from trellis.engine.content_blueprint_solver import (
    BlueprintInstance,
    ContentBlueprintSolver,
)
from trellis.engine.diagnostic_engine import (
    ConditionalRevocationReport,
    DiagnosticEngine,
)
from trellis.engine.frontier_service import FrontierService
from trellis.engine.hsrs_fire_engine import HSRSFireEngine, RepetitionResult
from trellis.engine.session_scheduler import SessionScheduler
from trellis.engine.topology_service import TopologyService
from trellis.engine.gamification_efficiency import GamificationEfficiencyEngine
from trellis.ports.storage_port import TrellisStoragePort
from trellis.pedagogy.instruction_coach_service import InstructionCoachService


# ===========================================================================
# Section 1: Configuration & Domain DTOs
# ===========================================================================

class PracticeConfig(BaseModel):
    """Deterministic configuration constants for Tier 5 Active Learning Loops."""
    model_config = ConfigDict(frozen=True)

    default_mastery_gate_n: int = Field(default=3, ge=2, le=5)
    expertise_reversal_accuracy_threshold: float = Field(default=0.85, gt=0.0, le=1.0)
    kp_attempt_budget: int = Field(default=5, ge=2, le=10)
    tier2_cooldown_minutes: int = Field(default=30, ge=1)
    require_tier2_before_tier3: bool = Field(default=False)

    review_min_questions: int = Field(default=3, ge=3, le=5)
    review_max_questions: int = Field(default=5, ge=3, le=5)
    spotter_raw_delta_discount: float = Field(default=0.50, gt=0.0, le=1.0)

    automaticity_ceiling_ms: int = Field(default=3000, gt=0)
    automaticity_plateau_window: int = Field(default=3, ge=2)
    fluency_drill_item_count: int = Field(default=5, ge=3)

    quiz_xp_interval_min: int = Field(default=100, ge=50)
    quiz_xp_interval_max: int = Field(default=150, ge=100)
    quiz_xp_trigger_threshold: int = Field(default=125, ge=50)
    quiz_question_count: int = Field(default=5, ge=1, le=15)
    quiz_target_accuracy_min: float = Field(default=0.80, gt=0.0, le=1.0)
    quiz_target_accuracy_max: float = Field(default=0.85, gt=0.0, le=1.0)
    quiz_default_question_time_seconds: int = Field(default=60, gt=0)

    # Tier 7 Gamification & Anti-Cheat Toggles
    enable_tier7_gamification: bool = Field(default=False)


from trellis.pedagogy.models import (
    CoachingFeedback,
    InstructionCardPayload,
    RemediationCardPayload,
)


class LessonStepPresentation(BaseModel):
    """State presented to student at the current step of a lesson."""
    model_config = ConfigDict(frozen=True)

    student_id: str
    topic_id: str
    kp_id: str
    kp_index: int
    total_kps: int
    mastery_gate_n: int
    consecutive_correct: int
    show_instruction_card: bool
    expertise_reversal_bypass: bool
    instruction_card: Optional[InstructionCardPayload] = None
    problem_instance: BlueprintInstance
    requires_automaticity: bool = False
    automaticity_ceiling_ms: Optional[int] = None


class PracticeSubmission(BaseModel):
    """Input sensor payload for lesson, review, or fluency item submission."""
    student_id: str
    topic_id: str
    kp_id: str
    kp_index: int = 0
    problem_id: str = ""
    student_answer: str
    duration_ms: int = Field(..., ge=0)
    reference_peeked: bool = False
    submission_flag: Literal["ANSWER_SUBMITTED"] = "ANSWER_SUBMITTED"
    target_answer: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None
    as_of: Optional[datetime] = None
    seed: Optional[int] = None


class LessonEvaluationOutcome(BaseModel):
    """Outcome payload returned after evaluating a lesson submission."""
    model_config = ConfigDict(frozen=True)

    evaluation: SubmissionEvaluationResult
    remediation_card: Optional[RemediationCardPayload] = None
    next_step: Optional[LessonStepPresentation] = None
    automaticity_violation: bool = False
    fluency_drill_injected: bool = False
    repetition_result: Optional[RepetitionResult] = None
    unlocked_postrequisite_ids: List[str] = Field(default_factory=list)
    conditional_revocation: Optional[ConditionalRevocationReport] = None
    coaching_feedback: Optional[CoachingFeedback] = None


class ReviewQuestionItem(BaseModel):
    """Individual question within a micro-interleaved review session."""
    question_index: int
    topic_id: str
    kp_id: str
    kp_index: int
    problem_instance: BlueprintInstance
    worked_example_hidden: bool = True
    input_locked: bool = False
    reference_peeked: bool = False
    submitted_answer: Optional[str] = None
    is_correct: Optional[bool] = None
    duration_ms: Optional[int] = None


class ReviewSession(BaseModel):
    """State of an ongoing micro-interleaved review session."""
    session_id: str
    student_id: str
    topic_id: str
    task_type: Literal["SPACED_REVIEW", "TARGETED_REMEDIATION"] = "SPACED_REVIEW"
    blocked_dependent_topic_id: Optional[str] = None
    questions: List[ReviewQuestionItem]
    answer_sequence: List[bool] = Field(default_factory=list)
    is_complete: bool = False
    passed: Optional[bool] = None


class ReviewEvaluationResult(BaseModel):
    """Outcome emitted after concluding a review session."""
    model_config = ConfigDict(frozen=True)

    student_id: str
    topic_id: str
    task_type: str
    answer_sequence: List[bool]
    sequence_pattern: str
    passed: bool
    reference_peeked_count: int
    raw_delta: float
    xp_awarded: int
    quality_tier: QualityTier
    repetition_result: RepetitionResult
    peeled_back_to_lesson: bool = False
    unblocked_dependent_topic_id: Optional[str] = None


class FactFluencyDrillSession(BaseModel):
    """Rapid-fire atomic retrieval drill session for fluency facts."""
    drill_id: str
    student_id: str
    topic_id: str
    ceiling_ms: int = 3000
    items: List[ReviewQuestionItem]


class FactFluencyDrillResult(BaseModel):
    """Outcome emitted after evaluating a fact fluency drill."""
    model_config = ConfigDict(frozen=True)

    student_id: str
    topic_id: str
    ceiling_ms: int
    total_items: int
    accurate_count: int
    automatic_count: int
    max_consecutive_automatic: int
    passed: bool
    rolling_latency_ms: int
    xp_awarded: int


class QuizQuestionItem(BaseModel):
    """Question item within a timed interleaved quiz."""
    model_config = ConfigDict(frozen=True)

    question_index: int
    topic_id: str
    domain_cluster: str
    kp_id: str
    kp_index: int
    expected_accuracy: float
    time_limit_seconds: int
    requires_automaticity: bool = False
    problem_instance: BlueprintInstance


class TimedQuizSession(BaseModel):
    """Timed interleaved quiz session covering mastered topics."""
    model_config = ConfigDict(frozen=True)

    quiz_id: str
    student_id: str
    is_retake: bool = False
    original_quiz_id: Optional[str] = None
    questions: List[QuizQuestionItem]
    expected_mean_accuracy: float
    total_time_limit_seconds: int
    started_at: datetime


class QuizSubmissionItem(BaseModel):
    """Individual answer item in a timed quiz submission."""
    question_index: int
    topic_id: str
    kp_id: str
    student_answer: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    target_answer: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None


class QuizEvaluationResult(BaseModel):
    """Evaluation summary for a timed quiz."""
    model_config = ConfigDict(frozen=True)

    quiz_id: str
    student_id: str
    is_retake: bool
    total_questions: int
    correct_count: int
    score_pct: float
    expected_mean_accuracy: float
    passed_topic_ids: List[str]
    missed_topic_ids: List[str]
    queued_remedial_topic_ids: List[str]
    retake_required: bool
    retake_unlocked: bool
    xp_awarded: int
    repetition_results: Dict[str, RepetitionResult] = Field(default_factory=dict)


class QuizRetakeTracker(BaseModel):
    """Tracks required remedial reviews before a quiz retake can be unlocked."""
    original_quiz_id: str
    student_id: str
    missed_topic_ids: List[str]
    remediated_topic_ids: List[str] = Field(default_factory=list)
    retake_unlocked: bool = False
    retake_completed: bool = False


class SynthesisPartSpec(BaseModel):
    """Specification for a single sequential step of a multi-part synthesis problem."""
    model_config = ConfigDict(frozen=True)

    part_index: int
    component_topic_id: str
    component_kp_id: str
    prompt_stem: str
    problem_instance: BlueprintInstance
    weight: float = 1.0


class MultiPartSynthesisSession(BaseModel):
    """Active multi-part applied synthesis problem session."""
    session_id: str
    student_id: str
    title: str
    scenario_context: str
    parts: List[SynthesisPartSpec]
    current_part_index: int = 0
    part_answers: Dict[int, str] = Field(default_factory=dict)
    part_results: Dict[int, bool] = Field(default_factory=dict)
    completed: bool = False
    halted: bool = False
    failed_component_topic_id: Optional[str] = None


class SynthesisPartEvaluation(BaseModel):
    """Outcome emitted after evaluating a single step of a synthesis problem."""
    model_config = ConfigDict(frozen=True)

    session_id: str
    part_index: int
    component_topic_id: str
    component_kp_id: str
    correct: bool
    remediation_card: Optional[RemediationCardPayload] = None
    next_part_index: Optional[int] = None
    is_session_complete: bool = False
    halted_to_targeted_remediation: bool = False
    xp_awarded: int = 0


class TestPrepState(BaseModel):
    """Test-prep mode state configuration for a student."""
    student_id: str
    course_id: str
    enabled: bool = False
    course_promotion_paused: bool = True
    peak_freshness_memory_threshold: float = 0.85
    exam_xp_interval: int = 500
    last_exam_xp: int = 0


class PracticeExamProblemItem(BaseModel):
    """Question item within a cumulative practice exam."""
    model_config = ConfigDict(frozen=True)

    problem_index: int
    topic_id: str
    kp_id: str
    domain_cluster: str
    base_difficulty: float
    time_limit_seconds: int = 120
    problem_instance: BlueprintInstance


class PracticeExamSession(BaseModel):
    """Cumulative practice exam session."""
    model_config = ConfigDict(frozen=True)

    exam_id: str
    student_id: str
    course_id: str
    problems: List[PracticeExamProblemItem]
    is_sliced: bool = False
    slice_index: Optional[int] = None
    slice_total: Optional[int] = None
    created_at: datetime


class MissedExamProblemReattempt(BaseModel):
    """Tracks 1d -> 3d -> 7d spaced re-attempts for missed exam problems."""
    reattempt_id: str
    student_id: str
    exam_id: str
    topic_id: str
    kp_id: str
    problem_index: int
    target_answer: str
    schedule_days: List[int] = Field(default_factory=lambda: [1, 3, 7])
    stage_index: int = 0  # 0 -> 1d, 1 -> 3d, 2 -> 7d, 3 -> GRADUATED
    immediate_rework_completed: bool = False
    next_due_at: datetime
    graduated: bool = False
    last_attempt_at: Optional[datetime] = None


class PracticeExamEvaluationResult(BaseModel):
    """Outcome emitted after evaluating a practice exam."""
    model_config = ConfigDict(frozen=True)

    exam_id: str
    student_id: str
    score_pct: float
    total_problems: int
    correct_count: int
    missed_topic_ids: List[str]
    reattempts: List[MissedExamProblemReattempt]
    xp_awarded: int


# ===========================================================================
# Section 2: Practice Evaluator Engine Core Facade
# ===========================================================================

class PracticeEvaluator:
    """
    Tier 5 Practice Evaluator Engine.
    Executes lesson staircases, 3-tier targeted remediation routing,
    micro-interleaved reviews, automaticity sensors, and timed quizzes.
    Interacts with persistence exclusively through TrellisStoragePort.
    Zero raw queries or direct database access permitted.
    """

    def __init__(
        self,
        storage: TrellisStoragePort,
        topology_service: Optional[TopologyService] = None,
        blueprint_solver: Optional[ContentBlueprintSolver] = None,
        diagnostic_engine: Optional[DiagnosticEngine] = None,
        frontier_service: Optional[FrontierService] = None,
        hsrs_engine: Optional[HSRSFireEngine] = None,
        scheduler: Optional[SessionScheduler] = None,
        coach_service: Optional[InstructionCoachService] = None,
        gamification_engine: Optional[GamificationEfficiencyEngine] = None,
        config: PracticeConfig = PracticeConfig(),
    ) -> None:
        self.storage = storage
        self.topology = topology_service or TopologyService(storage)
        self.solver = blueprint_solver or ContentBlueprintSolver(storage)
        self.coach_service = coach_service or InstructionCoachService(storage, self.solver)
        self.diagnostic_engine = diagnostic_engine or DiagnosticEngine(storage, self.topology, self.solver)
        self.frontier_service = frontier_service or FrontierService(storage, self.topology)
        self.hsrs_engine = hsrs_engine or HSRSFireEngine(storage)
        self.scheduler = scheduler or SessionScheduler(
            storage, frontier_service=self.frontier_service, hsrs_engine=self.hsrs_engine
        )
        self.gamification_engine = gamification_engine or GamificationEfficiencyEngine(storage)
        self.config = config

        # In-memory tracking structures for ephemeral active sessions & trackers
        self._kp_attempt_counts: Dict[Tuple[str, str, int], int] = {}
        self._completed_cooldown_topics: Set[Tuple[str, str]] = set()
        self._blocked_by_key_prereq: Dict[Tuple[str, str], Set[str]] = {}
        self._slow_plateau_counts: Dict[Tuple[str, str], int] = {}
        self._retake_trackers: Dict[Tuple[str, str], QuizRetakeTracker] = {}
        self._last_quiz_xp: Dict[str, int] = {}
        self._active_problem_instances: Dict[Tuple[str, str], BlueprintInstance] = {}
        self._synthesis_sessions: Dict[str, MultiPartSynthesisSession] = {}
        self._synthesis_part_failures: Dict[Tuple[str, int], int] = {}
        self._test_prep_states: Dict[str, TestPrepState] = {}
        self._practice_exam_sessions: Dict[str, PracticeExamSession] = {}
        self._missed_reattempts: Dict[str, MissedExamProblemReattempt] = {}

    # -----------------------------------------------------------------------
    # FR-5.1: Hyper-Scaffolded Lesson Loop & N=3 Mastery Gate
    # -----------------------------------------------------------------------

    def start_lesson(
        self,
        student_id: str,
        topic_id: str,
        as_of: Optional[datetime] = None,
        diagnostic_accuracy: Optional[float] = None,
        seed: Optional[int] = None,
    ) -> LessonStepPresentation:
        """
        Initializes or resumes an unmastered lesson staircase on topic_id.
        Enforces prerequisite gating, active cooldown holds, and Tier-3 blocks.
        Applies Expertise Reversal bypass (>85% diagnostic accuracy).
        """
        curr_as_of = as_of or datetime.now(timezone.utc)
        node = self.storage.get_topic_node(topic_id)
        state = self.hsrs_engine.get_or_create_student_topic_state(student_id, topic_id)

        # 1. Prerequisite unlock verification
        if state.status == "LOCKED":
            if not self.frontier_service.is_topic_unlocked(student_id, topic_id, include_due_reviews=True):
                raise ValueError(f"Topic '{topic_id}' is LOCKED behind unmastered hard prerequisites.")

        # 2. Consolidation Cooldown check (Tier 2)
        if state.cooldown_until is not None:
            cd_dt = state.cooldown_until
            if cd_dt.tzinfo is None and curr_as_of.tzinfo is not None:
                cd_dt = cd_dt.replace(tzinfo=timezone.utc)
            elif cd_dt.tzinfo is not None and curr_as_of.tzinfo is None:
                curr_as_of = curr_as_of.replace(tzinfo=timezone.utc)

            if cd_dt > curr_as_of:
                raise ValueError(
                    f"Topic '{topic_id}' is in consolidation cooldown until {state.cooldown_until.isoformat()}."
                )
            else:
                state.cooldown_until = None
                self._completed_cooldown_topics.add((student_id, topic_id))

        # 3. Tier-3 Remediation block check
        if state.status == "TIER_3_REMEDIATION":
            kp_idx = min(state.current_kp_index, len(node.knowledge_points) - 1)
            kp = node.knowledge_points[kp_idx]
            req_id = kp.key_prerequisite_id or node.key_prerequisite_id
            if req_id:
                req_states = self.storage.get_student_topic_states(student_id, [req_id])
                req_st = req_states.get(req_id)
                if req_st and req_st.status in ("MASTERED", "MASTERED_FLOOR"):
                    state.status = "IN_PROGRESS"
                    state.kp_failure_counts[kp_idx] = 0
                    state.consecutive_correct = 0
                else:
                    raise ValueError(
                        f"Topic '{topic_id}' is halted in TIER_3_REMEDIATION awaiting '{req_id}'."
                    )
            else:
                state.status = "IN_PROGRESS"

        # Transition to IN_PROGRESS
        if state.status in ("AVAILABLE", "LOCKED"):
            state.status = "IN_PROGRESS"

        total_kps = len(node.knowledge_points)
        if state.current_kp_index >= total_kps:
            state.current_kp_index = 0
            state.consecutive_correct = 0

        self.storage.upsert_student_topic_state(state)

        return self.get_current_lesson_step(
            student_id=student_id,
            topic_id=topic_id,
            diagnostic_accuracy=diagnostic_accuracy,
            seed=seed,
        )

    def get_current_lesson_step(
        self,
        student_id: str,
        topic_id: str,
        diagnostic_accuracy: Optional[float] = None,
        seed: Optional[int] = None,
    ) -> LessonStepPresentation:
        """Constructs the presentation payload for the student's current KP step."""
        node = self.storage.get_topic_node(topic_id)
        state = self.hsrs_engine.get_or_create_student_topic_state(student_id, topic_id)
        total_kps = len(node.knowledge_points)
        kp_idx = min(state.current_kp_index, total_kps - 1)
        kp = node.knowledge_points[kp_idx]

        # Hydrate decoupled content shell
        shell = self.solver.get_content_shell(kp.kp_id)

        # Expertise reversal determination
        eff_diag_acc = diagnostic_accuracy
        if eff_diag_acc is None:
            if state.diagnostic_plus_minus_balance > 0.0:
                eff_diag_acc = DiagnosticEngine.balance_to_mastery_probability(
                    state.diagnostic_plus_minus_balance
                )
            else:
                eff_diag_acc = 0.0

        is_bypass = (
            eff_diag_acc > self.config.expertise_reversal_accuracy_threshold
            and state.kp_failure_counts.get(kp_idx, 0) == 0
        )
        show_instruction = (not is_bypass) and (state.consecutive_correct == 0)

        instruction_payload: Optional[InstructionCardPayload] = None
        if show_instruction:
            try:
                instruction_payload = self.coach_service.generate_instruction_card(
                    kp_id=kp.kp_id,
                    svg_asset_uri=shell.svg_asset_uri,
                )
            except Exception:
                instruction_payload = InstructionCardPayload(
                    topic_id=topic_id,
                    kp_id=kp.kp_id,
                    kp_index=kp.kp_index,
                    scaffolding_tier=kp.scaffolding_tier,
                    kp_focus=kp.kp_focus,
                    novel_chunk_count=kp.novel_chunk_count,
                    rule_statement=shell.rule_statement or kp.rule_statement,
                    worked_example=shell.worked_example or kp.canonical_worked_example,
                    svg_asset_uri=shell.svg_asset_uri,
                )

        eff_seed = seed
        if eff_seed is None:
            eff_seed = (state.current_kp_index + 1) * 1000 + state.consecutive_correct + 1

        variant = self.solver.generate_variant(kp.kp_id, seed=eff_seed)
        self._active_problem_instances[(student_id, topic_id)] = variant
        req_auto = bool(node.requires_automaticity or node.skill_type == "FLUENCY_FACT")
        ceil_ms = node.automaticity_Defining_latency_ms or self.config.automaticity_ceiling_ms

        return LessonStepPresentation(
            student_id=student_id,
            topic_id=topic_id,
            kp_id=kp.kp_id,
            kp_index=kp_idx,
            total_kps=total_kps,
            mastery_gate_n=node.mastery_gate_n,
            consecutive_correct=state.consecutive_correct,
            show_instruction_card=show_instruction,
            expertise_reversal_bypass=is_bypass,
            instruction_card=instruction_payload,
            problem_instance=variant,
            requires_automaticity=req_auto,
            automaticity_ceiling_ms=ceil_ms if req_auto else None,
        )

    def evaluate_lesson_submission(
        self,
        submission: PracticeSubmission,
    ) -> LessonEvaluationOutcome:
        """
        Evaluates a student's answer submission on a lesson KP.
        Enforces N=3 unassisted mastery gate, 3-tier remediation routing,
        automaticity constraints, and HSRS propagation upon completion.
        """
        curr_as_of = submission.as_of or datetime.now(timezone.utc)
        student_id = submission.student_id
        topic_id = submission.topic_id
        node = self.storage.get_topic_node(topic_id)
        state = self.hsrs_engine.get_or_create_student_topic_state(student_id, topic_id)

        kp_idx = min(state.current_kp_index, len(node.knowledge_points) - 1)
        kp = node.knowledge_points[kp_idx]
        shell = self.solver.get_content_shell(kp.kp_id)

        # 1. Mathematical equivalence grading
        target_ans = submission.target_answer
        params = dict(submission.parameters or {})
        if target_ans is None:
            active_inst = self._active_problem_instances.get((student_id, topic_id))
            if active_inst is not None:
                target_ans = active_inst.target_answer
                if not params:
                    params = dict(active_inst.parameters or {})
            elif shell.worked_example:
                target_ans = shell.worked_example.final_answer
            elif kp.canonical_worked_example:
                target_ans = kp.canonical_worked_example.final_answer
            else:
                target_ans = ""

        is_math_correct = self.solver.grade_student_answer(
            student_input=submission.student_answer,
            target_answer=target_ans,
            parameters=params,
        )

        # 2. Automaticity validation (FR-5.4)
        req_auto = bool(node.requires_automaticity or node.skill_type == "FLUENCY_FACT")
        ceiling_ms = node.automaticity_Defining_latency_ms or self.config.automaticity_ceiling_ms
        auto_violation = False
        fluency_injected = False

        if req_auto:
            is_auto, plat_triggered = self.evaluate_automaticity_latency(
                student_id=student_id,
                topic_id=topic_id,
                is_correct=is_math_correct,
                duration_ms=submission.duration_ms,
                as_of=curr_as_of,
            )
            if is_math_correct and not is_auto:
                auto_violation = True
            if plat_triggered:
                fluency_injected = True

        # Track attempt count for this specific KP
        attempt_key = (student_id, topic_id, kp_idx)
        kp_attempts = self._kp_attempt_counts.get(attempt_key, 0) + 1
        self._kp_attempt_counts[attempt_key] = kp_attempts

        # 3. Branching: Correct vs. Incorrect
        if is_math_correct:
            # Record telemetry attempt
            attempt = InteractionAttempt(
                attempt_id=f"att_{uuid.uuid4().hex[:12]}",
                student_id=student_id,
                topic_id=topic_id,
                kp_id=kp.kp_id,
                submitted_answer=submission.student_answer,
                is_correct=True,
                duration_ms=submission.duration_ms,
                reference_peeked=submission.reference_peeked,
                created_at=curr_as_of,
            )
            self.storage.record_attempt(attempt)
            self.hsrs_engine.record_attempt(student_id, topic_id, is_correct=True)

            feedback = self.coach_service.evaluate_coaching_feedback(
                student_id=student_id,
                topic_id=topic_id,
                is_correct=True,
                kp_index=kp_idx,
                duration_ms=submission.duration_ms,
                step_description=f"Completed {kp.kp_focus}",
                student_answer=submission.student_answer,
                target_answer=target_ans,
            )

            # Case A1: Unassisted & Automatic
            if (not submission.reference_peeked) and (not auto_violation):
                state.consecutive_correct += 1

                # Check KP completion gate
                if state.consecutive_correct >= node.mastery_gate_n:
                    state.consecutive_correct = 0
                    state.current_kp_index += 1
                    self._kp_attempt_counts[attempt_key] = 0

                    # Check Node Mastery
                    if state.current_kp_index >= len(node.knowledge_points):
                        state.status = "MASTERED"
                        self.storage.upsert_student_topic_state(state)

                        rep_res = self.hsrs_engine.process_explicit_repetition(
                            student_id=student_id,
                            topic_id=topic_id,
                            is_success=True,
                            raw_delta=1.0,
                            as_of=curr_as_of,
                        )

                        if self.config.enable_tier7_gamification:
                            total_fails = sum(state.kp_failure_counts.values())
                            q_tier, xp_award = self.gamification_engine.compute_lesson_quality(
                                total_kps=len(node.knowledge_points),
                                errors_across_kps=total_fails,
                                hints_used=1 if submission.reference_peeked else 0,
                                is_first_attempt=(total_fails == 0),
                                base_xp=node.estimated_xp,
                            )
                        else:
                            q_tier = "FULL_XP"
                            xp_award = node.estimated_xp

                        xp_entry = XPLedgerEntry(
                            entry_id=f"xp_{uuid.uuid4().hex[:12]}",
                            student_id=student_id,
                            topic_id=topic_id,
                            task_type="FRONTIER_LESSON",
                            quality_tier=q_tier,
                            delta_xp=xp_award,
                            reason="Mastered topic node staircase",
                            created_at=curr_as_of,
                        )
                        self.storage.append_xp_ledger(xp_entry)

                        try:
                            prof = self.storage.get_student_profile(student_id)
                            new_xp = max(0, prof.xp_total + xp_award)
                            self.storage.upsert_student_profile(prof.model_copy(update={"xp_total": new_xp}))
                        except KeyError:
                            pass

                        # Unblock any dependent topic that was halted in TIER_3_REMEDIATION awaiting topic_id
                        self._unblock_dependent_topics(student_id, topic_id, curr_as_of)

                        # Refresh knowledge frontier & sticky dashboard
                        unlocked_ids = self.frontier_service.refresh_frontier_states(student_id)
                        self.scheduler.refresh_dashboard(student_id, as_of=curr_as_of)

                        eval_res = SubmissionEvaluationResult(
                            correct=True,
                            consecutive_correct=0,
                            kp_completed=True,
                            node_mastered=True,
                            xp_awarded=xp_award,
                            quality_tier=q_tier,
                            tier_remediation=None,
                            remedial_target_topic_id=None,
                            next_action="SERVE_NEXT_TASK",
                        )
                        return LessonEvaluationOutcome(
                            evaluation=eval_res,
                            remediation_card=None,
                            next_step=None,
                            automaticity_violation=False,
                            fluency_drill_injected=fluency_injected,
                            repetition_result=rep_res,
                            unlocked_postrequisite_ids=unlocked_ids,
                            conditional_revocation=None,
                            coaching_feedback=feedback,
                        )

                    else:
                        # Advance to next KP in staircase
                        self.storage.upsert_student_topic_state(state)
                        eval_res = SubmissionEvaluationResult(
                            correct=True,
                            consecutive_correct=0,
                            kp_completed=True,
                            node_mastered=False,
                            xp_awarded=1,
                            quality_tier="FULL_XP",
                            tier_remediation=None,
                            remedial_target_topic_id=None,
                            next_action="SERVE_NEXT_STEP",
                        )
                        next_step = self.get_current_lesson_step(
                            student_id=student_id,
                            topic_id=topic_id,
                            seed=submission.seed + 1 if submission.seed is not None else None,
                        )
                        return LessonEvaluationOutcome(
                            evaluation=eval_res,
                            remediation_card=None,
                            next_step=next_step,
                            automaticity_violation=False,
                            fluency_drill_injected=fluency_injected,
                            coaching_feedback=feedback,
                        )

                else:
                    # Check attempt budget exhaustion within current KP
                    if kp_attempts >= self.config.kp_attempt_budget:
                        return self._trigger_tier2_cooldown(
                            student_id=student_id,
                            topic_id=topic_id,
                            state=state,
                            kp_idx=kp_idx,
                            as_of=curr_as_of,
                            fluency_injected=fluency_injected,
                            coaching_feedback=feedback,
                        )

                    # Consecutive correct incremented, still on current KP
                    self.storage.upsert_student_topic_state(state)
                    eval_res = SubmissionEvaluationResult(
                        correct=True,
                        consecutive_correct=state.consecutive_correct,
                        kp_completed=False,
                        node_mastered=False,
                        xp_awarded=1,
                        quality_tier="FULL_XP",
                        tier_remediation=None,
                        remedial_target_topic_id=None,
                        next_action="SERVE_NEXT_STEP",
                    )
                    next_step = self.get_current_lesson_step(
                        student_id=student_id,
                        topic_id=topic_id,
                        seed=submission.seed + 1 if submission.seed is not None else None,
                    )
                    return LessonEvaluationOutcome(
                        evaluation=eval_res,
                        remediation_card=None,
                        next_step=next_step,
                        automaticity_violation=False,
                        fluency_drill_injected=fluency_injected,
                        coaching_feedback=feedback,
                    )

            else:
                # Case A2: Assisted or Overtime
                if kp_attempts >= self.config.kp_attempt_budget:
                    return self._trigger_tier2_cooldown(
                        student_id=student_id,
                        topic_id=topic_id,
                        state=state,
                        kp_idx=kp_idx,
                        as_of=curr_as_of,
                        fluency_injected=fluency_injected,
                        coaching_feedback=feedback,
                    )

                if auto_violation:
                    state.consecutive_correct = 0
                self.storage.upsert_student_topic_state(state)

                eval_res = SubmissionEvaluationResult(
                    correct=not auto_violation,
                    consecutive_correct=state.consecutive_correct,
                    kp_completed=False,
                    node_mastered=False,
                    xp_awarded=0,
                    quality_tier="ZERO_XP",
                    tier_remediation=None,
                    remedial_target_topic_id=None,
                    next_action="SERVE_NEXT_STEP",
                )
                next_step = self.get_current_lesson_step(
                    student_id=student_id,
                    topic_id=topic_id,
                    seed=submission.seed + 1 if submission.seed is not None else None,
                )
                return LessonEvaluationOutcome(
                    evaluation=eval_res,
                    remediation_card=None,
                    next_step=next_step,
                    automaticity_violation=auto_violation,
                    fluency_drill_injected=fluency_injected,
                    coaching_feedback=feedback,
                )

        else:
            # ---------------------------------------------------------------
            # FR-5.2: 3-Tier Targeted Remediation Routing (Incorrect Answer)
            # ---------------------------------------------------------------
            attempt = InteractionAttempt(
                attempt_id=f"att_{uuid.uuid4().hex[:12]}",
                student_id=student_id,
                topic_id=topic_id,
                kp_id=kp.kp_id,
                submitted_answer=submission.student_answer,
                is_correct=False,
                duration_ms=submission.duration_ms,
                reference_peeked=submission.reference_peeked,
                created_at=curr_as_of,
            )
            self.storage.record_attempt(attempt)
            self.hsrs_engine.record_attempt(student_id, topic_id, is_correct=False)

            feedback = self.coach_service.evaluate_coaching_feedback(
                student_id=student_id,
                topic_id=topic_id,
                is_correct=False,
                kp_index=kp_idx,
                duration_ms=submission.duration_ms,
                step_description=f"Missed {kp.kp_focus}",
                student_answer=submission.student_answer,
                target_answer=target_ans,
            )

            state.consecutive_correct = 0
            fail_count = state.kp_failure_counts.get(kp_idx, 0) + 1
            state.kp_failure_counts[kp_idx] = fail_count

            # Tier 7 Anti-Cheat: Detect rapid random guessing / blow-offs (< 20% expected duration)
            if self.config.enable_tier7_gamification:
                min_exp = self.gamification_engine.compute_min_expected_duration(
                    skill_type=node.skill_type,
                    word_count=len(kp.kp_focus.split()),
                )
                if self.gamification_engine.detect_blow_off(submission.duration_ms, min_exp, is_correct=False):
                    pen_entry, alert = self.gamification_engine.apply_anti_hacker_penalty(
                        student_id=student_id,
                        topic_id=topic_id,
                        task_type="FRONTIER_LESSON",
                        duration_ms=submission.duration_ms,
                        min_expected_ms=min_exp,
                        as_of=curr_as_of,
                    )
                    eval_res = SubmissionEvaluationResult(
                        correct=False,
                        consecutive_correct=0,
                        kp_completed=False,
                        node_mastered=False,
                        xp_awarded=pen_entry.delta_xp,
                        quality_tier="NEGATIVE_PENALTY",
                        tier_remediation=2,
                        remedial_target_topic_id=None,
                        next_action="CONSOLIDATION_COOLDOWN",
                    )
                    return LessonEvaluationOutcome(
                        evaluation=eval_res,
                        remediation_card=None,
                        next_step=None,
                        automaticity_violation=False,
                        fluency_drill_injected=False,
                        coaching_feedback=feedback,
                    )
                else:
                    self.gamification_engine.reset_consecutive_blowoffs(student_id)

            key_prereq_id = kp.key_prerequisite_id or node.key_prerequisite_id
            cond_rev: Optional[ConditionalRevocationReport] = None

            # Tier 3 Trigger: 2nd error at identical KP_j
            has_prior_cooldown = (student_id, topic_id) in self._completed_cooldown_topics
            trigger_tier3 = (
                fail_count >= 2
                and key_prereq_id is not None
                and (not self.config.require_tier2_before_tier3 or has_prior_cooldown)
            )

            if trigger_tier3:
                state.status = "TIER_3_REMEDIATION"
                state.cooldown_until = None
                self.storage.upsert_student_topic_state(state)

                # Record key prerequisite blockage
                self._blocked_by_key_prereq.setdefault((student_id, key_prereq_id), set()).add(topic_id)

                # Check conditional completion revocation
                if state.is_conditionally_completed:
                    cond_rev = self.diagnostic_engine.revoke_conditional_completion(student_id, topic_id)

                # Inject sticky TARGETED_REMEDIATION task onto dashboard
                rem_item = DashboardTaskItem(
                    student_id=student_id,
                    topic_id=key_prereq_id,
                    task_type="TARGETED_REMEDIATION",
                    rank_score=100.0,
                    is_sticky=True,
                    assigned_at=curr_as_of,
                )
                curr_menu = self.storage.get_dashboard_menu(student_id)
                menu_dict = {it.topic_id: it for it in curr_menu if it.topic_id != topic_id}
                menu_dict[key_prereq_id] = rem_item
                self.storage.save_dashboard_menu(student_id, list(menu_dict.values()))
                self.scheduler.refresh_dashboard(student_id, as_of=curr_as_of)

                eval_res = SubmissionEvaluationResult(
                    correct=False,
                    consecutive_correct=0,
                    kp_completed=False,
                    node_mastered=False,
                    xp_awarded=0,
                    quality_tier="ZERO_XP",
                    tier_remediation=3,
                    remedial_target_topic_id=key_prereq_id,
                    next_action="HALT_TO_KEY_PREREQUISITE",
                )
                return LessonEvaluationOutcome(
                    evaluation=eval_res,
                    remediation_card=None,
                    next_step=None,
                    automaticity_violation=False,
                    fluency_drill_injected=fluency_injected,
                    conditional_revocation=cond_rev,
                    coaching_feedback=feedback,
                )

            # Tier 2 Trigger: Attempt budget exhaustion or cooldown required before Tier 3
            trigger_tier2 = (
                kp_attempts >= self.config.kp_attempt_budget
                or (fail_count >= 2 and (key_prereq_id is None or self.config.require_tier2_before_tier3))
            )

            if trigger_tier2:
                return self._trigger_tier2_cooldown(
                    student_id=student_id,
                    topic_id=topic_id,
                    state=state,
                    kp_idx=kp_idx,
                    as_of=curr_as_of,
                    fluency_injected=fluency_injected,
                    coaching_feedback=feedback,
                )

            # Tier 1 Trigger: 1st error at identical KP_j
            self.storage.upsert_student_topic_state(state)

            # FR-6.2: Precision non-Socratic remediation card (sub-5ms misconception fast path or solver/LLM fallback)
            active_inst = self._active_problem_instances.get((student_id, topic_id))
            prob_stem = active_inst.rendered_stem if active_inst else (shell.problem_blueprint.stem_template if shell.problem_blueprint else kp.kp_focus)

            rem_card = self.coach_service.generate_tier1_remediation(
                topic_id=topic_id,
                kp_id=kp.kp_id,
                student_answer=submission.student_answer,
                target_answer=target_ans,
                problem_stem=prob_stem,
                parameters=submission.parameters,
            )

            # Serve fresh variant with new seed
            new_seed = (submission.seed or 101) + 101 + kp_attempts
            next_step = self.get_current_lesson_step(
                student_id=student_id,
                topic_id=topic_id,
                seed=new_seed,
            )

            eval_res = SubmissionEvaluationResult(
                correct=False,
                consecutive_correct=0,
                kp_completed=False,
                node_mastered=False,
                xp_awarded=0,
                quality_tier="ZERO_XP",
                tier_remediation=1,
                remedial_target_topic_id=None,
                next_action="TIER_1_REMEDIATION",
            )
            return LessonEvaluationOutcome(
                evaluation=eval_res,
                remediation_card=rem_card,
                next_step=next_step,
                automaticity_violation=False,
                fluency_drill_injected=fluency_injected,
                coaching_feedback=feedback,
            )

    def _trigger_tier2_cooldown(
        self,
        student_id: str,
        topic_id: str,
        state: StudentTopicState,
        kp_idx: int,
        as_of: datetime,
        fluency_injected: bool = False,
        coaching_feedback: Optional[CoachingFeedback] = None,
    ) -> LessonEvaluationOutcome:
        """Halts a lesson attempt and enforces a Tier-2 consolidation cooldown."""
        cooldown_end = as_of + timedelta(minutes=self.config.tier2_cooldown_minutes)
        state.cooldown_until = cooldown_end
        state.consecutive_correct = 0
        self._kp_attempt_counts[(student_id, topic_id, kp_idx)] = 0
        self.storage.upsert_student_topic_state(state)

        # Evict lesson from active dashboard menu during cooldown
        self.scheduler.refresh_dashboard(student_id, as_of=as_of)

        eval_res = SubmissionEvaluationResult(
            correct=False,
            consecutive_correct=0,
            kp_completed=False,
            node_mastered=False,
            xp_awarded=0,
            quality_tier="ZERO_XP",
            tier_remediation=2,
            remedial_target_topic_id=None,
            next_action="CONSOLIDATION_COOLDOWN",
        )
        return LessonEvaluationOutcome(
            evaluation=eval_res,
            remediation_card=None,
            next_step=None,
            automaticity_violation=False,
            fluency_drill_injected=fluency_injected,
            coaching_feedback=coaching_feedback,
        )

    def _unblock_dependent_topics(self, student_id: str, prereq_topic_id: str, as_of: datetime) -> None:
        """Unblocks any lesson halted in TIER_3_REMEDIATION awaiting prereq_topic_id."""
        blocked_set = self._blocked_by_key_prereq.get((student_id, prereq_topic_id), set())
        for dep_id in list(blocked_set):
            dep_states = self.storage.get_student_topic_states(student_id, [dep_id])
            st = dep_states.get(dep_id)
            if st and st.status == "TIER_3_REMEDIATION":
                st.status = "IN_PROGRESS"
                st.consecutive_correct = 0
                st.kp_failure_counts[st.current_kp_index] = 0
                self.storage.upsert_student_topic_state(st)
        self._blocked_by_key_prereq.pop((student_id, prereq_topic_id), None)

    # -----------------------------------------------------------------------
    # FR-5.3: Micro-Interleaved Spaced Review Loop & Spotter Reference Mode
    # -----------------------------------------------------------------------

    def start_review_session(
        self,
        student_id: str,
        topic_id: str,
        task_type: Literal["SPACED_REVIEW", "TARGETED_REMEDIATION"] = "SPACED_REVIEW",
        blocked_dependent_topic_id: Optional[str] = None,
        seed: Optional[int] = None,
    ) -> ReviewSession:
        """
        Initializes a micro-interleaved spaced review session.
        Generates 5 questions cycling across KPs with worked examples hidden.
        """
        node = self.storage.get_topic_node(topic_id)
        session_id = f"rev_{uuid.uuid4().hex[:12]}"
        questions: List[ReviewQuestionItem] = []

        total_kps = len(node.knowledge_points)
        base_seed = seed or 42

        for i in range(self.config.review_max_questions):
            kp = node.knowledge_points[i % total_kps]
            variant = self.solver.generate_variant(kp.kp_id, seed=base_seed + (i + 1) * 17)
            questions.append(
                ReviewQuestionItem(
                    question_index=i,
                    topic_id=topic_id,
                    kp_id=kp.kp_id,
                    kp_index=kp.kp_index,
                    problem_instance=variant,
                    worked_example_hidden=True,
                    input_locked=False,
                    reference_peeked=False,
                )
            )

        return ReviewSession(
            session_id=session_id,
            student_id=student_id,
            topic_id=topic_id,
            task_type=task_type,
            blocked_dependent_topic_id=blocked_dependent_topic_id,
            questions=questions,
            answer_sequence=[],
            is_complete=False,
            passed=None,
        )

    def open_spotter_reference(
        self,
        session: ReviewSession,
        question_index: int,
        input_locked: bool,
    ) -> InstructionCardPayload:
        """
        Enables read-only Spotter Reference Mode for an individual review question.
        Requires problem input to be locked before viewing reference material.
        """
        if not input_locked:
            raise ValueError(
                "Spotter reference mode requires problem input to be locked/closed before viewing reference material."
            )

        if question_index < 0 or question_index >= len(session.questions):
            raise IndexError("Invalid question index for review session.")

        q = session.questions[question_index]
        q.input_locked = True
        q.reference_peeked = True

        node = self.storage.get_topic_node(session.topic_id)
        kp = next((k for k in node.knowledge_points if k.kp_id == q.kp_id), node.knowledge_points[0])
        shell = self.solver.get_content_shell(kp.kp_id)

        return InstructionCardPayload(
            topic_id=session.topic_id,
            kp_id=kp.kp_id,
            kp_index=kp.kp_index,
            scaffolding_tier=kp.scaffolding_tier,
            kp_focus=kp.kp_focus,
            novel_chunk_count=kp.novel_chunk_count,
            rule_statement=shell.rule_statement or kp.rule_statement,
            worked_example=shell.worked_example or kp.canonical_worked_example,
            svg_asset_uri=shell.svg_asset_uri,
        )

    @staticmethod
    def evaluate_review_sequence(seq: List[bool]) -> Tuple[bool, Optional[bool]]:
        """
        Deterministic review sequence grading evaluator (FR-5.3 / MAW):
          - Clean 3-for-3 (✔✔✔) -> PASS (True, True)
          - 3 errors (✖✖✖) -> FAIL (True, False)
          - Length 4: ✔✔✔ with 2-consecutive finish (✖✔✔✔, ✔✖✔✔) -> PASS (True, True)
          - Length 5: >=3 correct AND ends in at least 2 consecutive correct -> PASS
          - Otherwise continues or fails.
        """
        length = len(seq)
        correct_count = sum(1 for x in seq if x)
        fail_count = length - correct_count

        if fail_count >= 3:
            return (True, False)

        if length < 3:
            return (False, None)

        if length == 3:
            if correct_count == 3:
                return (True, True)
            return (False, None)

        if length == 4:
            if correct_count == 3 and seq[-2:] == [True, True]:
                return (True, True)
            return (False, None)

        # Length >= 5
        passed = (correct_count >= 3) and (seq[-2:] == [True, True])
        return (True, passed)

    def submit_review_answer(
        self,
        session: ReviewSession,
        question_index: int,
        student_answer: str,
        duration_ms: int = 1000,
        as_of: Optional[datetime] = None,
    ) -> Tuple[ReviewSession, Optional[ReviewEvaluationResult]]:
        """
        Submits an answer to a review question and checks sequence completion.
        If review concludes, applies HSRS repetition (with Spotter discount if peeked)
        or triggers prerequisite lesson peel-back if remedial review failed.
        """
        if session.is_complete:
            raise ValueError("Review session is already complete.")

        if question_index < 0 or question_index >= len(session.questions):
            raise IndexError("Question index out of bounds.")

        curr_as_of = as_of or datetime.now(timezone.utc)
        q = session.questions[question_index]
        q.submitted_answer = student_answer
        q.duration_ms = duration_ms

        # Grade equivalence
        is_correct = self.solver.grade_student_answer(
            student_input=student_answer,
            target_answer=q.problem_instance.target_answer,
            parameters=q.problem_instance.parameters,
        )
        q.is_correct = is_correct
        session.answer_sequence.append(is_correct)

        # Record attempt
        attempt = InteractionAttempt(
            attempt_id=f"rev_att_{uuid.uuid4().hex[:12]}",
            student_id=session.student_id,
            topic_id=session.topic_id,
            kp_id=q.kp_id,
            submitted_answer=student_answer,
            is_correct=is_correct,
            duration_ms=duration_ms,
            reference_peeked=q.reference_peeked,
            created_at=curr_as_of,
        )
        self.storage.record_attempt(attempt)
        self.hsrs_engine.record_attempt(session.student_id, session.topic_id, is_correct=is_correct)

        # Check sequence completion
        is_done, passed = self.evaluate_review_sequence(session.answer_sequence)

        if not is_done:
            return session, None

        # Conclude review session
        session.is_complete = True
        session.passed = bool(passed)

        peeked_count = sum(1 for qu in session.questions if qu.reference_peeked)
        pattern_str = "".join("✔" if b else "✖" for b in session.answer_sequence)

        peeled_back = False
        unblocked_dep: Optional[str] = None

        if session.passed:
            # Positive repetition with Spotter rawDelta discount
            raw_delta = 1.0
            if peeked_count > 0:
                raw_delta = round(1.0 * (self.config.spotter_raw_delta_discount ** peeked_count), 4)

            rep_res = self.hsrs_engine.process_explicit_repetition(
                student_id=session.student_id,
                topic_id=session.topic_id,
                is_success=True,
                raw_delta=raw_delta,
                as_of=curr_as_of,
            )

            xp_awarded = 3
            q_tier: QualityTier = "FULL_XP" if peeked_count == 0 else "MOST_XP"

            xp_entry = XPLedgerEntry(
                entry_id=f"xp_{uuid.uuid4().hex[:12]}",
                student_id=session.student_id,
                topic_id=session.topic_id,
                task_type=session.task_type,
                quality_tier=q_tier,
                delta_xp=xp_awarded,
                reason=f"Passed review ({pattern_str})",
                created_at=curr_as_of,
            )
            self.storage.append_xp_ledger(xp_entry)

            # Targeted remediation resolution
            if session.task_type == "TARGETED_REMEDIATION":
                # Remove TARGETED_REMEDIATION task from dashboard menu
                menu = self.storage.get_dashboard_menu(session.student_id)
                new_menu = [it for it in menu if it.topic_id != session.topic_id]
                self.storage.save_dashboard_menu(session.student_id, new_menu)

                # Unblock dependent lesson
                unblocked_dep = session.blocked_dependent_topic_id
                if unblocked_dep:
                    dep_states = self.storage.get_student_topic_states(session.student_id, [unblocked_dep])
                    st = dep_states.get(unblocked_dep)
                    if st and st.status == "TIER_3_REMEDIATION":
                        st.status = "IN_PROGRESS"
                        st.consecutive_correct = 0
                        st.kp_failure_counts[st.current_kp_index] = 0
                        self.storage.upsert_student_topic_state(st)

                self._unblock_dependent_topics(session.student_id, session.topic_id, curr_as_of)

                # Update quiz retake tracker if applicable
                self._mark_retake_topic_remediated(session.student_id, session.topic_id)

            self.scheduler.refresh_dashboard(session.student_id, as_of=curr_as_of)

        else:
            # Failed review repetition: upward penalty trickle-up
            rep_res = self.hsrs_engine.process_explicit_repetition(
                student_id=session.student_id,
                topic_id=session.topic_id,
                is_success=False,
                raw_delta=-1.0,
                as_of=curr_as_of,
            )
            xp_awarded = 0
            q_tier = "ZERO_XP"

            # Check conditional revocation
            self.diagnostic_engine.revoke_conditional_completion(session.student_id, session.topic_id)

            # If a TARGETED_REMEDIATION review is failed, peel back to full lesson!
            if session.task_type == "TARGETED_REMEDIATION":
                peeled_back = True
                states = self.storage.get_student_topic_states(session.student_id, [session.topic_id])
                p_state = states.get(session.topic_id)
                if p_state:
                    p_state.status = "AVAILABLE"
                    p_state.current_kp_index = 0
                    p_state.consecutive_correct = 0
                    p_state.kp_failure_counts = {}
                    self.storage.upsert_student_topic_state(p_state)

                # Replace dashboard item with high-priority FRONTIER_LESSON
                menu = self.storage.get_dashboard_menu(session.student_id)
                new_menu = [it for it in menu if it.topic_id != session.topic_id]
                new_menu.append(
                    DashboardTaskItem(
                        student_id=session.student_id,
                        topic_id=session.topic_id,
                        task_type="FRONTIER_LESSON",
                        rank_score=95.0,
                        is_sticky=True,
                        assigned_at=curr_as_of,
                    )
                )
                self.storage.save_dashboard_menu(session.student_id, new_menu)

            self.scheduler.refresh_dashboard(session.student_id, as_of=curr_as_of)

        outcome = ReviewEvaluationResult(
            student_id=session.student_id,
            topic_id=session.topic_id,
            task_type=session.task_type,
            answer_sequence=session.answer_sequence,
            sequence_pattern=pattern_str,
            passed=bool(session.passed),
            reference_peeked_count=peeked_count,
            raw_delta=raw_delta if session.passed else -1.0,
            xp_awarded=xp_awarded,
            quality_tier=q_tier,
            repetition_result=rep_res,
            peeled_back_to_lesson=peeled_back,
            unblocked_dependent_topic_id=unblocked_dep,
        )
        return session, outcome

    def _mark_retake_topic_remediated(self, student_id: str, topic_id: str) -> None:
        """Marks topic_id remediated across any active QuizRetakeTracker."""
        for (sid, qid), tracker in list(self._retake_trackers.items()):
            if sid == student_id and not tracker.retake_completed:
                if topic_id in tracker.missed_topic_ids and topic_id not in tracker.remediated_topic_ids:
                    tracker.remediated_topic_ids.append(topic_id)
                    if set(tracker.missed_topic_ids).issubset(set(tracker.remediated_topic_ids)):
                        tracker.retake_unlocked = True

    # -----------------------------------------------------------------------
    # FR-5.4: Automaticity Latency Sensor (<= 3.0s) & Fluency Interventions
    # -----------------------------------------------------------------------

    def evaluate_automaticity_latency(
        self,
        student_id: str,
        topic_id: str,
        is_correct: bool,
        duration_ms: int,
        as_of: Optional[datetime] = None,
    ) -> Tuple[bool, bool]:
        """
        Sensors retrieval latency against <= 3000 ms ceiling on FLUENCY_FACT nodes.
        Returns: (is_automatic: bool, fluency_intervention_triggered: bool)
        """
        curr_as_of = as_of or datetime.now(timezone.utc)
        node = self.storage.get_topic_node(topic_id)
        ceiling_ms = node.automaticity_Defining_latency_ms or self.config.automaticity_ceiling_ms

        is_automatic = bool(is_correct and duration_ms <= ceiling_ms)
        plat_key = (student_id, topic_id)

        # Update StudentProfile rolling latency
        try:
            profile = self.storage.get_student_profile(student_id)
            prior_lat = profile.rolling_latency_ms.get(topic_id, duration_ms)
            ewma_lat = int(round(0.70 * prior_lat + 0.30 * duration_ms))
            profile.rolling_latency_ms[topic_id] = ewma_lat

            prior_facts = profile.rolling_latency_ms.get("math_facts", duration_ms)
            profile.rolling_latency_ms["math_facts"] = int(round(0.80 * prior_facts + 0.20 * duration_ms))
            self.storage.upsert_student_profile(profile)
        except KeyError:
            pass

        intervention_triggered = False

        if is_correct and not is_automatic:
            # Slow accurate response: recalculation / finger-counting streak increment
            curr_streak = self._slow_plateau_counts.get(plat_key, 0) + 1
            self._slow_plateau_counts[plat_key] = curr_streak

            if curr_streak >= self.config.automaticity_plateau_window:
                intervention_triggered = True
                # Inject FACT_FLUENCY task onto dashboard
                menu = self.storage.get_dashboard_menu(student_id)
                menu_dict = {it.topic_id: it for it in menu}
                menu_dict[topic_id] = DashboardTaskItem(
                    student_id=student_id,
                    topic_id=topic_id,
                    task_type="FACT_FLUENCY",
                    rank_score=90.0,
                    is_sticky=True,
                    assigned_at=curr_as_of,
                )
                self.storage.save_dashboard_menu(student_id, list(menu_dict.values()))
                self.scheduler.refresh_dashboard(student_id, as_of=curr_as_of)
        elif is_automatic:
            self._slow_plateau_counts[plat_key] = 0

        return is_automatic, intervention_triggered

    def build_fluency_drill(
        self,
        student_id: str,
        topic_id: str,
        seed: Optional[int] = None,
    ) -> FactFluencyDrillSession:
        """Constructs a 5-item rapid-fire atomic fact fluency drill session."""
        node = self.storage.get_topic_node(topic_id)
        drill_id = f"drill_{uuid.uuid4().hex[:12]}"
        ceiling_ms = node.automaticity_Defining_latency_ms or self.config.automaticity_ceiling_ms

        base_seed = seed or 555
        items: List[ReviewQuestionItem] = []

        total_kps = len(node.knowledge_points)
        for i in range(self.config.fluency_drill_item_count):
            kp = node.knowledge_points[i % total_kps]
            variant = self.solver.generate_variant(kp.kp_id, seed=base_seed + (i + 1) * 31)
            items.append(
                ReviewQuestionItem(
                    question_index=i,
                    topic_id=topic_id,
                    kp_id=kp.kp_id,
                    kp_index=kp.kp_index,
                    problem_instance=variant,
                    worked_example_hidden=True,
                    input_locked=False,
                    reference_peeked=False,
                )
            )

        return FactFluencyDrillSession(
            drill_id=drill_id,
            student_id=student_id,
            topic_id=topic_id,
            ceiling_ms=ceiling_ms,
            items=items,
        )

    def evaluate_fluency_drill(
        self,
        student_id: str,
        drill: FactFluencyDrillSession,
        submissions: List[Tuple[str, int]],  # List of (student_answer, duration_ms)
        as_of: Optional[datetime] = None,
    ) -> FactFluencyDrillResult:
        """
        Grades a fact fluency drill. Requires >= 80% accuracy within <= 3000 ms ceiling
        and max consecutive automatic >= 3 to pass.
        """
        curr_as_of = as_of or datetime.now(timezone.utc)
        total_items = len(drill.items)
        accurate_count = 0
        automatic_count = 0
        consec_auto = 0
        max_consec_auto = 0

        for idx, (ans, dur_ms) in enumerate(submissions):
            if idx >= total_items:
                break
            q = drill.items[idx]
            is_correct = self.solver.grade_student_answer(
                student_input=ans,
                target_answer=q.problem_instance.target_answer,
                parameters=q.problem_instance.parameters,
            )
            is_auto = is_correct and (dur_ms <= drill.ceiling_ms)

            if is_correct:
                accurate_count += 1
            if is_auto:
                automatic_count += 1
                consec_auto += 1
                max_consec_auto = max(max_consec_auto, consec_auto)
            else:
                consec_auto = 0

            # Record attempt
            att = InteractionAttempt(
                attempt_id=f"fl_att_{uuid.uuid4().hex[:12]}",
                student_id=student_id,
                topic_id=drill.topic_id,
                kp_id=q.kp_id,
                submitted_answer=ans,
                is_correct=is_correct,
                duration_ms=dur_ms,
                reference_peeked=False,
                created_at=curr_as_of,
            )
            self.storage.record_attempt(att)

        node = self.storage.get_topic_node(drill.topic_id)
        passed = bool(
            (max_consec_auto >= node.mastery_gate_n)
            and (automatic_count / max(1, total_items) >= 0.80)
        )

        xp_awarded = 2 if passed else 0

        if passed:
            self._slow_plateau_counts[(student_id, drill.topic_id)] = 0
            # Remove FACT_FLUENCY task from dashboard menu
            menu = self.storage.get_dashboard_menu(student_id)
            new_menu = [it for it in menu if not (it.topic_id == drill.topic_id and it.task_type == "FACT_FLUENCY")]
            self.storage.save_dashboard_menu(student_id, new_menu)
            self.scheduler.refresh_dashboard(student_id, as_of=curr_as_of)

            if xp_awarded > 0:
                xp_entry = XPLedgerEntry(
                    entry_id=f"xp_{uuid.uuid4().hex[:12]}",
                    student_id=student_id,
                    topic_id=drill.topic_id,
                    task_type="FACT_FLUENCY",
                    quality_tier="FULL_XP",
                    delta_xp=xp_awarded,
                    reason="Passed fact fluency drill",
                    created_at=curr_as_of,
                )
                self.storage.append_xp_ledger(xp_entry)

        try:
            prof = self.storage.get_student_profile(student_id)
            r_lat = prof.rolling_latency_ms.get(drill.topic_id, drill.ceiling_ms)
        except KeyError:
            r_lat = drill.ceiling_ms

        return FactFluencyDrillResult(
            student_id=student_id,
            topic_id=drill.topic_id,
            ceiling_ms=drill.ceiling_ms,
            total_items=total_items,
            accurate_count=accurate_count,
            automatic_count=automatic_count,
            max_consecutive_automatic=max_consec_auto,
            passed=passed,
            rolling_latency_ms=r_lat,
            xp_awarded=xp_awarded,
        )

    # -----------------------------------------------------------------------
    # FR-5.5: Timed Interleaved Quizzes (80-85% Calibration), Remediation & Retakes
    # -----------------------------------------------------------------------

    def is_quiz_due(self, student_id: str) -> bool:
        """Determines if a timed quiz is due based on cumulative XP since last quiz."""
        try:
            prof = self.storage.get_student_profile(student_id)
            curr_xp = prof.xp_total
        except KeyError:
            return False

        last_quiz = self._last_quiz_xp.get(student_id, prof.rolling_latency_ms.get("__last_quiz_xp__", 0))
        xp_delta = curr_xp - last_quiz

        if xp_delta < self.config.quiz_xp_trigger_threshold:
            return False

        # Ensure student has at least 1 mastered or reviewable topic
        states = self.storage.get_student_topic_states(student_id)
        mastered_count = sum(
            1 for s in states.values() if s.status in ("MASTERED", "MASTERED_FLOOR", "DUE_FOR_REVIEW")
        )
        return mastered_count > 0

    def check_and_schedule_timed_quiz(
        self,
        student_id: str,
        as_of: Optional[datetime] = None,
        seed: Optional[int] = None,
    ) -> Optional[TimedQuizSession]:
        """Checks quiz XP threshold and schedules a calibrated quiz if due."""
        if not self.is_quiz_due(student_id):
            return None

        curr_as_of = as_of or datetime.now(timezone.utc)
        quiz = self.build_timed_quiz(student_id, as_of=curr_as_of, seed=seed)

        # Inject sticky TIMED_QUIZ task onto dashboard using the primary quiz topic
        primary_tid = quiz.questions[0].topic_id
        menu = self.storage.get_dashboard_menu(student_id)
        menu_dict = {it.topic_id: it for it in menu}
        menu_dict[primary_tid] = DashboardTaskItem(
            student_id=student_id,
            topic_id=primary_tid,
            task_type="TIMED_QUIZ",
            rank_score=98.0,
            is_sticky=True,
            assigned_at=curr_as_of,
        )
        self.storage.save_dashboard_menu(student_id, list(menu_dict.values()))
        self.scheduler.refresh_dashboard(student_id, as_of=curr_as_of)

        return quiz

    def build_timed_quiz(
        self,
        student_id: str,
        question_count: Optional[int] = None,
        as_of: Optional[datetime] = None,
        seed: Optional[int] = None,
        is_retake: bool = False,
        retake_topic_ids: Optional[List[str]] = None,
        original_quiz_id: Optional[str] = None,
    ) -> TimedQuizSession:
        """
        Builds a timed interleaved quiz calibrated to 80-85% expected score.
        Interleaves across distinct domain clusters to prevent blocked practice.
        """
        curr_as_of = as_of or datetime.now(timezone.utc)
        target_q_count = question_count or self.config.quiz_question_count
        base_seed = seed or 777

        states = self.storage.get_student_topic_states(student_id)
        eligible_tids: List[str] = []

        if is_retake and retake_topic_ids:
            eligible_tids = list(retake_topic_ids)
        else:
            for tid, st in states.items():
                if st.status in ("MASTERED", "MASTERED_FLOOR", "DUE_FOR_REVIEW"):
                    eligible_tids.append(tid)

        if not eligible_tids:
            raise ValueError(f"Student '{student_id}' has no mastered topics for quiz generation.")

        # Compute expected recall probabilities for all eligible topics
        cand_probs: Dict[str, float] = {}
        topic_nodes: Dict[str, TopicNode] = {}

        for tid in eligible_tids:
            try:
                node = self.storage.get_topic_node(tid)
            except KeyError:
                continue
            topic_nodes[tid] = node
            st = states.get(tid)
            mem = st.memory_strength if st else 0.5
            sp = st.speed if st else 1.0
            diff = node.base_difficulty

            # Deterministic recall probability calibration
            p_hat = 0.55 + 0.25 * mem + 0.10 * min(2.0, sp) - 0.15 * diff
            p_hat = max(0.35, min(0.98, round(p_hat, 4)))
            cand_probs[tid] = p_hat

        target_acc = (self.config.quiz_target_accuracy_min + self.config.quiz_target_accuracy_max) / 2.0  # 0.825

        # Interleaved greedy selection balancing cluster variety and 80-85% target
        selected_tids: List[str] = []
        occupied_clusters: Set[str] = set()

        sorted_cands = sorted(
            cand_probs.keys(),
            key=lambda tid: (abs(cand_probs[tid] - target_acc), tid),
        )

        for tid in sorted_cands:
            if len(selected_tids) >= target_q_count:
                break
            cluster = topic_nodes[tid].domain_cluster
            if cluster not in occupied_clusters:
                selected_tids.append(tid)
                occupied_clusters.add(cluster)

        # Backfill if not enough distinct clusters
        if len(selected_tids) < target_q_count:
            for tid in sorted_cands:
                if len(selected_tids) >= target_q_count:
                    break
                if tid not in selected_tids:
                    selected_tids.append(tid)

        quiz_id = f"quiz_{uuid.uuid4().hex[:12]}"
        questions: List[QuizQuestionItem] = []
        total_time_limit_sec = 0

        for idx, tid in enumerate(selected_tids):
            node = topic_nodes[tid]
            highest_kp = max(node.knowledge_points, key=lambda k: k.kp_index)
            variant = self.solver.generate_variant(highest_kp.kp_id, seed=base_seed + (idx + 1) * 43)

            req_auto = bool(node.requires_automaticity or node.skill_type == "FLUENCY_FACT")
            time_limit = 3 if req_auto else self.config.quiz_default_question_time_seconds
            total_time_limit_sec += time_limit

            questions.append(
                QuizQuestionItem(
                    question_index=idx,
                    topic_id=tid,
                    domain_cluster=node.domain_cluster,
                    kp_id=highest_kp.kp_id,
                    kp_index=highest_kp.kp_index,
                    expected_accuracy=cand_probs[tid],
                    time_limit_seconds=time_limit,
                    requires_automaticity=req_auto,
                    problem_instance=variant,
                )
            )

        mean_expected_acc = round(sum(q.expected_accuracy for q in questions) / max(1, len(questions)), 4)

        return TimedQuizSession(
            quiz_id=quiz_id,
            student_id=student_id,
            is_retake=is_retake,
            original_quiz_id=original_quiz_id,
            questions=questions,
            expected_mean_accuracy=mean_expected_acc,
            total_time_limit_seconds=total_time_limit_sec,
            started_at=curr_as_of,
        )

    def evaluate_timed_quiz(
        self,
        quiz: TimedQuizSession,
        submissions: List[QuizSubmissionItem],
        as_of: Optional[datetime] = None,
    ) -> QuizEvaluationResult:
        """
        Evaluates a completed timed quiz.
        Correct answers award XP and HSRS FIRe credit.
        Missed answers trigger upward penalty flow and queue mandatory Post-Quiz Remedial Reviews.
        """
        curr_as_of = as_of or datetime.now(timezone.utc)
        student_id = quiz.student_id

        total_questions = len(quiz.questions)
        sub_by_idx = {s.question_index: s for s in submissions}

        correct_count = 0
        passed_tids: List[str] = []
        missed_tids: List[str] = []
        rep_results: Dict[str, RepetitionResult] = {}

        for idx, q in enumerate(quiz.questions):
            sub = sub_by_idx.get(idx)
            is_correct = False

            if sub is not None and not sub.timed_out:
                dur_ms = sub.duration_ms
                time_limit_ms = q.time_limit_seconds * 1000

                if dur_ms <= time_limit_ms:
                    is_correct = self.solver.grade_student_answer(
                        student_input=sub.student_answer,
                        target_answer=sub.target_answer or q.problem_instance.target_answer,
                        parameters=sub.parameters or q.problem_instance.parameters,
                    )

            # Record attempt
            att = InteractionAttempt(
                attempt_id=f"qz_att_{uuid.uuid4().hex[:12]}",
                student_id=student_id,
                topic_id=q.topic_id,
                kp_id=q.kp_id,
                submitted_answer=sub.student_answer if sub else "TIMED_OUT",
                is_correct=is_correct,
                duration_ms=sub.duration_ms if sub else q.time_limit_seconds * 1000,
                reference_peeked=False,
                created_at=curr_as_of,
            )
            self.storage.record_attempt(att)
            self.hsrs_engine.record_attempt(student_id, q.topic_id, is_correct=is_correct)

            if is_correct:
                correct_count += 1
                passed_tids.append(q.topic_id)
                # Correct quiz verification: process repetition on target node
                rep_res = self.hsrs_engine.process_explicit_repetition(
                    student_id=student_id,
                    topic_id=q.topic_id,
                    is_success=True,
                    raw_delta=1.0,
                    as_of=curr_as_of,
                )
                rep_results[q.topic_id] = rep_res
            else:
                missed_tids.append(q.topic_id)
                # Missed quiz item: upward penalty trickle-up
                rep_res = self.hsrs_engine.process_explicit_repetition(
                    student_id=student_id,
                    topic_id=q.topic_id,
                    is_success=False,
                    raw_delta=-1.0,
                    as_of=curr_as_of,
                )
                rep_results[q.topic_id] = rep_res

        score_pct = round((correct_count / max(1, total_questions)) * 100.0, 2)
        xp_awarded = correct_count * 1  # 1 XP per verified quiz item

        if xp_awarded > 0:
            xp_entry = XPLedgerEntry(
                entry_id=f"xp_{uuid.uuid4().hex[:12]}",
                student_id=student_id,
                topic_id=quiz.questions[0].topic_id,
                task_type="TIMED_QUIZ",
                quality_tier="FULL_XP",
                delta_xp=xp_awarded,
                reason=f"Completed timed quiz ({correct_count}/{total_questions})",
                created_at=curr_as_of,
            )
            self.storage.append_xp_ledger(xp_entry)

        # Update last quiz XP
        try:
            prof = self.storage.get_student_profile(student_id)
            new_xp = max(0, prof.xp_total + xp_awarded)
            prof.rolling_latency_ms["__last_quiz_xp__"] = new_xp
            self.storage.upsert_student_profile(prof.model_copy(update={"xp_total": new_xp}))
            self._last_quiz_xp[student_id] = new_xp
        except KeyError:
            pass

        # Remove the TIMED_QUIZ task from the dashboard menu
        menu = self.storage.get_dashboard_menu(student_id)
        new_menu = [it for it in menu if it.task_type != "TIMED_QUIZ"]

        # Inject mandatory Post-Quiz TARGETED_REMEDIATION reviews for missed topics
        queued_remedial_tids: List[str] = []
        for tid in missed_tids:
            queued_remedial_tids.append(tid)
            new_menu.append(
                DashboardTaskItem(
                    student_id=student_id,
                    topic_id=tid,
                    task_type="TARGETED_REMEDIATION",
                    rank_score=95.0,
                    is_sticky=True,
                    assigned_at=curr_as_of,
                )
            )

        self.storage.save_dashboard_menu(student_id, new_menu)
        self.scheduler.refresh_dashboard(student_id, as_of=curr_as_of)

        retake_req = len(missed_tids) > 0
        retake_unl = False

        if retake_req:
            tracker = QuizRetakeTracker(
                original_quiz_id=quiz.quiz_id,
                student_id=student_id,
                missed_topic_ids=missed_tids,
                remediated_topic_ids=[],
                retake_unlocked=False,
            )
            self._retake_trackers[(student_id, quiz.quiz_id)] = tracker

        return QuizEvaluationResult(
            quiz_id=quiz.quiz_id,
            student_id=student_id,
            is_retake=quiz.is_retake,
            total_questions=total_questions,
            correct_count=correct_count,
            score_pct=score_pct,
            expected_mean_accuracy=quiz.expected_mean_accuracy,
            passed_topic_ids=passed_tids,
            missed_topic_ids=missed_tids,
            queued_remedial_topic_ids=queued_remedial_tids,
            retake_required=retake_req,
            retake_unlocked=retake_unl,
            xp_awarded=xp_awarded,
            repetition_results=rep_results,
        )

    def is_retake_unlocked(self, student_id: str, original_quiz_id: str) -> bool:
        """Checks if a quiz retake is unlocked (all missed topics remediated)."""
        tracker = self._retake_trackers.get((student_id, original_quiz_id))
        if not tracker:
            return False
        return tracker.retake_unlocked

    def build_quiz_retake(
        self,
        student_id: str,
        original_quiz_id: str,
        as_of: Optional[datetime] = None,
        seed: Optional[int] = None,
    ) -> TimedQuizSession:
        """
        Builds a Retake Quiz covering previously missed topics with fresh parameters.
        Raises ValueError if any missed topic has not completed its post-quiz remedial review.
        """
        tracker = self._retake_trackers.get((student_id, original_quiz_id))
        if not tracker:
            raise ValueError(f"No active retake tracker for quiz '{original_quiz_id}'.")

        if not tracker.retake_unlocked:
            unremediated = set(tracker.missed_topic_ids) - set(tracker.remediated_topic_ids)
            raise ValueError(
                f"Quiz retake is locked. Pending post-quiz remedial reviews for: {sorted(unremediated)}."
            )

        curr_as_of = as_of or datetime.now(timezone.utc)
        retake_quiz = self.build_timed_quiz(
            student_id=student_id,
            as_of=curr_as_of,
            seed=(seed or 888) + 123,
            is_retake=True,
            retake_topic_ids=tracker.missed_topic_ids,
            original_quiz_id=original_quiz_id,
        )
        tracker.retake_completed = True
        return retake_quiz

    # -----------------------------------------------------------------------
    # FR-5.6: Multi-Part Applied Synthesis Problems & Cumulative Test-Prep Mode
    # -----------------------------------------------------------------------

    def start_synthesis_session(
        self,
        student_id: str,
        title: str,
        scenario_context: str,
        component_topic_ids: List[str],
        part_stems: Optional[List[str]] = None,
        as_of: Optional[datetime] = None,
        seed: Optional[int] = None,
    ) -> MultiPartSynthesisSession:
        """
        Initializes an applied multi-part synthesis problem decomposed into sequential parts.
        Verifies that each component topic is mastered or in progress.
        """
        if not component_topic_ids:
            raise ValueError("Synthesis problem requires at least one component topic ID.")

        curr_as_of = as_of or datetime.now(timezone.utc)
        parts: List[SynthesisPartSpec] = []
        eff_seed = seed or 42

        for idx, tid in enumerate(component_topic_ids):
            node = self.storage.get_topic_node(tid)
            kp = node.knowledge_points[0]  # primary representative KP
            variant = self.solver.generate_variant(kp.kp_id, seed=eff_seed + (idx * 31))
            stem = (
                part_stems[idx]
                if part_stems and idx < len(part_stems)
                else f"Part {idx + 1}: Apply concept from '{node.title}' to the scenario."
            )
            part = SynthesisPartSpec(
                part_index=idx,
                component_topic_id=tid,
                component_kp_id=kp.kp_id,
                prompt_stem=stem,
                problem_instance=variant,
                weight=1.0,
            )
            parts.append(part)

        sess_id = f"synth_{uuid.uuid4().hex[:12]}"
        session = MultiPartSynthesisSession(
            session_id=sess_id,
            student_id=student_id,
            title=title,
            scenario_context=scenario_context,
            parts=parts,
            current_part_index=0,
        )
        self._synthesis_sessions[sess_id] = session
        return session

    def get_current_synthesis_part(self, session_id: str) -> Optional[SynthesisPartSpec]:
        """Returns the currently active sequential part of a synthesis problem."""
        sess = self._synthesis_sessions.get(session_id)
        if not sess or sess.completed or sess.halted:
            return None
        if sess.current_part_index >= len(sess.parts):
            return None
        return sess.parts[sess.current_part_index]

    def evaluate_synthesis_part(
        self,
        session_id: str,
        student_answer: str,
        duration_ms: int = 2000,
        as_of: Optional[datetime] = None,
    ) -> SynthesisPartEvaluation:
        """
        Evaluates a single part in a multi-part synthesis problem with component topic attribution.
        1st error on part: emits non-Socratic Tier-1 remediation and fresh variant.
        2nd error on part: halts problem, penalizes component topic in HSRS, and injects TARGETED_REMEDIATION.
        """
        curr_as_of = as_of or datetime.now(timezone.utc)
        session = self._synthesis_sessions.get(session_id)
        if not session:
            raise ValueError(f"Synthesis session '{session_id}' not found.")
        if session.completed:
            raise ValueError("Synthesis session is already completed.")
        if session.halted:
            raise ValueError("Synthesis session was halted due to prerequisite failure.")

        part_idx = session.current_part_index
        part = session.parts[part_idx]
        student_id = session.student_id

        # Mathematical equivalence grading against part blueprint instance
        is_correct = self.solver.grade_student_answer(
            student_input=student_answer,
            target_answer=part.problem_instance.target_answer,
            parameters=part.problem_instance.parameters,
        )

        session.part_answers[part_idx] = student_answer

        if is_correct:
            session.part_results[part_idx] = True
            xp_part = 2
            profile = self.storage.get_student_profile(student_id)
            if profile:
                profile.xp_total += xp_part
                self.storage.upsert_student_profile(profile)

            # Record interaction attempt on component KP
            attempt = InteractionAttempt(
                attempt_id=f"att_synth_{uuid.uuid4().hex[:10]}",
                student_id=student_id,
                topic_id=part.component_topic_id,
                kp_id=part.component_kp_id,
                submitted_answer=student_answer,
                is_correct=True,
                duration_ms=duration_ms,
                answered_at=curr_as_of,
                quality_tier="FULL_XP",
            )
            self.storage.record_attempt(attempt)

            # Check if all parts completed
            if part_idx + 1 < len(session.parts):
                session.current_part_index += 1
                return SynthesisPartEvaluation(
                    session_id=session_id,
                    part_index=part_idx,
                    component_topic_id=part.component_topic_id,
                    component_kp_id=part.component_kp_id,
                    correct=True,
                    next_part_index=session.current_part_index,
                    is_session_complete=False,
                    halted_to_targeted_remediation=False,
                    xp_awarded=xp_part,
                )
            else:
                session.completed = True
                bonus_xp = 5
                if profile:
                    profile.xp_total += bonus_xp
                    self.storage.upsert_student_profile(profile)

                # Process HSRS repetition credit across all component topics
                for p in session.parts:
                    self.hsrs_engine.process_explicit_repetition(
                        student_id=student_id,
                        topic_id=p.component_topic_id,
                        is_success=True,
                        raw_delta=1.0,
                        as_of=curr_as_of,
                    )

                return SynthesisPartEvaluation(
                    session_id=session_id,
                    part_index=part_idx,
                    component_topic_id=part.component_topic_id,
                    component_kp_id=part.component_kp_id,
                    correct=True,
                    next_part_index=None,
                    is_session_complete=True,
                    halted_to_targeted_remediation=False,
                    xp_awarded=xp_part + bonus_xp,
                )
        else:
            # Failure on this part
            fail_key = (session_id, part_idx)
            fails = self._synthesis_part_failures.get(fail_key, 0) + 1
            self._synthesis_part_failures[fail_key] = fails
            session.part_results[part_idx] = False

            attempt = InteractionAttempt(
                attempt_id=f"att_synth_{uuid.uuid4().hex[:10]}",
                student_id=student_id,
                topic_id=part.component_topic_id,
                kp_id=part.component_kp_id,
                submitted_answer=student_answer,
                is_correct=False,
                duration_ms=duration_ms,
                answered_at=curr_as_of,
                quality_tier="ZERO_XP",
            )
            self.storage.record_attempt(attempt)

            shell = self.solver.get_content_shell(part.component_kp_id)
            node = self.storage.get_topic_node(part.component_topic_id)
            kp = node.knowledge_points[0]

            if fails >= 2:
                # 2nd error: Halt synthesis session and route to Tier 3 targeted remediation
                session.halted = True
                session.failed_component_topic_id = part.component_topic_id

                # Downward HSRS penalty on the specific component topic
                self.hsrs_engine.record_attempt(student_id, part.component_topic_id, is_correct=False)
                self.hsrs_engine.process_explicit_repetition(
                    student_id=student_id,
                    topic_id=part.component_topic_id,
                    is_success=False,
                    raw_delta=-1.0,
                    as_of=curr_as_of,
                )

                # Inject sticky targeted remediation task
                remedial_task = DashboardTaskItem(
                    task_id=f"tsk_synth_rem_{uuid.uuid4().hex[:8]}",
                    student_id=student_id,
                    topic_id=part.component_topic_id,
                    task_type="TARGETED_REMEDIATION",
                    rank_score=95.0,
                    is_sticky=True,
                    assigned_at=curr_as_of,
                )
                menu = self.storage.get_dashboard_menu(student_id)
                menu = [t for t in menu if t.topic_id != part.component_topic_id or t.task_type != "TARGETED_REMEDIATION"]
                menu.insert(0, remedial_task)
                self.storage.save_dashboard_menu(student_id, menu)

                return SynthesisPartEvaluation(
                    session_id=session_id,
                    part_index=part_idx,
                    component_topic_id=part.component_topic_id,
                    component_kp_id=part.component_kp_id,
                    correct=False,
                    remediation_card=None,
                    next_part_index=None,
                    is_session_complete=False,
                    halted_to_targeted_remediation=True,
                    xp_awarded=0,
                )
            else:
                # 1st error: Tier 1 Remediation for this component KP
                card = RemediationCardPayload(
                    topic_id=part.component_topic_id,
                    kp_id=part.component_kp_id,
                    error_tag="ERR_SYNTHESIS_STEP",
                    violated_rule=shell.rule_statement or kp.rule_statement or f"Follow concept rule for {node.title}.",
                    identified_misstep=f"Error in step applying '{node.title}'. Check arithmetic and concept rules.",
                    corrected_steps=[
                        f"1. Apply rule: {shell.rule_statement or kp.rule_statement}",
                        f"2. Boxed target answer: {part.problem_instance.target_answer}",
                    ],
                    boxed_answer=part.problem_instance.target_answer,
                )
                return SynthesisPartEvaluation(
                    session_id=session_id,
                    part_index=part_idx,
                    component_topic_id=part.component_topic_id,
                    component_kp_id=part.component_kp_id,
                    correct=False,
                    remediation_card=card,
                    next_part_index=part_idx,
                    is_session_complete=False,
                    halted_to_targeted_remediation=False,
                    xp_awarded=0,
                )

    def set_test_prep_mode(
        self,
        student_id: str,
        enabled: bool,
        course_id: str,
        as_of: Optional[datetime] = None,
    ) -> TestPrepState:
        """
        Enables or disables Test-Prep Mode for a student.
        When enabled, pauses course promotion / new frontier topic introduction
        and triggers peak freshness sweeps and cumulative practice exams.
        """
        profile = self.storage.get_student_profile(student_id)
        last_xp = profile.xp_total if profile else 0

        existing = self._test_prep_states.get(student_id)
        if existing:
            state = TestPrepState(
                student_id=student_id,
                course_id=course_id,
                enabled=enabled,
                course_promotion_paused=enabled,
                peak_freshness_memory_threshold=0.85,
                exam_xp_interval=500,
                last_exam_xp=existing.last_exam_xp,
            )
        else:
            state = TestPrepState(
                student_id=student_id,
                course_id=course_id,
                enabled=enabled,
                course_promotion_paused=enabled,
                peak_freshness_memory_threshold=0.85,
                exam_xp_interval=500,
                last_exam_xp=last_xp,
            )
        self._test_prep_states[student_id] = state
        return state

    def is_course_promotion_paused(self, student_id: str) -> bool:
        """Returns True if student is currently in Test-Prep Mode with course promotion paused."""
        st = self._test_prep_states.get(student_id)
        if not st:
            return False
        return bool(st.enabled and st.course_promotion_paused)

    def sweep_test_prep_peak_freshness(
        self,
        student_id: str,
        as_of: Optional[datetime] = None,
    ) -> List[str]:
        """
        While in Test-Prep Mode, scans all course topics mastered by student.
        Computes memory retrievability M(t). If M(t) <= 0.85, marks topic DUE_FOR_REVIEW
        to guarantee peak memory freshness across the entire course.
        """
        st = self._test_prep_states.get(student_id)
        if not st or not st.enabled:
            return []

        curr_as_of = as_of or datetime.now(timezone.utc)
        subgraph = self.storage.get_course_subgraph(st.course_id)
        node_ids = [node.id for node in subgraph]
        state_map = self.storage.get_student_topic_states(student_id, node_ids)

        refreshed_tids: List[str] = []
        for node in subgraph:
            s = state_map.get(node.id)
            if not s or s.status not in ("MASTERED", "MASTERED_FLOOR", "DUE_FOR_REVIEW"):
                continue

            interval = s.stability_interval_days or 1.0
            last_review = s.last_reviewed_at or s.first_mastered_at or curr_as_of
            if last_review.tzinfo is None and curr_as_of.tzinfo is not None:
                last_review = last_review.replace(tzinfo=timezone.utc)
            elif last_review.tzinfo is not None and curr_as_of.tzinfo is None:
                curr_as_of = curr_as_of.replace(tzinfo=timezone.utc)

            days_elapsed = max(0.0, (curr_as_of - last_review).total_seconds() / 86400.0)
            retrievability = 2.0 ** (-days_elapsed / max(0.1, interval))

            if retrievability <= st.peak_freshness_memory_threshold:
                if s.status != "DUE_FOR_REVIEW":
                    s.status = "DUE_FOR_REVIEW"
                    self.storage.upsert_student_topic_state(s)
                refreshed_tids.append(node.id)

        return refreshed_tids

    def is_practice_exam_due(self, student_id: str) -> bool:
        """Checks if a cumulative practice exam is due in Test-Prep Mode (every 500 XP)."""
        st = self._test_prep_states.get(student_id)
        if not st or not st.enabled:
            return False
        profile = self.storage.get_student_profile(student_id)
        if not profile:
            return False
        xp_delta = profile.xp_total - st.last_exam_xp
        return xp_delta >= st.exam_xp_interval

    def build_practice_exam(
        self,
        student_id: str,
        total_problems: int = 20,
        slice_modulus: int = 1,
        slice_remainder: int = 0,
        as_of: Optional[datetime] = None,
        seed: Optional[int] = None,
    ) -> PracticeExamSession:
        """
        Builds a cumulative practice exam covering mastered course topics.
        Orders topics from lowest base_difficulty to highest base_difficulty (easy-to-hard).
        Supports longitudinal slicing (e.g. slice_modulus=4, slice_remainder=0..3).
        """
        curr_as_of = as_of or datetime.now(timezone.utc)
        st = self._test_prep_states.get(student_id)
        course_id = st.course_id if st else "CRS_ALG1"

        subgraph = self.storage.get_course_subgraph(course_id)
        node_ids = [node.id for node in subgraph]
        state_map = self.storage.get_student_topic_states(student_id, node_ids)
        mastered_ids = {
            s.topic_id for s in state_map.values() if s.status in ("MASTERED", "MASTERED_FLOOR", "DUE_FOR_REVIEW")
        }

        candidate_nodes = [node for node in subgraph if node.id in mastered_ids]
        if not candidate_nodes:
            candidate_nodes = list(subgraph)

        # Sort easy-to-hard by base_difficulty
        candidate_nodes.sort(key=lambda n: (n.base_difficulty, n.id))

        # Longitudinal slicing
        sliced_nodes: List[TopicNode] = []
        for idx, n in enumerate(candidate_nodes):
            if (idx % slice_modulus) == slice_remainder:
                sliced_nodes.append(n)

        exam_nodes: List[TopicNode] = []
        eff_seed = seed or 777
        if sliced_nodes:
            while len(exam_nodes) < total_problems and len(exam_nodes) < len(sliced_nodes) * 4:
                exam_nodes.extend(sliced_nodes)
            exam_nodes = exam_nodes[:total_problems]

        problems: List[PracticeExamProblemItem] = []
        for p_idx, node in enumerate(exam_nodes):
            kp = node.knowledge_points[p_idx % len(node.knowledge_points)]
            variant = self.solver.generate_variant(kp.kp_id, seed=eff_seed + (p_idx * 43))
            problem = PracticeExamProblemItem(
                problem_index=p_idx,
                topic_id=node.id,
                kp_id=kp.kp_id,
                domain_cluster=node.domain_cluster,
                base_difficulty=node.base_difficulty,
                time_limit_seconds=120,
                problem_instance=variant,
            )
            problems.append(problem)

        exam_id = f"exam_{uuid.uuid4().hex[:12]}"
        session = PracticeExamSession(
            exam_id=exam_id,
            student_id=student_id,
            course_id=course_id,
            problems=problems,
            is_sliced=(slice_modulus > 1),
            slice_index=slice_remainder if slice_modulus > 1 else None,
            slice_total=slice_modulus if slice_modulus > 1 else None,
            created_at=curr_as_of,
        )
        self._practice_exam_sessions[exam_id] = session
        return session

    def evaluate_practice_exam(
        self,
        student_id: str,
        exam_id: str,
        submissions: List[QuizSubmissionItem],
        low_confidence_indices: Optional[List[int]] = None,
        as_of: Optional[datetime] = None,
    ) -> PracticeExamEvaluationResult:
        """
        Evaluates cumulative practice exam submissions.
        Creates MissedExamProblemReattempt (1d -> 3d -> 7d) for any problem missed
        OR tagged in low_confidence_indices.
        """
        curr_as_of = as_of or datetime.now(timezone.utc)
        exam = self._practice_exam_sessions.get(exam_id)
        if not exam:
            raise ValueError(f"Practice exam '{exam_id}' not found.")

        sub_by_idx = {s.question_index: s for s in submissions}
        low_conf = set(low_confidence_indices or [])

        correct_count = 0
        missed_tids: List[str] = []
        reattempts: List[MissedExamProblemReattempt] = []

        for p in exam.problems:
            sub = sub_by_idx.get(p.problem_index)
            is_correct = False
            if sub and not sub.timed_out and sub.duration_ms <= p.time_limit_seconds * 1000:
                is_correct = self.solver.grade_student_answer(
                    student_input=sub.student_answer,
                    target_answer=p.problem_instance.target_answer,
                    parameters=p.problem_instance.parameters,
                )

            # Treated as missed if incorrect OR low confidence
            if is_correct and (p.problem_index not in low_conf):
                correct_count += 1
            else:
                if p.topic_id not in missed_tids:
                    missed_tids.append(p.topic_id)

                reatt_id = f"reatt_{uuid.uuid4().hex[:12]}"
                reatt = MissedExamProblemReattempt(
                    reattempt_id=reatt_id,
                    student_id=student_id,
                    exam_id=exam_id,
                    topic_id=p.topic_id,
                    kp_id=p.kp_id,
                    problem_index=p.problem_index,
                    target_answer=p.problem_instance.target_answer,
                    schedule_days=[1, 3, 7],
                    stage_index=0,  # 1 day
                    immediate_rework_completed=False,
                    next_due_at=curr_as_of + timedelta(days=1),
                    graduated=False,
                )
                self._missed_reattempts[reatt_id] = reatt
                reattempts.append(reatt)

        total = len(exam.problems)
        score_pct = round((correct_count / max(1, total)) * 100.0, 1)

        # Update last_exam_xp in TestPrepState
        st = self._test_prep_states.get(student_id)
        profile = self.storage.get_student_profile(student_id)
        if st and profile:
            st.last_exam_xp = profile.xp_total

        # Award exam completion XP
        xp_awarded = int(correct_count * 2)
        if profile:
            profile.xp_total += xp_awarded
            self.storage.upsert_student_profile(profile)

        return PracticeExamEvaluationResult(
            exam_id=exam_id,
            student_id=student_id,
            score_pct=score_pct,
            total_problems=total,
            correct_count=correct_count,
            missed_topic_ids=missed_tids,
            reattempts=reattempts,
            xp_awarded=xp_awarded,
        )

    def complete_immediate_exam_rework(
        self,
        student_id: str,
        reattempt_id: str,
        student_answer: str,
    ) -> bool:
        """
        Completes the immediate rework phase for a missed exam problem by studying the solution.
        """
        reatt = self._missed_reattempts.get(reattempt_id)
        if not reatt:
            raise ValueError(f"Reattempt '{reattempt_id}' not found.")
        if reatt.student_id != student_id:
            raise ValueError("Student ID mismatch for reattempt.")

        is_correct = self.solver.grade_student_answer(
            student_input=student_answer,
            target_answer=reatt.target_answer,
        )
        if is_correct:
            reatt.immediate_rework_completed = True
            return True
        return False

    def evaluate_exam_spaced_reattempt(
        self,
        student_id: str,
        reattempt_id: str,
        student_answer: str,
        as_of: Optional[datetime] = None,
    ) -> MissedExamProblemReattempt:
        """
        Evaluates a spaced re-attempt (1d -> 3d -> 7d) for a missed exam problem.
        If curr_as_of < next_due_at, raises ValueError.
        If correct: advances stage_index (0 -> 1d completed, next 3d; 1 -> 3d completed, next 7d; 2 -> 7d completed, graduated=True).
        If incorrect: resets stage_index = 0, immediate_rework_completed = False, next_due_at = +1 day (restarts 1-3-7 from Day 1).
        """
        curr_as_of = as_of or datetime.now(timezone.utc)
        reatt = self._missed_reattempts.get(reattempt_id)
        if not reatt:
            raise ValueError(f"Reattempt '{reattempt_id}' not found.")
        if reatt.student_id != student_id:
            raise ValueError("Student ID mismatch for reattempt.")
        if reatt.graduated:
            raise ValueError("Problem has already graduated from spaced re-attempts.")

        # Check due time
        due = reatt.next_due_at
        if due.tzinfo is None and curr_as_of.tzinfo is not None:
            due = due.replace(tzinfo=timezone.utc)
        elif due.tzinfo is not None and curr_as_of.tzinfo is None:
            curr_as_of = curr_as_of.replace(tzinfo=timezone.utc)

        if curr_as_of < due:
            raise ValueError(f"Reattempt '{reattempt_id}' is not yet due (due at {due.isoformat()}).")

        is_correct = self.solver.grade_student_answer(
            student_input=student_answer,
            target_answer=reatt.target_answer,
        )

        reatt.last_attempt_at = curr_as_of

        if is_correct:
            next_stage = reatt.stage_index + 1
            if next_stage == 1:
                # 1d passed -> advance to 3d interval
                reatt.stage_index = 1
                reatt.next_due_at = curr_as_of + timedelta(days=3)
            elif next_stage == 2:
                # 3d passed -> advance to 7d interval
                reatt.stage_index = 2
                reatt.next_due_at = curr_as_of + timedelta(days=7)
            else:
                # 7d passed -> Graduated!
                reatt.stage_index = 3
                reatt.graduated = True
        else:
            # Failed at 1d, 3d, or 7d:
            # Reset stage_index = 0, immediate_rework_completed = False, restart from Day 1 (+1 day)
            reatt.stage_index = 0
            reatt.immediate_rework_completed = False
            reatt.next_due_at = curr_as_of + timedelta(days=1)

        return reatt
