"""
src/trellis/adapters/db/memory_reference_adapter.py
Adapter C: In-Memory Reference Persistence Backend (SQLite in-memory + NetworkX).
Zero-setup reference backend for fast, deterministic unit testing and golden baseline comparison.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx

from trellis.domain.models import (
    AdultReSitAlert,
    CanonicalWorkedExample,
    CanonicalWorkedExampleStep,
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


class MemoryReferenceAdapter:
    """
    Adapter C: Zero-setup in-memory reference adapter conforming to TrellisStoragePort.
    Combines NetworkX DAG graphs with in-memory dictionaries for reference parity.
    """

    def __init__(self) -> None:
        self.courses: Dict[str, CourseCatalogItem] = {}
        self.nodes: Dict[str, TopicNode] = {}
        self.shells: Dict[str, KPContentShell] = {}

        # Graphs:
        # G_prereq: u (prereq) -> v (dependent)
        # G_hard: u (hard prereq) -> v (dependent)
        # G_enc: v (advanced) -> u (simpler encompassed skill)
        self.G_prereq: nx.DiGraph = nx.DiGraph()
        self.G_hard: nx.DiGraph = nx.DiGraph()
        self.G_enc: nx.DiGraph = nx.DiGraph()

        # Student State:
        self.profiles: Dict[str, StudentProfile] = {}
        self.topic_states: Dict[Tuple[str, str], StudentTopicState] = {}
        self.dashboards: Dict[str, List[DashboardTaskItem]] = {}
        self.attempts: List[InteractionAttempt] = []
        self.xp_ledger: List[XPLedgerEntry] = []
        self.league_cohorts: Dict[str, LeagueCohort] = {}
        self.parent_rewards: Dict[str, ParentCustomReward] = {}
        self.study_habit_schedules: Dict[str, StudyHabitSchedule] = {}
        self.adult_resit_alerts: List[AdultReSitAlert] = []
        self.content_flags: List[ContentRemediationFlag] = []

    # -----------------------------------------------------------------------
    # A. Ingestion & Graph Catalog
    # -----------------------------------------------------------------------

    def ingest_knowledge_graph(self, graph_payload: Dict[str, Any]) -> GraphIngestionReport:
        start_time = time.perf_counter()

        # 1. Ingest Courses
        for c_data in graph_payload.get("course_catalog", []):
            course = CourseCatalogItem(**c_data)
            self.courses[course.course_id] = course

        # 2. Ingest Nodes & Edges
        nodes_data = graph_payload.get("nodes", [])
        total_kps = 0
        prereq_edge_count = 0
        enc_edge_count = 0
        content_shells_count = 0

        for n_data in nodes_data:
            node = TopicNode(**n_data)
            self.nodes[node.id] = node
            self.G_prereq.add_node(node.id)
            self.G_hard.add_node(node.id)
            self.G_enc.add_node(node.id)

            total_kps += len(node.knowledge_points)

            # Auto-hydrate content shells if not already present
            for kp in node.knowledge_points:
                if kp.kp_id not in self.shells:
                    shell = KPContentShell(
                        kp_id=kp.kp_id,
                        content_status="BLUEPRINT_READY",
                        rule_statement=kp.rule_statement,
                        worked_example=kp.canonical_worked_example,
                        problem_blueprint=kp.problem_blueprint,
                        diagnostic_misconceptions=kp.diagnostic_misconceptions,
                    )
                    self.shells[kp.kp_id] = shell
                    content_shells_count += 1

            for p in node.prerequisites:
                self.G_prereq.add_edge(p.prerequisite_id, node.id, strength=p.strength, rationale=p.rationale)
                if p.strength == "hard":
                    self.G_hard.add_edge(p.prerequisite_id, node.id)
                prereq_edge_count += 1

            for e in node.encompassed_skills:
                self.G_enc.add_edge(
                    node.id,
                    e.encompassed_id,
                    fire_weight=e.fire_weight,
                    is_non_ancestor=e.is_non_ancestor,
                    rationale=e.rationale,
                )
                enc_edge_count += 1

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return GraphIngestionReport(
            courses_ingested=len(self.courses),
            nodes_ingested=len(self.nodes),
            knowledge_points_ingested=total_kps,
            prereq_edges_ingested=prereq_edge_count,
            encompassing_edges_ingested=enc_edge_count,
            content_shells_ingested=content_shells_count,
            closure_paths_computed=0,
            duration_ms=round(duration_ms, 2),
        )

    def get_course_catalog(self) -> List[CourseCatalogItem]:
        return list(self.courses.values())

    def get_topic_node(self, topic_id: str) -> TopicNode:
        if topic_id not in self.nodes:
            raise KeyError(f"TopicNode with id '{topic_id}' not found.")
        return self.nodes[topic_id]

    def get_course_subgraph(
        self,
        course_id: str,
        foundations_only: bool = False,
        core_only: bool = False,
    ) -> List[TopicNode]:
        results: List[TopicNode] = []
        for node in self.nodes.values():
            if node.course_id == course_id:
                if foundations_only and not node.in_foundations_sequence:
                    continue
                if core_only and not node.is_core:
                    continue
                results.append(node)
        return results

    # -----------------------------------------------------------------------
    # B. Multi-Hop Graph Traversals
    # -----------------------------------------------------------------------

    def get_hard_parents(self, topic_id: str) -> List[str]:
        if topic_id not in self.G_hard:
            return []
        return sorted(list(self.G_hard.predecessors(topic_id)))

    def get_postrequisites(self, topic_id: str, max_hops: Optional[int] = None) -> List[str]:
        if topic_id not in self.G_prereq:
            return []
        if max_hops is None:
            return sorted(list(nx.descendants(self.G_prereq, topic_id)))

        results: Set[str] = set()
        queue: List[Tuple[str, int]] = [(topic_id, 0)]
        visited: Set[str] = {topic_id}

        while queue:
            curr, depth = queue.pop(0)
            if depth >= max_hops:
                continue
            for succ in self.G_prereq.successors(curr):
                if succ not in visited:
                    visited.add(succ)
                    results.add(succ)
                    queue.append((succ, depth + 1))
        return sorted(list(results))

    def get_encompassed_ancestors(
        self, topic_id: str, max_hops: int = 3
    ) -> List[WeightedEncompassingPath]:
        if topic_id not in self.G_enc:
            return []

        paths: List[WeightedEncompassingPath] = []
        # BFS traversal up to max_hops along G_enc (v -> u)
        # queue item: (current_node, current_depth, current_compound_weight, current_path)
        queue: List[Tuple[str, int, float, List[str]]] = [(topic_id, 0, 1.0, [topic_id])]

        while queue:
            curr, depth, c_weight, path = queue.pop(0)
            if depth >= max_hops:
                continue

            for succ in self.G_enc.successors(curr):
                edge_data = self.G_enc.get_edge_data(curr, succ) or {}
                fw = edge_data.get("fire_weight", 0.5)
                is_non_anc = edge_data.get("is_non_ancestor", False)
                next_weight = c_weight * fw
                next_path = path + [succ]
                next_depth = depth + 1

                paths.append(
                    WeightedEncompassingPath(
                        ancestor_id=succ,
                        hops=next_depth,
                        compound_weight=round(next_weight, 6),
                        path=next_path,
                        is_non_ancestor=is_non_anc,
                    )
                )
                queue.append((succ, next_depth, next_weight, next_path))

        paths.sort(key=lambda p: (p.hops, p.ancestor_id, -p.compound_weight, p.path))
        return paths

    def get_encompassing_descendants(
        self, topic_id: str, max_hops: int = 3
    ) -> List[WeightedEncompassingPath]:
        if topic_id not in self.G_enc:
            return []

        paths: List[WeightedEncompassingPath] = []
        # Traverse backwards on G_enc (predecessors of topic_id)
        queue: List[Tuple[str, int, float, List[str]]] = [(topic_id, 0, 1.0, [topic_id])]

        while queue:
            curr, depth, c_weight, path = queue.pop(0)
            if depth >= max_hops:
                continue

            for pred in self.G_enc.predecessors(curr):
                edge_data = self.G_enc.get_edge_data(pred, curr) or {}
                fw = edge_data.get("fire_weight", 0.5)
                is_non_anc = edge_data.get("is_non_ancestor", False)
                next_weight = c_weight * fw
                next_path = path + [pred]
                next_depth = depth + 1

                paths.append(
                    WeightedEncompassingPath(
                        ancestor_id=pred,
                        hops=next_depth,
                        compound_weight=round(next_weight, 6),
                        path=next_path,
                        is_non_ancestor=is_non_anc,
                    )
                )
                queue.append((pred, next_depth, next_weight, next_path))

        paths.sort(key=lambda p: (p.hops, p.ancestor_id, -p.compound_weight, p.path))
        return paths

    def get_local_neighborhood(self, topic_id: str) -> LocalNeighborhood:
        if topic_id not in self.nodes:
            raise KeyError(f"Topic {topic_id} not found.")

        target = self.nodes[topic_id]
        direct_prereqs = self.get_hard_parents(topic_id)
        enc_anc = [p.ancestor_id for p in self.get_encompassed_ancestors(topic_id, max_hops=1)]
        enc_desc = [p.ancestor_id for p in self.get_encompassing_descendants(topic_id, max_hops=1)]

        siblings = [
            nid for nid, node in self.nodes.items()
            if node.domain_cluster == target.domain_cluster and nid != topic_id
        ]

        return LocalNeighborhood(
            topic_id=topic_id,
            direct_prerequisites=direct_prereqs,
            key_prerequisite_id=target.key_prerequisite_id,
            encompassed_ancestors=enc_anc,
            encompassing_descendants=enc_desc,
            cluster_siblings=sorted(siblings),
        )

    def compute_knowledge_frontier(self, student_id: str, course_id: str) -> List[TopicNode]:
        course_nodes = self.get_course_subgraph(course_id)
        student_states = self.get_student_topic_states(student_id)

        frontier: List[TopicNode] = []
        for node in course_nodes:
            state = student_states.get(node.id)
            current_status = state.status if state else "LOCKED"

            if current_status in ("MASTERED", "MASTERED_FLOOR"):
                continue

            hard_parents = self.get_hard_parents(node.id)
            all_parents_mastered = True
            for pid in hard_parents:
                p_state = student_states.get(pid)
                p_status = p_state.status if p_state else "LOCKED"
                if p_status not in ("MASTERED", "MASTERED_FLOOR"):
                    all_parents_mastered = False
                    break

            if all_parents_mastered:
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

        # Greedy Set-Cover over candidate topics whose encompassing ancestors cover due_reviews
        uncovered = set(due_reviews)
        selected_tasks: List[str] = []

        candidate_coverage: Dict[str, Set[str]] = {}
        for cand_id in self.nodes.keys():
            enc_paths = self.get_encompassed_ancestors(cand_id, max_hops=2)
            covered = {cand_id} if cand_id in due_reviews else set()
            for p in enc_paths:
                if p.ancestor_id in due_reviews:
                    covered.add(p.ancestor_id)
            if covered:
                candidate_coverage[cand_id] = covered

        while uncovered and candidate_coverage:
            # Pick candidate that covers maximum remaining uncovered reviews, deterministic tie-breaking
            best_cand = min(candidate_coverage.keys(), key=lambda c: (-len(candidate_coverage[c] & uncovered), c))
            newly_covered = candidate_coverage[best_cand] & uncovered
            if not newly_covered:
                break
            selected_tasks.append(best_cand)
            uncovered -= newly_covered
            del candidate_coverage[best_cand]

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

        # Return a representative sample covering the course DAG (~1 in every 3 nodes)
        cover: List[str] = []
        for i, nid in enumerate(sorted(course_nodes)):
            if i % 3 == 0 or len(self.get_hard_parents(nid)) == 0:
                cover.append(nid)
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
        if student_id not in self.profiles:
            raise KeyError(f"StudentProfile for '{student_id}' not found.")
        return self.profiles[student_id]

    def list_student_profiles(self, course_id: Optional[str] = None) -> List[StudentProfile]:
        profiles = list(self.profiles.values())
        if course_id:
            profiles = [p for p in profiles if p.enrolled_course_id == course_id]
        return [p.model_copy(deep=True) for p in profiles]

    def upsert_student_profile(self, profile: StudentProfile) -> None:
        self.profiles[profile.student_id] = profile

    def get_student_topic_states(
        self, student_id: str, topic_ids: Optional[List[str]] = None
    ) -> Dict[str, StudentTopicState]:
        results: Dict[str, StudentTopicState] = {}
        for (sid, tid), state in self.topic_states.items():
            if sid == student_id:
                if topic_ids is None or tid in topic_ids:
                    results[tid] = state
        return results

    def upsert_student_topic_state(self, state: StudentTopicState) -> None:
        self.topic_states[(state.student_id, state.topic_id)] = state

    def apply_submission_and_propagate_fire(
        self,
        attempt: InteractionAttempt,
        updated_states: List[StudentTopicState],
        xp_entry: Optional[XPLedgerEntry] = None,
    ) -> None:
        # Atomic commit in memory
        self.attempts.append(attempt)
        for s in updated_states:
            self.topic_states[(s.student_id, s.topic_id)] = s

        if xp_entry is not None:
            self.xp_ledger.append(xp_entry)
            if xp_entry.student_id in self.profiles:
                p = self.profiles[xp_entry.student_id]
                new_total = max(0, p.xp_total + xp_entry.delta_xp)
                self.profiles[xp_entry.student_id] = p.model_copy(update={"xp_total": new_total})

    def get_dashboard_menu(self, student_id: str) -> List[DashboardTaskItem]:
        items = self.dashboards.get(student_id, [])
        sorted_items = sorted(
            items,
            key=lambda it: (-it.rank_score, it.assigned_at, it.topic_id),
        )
        return [it.model_copy(deep=True) for it in sorted_items]

    def save_dashboard_menu(self, student_id: str, items: List[DashboardTaskItem]) -> None:
        deduped: Dict[str, DashboardTaskItem] = {}
        for it in items:
            deduped[it.topic_id] = it.model_copy(deep=True)
        self.dashboards[student_id] = list(deduped.values())

    def record_attempt(self, attempt: InteractionAttempt) -> None:
        self.attempts.append(attempt)

    def append_xp_ledger(self, entry: XPLedgerEntry) -> None:
        self.xp_ledger.append(entry)
        if entry.student_id in self.profiles:
            p = self.profiles[entry.student_id]
            new_total = max(0, p.xp_total + entry.delta_xp)
            self.profiles[entry.student_id] = p.model_copy(update={"xp_total": new_total})

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
            self.parent_rewards[reward_id] = ParentCustomReward(**data)

    def save_study_habit_schedule(self, schedule: StudyHabitSchedule) -> None:
        self.study_habit_schedules[schedule.student_id] = schedule.model_copy(deep=True)

    def get_study_habit_schedule(self, student_id: str) -> Optional[StudyHabitSchedule]:
        sched = self.study_habit_schedules.get(student_id)
        return sched.model_copy(deep=True) if sched else None

    def record_adult_resit_alert(self, alert: AdultReSitAlert) -> None:
        self.adult_resit_alerts.append(alert.model_copy(deep=True))

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
                self.adult_resit_alerts[idx] = AdultReSitAlert(**data)

    def record_content_flag(self, flag: ContentRemediationFlag) -> None:
        self.content_flags.append(flag.model_copy(deep=True))

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
                self.content_flags[idx] = ContentRemediationFlag(**data)

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

