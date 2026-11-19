"""
src/trellis/api/routers/author.py
Teacher & Curriculum Author Quality Triage endpoints (Tier 8 / FR-8.3).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from trellis.api.deps import TrellisServiceContainer, get_container
from trellis.domain.models import ContentFlagType, ContentRemediationFlag

router = APIRouter(prefix="/author", tags=["Teacher & Author Quality"])


class CreateContentFlagRequest(BaseModel):
    student_id: str
    topic_id: str
    kp_id: Optional[str] = None
    problem_stem: Optional[str] = None
    flag_type: ContentFlagType = "AMBIGUOUS_WORDING"
    student_comment: str = ""


class ResolveFlagRequest(BaseModel):
    status: str = "RESOLVED"


@router.get("/remediation-queue")
def get_remediation_queue(
    course_id: Optional[str] = Query(default=None, description="Optional course filter"),
    container: TrellisServiceContainer = Depends(get_container),
) -> Dict[str, Any]:
    """Returns prioritized 95%/99% pass-bar defects and open student content flags."""
    return container.accountability.get_content_author_remediation_queue(course_id=course_id)


@router.post("/flags", response_model=ContentRemediationFlag)
def create_content_flag(
    payload: CreateContentFlagRequest,
    container: TrellisServiceContainer = Depends(get_container),
) -> ContentRemediationFlag:
    """Records an in-task student or coach content defect flag."""
    return container.accountability.flag_content_item(
        student_id=payload.student_id,
        topic_id=payload.topic_id,
        kp_id=payload.kp_id,
        problem_stem=payload.problem_stem,
        flag_type=payload.flag_type,
        student_comment=payload.student_comment,
    )


@router.post("/flags/{flag_id}/resolve")
def resolve_content_flag(
    flag_id: str,
    payload: ResolveFlagRequest = ResolveFlagRequest(),
    container: TrellisServiceContainer = Depends(get_container),
) -> Dict[str, str]:
    """Marks an open content remediation flag as RESOLVED or UNDER_REVIEW."""
    container.accountability.resolve_content_flag(flag_id=flag_id, status=payload.status)
    return {"flag_id": flag_id, "status": payload.status}
