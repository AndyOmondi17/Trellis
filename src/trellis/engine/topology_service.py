"""
src/trellis/engine/topology_service.py
Deterministic Curriculum Graph Topology, Course Tracks & Mastery Floor Service (FR-1.1 & FR-1.2).
Provides multi-hop dual-edge graph traversals, Core Ancestor Closure verification,
Foundations track filtering, and University Course Mastery Floor management.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from trellis.domain.models import (
    CourseCatalogItem,
    LocalNeighborhood,
    StudentTopicState,
    TopicNode,
    WeightedEncompassingPath,
)
from trellis.ports.storage_port import TrellisStoragePort


class TopologyService:
    """
    Curriculum Graph Topology & Track Domain Service.
    Interacts with persistence exclusively through TrellisStoragePort.
    Zero raw queries or direct database access permitted.
    """

    def __init__(self, storage: TrellisStoragePort) -> None:
        self.storage = storage

    # -----------------------------------------------------------------------
    # FR-1.1: Dual-Edge Graph Traversals (Prerequisites vs. Encompassings)
    # -----------------------------------------------------------------------

    def get_hard_parents(self, topic_id: str) -> List[TopicNode]:
        """
        Retrieves direct incoming prerequisite TopicNodes with strength == 'hard'.
        Hard prerequisites strictly gate unlock from LOCKED -> AVAILABLE.
        """
        parent_ids = self.storage.get_hard_parents(topic_id)
        nodes: List[TopicNode] = []
        for pid in parent_ids:
            try:
                nodes.append(self.storage.get_topic_node(pid))
            except KeyError:
                continue
        return nodes

    def get_hard_parent_ids(self, topic_id: str) -> List[str]:
        """Returns direct incoming prerequisite topic IDs with strength == 'hard'."""
        return self.storage.get_hard_parents(topic_id)

    def get_postrequisites(
        self, topic_id: str, hard_only: bool = True, max_hops: Optional[int] = None
    ) -> List[TopicNode]:
        """
        Retrieves downstream dependent TopicNodes requiring topic_id.
        Traverses downstream in G_prereq up to max_hops (or unbounded if None).
        """
        postreq_ids = self.storage.get_postrequisites(topic_id, max_hops=max_hops)
        nodes: List[TopicNode] = []
        for pid in postreq_ids:
            try:
                node = self.storage.get_topic_node(pid)
                if hard_only:
                    # Verify if topic_id is part of hard prerequisites of this node or its ancestors
                    # If max_hops == 1, check direct hard prerequisites
                    if max_hops == 1:
                        hard_pids = {p.prerequisite_id for p in node.prerequisites if p.strength == "hard"}
                        if topic_id not in hard_pids:
                            continue
                nodes.append(node)
            except KeyError:
                continue
        return nodes

    def get_postrequisite_ids(
        self, topic_id: str, max_hops: Optional[int] = None
    ) -> List[str]:
        """Returns downstream dependent topic IDs requiring topic_id."""
        return self.storage.get_postrequisites(topic_id, max_hops=max_hops)

    def get_encompassed_ancestors(
        self, topic_id: str, max_hops: int = 3
    ) -> List[WeightedEncompassingPath]:
        """
        Returns weighted backward FIRe paths from topic_id down encompassed skills (G_enc).
        Compound weight = product of fire_weight along path.
        Used for downward credit trickle-down when an advanced topic is passed.
        """
        return self.storage.get_encompassed_ancestors(topic_id, max_hops=max_hops)

    def get_encompassed_ancestor_nodes(
        self, topic_id: str, max_hops: int = 3
    ) -> List[Tuple[TopicNode, float]]:
        """
        Returns pairs of (ancestor_node, compound_weight) for all reachable
        encompassed skills within max_hops.
        """
        paths = self.get_encompassed_ancestors(topic_id, max_hops=max_hops)
        results: List[Tuple[TopicNode, float]] = []
        for path in paths:
            try:
                node = self.storage.get_topic_node(path.ancestor_id)
                results.append((node, path.compound_weight))
            except KeyError:
                continue
        return results

    def get_encompassing_descendants(
        self, topic_id: str, max_hops: int = 3
    ) -> List[WeightedEncompassingPath]:
        """
        Returns weighted upward FIRe paths from topic_id to encompassing descendants.
        Used for summer slide penalty trickle-up when a foundational topic is failed.
        """
        return self.storage.get_encompassing_descendants(topic_id, max_hops=max_hops)

    def get_encompassing_descendant_nodes(
        self, topic_id: str, max_hops: int = 3
    ) -> List[Tuple[TopicNode, float]]:
        """
        Returns pairs of (descendant_node, compound_weight) for all reachable
        encompassing descendants within max_hops.
        """
        paths = self.get_encompassing_descendants(topic_id, max_hops=max_hops)
        results: List[Tuple[TopicNode, float]] = []
        for path in paths:
            try:
                node = self.storage.get_topic_node(path.ancestor_id)
                results.append((node, path.compound_weight))
            except KeyError:
                continue
        return results

    def get_local_neighborhood(self, topic_id: str) -> LocalNeighborhood:
        """
        Retrieves the local graph neighborhood: direct prerequisites, key prerequisite ID,
        encompassed ancestors, encompassing descendants, and same-module domain_cluster siblings.
        """
        return self.storage.get_local_neighborhood(topic_id)

    # -----------------------------------------------------------------------
    # FR-1.2: Course Tracks, Core Prioritization & Mastery Floor Service
    # -----------------------------------------------------------------------

    def validate_core_ancestor_closure(
        self, course_id: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """
        Validates the Core Ancestor Closure Invariant:
          u in Ancestors(v) and is_core(v) ==> is_core(u)
        Every prerequisite of a core topic must also be designated as core.
        Returns a list of violations: [{"core_node": v_id, "non_core_prerequisite": u_id}].
        """
        violations: List[Dict[str, str]] = []

        if course_id:
            topics = self.storage.get_course_subgraph(course_id)
        else:
            catalog = self.storage.get_course_catalog()
            topics = []
            for course in catalog:
                topics.extend(self.storage.get_course_subgraph(course.course_id))

        for topic in topics:
            if not topic.is_core:
                continue
            for prereq in topic.prerequisites:
                try:
                    p_node = self.storage.get_topic_node(prereq.prerequisite_id)
                    if not p_node.is_core:
                        violations.append(
                            {
                                "core_node": topic.id,
                                "non_core_prerequisite": p_node.id,
                            }
                        )
                except KeyError:
                    # Prerequisite node missing from storage
                    violations.append(
                        {
                            "core_node": topic.id,
                            "non_core_prerequisite": prereq.prerequisite_id,
                        }
                    )
        return violations

    def prioritize_core_nodes(self, topic_nodes: List[TopicNode]) -> List[TopicNode]:
        """
        Prioritizes is_core == True topics ahead of supplemental topics (is_core == False).
        Preserves relative ordering within each group.
        """
        core_nodes = [t for t in topic_nodes if t.is_core]
        supplemental_nodes = [t for t in topic_nodes if not t.is_core]
        return core_nodes + supplemental_nodes

    def get_foundations_subgraph(
        self, course_id: Optional[str] = None
    ) -> List[TopicNode]:
        """
        Filters the curriculum subgraph to the accelerated Mathematical Foundations (MF) sequence
        where in_foundations_sequence == True.
        Prunes ~30% of decorative/recreational topics for rapid STEM readiness.
        """
        if course_id:
            return self.storage.get_course_subgraph(course_id, foundations_only=True)

        catalog = self.storage.get_course_catalog()
        foundations_nodes: List[TopicNode] = []
        for course in catalog:
            foundations_nodes.extend(
                self.storage.get_course_subgraph(course.course_id, foundations_only=True)
            )
        return foundations_nodes

    def apply_mastery_floors(
        self, student_id: str, enrolled_course_id: str
    ) -> List[StudentTopicState]:
        """
        Enforces Course-Level Mastery Floors.
        University and advanced modular courses specify mastery_floor_course_ids,
        automatically assuming and crediting baseline prerequisite courses to prevent
        diagnostic fatigue.
        Sets status = 'MASTERED_FLOOR', rep_num = 3.0 for all topics in floor courses.
        """
        catalog = self.storage.get_course_catalog()
        target_course: Optional[CourseCatalogItem] = None
        for c in catalog:
            if c.course_id == enrolled_course_id:
                target_course = c
                break

        if not target_course:
            raise KeyError(f"Course '{enrolled_course_id}' not found in course catalog.")

        floor_course_ids = target_course.mastery_floor_course_ids
        if not floor_course_ids:
            return []

        credited_states: List[StudentTopicState] = []

        for floor_id in floor_course_ids:
            floor_topics = self.storage.get_course_subgraph(floor_id)
            topic_ids = [t.id for t in floor_topics]
            existing_states = self.storage.get_student_topic_states(student_id, topic_ids)

            for topic in floor_topics:
                curr_state = existing_states.get(topic.id)
                # If student already mastered this topic explicitly, preserve MASTERED
                if curr_state and curr_state.status in ("MASTERED", "MASTERED_FLOOR"):
                    continue

                new_state = StudentTopicState(
                    student_id=student_id,
                    topic_id=topic.id,
                    status="MASTERED_FLOOR",
                    rep_num=3.0,
                    stability_interval_days=30.0,
                    memory_strength=1.0,
                    student_ability=1.0,
                    topic_difficulty=topic.base_difficulty,
                    speed=1.0,
                    allow_implicit_fire_credit=True,
                    is_conditionally_completed=False,
                )
                self.storage.upsert_student_topic_state(new_state)
                credited_states.append(new_state)

        return credited_states

    def get_course_ancestor_prerequisite_subgraph(
        self, enrolled_course_id: str
    ) -> Set[str]:
        """
        Computes the complete ancestor prerequisite subgraph for an enrolled course:
          Ancestors(G_hard, enrolled_course_topics) union enrolled_course_topics.
        University modular mathematics enforces Non-Holistic Targeted Remediation:
        remediation and diagnostic sweeps are strictly confined to this ancestor subgraph.
        """
        course_topics = self.storage.get_course_subgraph(enrolled_course_id)
        subgraph_ids: Set[str] = {t.id for t in course_topics}

        # BFS / DFS backwards along hard prerequisite edges
        queue: List[str] = list(subgraph_ids)
        visited: Set[str] = set(subgraph_ids)

        while queue:
            curr_id = queue.pop(0)
            hard_parent_ids = self.storage.get_hard_parents(curr_id)
            for pid in hard_parent_ids:
                if pid not in visited:
                    visited.add(pid)
                    subgraph_ids.add(pid)
                    queue.append(pid)

        return subgraph_ids

    def restrict_remediation_to_course_ancestors(
        self, enrolled_course_id: str, candidate_topic_ids: List[str]
    ) -> List[str]:
        """
        Filters candidate remediation or diagnostic topics to ensure they belong
        strictly to the ancestor prerequisite subgraph of the enrolled course.
        Prevents inappropriate regressions (e.g., college Linear Algebra students
        forced into high-school Geometry).
        """
        allowed_ids = self.get_course_ancestor_prerequisite_subgraph(enrolled_course_id)
        return [tid for tid in candidate_topic_ids if tid in allowed_ids]
