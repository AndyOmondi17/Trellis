"""
src/trellis/ports/storage_port.py
Unified Persistence Boundary (FR-0.1).
Exposes a single repository protocol covering 100% of curriculum ingestion,
multi-hop graph traversals, decoupled content shells, and transactional
student state reads/writes.
Zero cross-database coordination.
Zero raw queries permitted outside src/trellis/adapters/db/.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from trellis.domain.models import (
    AdultReSitAlert,
    ContentRemediationFlag,
    CourseCatalogItem,
    DashboardTaskItem,
    GraphIngestionReport,
    InteractionAttempt,
    KPContentShell,
    LeagueCohort,
    LocalNeighborhood,
    ParentCustomReward,
    ReviewCompressionPlan,
    StudentProfile,
    StudentTopicState,
    StudyHabitSchedule,
    TopicNode,
    WeightedEncompassingPath,
    XPLedgerEntry,
)


@runtime_checkable
class TrellisStoragePort(Protocol):
    """
    Unified Hexagonal Storage Port for Trellis.
    Implemented independently by StandaloneRelationalAdapter (Adapter A),
    StandaloneGraphAdapter (Adapter B), and MemoryReferenceAdapter (Adapter C).
    """

    # --- A. Curriculum & Full-State DAG Ingestion ---
    def ingest_knowledge_graph(self, graph_payload: Dict[str, Any]) -> GraphIngestionReport:
        """Ingests all courses, topics, KPs, and 4 edge types into the active backend."""
        ...

    def get_course_catalog(self) -> List[CourseCatalogItem]:
        """Returns all configured courses in the curriculum catalog."""
        ...

    def get_topic_node(self, topic_id: str) -> TopicNode:
        """Retrieves a single TopicNode by ID. Raises KeyError if not found."""
        ...

    def get_course_subgraph(
        self,
        course_id: str,
        foundations_only: bool = False,
        core_only: bool = False,
    ) -> List[TopicNode]:
        """Retrieves all topic nodes belonging to a course, with optional track filters."""
        ...

    # --- B. Multi-Hop Graph Traversals (Executed 100% inside Active Backend) ---
    def get_hard_parents(self, topic_id: str) -> List[str]:
        """Returns direct incoming prerequisite topic IDs with strength == 'hard'."""
        ...

    def get_postrequisites(self, topic_id: str, max_hops: Optional[int] = None) -> List[str]:
        """Returns downstream dependent topic IDs up to max_hops (or unbounded)."""
        ...

    def get_encompassed_ancestors(
        self, topic_id: str, max_hops: int = 3
    ) -> List[WeightedEncompassingPath]:
        """
        Returns weighted backward FIRe paths from topic_id down encompassed skills.
        Compound weight = product of fire_weight along path.
        """
        ...

    def get_encompassing_descendants(
        self, topic_id: str, max_hops: int = 3
    ) -> List[WeightedEncompassingPath]:
        """
        Returns weighted upward FIRe paths from topic_id to encompassing descendants.
        Used for summer slide penalty trickle-up.
        """
        ...

    def get_local_neighborhood(self, topic_id: str) -> LocalNeighborhood:
        """Retrieves the local graph neighborhood (prereqs, key prereqs, encompassings, siblings)."""
        ...

    def compute_knowledge_frontier(self, student_id: str, course_id: str) -> List[TopicNode]:
        """
        Computes all AVAILABLE topic nodes in course_id for student_id where 100%
        of incoming hard prerequisites are MASTERED or MASTERED_FLOOR.
        """
        ...

    def compute_compressed_review_cover(self, student_id: str) -> ReviewCompressionPlan:
        """
        Selects the minimal set of candidate tasks that cover all due reviews
        via multi-hop FIRe flow, plus look-ahead fending for upcoming reviews.
        """
        ...

    def get_compressed_diagnostic_cover(self, course_id: str) -> List[str]:
        """
        Returns the compressed pre-diagnostic candidate topic IDs covering the
        subgraph where every topic has an ancestor and progeny within <= 3 hops.
        """
        ...

    # --- C. Content Shell Registry ---
    def get_kp_content_shell(self, kp_id: str) -> KPContentShell:
        """Retrieves content shell for a specific Knowledge Point."""
        ...

    def upsert_kp_content_shell(self, shell: KPContentShell) -> None:
        """Upserts a content shell into the active backend."""
        ...

    # --- D. Transactional Student State & Telemetry ---
    def get_student_profile(self, student_id: str) -> StudentProfile:
        """Retrieves student profile. Raises KeyError if student does not exist."""
        ...

    def list_student_profiles(self, course_id: Optional[str] = None) -> List[StudentProfile]:
        """Retrieves all registered student profiles, optionally filtered by enrolled course."""
        ...

    def upsert_student_profile(self, profile: StudentProfile) -> None:
        """Creates or updates a student profile."""
        ...

    def get_student_topic_states(
        self, student_id: str, topic_ids: Optional[List[str]] = None
    ) -> Dict[str, StudentTopicState]:
        """Retrieves topic states for a student, optionally filtered by topic_ids."""
        ...

    def upsert_student_topic_state(self, state: StudentTopicState) -> None:
        """Creates or updates an individual student topic state."""
        ...

    def apply_submission_and_propagate_fire(
        self,
        attempt: InteractionAttempt,
        updated_states: List[StudentTopicState],
        xp_entry: Optional[XPLedgerEntry] = None,
    ) -> None:
        """
        Atomically records an attempt, mutates updated student topic states,
        and appends an XP ledger entry in a single transaction.
        """
        ...

    def get_dashboard_menu(self, student_id: str) -> List[DashboardTaskItem]:
        """
        Retrieves the student's sticky 'Math Buffet' task dashboard, canonically
        ordered by (-rank_score, assigned_at, topic_id).
        """
        ...

    def save_dashboard_menu(self, student_id: str, items: List[DashboardTaskItem]) -> None:
        """Saves the student's sticky task dashboard menu."""
        ...

    def record_attempt(self, attempt: InteractionAttempt) -> None:
        """Appends an interaction attempt telemetry record."""
        ...

    def append_xp_ledger(self, entry: XPLedgerEntry) -> None:
        """Appends an XP ledger transaction."""
        ...

    def get_xp_ledger_entries(
        self,
        student_id: str,
        limit: int = 50,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> List[XPLedgerEntry]:
        """Returns recent XP ledger entries for an individual student, with optional date filtering."""
        ...

    # --- E. Tier 7 Weekly Leagues Persistence ---
    def save_league_cohort(self, cohort: LeagueCohort) -> None:
        """Creates or updates a weekly league cohort."""
        ...

    def get_league_cohort(self, cohort_id: str) -> Optional[LeagueCohort]:
        """Retrieves a league cohort by ID."""
        ...

    def get_student_league_cohort(self, student_id: str, week_key: str) -> Optional[LeagueCohort]:
        """Retrieves the cohort a student belongs to for a given ISO week."""
        ...

    def list_league_cohorts(self, week_key: Optional[str] = None) -> List[LeagueCohort]:
        """Lists league cohorts, optionally filtered by week_key."""
        ...

    # --- F. Tier 8 Accountability, Habit & Content Remediation Persistence ---
    def save_parent_reward(self, reward: ParentCustomReward) -> None:
        """Creates or updates a custom parent reward."""
        ...

    def get_parent_rewards(self, student_id: str) -> List[ParentCustomReward]:
        """Returns all parent custom rewards for a student."""
        ...

    def update_parent_reward_status(
        self, reward_id: str, status: str, claimed_at: Optional[datetime] = None
    ) -> None:
        """Updates status of a parent reward ('PENDING' -> 'UNLOCKED' -> 'CLAIMED')."""
        ...

    def save_study_habit_schedule(self, schedule: StudyHabitSchedule) -> None:
        """Saves or updates a student's daily study habit window schedule."""
        ...

    def get_study_habit_schedule(self, student_id: str) -> Optional[StudyHabitSchedule]:
        """Retrieves a student's study habit window schedule."""
        ...

    def record_adult_resit_alert(self, alert: AdultReSitAlert) -> None:
        """Records an advisory adult re-sit alert."""
        ...

    def list_adult_resit_alerts(
        self, student_id: str, unacknowledged_only: bool = False
    ) -> List[AdultReSitAlert]:
        """Lists adult re-sit alerts for a student."""
        ...

    def acknowledge_adult_resit_alert(
        self, alert_id: str, acknowledged_at: Optional[datetime] = None
    ) -> None:
        """Marks an adult re-sit alert as acknowledged by parent or coach."""
        ...

    def record_content_flag(self, flag: ContentRemediationFlag) -> None:
        """Records an in-task student or coach content question/explanation flag."""
        ...

    def list_content_flags(
        self, topic_id: Optional[str] = None, status: Optional[str] = None
    ) -> List[ContentRemediationFlag]:
        """Lists content flags, optionally filtered by topic_id or status."""
        ...

    def update_content_flag_status(self, flag_id: str, status: str) -> None:
        """Updates status of a content flag ('OPEN', 'UNDER_REVIEW', 'RESOLVED')."""
        ...

    def get_interaction_attempts(
        self,
        student_id: Optional[str] = None,
        topic_id: Optional[str] = None,
        kp_id: Optional[str] = None,
        limit: int = 1000,
    ) -> List[InteractionAttempt]:
        """Queries interaction attempts matching student_id, topic_id, and/or kp_id."""
        ...


def get_storage_adapter(backend_type: Optional[str] = None, **kwargs: Any) -> TrellisStoragePort:
    """
    Runtime Backend Factory for Trellis.
    Selects standalone adapter based on DB_BACKEND environment variable or argument:
      - 'memory': MemoryReferenceAdapter (SQLite in-memory + NetworkX)
      - 'relational': StandaloneRelationalAdapter (SQLite / PostgreSQL)
      - 'graph': StandaloneGraphAdapter (Neo4j / Memgraph)
    """
    target_backend = (backend_type or os.getenv("DB_BACKEND", "memory")).lower().strip()

    if target_backend == "memory":
        from trellis.adapters.db.memory_reference_adapter import MemoryReferenceAdapter
        return MemoryReferenceAdapter(**kwargs)
    elif target_backend == "relational":
        from trellis.adapters.db.standalone_relational_adapter import StandaloneRelationalAdapter
        return StandaloneRelationalAdapter(**kwargs)
    elif target_backend == "graph":
        from trellis.adapters.db.standalone_graph_adapter import StandaloneGraphAdapter
        return StandaloneGraphAdapter(**kwargs)
    else:
        raise ValueError(
            f"Unsupported DB_BACKEND '{target_backend}'. Must be 'memory', 'relational', or 'graph'."
        )
