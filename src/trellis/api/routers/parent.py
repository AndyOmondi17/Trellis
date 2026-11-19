"""
src/trellis/api/routers/parent.py
Parent & Coach Accountability Portal endpoints (Tier 8).
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from trellis.api.deps import TrellisServiceContainer, get_container
from trellis.domain.models import ParentCustomReward

router = APIRouter(prefix="/parent", tags=["Parent & Coach Portal"])


class CreateRewardRequest(BaseModel):
    student_id: str
    title: str = Field(..., min_length=1, max_length=120)
    xp_target: int = Field(..., ge=1)


@router.get("/summary")
def get_parent_summary(
    student_id: str = Query(..., description="Student identifier"),
    container: TrellisServiceContainer = Depends(get_container),
) -> Dict[str, Any]:
    """
    Returns the consolidated Parent / Coach Portal summary including
    5-stage independence progress, habit health, streaks, rewards, and alerts.
    """
    return container.accountability.get_parent_portal_summary(student_id)


@router.post("/rewards", response_model=ParentCustomReward)
def create_parent_reward(
    payload: CreateRewardRequest,
    container: TrellisServiceContainer = Depends(get_container),
) -> ParentCustomReward:
    """Creates a custom parent reward bound to a cumulative XP milestone."""
    return container.accountability.create_parent_reward(
        student_id=payload.student_id,
        title=payload.title,
        xp_target=payload.xp_target,
    )
