"""
src/trellis/engine/frontier_service.py
Tier 3.1: Knowledge Frontier Calculation & Foundational Momentum Sequencer (FR-3.1).

Implements:
  - Prerequisite Gating Invariant: Unlocks LOCKED -> AVAILABLE strictly when 100%
    of incoming hard prerequisites evaluate to MASTERED or MASTERED_FLOOR.
    Automatically re-locks AVAILABLE -> LOCKED if upstream conditional credit is revoked.
  - Foundational Momentum Sequencer: Prioritizes AVAILABLE topics within the
    student's enrolled course before dropping down to missing lower-grade
    foundational remediation nodes, building early student momentum and self-efficacy.
  - Seamless FR-3.2 Integration: Initializes newly unlocked StudentTopicState records
    with Local Neighborhood ability mean, calibrated learning speed in [0.25, 2.50],
    and preventative remediation gating (allow_implicit_fire_credit = speed >= 1.0).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set
from pydantic import BaseModel, Field

from trellis.domain.models import StudentTopicState, TopicNode
from trellis.engine.topology_service import TopologyService
from trellis.ports.storage_port import TrellisStoragePort


class FrontierSequencingResult(BaseModel):
    """
    Structured outcome of the Foundational Momentum Sequencer.
    Enrolled course topics are sequenced ahead of lower-grade foundational gaps.
    """
    student_id: str
    enrolled_course_id: str
    enrolled_frontier_nodes: List[TopicNode] = Field(default_factory=list)
    foundational_gap_nodes: List[TopicNode] = Field(default_factory=list)
    prioritized_sequence: List[TopicNode] = Field(default_factory=list)
    has_enrolled_momentum: bool = Field(
        default=False,
        description="True if student has at least 1 immediately available topic in enrolled course",
    )
    total_available_count: int = Field(default=0, ge=0)


class FrontierService:
    """
    Tier 3.1 Knowledge Frontier and Foundational Momentum Domain Service.
    Interacts with persistence exclusively through TrellisStoragePort.
    Zero raw queries or direct database access permitted.
    """

    def __init__(
        self,
        storage: TrellisStoragePort,
        topology_service: Optional[TopologyService] = None,
    ) -> None:
        self.storage = storage
        self.topology = topology_service or TopologyService(storage)

    # -----------------------------------------------------------------------
    # Internal Ability & Speed Initialization Helper (FR-3.1 <-> FR-3.2 Seam)
    # -----------------------------------------------------------------------

    def _build_unlocked_state(
        self,
        student_id: str,
        topic_id: str,
        base_difficulty: float,
        existing_state: Optional[StudentTopicState] = None,
    ) -> StudentTopicState:
        """
        Constructs or transitions a StudentTopicState to AVAILABLE, initializing
        student_ability from the Local Neighborhood mean and calibrating speed
        when no prior practice history exists on the node.
        """
        diff = max(0.1, float(base_difficulty))

        if existing_state is not None:
            # Preserve existing ability/speed if already calibrated
            speed = max(0.25, min(2.50, round(existing_state.student_ability / diff, 4)))
            return existing_state.model_copy(
                update={
                    "status": "AVAILABLE",
                    "topic_difficulty": base_difficulty,
                    "speed": speed,
                    "allow_implicit_fire_credit": speed >= 1.0,
                }
            )

        init_ability = self._estimate_neighborhood_ability(student_id, topic_id)
        speed = max(0.25, min(2.50, round(init_ability / diff, 4)))
        return StudentTopicState(
            student_id=student_id,
            topic_id=topic_id,
            status="AVAILABLE",
            student_ability=init_ability,
            topic_difficulty=base_difficulty,
            speed=speed,
            allow_implicit_fire_credit=speed >= 1.0,
        )

    def _estimate_neighborhood_ability(self, student_id: str, topic_id: str) -> float:
        """
        Predicts initial student ability for topic_id from practiced neighbors in
        its Local Neighborhood (prerequisites, key prerequisite, encompassings, siblings).
        """
        try:
            neighborhood = self.storage.get_local_neighborhood(topic_id)
        except Exception:
            return 1.0

        neighbor_ids: Set[str] = set()
        neighbor_ids.update(neighborhood.direct_prerequisites)
        if neighborhood.key_prerequisite_id:
            neighbor_ids.add(neighborhood.key_prerequisite_id)
        neighbor_ids.update(neighborhood.encompassed_ancestors)
        neighbor_ids.update(neighborhood.encompassing_descendants)
        neighbor_ids.update(neighborhood.cluster_siblings)
        neighbor_ids.discard(topic_id)

        if not neighbor_ids:
            return 1.0

        neighbor_states = self.storage.get_student_topic_states(
            student_id, list(neighbor_ids)
        )
        abilities = [
            st.student_ability
            for st in neighbor_states.values()
            if st.student_ability is not None
            and st.student_ability > 0.0
            and (
                st.status in ("MASTERED", "MASTERED_FLOOR", "DUE_FOR_REVIEW", "IN_PROGRESS", "TIER_3_REMEDIATION")
                or abs(st.student_ability - 1.0) > 1e-6
            )
        ]

        if not abilities:
            return 1.0

        mean_ability = sum(abilities) / len(abilities)
        return max(0.1, min(3.0, round(mean_ability, 4)))

    # -----------------------------------------------------------------------
    # FR-3.1: Deterministic Knowledge Frontier Calculation
    # -----------------------------------------------------------------------

    def is_topic_unlocked(
        self,
        student_id: str,
        topic_id: str,
        include_due_reviews: bool = False,
    ) -> bool:
        """
        Determines if all incoming hard prerequisites for topic_id evaluate to
        MASTERED or MASTERED_FLOOR (or DUE_FOR_REVIEW if include_due_reviews=True)
        for the given student.
        A topic with zero incoming hard prerequisites is unlocked by default.
        """
        hard_parent_ids = self.storage.get_hard_parents(topic_id)
        if not hard_parent_ids:
            return True

        valid_statuses = (
            ("MASTERED", "MASTERED_FLOOR", "DUE_FOR_REVIEW")
            if include_due_reviews
            else ("MASTERED", "MASTERED_FLOOR")
        )
        parent_states = self.storage.get_student_topic_states(student_id, hard_parent_ids)
        for pid in hard_parent_ids:
            st = parent_states.get(pid)
            if not st or st.status not in valid_statuses:
                return False
        return True

    def compute_knowledge_frontier(
        self,
        student_id: str,
        course_id: Optional[str] = None,
        persist_state_transitions: bool = True,
        include_due_reviews: bool = False,
    ) -> List[TopicNode]:
        """
        Computes all AVAILABLE (or IN_PROGRESS) topic nodes within course_id.
        Transitions candidate topic states from LOCKED -> AVAILABLE strictly
        when 100% of incoming hard prerequisites are MASTERED or MASTERED_FLOOR
        (or DUE_FOR_REVIEW if include_due_reviews=True).
        Topics already MASTERED, MASTERED_FLOOR, or DUE_FOR_REVIEW are excluded
        from the unmastered learning frontier.
        """
        if course_id is None:
            profile = self.storage.get_student_profile(student_id)
            course_id = profile.enrolled_course_id

        course_nodes = self.storage.get_course_subgraph(course_id)
        candidate_ids = [n.id for n in course_nodes]
        student_states = self.storage.get_student_topic_states(student_id, candidate_ids)

        frontier_nodes: List[TopicNode] = []

        for node in course_nodes:
            st = student_states.get(node.id)
            current_status = st.status if st else "LOCKED"

            # Mastered and post-mastery review topics are not new frontier lessons
            if current_status in ("MASTERED", "MASTERED_FLOOR", "DUE_FOR_REVIEW"):
                continue

            # Verify hard prerequisite gating invariant
            if self.is_topic_unlocked(student_id, node.id, include_due_reviews=include_due_reviews):
                if current_status == "LOCKED":
                    if persist_state_transitions:
                        new_state = self._build_unlocked_state(
                            student_id=student_id,
                            topic_id=node.id,
                            base_difficulty=node.base_difficulty,
                            existing_state=st,
                        )
                        self.storage.upsert_student_topic_state(new_state)
                        student_states[node.id] = new_state
                frontier_nodes.append(node)
            elif current_status == "AVAILABLE" and persist_state_transitions and st is not None:
                # Upstream conditional completion was revoked -> re-lock topic
                # Invariant: Never re-lock an AVAILABLE node if its parents are in ("MASTERED", "MASTERED_FLOOR", "DUE_FOR_REVIEW")
                if not self.is_topic_unlocked(student_id, node.id, include_due_reviews=True):
                    relocked = st.model_copy(update={"status": "LOCKED"})
                    self.storage.upsert_student_topic_state(relocked)
                    student_states[node.id] = relocked

        # Deterministic ordering within course: core topics first, then grade_level_min, base_difficulty, id
        frontier_nodes.sort(
            key=lambda t: (not t.is_core, t.grade_level_min, t.base_difficulty, t.id)
        )
        return frontier_nodes

    # -----------------------------------------------------------------------
    # FR-3.1: Foundational Gap Discovery & Momentum Sequencer
    # -----------------------------------------------------------------------

    def get_available_foundational_gap_topics(
        self,
        student_id: str,
        enrolled_course_id: Optional[str] = None,
        persist_state_transitions: bool = True,
        include_due_reviews: bool = False,
    ) -> List[TopicNode]:
        """
        Identifies missing lower-grade foundational remediation nodes outside the
        enrolled course that belong to its prerequisite closure and are currently AVAILABLE.
        """
        if enrolled_course_id is None:
            profile = self.storage.get_student_profile(student_id)
            enrolled_course_id = profile.enrolled_course_id

        # Ancestor prerequisite subgraph: Ancestors(G_hard, enrolled_topics)
        ancestor_subgraph_ids = self.topology.get_course_ancestor_prerequisite_subgraph(
            enrolled_course_id
        )

        enrolled_topics = self.storage.get_course_subgraph(enrolled_course_id)
        enrolled_ids = {t.id for t in enrolled_topics}

        # Foundational gap candidates: topics strictly outside enrolled_course_id
        foundational_candidate_ids = sorted(
            tid for tid in ancestor_subgraph_ids if tid not in enrolled_ids
        )

        if not foundational_candidate_ids:
            return []

        student_states = self.storage.get_student_topic_states(
            student_id, foundational_candidate_ids
        )

        available_foundational_nodes: List[TopicNode] = []

        for tid in foundational_candidate_ids:
            st = student_states.get(tid)
            current_status = st.status if st else "LOCKED"

            if current_status in ("MASTERED", "MASTERED_FLOOR", "DUE_FOR_REVIEW"):
                continue

            # Batch check unlock BEFORE hydrating topic node from storage (huge performance gain)
            if self.is_topic_unlocked(student_id, tid, include_due_reviews=include_due_reviews):
                try:
                    node = self.storage.get_topic_node(tid)
                except KeyError:
                    continue

                if current_status == "LOCKED":
                    if persist_state_transitions:
                        new_state = self._build_unlocked_state(
                            student_id=student_id,
                            topic_id=node.id,
                            base_difficulty=node.base_difficulty,
                            existing_state=st,
                        )
                        self.storage.upsert_student_topic_state(new_state)
                        student_states[node.id] = new_state

                available_foundational_nodes.append(node)
            elif current_status == "AVAILABLE" and persist_state_transitions and st is not None:
                if not self.is_topic_unlocked(student_id, tid, include_due_reviews=True):
                    relocked = st.model_copy(update={"status": "LOCKED"})
                    self.storage.upsert_student_topic_state(relocked)
                    student_states[tid] = relocked

        # Sort foundational nodes: core first, grade_level_min ascending, base_difficulty ascending, id
        available_foundational_nodes.sort(
            key=lambda t: (not t.is_core, t.grade_level_min, t.base_difficulty, t.id)
        )
        return available_foundational_nodes

    def get_prioritized_frontier(
        self,
        student_id: str,
        enrolled_course_id: Optional[str] = None,
        persist_state_transitions: bool = True,
        include_due_reviews: bool = False,
    ) -> List[TopicNode]:
        """
        Foundational Momentum Sequencer:
        Prioritizes AVAILABLE topics *within* the enrolled course before dropping
        down to missing lower-grade foundational remediation nodes.
        If enrolled topics are available, they lead the queue to establish momentum.
        If all enrolled topics are locked behind foundational gaps, foundational gap
        nodes are served immediately to unblock the student.
        """
        result = self.get_sequenced_frontier_result(
            student_id=student_id,
            enrolled_course_id=enrolled_course_id,
            persist_state_transitions=persist_state_transitions,
            include_due_reviews=include_due_reviews,
        )
        return result.prioritized_sequence

    def get_sequenced_frontier_result(
        self,
        student_id: str,
        enrolled_course_id: Optional[str] = None,
        persist_state_transitions: bool = True,
        include_due_reviews: bool = False,
    ) -> FrontierSequencingResult:
        """
        Calculates the complete Foundational Momentum Sequencing Result with
        partitioned enrolled vs. foundational topics and diagnostic flags.
        """
        if enrolled_course_id is None:
            profile = self.storage.get_student_profile(student_id)
            enrolled_course_id = profile.enrolled_course_id

        enrolled_frontier = self.compute_knowledge_frontier(
            student_id=student_id,
            course_id=enrolled_course_id,
            persist_state_transitions=persist_state_transitions,
            include_due_reviews=include_due_reviews,
        )

        foundational_gaps = self.get_available_foundational_gap_topics(
            student_id=student_id,
            enrolled_course_id=enrolled_course_id,
            persist_state_transitions=persist_state_transitions,
            include_due_reviews=include_due_reviews,
        )

        # Enrolled topics lead the sequence; foundational gaps follow
        prioritized = list(enrolled_frontier) + list(foundational_gaps)
        has_momentum = len(enrolled_frontier) > 0

        return FrontierSequencingResult(
            student_id=student_id,
            enrolled_course_id=enrolled_course_id,
            enrolled_frontier_nodes=enrolled_frontier,
            foundational_gap_nodes=foundational_gaps,
            prioritized_sequence=prioritized,
            has_enrolled_momentum=has_momentum,
            total_available_count=len(prioritized),
        )

    def refresh_frontier_states(
        self,
        student_id: str,
        course_id: Optional[str] = None,
        include_due_reviews: bool = False,
    ) -> List[str]:
        """
        Scans all locked topics in course_id (and its ancestor prerequisites),
        unlocking any whose hard prerequisites have now been mastered.
        Returns the sorted list of topic IDs that were transitioned from LOCKED -> AVAILABLE.
        """
        if course_id is None:
            profile = self.storage.get_student_profile(student_id)
            course_id = profile.enrolled_course_id

        ancestor_subgraph_ids = sorted(
            self.topology.get_course_ancestor_prerequisite_subgraph(course_id)
        )
        student_states = self.storage.get_student_topic_states(
            student_id, ancestor_subgraph_ids
        )

        transitioned_ids: List[str] = []
        for tid in ancestor_subgraph_ids:
            st = student_states.get(tid)
            current_status = st.status if st else "LOCKED"

            if current_status == "LOCKED" and self.is_topic_unlocked(student_id, tid, include_due_reviews=include_due_reviews):
                try:
                    node = self.storage.get_topic_node(tid)
                    diff = node.base_difficulty
                except KeyError:
                    diff = 0.5

                new_state = self._build_unlocked_state(
                    student_id=student_id,
                    topic_id=tid,
                    base_difficulty=diff,
                    existing_state=st,
                )
                self.storage.upsert_student_topic_state(new_state)
                transitioned_ids.append(tid)

        return transitioned_ids
