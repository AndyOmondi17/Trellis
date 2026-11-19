"""
src/trellis/pedagogy/__init__.py
Pedagogy package for Trellis K-16 Mathematics Platform.
Constrained Direct-Instruction, Precision Remediation, and John Wooden 5-to-1 Process Coach.
"""

from trellis.pedagogy.instruction_coach_service import InstructionCoachService
from trellis.pedagogy.llm_client import (
    DeterministicMockLLMClient,
    GeminiPedagogyLLMClient,
    PedagogyLLMClient,
)
from trellis.pedagogy.models import (
    CoachStudentState,
    CoachingFeedback,
    CoachingFeedbackType,
    GenerateInstructionRequest,
    InstructionCardPayload,
    InstructionCardValidationResult,
    RemediationCardPayload,
    RemediationCardValidationResult,
    RemediationRequest,
)
from trellis.pedagogy.prompt_templates import (
    PAPER_PENCIL_PROMPT_MESSAGE,
    contains_hollow_praise,
    contains_slip_dismissal,
    contains_socratic_inquiry,
    split_into_sentences,
)

__all__ = [
    "InstructionCoachService",
    "PedagogyLLMClient",
    "DeterministicMockLLMClient",
    "GeminiPedagogyLLMClient",
    "GenerateInstructionRequest",
    "InstructionCardPayload",
    "InstructionCardValidationResult",
    "RemediationRequest",
    "RemediationCardPayload",
    "RemediationCardValidationResult",
    "CoachingFeedback",
    "CoachingFeedbackType",
    "CoachStudentState",
    "PAPER_PENCIL_PROMPT_MESSAGE",
    "contains_socratic_inquiry",
    "contains_hollow_praise",
    "contains_slip_dismissal",
    "split_into_sentences",
]
