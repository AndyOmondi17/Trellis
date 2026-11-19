"""
src/trellis/domain/models.py
Pydantic v2 domain models for Trellis K-16 Mathematics Platform.
Strictly encapsulates all curriculum DAG topologies, content shells,
student profiles, mastery state machines, and transactional telemetry.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# 1. Course Catalog & Structure
# ---------------------------------------------------------------------------

class CourseCatalogItem(BaseModel):
    """Represents a discrete course boundary across the K-16 continuum."""
    model_config = ConfigDict(frozen=True)

    course_id: str = Field(..., pattern=r"^CRS_[A-Z0-9_]+$", description="Canonical course ID, e.g. CRS_ALG1")
    course_code: str = Field(..., description="Short course code, e.g. alg, fnd, ca1")
    title: str = Field(..., description="Human-readable course title")
    grade_band: str = Field(..., description="Target grade/level band, e.g. K-2, 8-9, 13-14")
    benchmark_xp: int = Field(default=1500, ge=500, description="Expected total XP to achieve 100% mastery")
    mastery_floor_course_ids: List[str] = Field(default_factory=list, description="Lower courses credited automatically upon enrollment")


# ---------------------------------------------------------------------------
# 2. Knowledge Points, Blueprints & Misconceptions
# ---------------------------------------------------------------------------

class CanonicalWorkedExampleStep(BaseModel):
    """Subgoal-labeled step within a worked example (Catrambone, 1995)."""
    model_config = ConfigDict(frozen=True)

    subgoal_label: str = Field(..., description="Cognitive purpose of this procedural step")
    operation: str = Field(..., description="Mathematical or symbolic operation executed")
    justification: str = Field(..., description="Mathematical rationale or theorem applied")


class CanonicalWorkedExample(BaseModel):
    """Demonstrative worked example deposited into long-term memory ('I Do')."""
    model_config = ConfigDict(frozen=True)

    problem: str = Field(..., description="Problem statement with concrete numerical/symbolic parameters")
    subgoal_steps: List[CanonicalWorkedExampleStep] = Field(default_factory=list, description="Sequential subgoal steps")
    final_answer: str = Field(..., description="Final boxed answer")


class ProblemBlueprint(BaseModel):
    """Deterministic SymPy problem generation blueprint."""
    model_config = ConfigDict(frozen=True)

    parameter_constraints: str = Field(..., description="Constraint DSL string for parameter sampling")
    stem_template: str = Field(..., description="LaTeX question stem template with parameter placeholders")
    answer_format: str = Field(default="ALGEBRAIC_EXPRESSION", description="Expected format: ALGEBRAIC_EXPRESSION, NUMERIC_EXACT, etc.")
    verification_solver_expr: str = Field(..., description="Python/SymPy expression string to grade student response")


class DiagnosticMisconception(BaseModel):
    """Documented empirical error pattern and non-Socratic Tier-1 remediation."""
    model_config = ConfigDict(frozen=True)

    error_tag: str = Field(..., description="Identifier for this error type, e.g. ERR_SIGN_INVERSION")
    trap_answer_pattern: str = Field(..., description="Expected student distractor / wrong answer expression")
    violated_rule: str = Field(..., description="Authoritative 1-sentence statement of violated theorem")
    tier1_remediation_template: str = Field(..., description="Direct step-by-step resolution without Socratic questions")


class KnowledgePoint(BaseModel):
    """Atomic knowledge unit within a TopicNode (2 to 4 KPs per topic)."""
    model_config = ConfigDict(frozen=True)

    kp_index: int = Field(..., ge=0, le=3, description="0-based sequence order (0, 1, 2, 3)")
    kp_id: str = Field(..., description="Deterministic ID: <topic_id>#kp_<index>")
    scaffolding_tier: str = Field(..., description="e.g. KP_1_BASELINE, KP_2_EDGE_NEGATIVE_FRACTIONAL, KP_3_MULTI_STEP_INVERSION")
    kp_focus: str = Field(..., description="Pedagogical focus and novel cognitive chunk boundary")
    novel_chunk_count: int = Field(default=2, ge=1, le=4, description="Cognitive load budget <= 4 chunks")
    key_prerequisite_id: Optional[str] = Field(default=None, description="Topic ID to remediate if student fails this KP twice")
    rule_statement: str = Field(default="", description="Authoritative definition/rule (<= 2 sentences)")
    canonical_worked_example: Optional[CanonicalWorkedExample] = None
    problem_blueprint: Optional[ProblemBlueprint] = None
    diagnostic_misconceptions: List[DiagnosticMisconception] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 3. Dual-Edge Hierarchy (Prerequisites vs. Encompassings)
# ---------------------------------------------------------------------------

class PrerequisiteEdge(BaseModel):
    """Forward learning gating dependency (u -> v). Transitively reduced."""
    model_config = ConfigDict(frozen=True)

    prerequisite_id: str = Field(..., description="Topic ID that must be mastered before unlocking dependent")
    strength: Literal["hard", "soft"] = Field(default="hard", description="'hard' blocks unlock; 'soft' advisory")
    rationale: str = Field(default="", description="Pedagogical reason for dependency")


class EncompassingEdge(BaseModel):
    """Backward Fractional Implicit Repetition (FIRe) containment (v -> u)."""
    model_config = ConfigDict(frozen=True)

    encompassed_id: str = Field(..., description="Simpler topic ID that receives implicit repetition credit")
    fire_weight: float = Field(default=0.5, gt=0.0, le=1.0, description="Implicit credit fraction w in (0.0, 1.0]")
    is_non_ancestor: bool = Field(default=False, description="True for cross-course or equivalent lateral topics")
    rationale: str = Field(default="", description="Rationale for implicit practice overlap")


# ---------------------------------------------------------------------------
# 4. Topic Node Specification
# ---------------------------------------------------------------------------

SkillType = Literal["PROCEDURAL", "CONCEPTUAL", "REPRESENTATIONAL", "FLUENCY_FACT", "THEOREM_PROOF", "SYNTHESIS"]
BloomCognitiveTier = Literal[
    "RETRIEVAL_AUTOMATICITY",
    "PROCEDURAL_EXECUTION",
    "CONCEPTUAL_CLASSIFICATION",
    "MULTI_STEP_SYNTHESIS",
    "FORMAL_DERIVATION",
]

class TopicNode(BaseModel):
    """Atomic curriculum node in the Trellis K-16 Knowledge Graph."""
    model_config = ConfigDict(frozen=True)

    id: str = Field(..., pattern=r"^trl_[a-z0-9_]+$", description="Proprietary ID: trl_<course>_<domain>_<seq>")
    title: str = Field(..., description="Standard mathematical concept title")
    course_id: str = Field(..., description="Course container ID, e.g. CRS_ALG1")
    canonical_domain: str = Field(..., description="Canonical 16-domain ontology category")
    domain_cluster: str = Field(..., description="Module cluster, e.g. CRS_ALG1::lin")
    skill_type: SkillType = Field(default="PROCEDURAL")
    bloom_cognitive_tier: str = Field(default="PROCEDURAL_EXECUTION")
    grade_level_min: int = Field(..., ge=0, le=16)
    grade_level_max: int = Field(..., ge=0, le=16)
    is_core: bool = Field(default=True, description="Satisfies Core Ancestor Closure invariant")
    in_foundations_sequence: bool = Field(default=False, description="Included in accelerated university-prep track")
    requires_automaticity: bool = Field(default=False, description="True for atomic fluency facts requiring <= 3.0s")
    automaticity_Defining_latency_ms: Optional[int] = Field(default=None, description="Max latency ceiling in ms (e.g. 3000)")
    base_difficulty: float = Field(default=0.5, ge=0.0, le=1.0)
    estimated_xp: int = Field(default=3, ge=1, le=10, description="Nominal XP award (1 XP ~ 1 minute)")
    mastery_gate_n: int = Field(default=3, ge=2, le=5, description="Required consecutive unassisted correct (default 3)")
    key_prerequisite_id: Optional[str] = Field(default=None, description="Topic-level Tier-3 foundational fallback")
    aligned_standards: List[str] = Field(default_factory=list, description="Public standard identifiers (CCSS, UK-NC)")
    mastery_criteria: List[str] = Field(default_factory=list)
    knowledge_points: List[KnowledgePoint] = Field(default_factory=list)
    prerequisites: List[PrerequisiteEdge] = Field(default_factory=list)
    encompassed_skills: List[EncompassingEdge] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 5. Decoupled Content Shell
# ---------------------------------------------------------------------------

ContentStatus = Literal["BLUEPRINT_READY", "LLM_DRAFTED", "VERIFIED_PRODUCTION"]

class KPContentShell(BaseModel):
    """Decoupled content shell stored per Knowledge Point."""
    kp_id: str = Field(..., description="Deterministic KP identifier")
    content_status: ContentStatus = Field(default="BLUEPRINT_READY")
    rule_statement: str = Field(default="")
    worked_example: Optional[CanonicalWorkedExample] = None
    problem_blueprint: Optional[ProblemBlueprint] = None
    diagnostic_misconceptions: List[DiagnosticMisconception] = Field(default_factory=list)
    svg_asset_uri: Optional[str] = Field(default=None, description="URI or path to dual-coded SVG visual schema")


# ---------------------------------------------------------------------------
# 6. Student State, Mastery & HSRS/FIRe
# ---------------------------------------------------------------------------

TopicMasteryStatus = Literal[
    "LOCKED",
    "AVAILABLE",
    "IN_PROGRESS",
    "MASTERED",
    "MASTERED_FLOOR",
    "DUE_FOR_REVIEW",
    "TIER_3_REMEDIATION",
]

class StudentTopicState(BaseModel):
    """Complete mutable learning and memory state for a (student, topic) pair."""
    student_id: str
    topic_id: str
    status: TopicMasteryStatus = "LOCKED"
    rep_num: float = Field(default=0.0, ge=0.0)
    stability_interval_days: float = Field(default=1.0, gt=0.0)
    memory_strength: float = Field(default=1.0, ge=0.0, le=1.0)
    last_reviewed_at: Optional[datetime] = None
    due_review_at: Optional[datetime] = None
    cumulative_fire_credit: float = Field(default=0.0, ge=0.0)
    student_ability: float = Field(default=1.0, gt=0.0)
    topic_difficulty: float = Field(default=0.5, gt=0.0)
    speed: float = Field(default=1.0, ge=0.25, le=2.50)
    allow_implicit_fire_credit: bool = True
    diagnostic_plus_minus_balance: float = Field(default=0.0)
    is_conditionally_completed: bool = False
    current_kp_index: int = Field(default=0, ge=0)
    consecutive_correct: int = Field(default=0, ge=0)
    kp_failure_counts: Dict[int, int] = Field(default_factory=dict)
    cooldown_until: Optional[datetime] = None


IndependenceStage = Literal[
    "CO_SITTING",
    "PER_TASK_CHECKIN",
    "DAILY_CHECKIN",
    "EVERY_FEW_DAYS_CHECKIN",
    "WEEKLY_CHECKIN",
]

class StudentProfile(BaseModel):
    """Learner progress profile, pace telemetry, and coaching state."""
    student_id: str
    enrolled_course_id: str
    xp_total: int = Field(default=0, ge=0)
    progress_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    daily_xp_target: int = Field(default=40, ge=10, le=90)
    rolling_pace_xp: float = Field(default=0.0, ge=0.0)
    independence_stage: IndependenceStage = "DAILY_CHECKIN"
    process_praise_counter: int = Field(default=0, ge=0)
    rolling_latency_ms: Dict[str, int] = Field(default_factory=dict)
    league_opt_in: bool = Field(default=False)
    league_tier: str = Field(default="APPRENTICE")
    consecutive_blowoffs: int = Field(default=0)


# ---------------------------------------------------------------------------
# 7. Dashboard & Telemetry Logs
# ---------------------------------------------------------------------------

TaskType = Literal[
    "FRONTIER_LESSON",
    "SPACED_REVIEW",
    "TARGETED_REMEDIATION",
    "FACT_FLUENCY",
    "TIMED_QUIZ",
]

class DashboardTaskItem(BaseModel):
    """Task item on the student's sticky 'Math Buffet' task dashboard."""
    student_id: str
    topic_id: str
    task_type: TaskType
    rank_score: float = Field(default=0.0)
    is_sticky: bool = Field(default=True)
    assigned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class InteractionAttempt(BaseModel):
    """Append-only audit record of an individual student practice attempt."""
    attempt_id: str
    student_id: str
    topic_id: str
    kp_id: str
    submitted_answer: str
    is_correct: bool
    duration_ms: int
    reference_peeked: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


QualityTier = Literal[
    "BONUS_XP",
    "FULL_XP",
    "MOST_XP",
    "LITTLE_XP",
    "ZERO_XP",
    "NEGATIVE_PENALTY",
]

class XPLedgerEntry(BaseModel):
    """Auditable XP credit or negative penalty ledger entry."""
    entry_id: str
    student_id: str
    topic_id: str
    task_type: str
    quality_tier: QualityTier
    delta_xp: int
    reason: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# 8. Query Result & Service DTOs
# ---------------------------------------------------------------------------

class WeightedEncompassingPath(BaseModel):
    """Represents a multi-hop FIRe propagation path."""
    model_config = ConfigDict(frozen=True)

    ancestor_id: str
    hops: int = Field(..., ge=1, le=3)
    compound_weight: float = Field(..., gt=0.0, le=1.0)
    path: List[str]
    is_non_ancestor: bool = False


class LocalNeighborhood(BaseModel):
    """Local graph context around a specific topic node."""
    topic_id: str
    direct_prerequisites: List[str]
    key_prerequisite_id: Optional[str]
    encompassed_ancestors: List[str]
    encompassing_descendants: List[str]
    cluster_siblings: List[str]


class ReviewCompressionPlan(BaseModel):
    """Output plan from the Set-Cover Spaced Repetition Compressor."""
    selected_task_ids: List[str]
    covered_review_ids: List[str]
    fended_review_ids: List[str]
    efficiency_ratio: float


class GraphIngestionReport(BaseModel):
    """Summary metrics emitted following knowledge graph ingestion."""
    courses_ingested: int
    nodes_ingested: int
    knowledge_points_ingested: int
    prereq_edges_ingested: int
    encompassing_edges_ingested: int
    content_shells_ingested: int
    closure_paths_computed: int
    duration_ms: float


class SubmissionEvaluationResult(BaseModel):
    """Deterministic result payload returned from submission evaluation."""
    correct: bool
    consecutive_correct: int
    kp_completed: bool
    node_mastered: bool
    xp_awarded: int
    quality_tier: QualityTier
    tier_remediation: Optional[int] = None
    remedial_target_topic_id: Optional[str] = None
    next_action: Literal["SERVE_NEXT_STEP", "SERVE_NEXT_TASK", "TIER_1_REMEDIATION", "HALT_TO_KEY_PREREQUISITE", "CONSOLIDATION_COOLDOWN"]


# ---------------------------------------------------------------------------
# 9. Tier 7 Gamification, Anti-Cheat & League DTOs
# ---------------------------------------------------------------------------

LeagueTier = Literal["APPRENTICE", "PRACTITIONER", "SCHOLAR", "FELLOW", "POLYMATH"]


class LeagueMemberStanding(BaseModel):
    """Member standing record within a weekly league cohort."""
    model_config = ConfigDict(frozen=True)

    student_id: str
    net_weekly_xp: int = 0
    rank: int = 1
    status: Literal["PROMOTING", "RETAINING", "DEMOTING"] = "RETAINING"


class LeagueCohort(BaseModel):
    """Ability-grouped weekly league cohort (20-30 learners)."""
    model_config = ConfigDict(frozen=True)

    cohort_id: str
    week_key: str  # ISO week e.g. "2026-W43"
    tier: str = "APPRENTICE"
    member_ids: List[str] = Field(default_factory=list)
    standings: List[LeagueMemberStanding] = Field(default_factory=list)
    finalized: bool = False


class XPHackerAlert(BaseModel):
    """Alert record emitted when a rapid task blow-off is detected."""
    model_config = ConfigDict(frozen=True)

    alert_id: str
    student_id: str
    topic_id: str
    duration_ms: int
    min_expected_ms: int
    penalty_xp: int
    consecutive_blowoffs: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CourseCompletionProjection(BaseModel):
    """Course completion forecast computed via the (pace/40)^0.1 efficiency curve."""
    model_config = ConfigDict(frozen=True)

    student_id: str
    course_id: str
    current_progress_pct: float
    benchmark_xp: int
    remaining_xp: int
    rolling_pace_xp: float
    efficiency_multiplier: float
    projected_weekdays: int
    projected_calendar_weeks: float
    projected_completion_date: str
    viability_warning: bool = False
    burnout_warning: bool = False


# ---------------------------------------------------------------------------
# 10. Tier 8 Parent Portal, Habit & Content Remediation DTOs
# ---------------------------------------------------------------------------

class ParentCustomReward(BaseModel):
    """Custom parent/coach reward bound to an cumulative XP target."""
    model_config = ConfigDict(frozen=True)

    reward_id: str
    student_id: str
    title: str
    xp_target: int = Field(..., ge=1, description="Cumulative XP required to unlock reward")
    status: Literal["PENDING", "UNLOCKED", "CLAIMED"] = "PENDING"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    unlocked_at: Optional[datetime] = None
    claimed_at: Optional[datetime] = None


class AdultReSitAlert(BaseModel):
    """Advisory check-in / re-sit notification delivered to parent or coach."""
    model_config = ConfigDict(frozen=True)

    alert_id: str
    student_id: str
    reason: Literal[
        "XP_HACKER_PENALTY",
        "PROLONGED_STALL",
        "FREQUENT_REMEDIATION_HALTS",
        "BURNOUT_WARNING",
        "PACING_BELOW_VIABILITY",
        "MANUAL_REQUEST",
    ]
    details: str
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    acknowledged: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    acknowledged_at: Optional[datetime] = None


class StudyHabitSchedule(BaseModel):
    """Contextual daily study window settings, streak records, and anchor schedule."""
    model_config = ConfigDict(frozen=True)

    student_id: str
    target_daily_xp: int = Field(default=40, ge=10, le=90)
    scheduled_start_time: str = Field(default="16:00", description="HH:MM 24-hr format")
    scheduled_end_time: str = Field(default="17:00", description="HH:MM 24-hr format")
    active_days: List[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4], description="0=Mon, 4=Fri, 6=Sun")
    current_streak_days: int = 0
    longest_streak_days: int = 0
    last_active_date: Optional[str] = None  # YYYY-MM-DD


HabitHealthStatus = Literal[
    "STRONG_HABIT",
    "DEVELOPING_HABIT",
    "INCONSISTENT",
    "AT_RISK_DROPOUT",
    "BURNOUT_RISK",
]

class HabitTelemetryReport(BaseModel):
    """Habit formation and pace health diagnostic report."""
    model_config = ConfigDict(frozen=True)

    student_id: str
    rolling_pace_xp: float
    viability_warning: bool = False
    burnout_warning: bool = False
    streak_days: int = 0
    window_adherence_pct: float = 100.0
    habit_health: HabitHealthStatus = "DEVELOPING_HABIT"
    recommendation: str = ""


ContentFlagType = Literal[
    "AMBIGUOUS_WORDING",
    "UNEXPLAINED_STEP",
    "MATH_TYPO",
    "INCORRECT_EVALUATION",
    "POOR_EXPLANATION",
    "OTHER",
]

class ContentRemediationFlag(BaseModel):
    """Student or coach in-task question or explanation defect flag."""
    model_config = ConfigDict(frozen=True)

    flag_id: str
    student_id: str
    topic_id: str
    kp_id: Optional[str] = None
    problem_stem: Optional[str] = None
    flag_type: ContentFlagType = "AMBIGUOUS_WORDING"
    student_comment: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: Literal["OPEN", "UNDER_REVIEW", "RESOLVED"] = "OPEN"


class GranularPassRateMetric(BaseModel):
    """Pass rate diagnostic metric for Topic, KP, or Problem Blueprint against 95%/99% bar."""
    model_config = ConfigDict(frozen=True)

    entity_id: str
    entity_type: Literal["TOPIC", "KNOWLEDGE_POINT", "BLUEPRINT"]
    total_first_attempts: int
    first_attempt_passes: int
    second_attempt_passes: int
    pass_rate_first_attempt: float
    pass_rate_two_attempts: float
    flag_first_pass_deficit: bool = False  # True if < 0.95
    flag_critical_defect: bool = False     # True if < 0.99
    recommended_action: Optional[str] = None


class PrimingEvaluationReport(BaseModel):
    """Evaluation cross-checking high lesson pass rates against unprimed retention."""
    model_config = ConfigDict(frozen=True)

    topic_id: str
    lesson_pass_rate: float
    unprimed_review_quiz_accuracy: float
    retention_gap: float
    superficial_priming_detected: bool = False
    recommendation: str = ""


class IndependenceStageProgress(BaseModel):
    """Evaluation status for 5-stage scaffolded independence."""
    model_config = ConfigDict(frozen=True)

    student_id: str
    current_stage: IndependenceStage
    eligible_for_promotion: bool = False
    next_stage: Optional[IndependenceStage] = None
    advisory_recommendation: Optional[str] = None

