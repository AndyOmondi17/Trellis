"""
src/trellis/api/routers/session.py
Student Dashboard & Set-Cover Review Compression endpoints (Tier 4).
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from trellis.api.deps import TrellisServiceContainer, get_container
from trellis.domain.models import DashboardTaskItem
from trellis.engine.session_scheduler import DashboardRefreshResult

router = APIRouter(prefix="/session", tags=["Session & Dashboard"])


class SessionRefreshRequest(BaseModel):
    student_id: str


@router.get("/dashboard", response_model=List[DashboardTaskItem])
def get_dashboard(
    student_id: str = Query(..., description="Student identifier"),
    container: TrellisServiceContainer = Depends(get_container),
) -> List[DashboardTaskItem]:
    """
    Returns the student's sticky 6-item 'Math Buffet' task dashboard.
    If no menu exists yet, triggers an initial Set-Cover refresh.
    """
    container.storage.get_student_profile(student_id)
    menu = container.storage.get_dashboard_menu(student_id)
    if not menu:
        refresh_result = container.scheduler.refresh_dashboard(student_id)
        return refresh_result.menu
    return menu


@router.post("/refresh", response_model=DashboardRefreshResult)
def refresh_dashboard(
    payload: SessionRefreshRequest,
    container: TrellisServiceContainer = Depends(get_container),
) -> DashboardRefreshResult:
    """
    Executes HSRS memory decay sweep, Set-Cover review compression,
    70/30 macro-interleaving, and persists the updated sticky dashboard.
    """
    container.storage.get_student_profile(payload.student_id)
    return container.scheduler.refresh_dashboard(payload.student_id)
