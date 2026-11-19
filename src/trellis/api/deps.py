"""
src/trellis/api/deps.py
Dependency Injection Container & Session State Registry for Tier 9 FastAPI Layer.
Wires all domain services exclusively to a single TrellisStoragePort backend
selected via DB_BACKEND='memory' | 'relational' | 'graph'.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from fastapi import Request

from trellis.engine.content_blueprint_solver import ContentBlueprintSolver
from trellis.engine.diagnostic_engine import DiagnosticEngine
from trellis.engine.frontier_service import FrontierService
from trellis.engine.gamification_efficiency import GamificationEfficiencyEngine
from trellis.engine.hsrs_fire_engine import HSRSFireEngine
from trellis.engine.practice_evaluator import PracticeEvaluator, ReviewSession
from trellis.engine.session_scheduler import SessionScheduler
from trellis.engine.topology_service import TopologyService
from trellis.pedagogy.instruction_coach_service import InstructionCoachService
from trellis.ports.storage_port import TrellisStoragePort, get_storage_adapter
from trellis.services.accountability_analytics import AccountabilityAnalyticsService
from trellis.services.ingestion_service import KnowledgeGraphIngestionService


@dataclass
class DiagnosticSessionState:
    """Ephemeral tracking for an active adaptive placement diagnostic session."""

    student_id: str
    enrolled_course_id: str
    foundations_only: bool = False
    min_questions: int = 20
    max_questions: int = 60
    candidate_pool: List[str] = field(default_factory=list)
    asked_topic_ids: Set[str] = field(default_factory=set)
    failed_direct_topic_ids: Set[str] = field(default_factory=set)


class TrellisServiceContainer:
    """
    Holds the singleton TrellisStoragePort and wired domain services for a FastAPI app.
    Zero raw SQL or Cypher queries exist in this layer.
    """

    def __init__(self, storage: Optional[TrellisStoragePort] = None) -> None:
        if storage is None:
            backend = os.getenv("DB_BACKEND", "memory")
            storage = get_storage_adapter(backend_type=backend)

        self.storage: TrellisStoragePort = storage
        self.ingestion = KnowledgeGraphIngestionService(self.storage)
        self.topology = TopologyService(self.storage)
        self.solver = ContentBlueprintSolver(self.storage)
        self.coach = InstructionCoachService(self.storage, self.solver)
        self.diagnostic = DiagnosticEngine(self.storage, self.topology, self.solver)
        self.frontier = FrontierService(self.storage, self.topology)
        self.hsrs = HSRSFireEngine(self.storage)
        self.scheduler = SessionScheduler(
            self.storage,
            frontier_service=self.frontier,
            hsrs_engine=self.hsrs,
        )
        self.gamification = GamificationEfficiencyEngine(self.storage)
        self.practice = PracticeEvaluator(
            storage=self.storage,
            topology_service=self.topology,
            blueprint_solver=self.solver,
            diagnostic_engine=self.diagnostic,
            frontier_service=self.frontier,
            hsrs_engine=self.hsrs,
            scheduler=self.scheduler,
            coach_service=self.coach,
            gamification_engine=self.gamification,
        )
        self.accountability = AccountabilityAnalyticsService(self.storage)

        # In-memory active session registries for stateful REST flows
        self.review_sessions: Dict[str, ReviewSession] = {}
        self.diagnostic_sessions: Dict[str, DiagnosticSessionState] = {}


def get_container(request: Request) -> TrellisServiceContainer:
    """FastAPI dependency returning the app-scoped TrellisServiceContainer."""
    return request.app.state.container
