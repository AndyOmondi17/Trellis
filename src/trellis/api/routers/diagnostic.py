"""
src/trellis/api/routers/diagnostic.py
Adaptive Placement Diagnostic endpoints (Tier 2).
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from trellis.api.deps import DiagnosticSessionState, TrellisServiceContainer, get_container
from trellis.domain.models import StudentProfile
from trellis.engine.diagnostic_engine import (
    DiagnosticPlacementResult,
    DiagnosticPropagationReport,
    DiagnosticQuestion,
    DiagnosticSubmission,
)

router = APIRouter(prefix="/diagnostic", tags=["Placement Diagnostic"])


class StartDiagnosticRequest(BaseModel):
    student_id: str
    enrolled_course_id: str
    foundations_only: bool = False
    min_questions: int = Field(default=20, ge=1, le=60)
    max_questions: int = Field(default=60, ge=1, le=60)
    seed: Optional[int] = None


class StartDiagnosticResponse(BaseModel):
    session_id: str
    student_id: str
    enrolled_course_id: str
    compressed_cover_size: int
    next_question: Optional[DiagnosticQuestion] = None


class SubmitDiagnosticRequest(BaseModel):
    session_id: str
    submission: DiagnosticSubmission
    finalize_if_complete: bool = True
    seed: Optional[int] = None


class SubmitDiagnosticResponse(BaseModel):
    session_id: str
    propagation: DiagnosticPropagationReport
    is_complete: bool
    next_question: Optional[DiagnosticQuestion] = None
    placement_result: Optional[DiagnosticPlacementResult] = None


@router.post("/start", response_model=StartDiagnosticResponse)
def start_diagnostic(
    payload: StartDiagnosticRequest,
    container: TrellisServiceContainer = Depends(get_container),
) -> StartDiagnosticResponse:
    """
    Compresses the target course + foundations graph into a <= 3-hop cover
    and returns the first maximum-entropy diagnostic question.
    """
    try:
        container.storage.get_student_profile(payload.student_id)
    except KeyError:
        container.storage.upsert_student_profile(
            StudentProfile(
                student_id=payload.student_id,
                enrolled_course_id=payload.enrolled_course_id,
            )
        )

    cover = container.diagnostic.compress_diagnostic_subgraph(
        enrolled_course_id=payload.enrolled_course_id,
        student_id=payload.student_id,
        foundations_only=payload.foundations_only,
    )

    session_id = f"diag_{uuid.uuid4().hex[:12]}"
    state = DiagnosticSessionState(
        student_id=payload.student_id,
        enrolled_course_id=payload.enrolled_course_id,
        foundations_only=payload.foundations_only,
        min_questions=payload.min_questions,
        max_questions=payload.max_questions,
        candidate_pool=cover,
    )
    container.diagnostic_sessions[session_id] = state

    next_q = container.diagnostic.select_next_diagnostic_question(
        student_id=payload.student_id,
        enrolled_course_id=payload.enrolled_course_id,
        asked_topic_ids=state.asked_topic_ids,
        candidate_pool=state.candidate_pool,
        foundations_only=state.foundations_only,
        min_questions=state.min_questions,
        max_questions=state.max_questions,
        seed=payload.seed,
    )

    return StartDiagnosticResponse(
        session_id=session_id,
        student_id=payload.student_id,
        enrolled_course_id=payload.enrolled_course_id,
        compressed_cover_size=len(cover),
        next_question=next_q,
    )


@router.post("/submit", response_model=SubmitDiagnosticResponse)
def submit_diagnostic(
    payload: SubmitDiagnosticRequest,
    container: TrellisServiceContainer = Depends(get_container),
) -> SubmitDiagnosticResponse:
    """
    Evaluates a diagnostic answer submission, propagates signed plus-minus
    confidence weights, and returns the next question or final placement.
    """
    if payload.submission.submission_flag == "I_DONT_KNOW":
        raise HTTPException(
            status_code=400,
            detail="The 'I_DONT_KNOW' submission flag is deprecated and no longer accepted; submit an answer instead.",
        )

    state = container.diagnostic_sessions.get(payload.session_id)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail=f"Diagnostic session '{payload.session_id}' not found.",
        )

    report = container.diagnostic.process_diagnostic_submission(payload.submission)
    state.asked_topic_ids.add(payload.submission.topic_id)
    if not report.is_correct:
        state.failed_direct_topic_ids.add(payload.submission.topic_id)

    next_q = container.diagnostic.select_next_diagnostic_question(
        student_id=state.student_id,
        enrolled_course_id=state.enrolled_course_id,
        asked_topic_ids=state.asked_topic_ids,
        candidate_pool=state.candidate_pool,
        foundations_only=state.foundations_only,
        min_questions=state.min_questions,
        max_questions=state.max_questions,
        seed=payload.seed,
    )

    is_complete = next_q is None
    placement: Optional[DiagnosticPlacementResult] = None
    if is_complete and payload.finalize_if_complete:
        placement = container.diagnostic.finalize_diagnostic_placement(
            student_id=state.student_id,
            enrolled_course_id=state.enrolled_course_id,
            foundations_only=state.foundations_only,
            failed_direct_topic_ids=state.failed_direct_topic_ids,
        )

    return SubmitDiagnosticResponse(
        session_id=payload.session_id,
        propagation=report,
        is_complete=is_complete,
        next_question=next_q,
        placement_result=placement,
    )
