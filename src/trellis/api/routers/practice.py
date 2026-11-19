"""
src/trellis/api/routers/practice.py
Active Practice Loop endpoints: Lesson ($N=3$), Spaced Review, and Spotter Mode (Tier 5).
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from trellis.api.deps import TrellisServiceContainer, get_container
from trellis.engine.practice_evaluator import (
    LessonEvaluationOutcome,
    LessonStepPresentation,
    PracticeSubmission,
    ReviewEvaluationResult,
    ReviewSession,
)
from trellis.pedagogy.models import InstructionCardPayload

router = APIRouter(prefix="/practice", tags=["Active Practice"])


class StartLessonRequest(BaseModel):
    student_id: str
    topic_id: str
    diagnostic_accuracy: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    seed: Optional[int] = None


class StartReviewRequest(BaseModel):
    student_id: str
    topic_id: str
    task_type: Literal["SPACED_REVIEW", "TARGETED_REMEDIATION"] = "SPACED_REVIEW"
    blocked_dependent_topic_id: Optional[str] = None
    seed: Optional[int] = None


class SubmitReviewRequest(BaseModel):
    session_id: str
    question_index: int = Field(..., ge=0)
    student_answer: str
    duration_ms: int = Field(default=1000, ge=0)


class ReviewSubmitResponse(BaseModel):
    session: ReviewSession
    evaluation: Optional[ReviewEvaluationResult] = None


class SpotterPeekRequest(BaseModel):
    session_id: str
    question_index: int = Field(..., ge=0)
    input_locked: bool


@router.post("/lesson/start", response_model=LessonStepPresentation)
def start_lesson(
    payload: StartLessonRequest,
    container: TrellisServiceContainer = Depends(get_container),
) -> LessonStepPresentation:
    """Initializes or resumes an unmastered lesson staircase at the current KP."""
    return container.practice.start_lesson(
        student_id=payload.student_id,
        topic_id=payload.topic_id,
        diagnostic_accuracy=payload.diagnostic_accuracy,
        seed=payload.seed,
    )


@router.post("/lesson/submit", response_model=LessonEvaluationOutcome)
def submit_lesson(
    submission: PracticeSubmission,
    container: TrellisServiceContainer = Depends(get_container),
) -> LessonEvaluationOutcome:
    """
    Grades a lesson answer, enforces the N=3 unassisted mastery gate,
    routes Tier 1-3 remediation, and applies Wooden 5:1 coaching + XP awards.
    """
    return container.practice.evaluate_lesson_submission(submission)


@router.post("/review/start", response_model=ReviewSession)
def start_review(
    payload: StartReviewRequest,
    container: TrellisServiceContainer = Depends(get_container),
) -> ReviewSession:
    """Initializes a micro-interleaved spaced review session (3-5 questions)."""
    session = container.practice.start_review_session(
        student_id=payload.student_id,
        topic_id=payload.topic_id,
        task_type=payload.task_type,
        blocked_dependent_topic_id=payload.blocked_dependent_topic_id,
        seed=payload.seed,
    )
    container.review_sessions[session.session_id] = session
    return session


@router.post("/review/submit", response_model=ReviewSubmitResponse)
def submit_review(
    payload: SubmitReviewRequest,
    container: TrellisServiceContainer = Depends(get_container),
) -> ReviewSubmitResponse:
    """Submits an answer to an active review session and evaluates sequence completion."""
    session = container.review_sessions.get(payload.session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Review session '{payload.session_id}' not found.",
        )

    updated_session, evaluation = container.practice.submit_review_answer(
        session=session,
        question_index=payload.question_index,
        student_answer=payload.student_answer,
        duration_ms=payload.duration_ms,
    )
    container.review_sessions[payload.session_id] = updated_session
    return ReviewSubmitResponse(session=updated_session, evaluation=evaluation)


@router.post("/spotter/peek", response_model=InstructionCardPayload)
def spotter_peek(
    payload: SpotterPeekRequest,
    container: TrellisServiceContainer = Depends(get_container),
) -> InstructionCardPayload:
    """
    Enables read-only Spotter Reference Mode for a review question.
    Requires input_locked=True and applies multiplicative FIRe credit discount.
    """
    session = container.review_sessions.get(payload.session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Review session '{payload.session_id}' not found.",
        )

    return container.practice.open_spotter_reference(
        session=session,
        question_index=payload.question_index,
        input_locked=payload.input_locked,
    )
