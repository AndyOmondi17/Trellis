"""
src/trellis/pedagogy/models.py
Pydantic v2 domain models and validation structures for Tier 6:
Constrained LLM Direct-Instruction & Process Coaching Layer (trellis-pedagogy).
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

from trellis.domain.models import (
    CanonicalWorkedExample,
    CanonicalWorkedExampleStep,
    DiagnosticMisconception,
    TopicNode,
)


# ---------------------------------------------------------------------------
# 1. FR-6.1 Direct Instruction ("I Do") Models
# ---------------------------------------------------------------------------

class InstructionCardPayload(BaseModel):
    """FR-5.1 / FR-6.1 'I Do' Direct Instruction Card presented before practice."""
    model_config = ConfigDict(frozen=True)

    topic_id: str
    kp_id: str
    kp_index: int
    scaffolding_tier: str
    kp_focus: str
    novel_chunk_count: int
    rule_statement: str
    worked_example: Optional[CanonicalWorkedExample] = None
    svg_asset_uri: Optional[str] = None


class GenerateInstructionRequest(BaseModel):
    """Input payload to request or draft an 'I Do' Direct Instruction card."""
    model_config = ConfigDict(frozen=True)

    topic_id: str = Field(..., description="Topic identifier, e.g. trl_alg_lin_01")
    topic_name: str = Field(..., description="Standard concept title")
    evidence_criteria: List[str] = Field(default_factory=list, description="Curriculum evidence criteria")
    kp_index: int = Field(..., ge=0, le=3, description="0-based sequence index")
    kp_id: str = Field(..., description="KP identifier, e.g. trl_alg_lin_01#kp_0")
    kp_focus: str = Field(..., description="Pedagogical focus and novel cognitive chunk boundary")
    scaffolding_tier: str = Field(default="KP_1_BASELINE", description="Scaffolding tier description")
    novel_chunk_count: int = Field(default=2, ge=1, le=4, description="Cognitive load budget <= 4 chunks")
    difficulty_level: str = Field(default="novice", description="Learner calibration level")
    problem_stem: Optional[str] = Field(default=None, description="Optional problem stem context")
    parameters: Optional[Dict[str, Any]] = Field(default=None, description="Concrete parameters sampled")


class InstructionCardValidationResult(BaseModel):
    """Outcome emitted by deterministic validation of an InstructionCardPayload."""
    model_config = ConfigDict(frozen=True)

    is_valid: bool
    rule_sentence_count: int
    novel_chunk_count: int
    has_canonical_worked_example: bool
    subgoal_step_count: int
    socratic_violations: List[str] = Field(default_factory=list)
    banned_phrase_violations: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 2. FR-6.2 Non-Socratic Tier-1 Precision Remediation Models
# ---------------------------------------------------------------------------

class RemediationCardPayload(BaseModel):
    """FR-5.2 / FR-6.2 Tier-1 Non-Socratic Corrective Remediation Card."""
    model_config = ConfigDict(frozen=True)

    topic_id: str
    kp_id: str
    error_tag: str
    violated_rule: str
    identified_misstep: str
    corrected_steps: List[str]
    boxed_answer: str


class RemediationRequest(BaseModel):
    """Input payload to format a non-Socratic Tier-1 corrective breakdown."""
    model_config = ConfigDict(frozen=True)

    topic_id: str
    topic_name: str
    kp_id: str
    kp_focus: str
    evidence_rubric: str
    problem_text: str
    student_answer: str
    correct_answer: str
    solver_trace: Optional[List[str]] = Field(default=None, description="Verified SymPy calculation trace")
    parameters: Optional[Dict[str, Any]] = Field(default=None)


class RemediationCardValidationResult(BaseModel):
    """Outcome emitted by deterministic validation of a RemediationCardPayload."""
    model_config = ConfigDict(frozen=True)

    is_valid: bool
    has_violated_rule: bool
    has_identified_misstep: bool
    has_step_by_step_resolution: bool
    has_boxed_answer: bool
    socratic_violations: List[str] = Field(default_factory=list)
    hollow_praise_violations: List[str] = Field(default_factory=list)
    slip_dismissal_violations: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 3. FR-6.3 John Wooden 5-to-1 Process Coach Models
# ---------------------------------------------------------------------------

CoachingFeedbackType = Literal[
    "PROCESS_CONFIRMATION",
    "CORRECTIVE_INTERVENTION",
    "PAPER_PENCIL_PROMPT",
    "NEUTRAL_INSTRUCTION",
]

class CoachingFeedback(BaseModel):
    """Process-grounded athletic coaching feedback and telemetry."""
    model_config = ConfigDict(frozen=True)

    feedback_type: CoachingFeedbackType
    message: str
    process_confirmations_count: int = Field(..., ge=0)
    corrective_interventions_count: int = Field(..., ge=0)
    current_praise_ratio: float = Field(..., ge=0.0)
    prompt_paper_and_pencil: bool = False
    paper_and_pencil_rationale: Optional[str] = None


class CoachStudentState(BaseModel):
    """Internal mutable tracking state for student coaching ratio and slips."""
    student_id: str
    confirmations_count: int = 0
    corrections_count: int = 0
    consecutive_correct_steps: int = 0
    recent_slip_on_kp: Optional[str] = None
    last_feedback_type: Optional[CoachingFeedbackType] = None
