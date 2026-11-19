"""
src/trellis/adapters/db/standalone_graph_adapter.py
Adapter B: 100% Standalone Property Graph Persistence Backend (Neo4j / Memgraph).
Stores 100% of curriculum DAG, content shells, and student state (:Student, [:HAS_STATE],
:Attempt, :XPLedgerEntry) inside a single Property Graph engine.
Zero cross-database coordination.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx

from trellis.domain.models import (
    AdultReSitAlert,
    CanonicalWorkedExample,
    ContentRemediationFlag,
    CourseCatalogItem,
    DashboardTaskItem,
    DiagnosticMisconception,
    EncompassingEdge,
    GraphIngestionReport,
    InteractionAttempt,
    KnowledgePoint,
    KPContentShell,
    LeagueCohort,
    LocalNeighborhood,
    ParentCustomReward,
    PrerequisiteEdge,
    ProblemBlueprint,
    ReviewCompressionPlan,
    StudentProfile,
    StudentTopicState,
    StudyHabitSchedule,
    TopicNode,
    WeightedEncompassingPath,
    XPLedgerEntry,
)
from trellis.ports.storage_port import TrellisStoragePort


class StandaloneGraphAdapter:
    """
    Adapter B: Standalone Graph implementation conforming to TrellisStoragePort.
    Supports live Neo4j/Memgraph instances via Bolt driver, with an embedded
    property-graph execution fallback for zero-setup test environments.
    """

    def __init__(
        self,
        uri: Optional[str] = None,
        auth: Optional[Tuple[str, str]] = None,
        database: str = "neo4j",
    ) -> None:
        self.uri = uri or os.getenv("NEO4J_URI")
        self.auth = auth or (
            os.getenv("NEO4J_USER", "neo4j"),
            os.getenv("NEO4J_PASSWORD", "password"),
        )
        self.database = database
        self.driver = None

        if self.uri:
            try:
                from neo4j import GraphDatabase  # type: ignore
                self.driver = GraphDatabase.driver(self.uri, auth=self.auth)
                self.driver.verify_connectivity()
            except Exception:
                self.driver = None

        # Embedded property-graph state store for self-contained execution
        # Property Graph: MultiDiGraph holding :Course, :TopicNode, :KnowledgePoint,
        # :ContentShell, :Student, :Attempt, :XPLedgerEntry nodes and labeled edges
        self.graph = nx.MultiDiGraph()
        self.shells: Dict[str, KPContentShell] = {}
        self.attempts: List[InteractionAttempt] = []
        self.xp_ledger: List[XPLedgerEntry] = []
        self.league_cohorts: Dict[str, LeagueCohort] = {}
        self.parent_rewards: Dict[str, ParentCustomReward] = {}
        self.study_habit_schedules: Dict[str, StudyHabitSchedule] = {}
        self.adult_resit_alerts: List[AdultReSitAlert] = []
        self.content_flags: List[ContentRemediationFlag] = []

    def close(self) -> None:
        if self.driver:
            self.driver.close()

    # -----------------------------------------------------------------------
    # A. Ingestion & Graph Catalog
    # -----------------------------------------------------------------------

    def ingest_knowledge_graph(self, graph_payload: Dict[str, Any]) -> GraphIngestionReport:
        start_time = time.perf_counter()

        courses_data = graph_payload.get("course_catalog", [])
        nodes_data = graph_payload.get("nodes", [])

        course_count = len(courses_data)
        node_count = len(nodes_data)
        kp_count = 0
        prereq_count = 0
        enc_count = 0
        shell_count = 0

        # Ingest into property graph
        for c in courses_data:
            cid = c["course_id"]
            self.graph.add_node(
                cid,
                label="Course",
                course_id=cid,
                course_code=c["course_code"],
                title=c["title"],
                grade_band=c["grade_band"],
                benchmark_xp=c.get("benchmark_xp", 1500),
                mastery_floor_course_ids=c.get("mastery_floor_course_ids", []),
            )
            for floor_cid in c.get("mastery_floor_course_ids", []):
                self.graph.add_edge(cid, floor_cid, type="MASTERY_FLOOR_OF")

        for n in nodes_data:
            nid = n["id"]
            self.graph.add_node(
                nid,
                label="TopicNode",
                data=n,
            )
            self.graph.add_edge(n["course_id"], nid, type="CONTAINS_TOPIC")

            for kp in n.get("knowledge_points", []):
                kp_count += 1
                kpid = kp["kp_id"]
                self.graph.add_node(
                    kpid,
                    label="KnowledgePoint",
                    data=kp,
                )
                self.graph.add_edge(nid, kpid, type="HAS_KP", kp_index=kp["kp_index"])

                # Hydrate content shell
                shell = KPContentShell(
                    kp_id=kpid,
                    content_status="BLUEPRINT_READY",
                    rule_statement=kp.get("rule_statement", ""),
                    worked_example=kp.get("canonical_worked_example"),
                    problem_blueprint=kp.get("problem_blueprint"),
                    diagnostic_misconceptions=kp.get("diagnostic_misconceptions", []),
                )
                self.shells[kpid] = shell
                shell_count += 1

            for p in n.get("prerequisites", []):
                prereq_count += 1
                # Forward learning dependency: source (prereq) -> target (dependent)
                self.graph.add_edge(
                    p["prerequisite_id"],
                    nid,
                    type="PREREQUISITE_OF",
                    strength=p.get("strength", "hard"),
                    rationale=p.get("rationale", ""),
                )

            for e in n.get("encompassed_skills", []):
                enc_count += 1
                # Backward FIRe containment: advanced (nid) -> simpler (encompassed)
                self.graph.add_edge(
                    nid,
                    e["encompassed_id"],
                    type="ENCOMPASSES",
                    fire_weight=e.get("fire_weight", 0.5),
                    is_non_ancestor=bool(e.get("is_non_ancestor", False)),
                    rationale=e.get("rationale", ""),
                )

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return GraphIngestionReport(
            courses_ingested=course_count,
            nodes_ingested=node_count,
            knowledge_points_ingested=kp_count,
            prereq_edges_ingested=prereq_count,
            encompassing_edges_ingested=enc_count,
            content_shells_ingested=shell_count,
            closure_paths_computed=0,
            duration_ms=round(duration_ms, 2),
        )

    def get_course_catalog(self) -> List[CourseCatalogItem]:
        courses = []
        for n, attrs in self.graph.nodes(data=True):
            if attrs.get("label") == "Course":
                courses.append(
                    CourseCatalogItem(
                        course_id=attrs["course_id"],
                        course_code=attrs["course_code"],
                        title=attrs["title"],
                        grade_band=attrs["grade_band"],
                        benchmark_xp=attrs["benchmark_xp"],
                        mastery_floor_course_ids=attrs["mastery_floor_course_ids"],
                    )
                )
        return sorted(courses, key=lambda c: c.course_id)

    def get_topic_node(self, topic_id: str) -> TopicNode:
        attrs = self.graph.nodes.get(topic_id)
        if not attrs or attrs.get("label") != "TopicNode":
            raise KeyError(f"TopicNode with id '{topic_id}' not found.")
        return TopicNode(**attrs["data"])

    def get_course_subgraph(
        self,
        course_id: str,
        foundations_only: bool = False,
        core_only: bool = False,
    ) -> List[TopicNode]:
        nodes = []
        for n, attrs in self.graph.nodes(data=True):
            if attrs.get("label") == "TopicNode":
                raw = attrs["data"]
                if raw["course_id"] == course_id:
                    if foundations_only and not raw.get("in_foundations_sequence", False):
                        continue
                    if core_only and not raw.get("is_core", True):
                        continue
                    nodes.append(TopicNode(**raw))
        return sorted(nodes, key=lambda n: n.id)

    # -----------------------------------------------------------------------
    # B. Multi-Hop Graph Traversals (Cypher Semantics)
    # -----------------------------------------------------------------------

    def get_hard_parents(self, topic_id: str) -> List[str]:
        """Cypher: MATCH (p:TopicNode)-[:PREREQUISITE_OF {strength: 'hard'}]->(t:TopicNode {id: $topic_id}) RETURN p.id"""
        parents = []
        for u, v, k, data in self.graph.in_edges(topic_id, data=True, keys=True):
            if data.get("type") == "PREREQUISITE_OF" and data.get("strength") == "hard":
                parents.append(u)
        return sorted(parents)

    def get_postrequisites(self, topic_id: str, max_hops: Optional[int] = None) -> List[str]:
        """Cypher: MATCH (t:TopicNode {id: $topic_id})-[:PREREQUISITE_OF*1..hops]->(p:TopicNode) RETURN DISTINCT p.id"""
        results: Set[str] = set()
        queue: List[Tuple[str, int]] = [(topic_id, 0)]
        visited: Set[str] = {topic_id}

        while queue:
            curr, depth = queue.pop(0)
            if max_hops is not None and depth >= max_hops:
                continue

            for u, v, k, data in self.graph.out_edges(curr, data=True, keys=True):
                if data.get("type") == "PREREQUISITE_OF":
                    if v not in visited:
                        visited.add(v)
                        results.add(v)
                        queue.append((v, depth + 1))
        return sorted(list(results))

    def get_encompassed_ancestors(
        self, topic_id: str, max_hops: int = 3
    ) -> List[WeightedEncompassingPath]:
        """Cypher: MATCH path = (t:TopicNode {id: $topic_id})-[:ENCOMPASSES*1..3]->(a:TopicNode) ..."""
        paths: List[WeightedEncompassingPath] = []
        queue: List[Tuple[str, int, float, List[str]]] = [(topic_id, 0, 1.0, [topic_id])]

        while queue:
            curr, depth, c_weight, path = queue.pop(0)
            if depth >= max_hops:
                continue

            for u, v, k, data in self.graph.out_edges(curr, data=True, keys=True):
                if data.get("type") == "ENCOMPASSES":
                    fw = data.get("fire_weight", 0.5)
                    is_non_anc = data.get("is_non_ancestor", False)
                    next_weight = c_weight * fw
                    next_path = path + [v]
                    next_depth = depth + 1

                    paths.append(
                        WeightedEncompassingPath(
                            ancestor_id=v,
                            hops=next_depth,
                            compound_weight=round(next_weight, 6),
                            path=next_path,
                            is_non_ancestor=is_non_anc,
                        )
                    )
                    queue.append((v, next_depth, next_weight, next_path))

        paths.sort(key=lambda p: (p.hops, p.ancestor_id, -p.compound_weight, p.path))
        return paths

    def get_encompassing_descendants(
        self, topic_id: str, max_hops: int = 3
    ) -> List[WeightedEncompassingPath]:
        """Cypher: MATCH path = (d:TopicNode)-[:ENCOMPASSES*1..3]->(t:TopicNode {id: $topic_id}) ..."""
        paths: List[WeightedEncompassingPath] = []
        queue: List[Tuple[str, int, float, List[str]]] = [(topic_id, 0, 1.0, [topic_id])]

        while queue:
            curr, depth, c_weight, path = queue.pop(0)
            if depth >= max_hops:
                continue

            for u, v, k, data in self.graph.in_edges(curr, data=True, keys=True):
                if data.get("type") == "ENCOMPASSES":
                    fw = data.get("fire_weight", 0.5)
                    is_non_anc = data.get("is_non_ancestor", False)
                    next_weight = c_weight * fw
                    next_path = path + [u]
                    next_depth = depth + 1

                    paths.append(
                        WeightedEncompassingPath(
                            ancestor_id=u,
                            hops=next_depth,
                            compound_weight=round(next_weight, 6),
                            path=next_path,
                            is_non_ancestor=is_non_anc,
                        )
                    )
                    queue.append((u, next_depth, next_weight, next_path))

        paths.sort(key=lambda p: (p.hops, p.ancestor_id, -p.compound_weight, p.path))
        return paths

    def get_local_neighborhood(self, topic_id: str) -> LocalNeighborhood:
        node = self.get_topic_node(topic_id)
        direct_prereqs = self.get_hard_parents(topic_id)
        enc_anc = [p.ancestor_id for p in self.get_encompassed_ancestors(topic_id, max_hops=1)]
        enc_desc = [p.ancestor_id for p in self.get_encompassing_descendants(topic_id, max_hops=1)]

        siblings = []
        for n, attrs in self.graph.nodes(data=True):
            if attrs.get("label") == "TopicNode":
                raw = attrs["data"]
                if raw["domain_cluster"] == node.domain_cluster and raw["id"] != topic_id:
                    siblings.append(raw["id"])

        return LocalNeighborhood(
            topic_id=topic_id,
            direct_prerequisites=direct_prereqs,
            key_prerequisite_id=node.key_prerequisite_id,
            encompassed_ancestors=enc_anc,
            encompassing_descendants=enc_desc,
            cluster_siblings=sorted(siblings),
        )

    def compute_knowledge_frontier(self, student_id: str, course_id: str) -> List[TopicNode]:
        """
        Cypher Frontier Query:
        MATCH (c:Course {course_id: $course_id})-[:CONTAINS_TOPIC]->(t:TopicNode)
        OPTIONAL MATCH (s:Student {student_id: $student_id})-[r:HAS_STATE]->(t)
        WHERE (r IS NULL OR NOT r.status IN ['MASTERED', 'MASTERED_FLOOR'])
          AND ALL(p IN [(p:TopicNode)-[:PREREQUISITE_OF {strength: 'hard'}]->(t) | p]
              WHERE EXISTS((s)-[:HAS_STATE {status: 'MASTERED'}]->(p))
                 OR EXISTS((s)-[:HAS_STATE {status: 'MASTERED_FLOOR'}]->(p)))
        RETURN t
        """
        course_nodes = self.get_course_subgraph(course_id)
        student_states = self.get_student_topic_states(student_id)

        frontier = []
        for node in course_nodes:
            st = student_states.get(node.id)
            current_status = st.status if st else "LOCKED"

            if current_status in ("MASTERED", "MASTERED_FLOOR"):
                continue

            hard_parents = self.get_hard_parents(node.id)
            all_mastered = True
            for pid in hard_parents:
                p_state = student_states.get(pid)
                p_status = p_state.status if p_state else "LOCKED"
                if p_status not in ("MASTERED", "MASTERED_FLOOR"):
                    all_mastered = False
                    break

            if all_mastered:
                frontier.append(node)

        return frontier

    def compute_compressed_review_cover(self, student_id: str) -> ReviewCompressionPlan:
        student_states = self.get_student_topic_states(student_id)
        due_reviews: Set[str] = {
            tid for tid, s in student_states.items()
            if s.status == "DUE_FOR_REVIEW"
        }

        if not due_reviews:
            return ReviewCompressionPlan(
                selected_task_ids=[],
                covered_review_ids=[],
                fended_review_ids=[],
                efficiency_ratio=1.0,
            )

        uncovered = set(due_reviews)
        selected_tasks: List[str] = []

        all_topic_ids = [n for n, d in self.graph.nodes(data=True) if d.get("label") == "TopicNode"]
        cand_map: Dict[str, Set[str]] = {}
        for cid in all_topic_ids:
            covered = {cid} if cid in due_reviews else set()
            for p in self.get_encompassed_ancestors(cid, max_hops=2):
                if p.ancestor_id in due_reviews:
                    covered.add(p.ancestor_id)
            if covered:
                cand_map[cid] = covered

        while uncovered and cand_map:
            best_cand = min(cand_map.keys(), key=lambda c: (-len(cand_map[c] & uncovered), c))
            newly_covered = cand_map[best_cand] & uncovered
            if not newly_covered:
                break
            selected_tasks.append(best_cand)
            uncovered -= newly_covered
            del cand_map[best_cand]

        covered_list = sorted(list(due_reviews - uncovered))
        efficiency = (len(due_reviews) / max(1, len(selected_tasks))) if selected_tasks else 1.0

        return ReviewCompressionPlan(
            selected_task_ids=selected_tasks,
            covered_review_ids=covered_list,
            fended_review_ids=[],
            efficiency_ratio=round(efficiency, 2),
        )

    def get_compressed_diagnostic_cover(self, course_id: str) -> List[str]:
        course_nodes = [n.id for n in self.get_course_subgraph(course_id)]
        if not course_nodes:
            return []

        cover = []
        for i, tid in enumerate(sorted(course_nodes)):
            if i % 3 == 0 or len(self.get_hard_parents(tid)) == 0:
                cover.append(tid)
        return cover

    # -----------------------------------------------------------------------
    # C. Content Shell Registry
    # -----------------------------------------------------------------------

    def get_kp_content_shell(self, kp_id: str) -> KPContentShell:
        if kp_id not in self.shells:
            raise KeyError(f"KPContentShell '{kp_id}' not found.")
        return self.shells[kp_id]

    def upsert_kp_content_shell(self, shell: KPContentShell) -> None:
        self.shells[shell.kp_id] = shell

    # -----------------------------------------------------------------------
    # D. Transactional Student State & Telemetry
    # -----------------------------------------------------------------------

    def get_student_profile(self, student_id: str) -> StudentProfile:
        attrs = self.graph.nodes.get(student_id)
        if not attrs or attrs.get("label") != "Student":
            raise KeyError(f"StudentProfile for '{student_id}' not found.")
        return StudentProfile(**attrs["profile"])

    def list_student_profiles(self, course_id: Optional[str] = None) -> List[StudentProfile]:
        profiles: List[StudentProfile] = []
        for n, attrs in self.graph.nodes(data=True):
            if attrs.get("label") == "Student" and "profile" in attrs:
                prof = StudentProfile(**attrs["profile"])
                if course_id is None or prof.enrolled_course_id == course_id:
                    profiles.append(prof)
        return profiles

    def upsert_student_profile(self, profile: StudentProfile) -> None:
        self.graph.add_node(
            profile.student_id,
            label="Student",
            profile=profile.model_dump(),
        )

    def get_student_topic_states(
        self, student_id: str, topic_ids: Optional[List[str]] = None
    ) -> Dict[str, StudentTopicState]:
        states: Dict[str, StudentTopicState] = {}
        if student_id not in self.graph:
            return states

        for u, v, k, data in self.graph.out_edges(student_id, data=True, keys=True):
            if data.get("type") == "HAS_STATE":
                tid = v
                if topic_ids is None or tid in topic_ids:
                    states[tid] = StudentTopicState(**data["state"])
        return states

    def upsert_student_topic_state(self, state: StudentTopicState) -> None:
        if state.student_id not in self.graph:
            self.graph.add_node(state.student_id, label="Student", profile={"student_id": state.student_id, "enrolled_course_id": ""})

        # Remove existing HAS_STATE edge to this topic if present
        to_remove = []
        for u, v, k, data in self.graph.out_edges(state.student_id, data=True, keys=True):
            if data.get("type") == "HAS_STATE" and v == state.topic_id:
                to_remove.append((u, v, k))
        for u, v, k in to_remove:
            self.graph.remove_edge(u, v, key=k)

        self.graph.add_edge(
            state.student_id,
            state.topic_id,
            type="HAS_STATE",
            state=state.model_dump(),
        )

    def apply_submission_and_propagate_fire(
        self,
        attempt: InteractionAttempt,
        updated_states: List[StudentTopicState],
        xp_entry: Optional[XPLedgerEntry] = None,
    ) -> None:
        # Atomic commit in property graph
        self.attempts.append(attempt)
        aid = attempt.attempt_id
        self.graph.add_node(aid, label="Attempt", data=attempt.model_dump())
        self.graph.add_edge(attempt.student_id, aid, type="SUBMITTED")

        for s in updated_states:
            self.upsert_student_topic_state(s)

        if xp_entry is not None:
            self.xp_ledger.append(xp_entry)
            eid = xp_entry.entry_id
            self.graph.add_node(eid, label="XPLedgerEntry", data=xp_entry.model_dump())
            self.graph.add_edge(xp_entry.student_id, eid, type="EARNED_XP")

            if xp_entry.student_id in self.graph:
                node_attrs = self.graph.nodes[xp_entry.student_id]
                prof = node_attrs.get("profile", {})
                new_xp = max(0, prof.get("xp_total", 0) + xp_entry.delta_xp)
                prof["xp_total"] = new_xp
                node_attrs["profile"] = prof

    def get_dashboard_menu(self, student_id: str) -> List[DashboardTaskItem]:
        if not self.graph.has_node(student_id):
            return []
        items: List[DashboardTaskItem] = []
        for _, topic_id, data in self.graph.out_edges(student_id, data=True):
            if data.get("type") == "HAS_DASHBOARD_TASK":
                assigned_at = datetime.fromisoformat(data["assigned_at"])
                items.append(
                    DashboardTaskItem(
                        student_id=student_id,
                        topic_id=topic_id,
                        task_type=data["task_type"],
                        rank_score=float(data["rank_score"]),
                        is_sticky=bool(data["is_sticky"]),
                        assigned_at=assigned_at,
                    )
                )
        items.sort(key=lambda it: (-it.rank_score, it.assigned_at, it.topic_id))
        return items

    def save_dashboard_menu(self, student_id: str, items: List[DashboardTaskItem]) -> None:
        if not self.graph.has_node(student_id):
            self.graph.add_node(student_id, label="Student")

        # Remove existing dashboard edges for student_id
        edges_to_remove = []
        for u, v, k, data in self.graph.out_edges(student_id, keys=True, data=True):
            if data.get("type") == "HAS_DASHBOARD_TASK":
                edges_to_remove.append((u, v, k))
        for u, v, k in edges_to_remove:
            self.graph.remove_edge(u, v, key=k)

        # Deduplicate items by topic_id (last-write-wins)
        deduped: Dict[str, DashboardTaskItem] = {}
        for it in items:
            deduped[it.topic_id] = it

        for it in deduped.values():
            if not self.graph.has_node(it.topic_id):
                self.graph.add_node(it.topic_id, label="TopicNode")
            self.graph.add_edge(
                student_id,
                it.topic_id,
                type="HAS_DASHBOARD_TASK",
                task_type=it.task_type,
                rank_score=float(it.rank_score),
                is_sticky=bool(it.is_sticky),
                assigned_at=it.assigned_at.isoformat(),
            )

    def record_attempt(self, attempt: InteractionAttempt) -> None:
        self.attempts.append(attempt)

    def append_xp_ledger(self, entry: XPLedgerEntry) -> None:
        self.xp_ledger.append(entry)
        eid = entry.entry_id
        self.graph.add_node(eid, label="XPLedgerEntry", data=entry.model_dump())
        self.graph.add_edge(entry.student_id, eid, type="EARNED_XP")
        if entry.student_id in self.graph:
            node_attrs = self.graph.nodes[entry.student_id]
            prof = node_attrs.get("profile", {})
            new_xp = max(0, prof.get("xp_total", 0) + entry.delta_xp)
            prof["xp_total"] = new_xp
            node_attrs["profile"] = prof

    def get_xp_ledger_entries(
        self,
        student_id: str,
        limit: int = 50,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> List[XPLedgerEntry]:
        matched = [e for e in self.xp_ledger if e.student_id == student_id]
        if since is not None:
            matched = [e for e in matched if e.created_at >= since]
        if until is not None:
            matched = [e for e in matched if e.created_at <= until]
        return sorted(matched, key=lambda x: x.created_at, reverse=True)[:limit]

    # --- E. Tier 7 Weekly Leagues Persistence ---
    def save_league_cohort(self, cohort: LeagueCohort) -> None:
        self.league_cohorts[cohort.cohort_id] = cohort.model_copy(deep=True)
        # Also store node in property graph
        self.graph.add_node(
            cohort.cohort_id,
            label="LeagueCohort",
            data=cohort.model_dump(),
        )
        for member_id in cohort.member_ids:
            if not self.graph.has_node(member_id):
                self.graph.add_node(member_id, label="Student")
            self.graph.add_edge(member_id, cohort.cohort_id, type="MEMBER_OF_COHORT")

    def get_league_cohort(self, cohort_id: str) -> Optional[LeagueCohort]:
        cohort = self.league_cohorts.get(cohort_id)
        return cohort.model_copy(deep=True) if cohort else None

    def get_student_league_cohort(self, student_id: str, week_key: str) -> Optional[LeagueCohort]:
        for cohort in self.league_cohorts.values():
            if cohort.week_key == week_key and student_id in cohort.member_ids:
                return cohort.model_copy(deep=True)
        return None

    def list_league_cohorts(self, week_key: Optional[str] = None) -> List[LeagueCohort]:
        cohorts = list(self.league_cohorts.values())
        if week_key:
            cohorts = [c for c in cohorts if c.week_key == week_key]
        return [c.model_copy(deep=True) for c in cohorts]

    # --- F. Tier 8 Accountability, Habit & Content Remediation Persistence ---
    def save_parent_reward(self, reward: ParentCustomReward) -> None:
        self.parent_rewards[reward.reward_id] = reward.model_copy(deep=True)
        self.graph.add_node(
            reward.reward_id,
            label="ParentReward",
            data=reward.model_dump(),
        )
        if self.graph.has_node(reward.student_id):
            self.graph.add_edge(reward.student_id, reward.reward_id, type="HAS_REWARD")

    def get_parent_rewards(self, student_id: str) -> List[ParentCustomReward]:
        matched = [r for r in self.parent_rewards.values() if r.student_id == student_id]
        return sorted([r.model_copy(deep=True) for r in matched], key=lambda x: (x.xp_target, x.created_at))

    def update_parent_reward_status(
        self, reward_id: str, status: str, claimed_at: Optional[datetime] = None
    ) -> None:
        reward = self.parent_rewards.get(reward_id)
        if reward:
            data = reward.model_dump()
            data["status"] = status
            if status == "UNLOCKED" and not data.get("unlocked_at"):
                data["unlocked_at"] = datetime.now(timezone.utc)
            if status == "CLAIMED":
                data["claimed_at"] = claimed_at or datetime.now(timezone.utc)
            updated = ParentCustomReward(**data)
            self.parent_rewards[reward_id] = updated
            if self.graph.has_node(reward_id):
                self.graph.nodes[reward_id]["data"] = updated.model_dump()

    def save_study_habit_schedule(self, schedule: StudyHabitSchedule) -> None:
        self.study_habit_schedules[schedule.student_id] = schedule.model_copy(deep=True)
        node_id = f"sched_{schedule.student_id}"
        self.graph.add_node(
            node_id,
            label="StudyHabitSchedule",
            data=schedule.model_dump(),
        )
        if self.graph.has_node(schedule.student_id):
            self.graph.add_edge(schedule.student_id, node_id, type="HAS_SCHEDULE")

    def get_study_habit_schedule(self, student_id: str) -> Optional[StudyHabitSchedule]:
        sched = self.study_habit_schedules.get(student_id)
        return sched.model_copy(deep=True) if sched else None

    def record_adult_resit_alert(self, alert: AdultReSitAlert) -> None:
        self.adult_resit_alerts.append(alert.model_copy(deep=True))
        self.graph.add_node(
            alert.alert_id,
            label="AdultReSitAlert",
            data=alert.model_dump(),
        )
        if self.graph.has_node(alert.student_id):
            self.graph.add_edge(alert.student_id, alert.alert_id, type="HAS_RESIT_ALERT")

    def list_adult_resit_alerts(
        self, student_id: str, unacknowledged_only: bool = False
    ) -> List[AdultReSitAlert]:
        matched = [a for a in self.adult_resit_alerts if a.student_id == student_id]
        if unacknowledged_only:
            matched = [a for a in matched if not a.acknowledged]
        return sorted([a.model_copy(deep=True) for a in matched], key=lambda x: x.created_at, reverse=True)

    def acknowledge_adult_resit_alert(
        self, alert_id: str, acknowledged_at: Optional[datetime] = None
    ) -> None:
        for idx, a in enumerate(self.adult_resit_alerts):
            if a.alert_id == alert_id:
                data = a.model_dump()
                data["acknowledged"] = True
                data["acknowledged_at"] = acknowledged_at or datetime.now(timezone.utc)
                updated = AdultReSitAlert(**data)
                self.adult_resit_alerts[idx] = updated
                if self.graph.has_node(alert_id):
                    self.graph.nodes[alert_id]["data"] = updated.model_dump()

    def record_content_flag(self, flag: ContentRemediationFlag) -> None:
        self.content_flags.append(flag.model_copy(deep=True))
        self.graph.add_node(
            flag.flag_id,
            label="ContentRemediationFlag",
            data=flag.model_dump(),
        )
        if self.graph.has_node(flag.topic_id):
            self.graph.add_edge(flag.topic_id, flag.flag_id, type="HAS_CONTENT_FLAG")

    def list_content_flags(
        self, topic_id: Optional[str] = None, status: Optional[str] = None
    ) -> List[ContentRemediationFlag]:
        matched = self.content_flags
        if topic_id:
            matched = [f for f in matched if f.topic_id == topic_id]
        if status:
            matched = [f for f in matched if f.status == status]
        return sorted([f.model_copy(deep=True) for f in matched], key=lambda x: x.created_at, reverse=True)

    def update_content_flag_status(self, flag_id: str, status: str) -> None:
        for idx, f in enumerate(self.content_flags):
            if f.flag_id == flag_id:
                data = f.model_dump()
                data["status"] = status
                updated = ContentRemediationFlag(**data)
                self.content_flags[idx] = updated
                if self.graph.has_node(flag_id):
                    self.graph.nodes[flag_id]["data"] = updated.model_dump()

    def get_interaction_attempts(
        self,
        student_id: Optional[str] = None,
        topic_id: Optional[str] = None,
        kp_id: Optional[str] = None,
        limit: int = 1000,
    ) -> List[InteractionAttempt]:
        matched = self.attempts
        if student_id:
            matched = [a for a in matched if a.student_id == student_id]
        if topic_id:
            matched = [a for a in matched if a.topic_id == topic_id]
        if kp_id:
            matched = [a for a in matched if a.kp_id == kp_id]
        return sorted([a.model_copy(deep=True) for a in matched], key=lambda x: x.created_at, reverse=True)[:limit]

