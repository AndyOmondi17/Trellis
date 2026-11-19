"""
src/trellis/engine/diagnostic_engine.py
Tier 2: Student Knowledge Profile & Adaptive Placement Diagnostic Engine (FR-2.1, FR-2.2, FR-2.3).
Implements:
  - FR-2.1: Pre-Diagnostic Graph Compression (<= 3-hop ancestor & progeny Set-Cover)
            & Maximum-Entropy Question Selector (~50% accuracy boundary, 20-60 questions).
  - FR-2.2: Plus-Minus Confidence Balance & Conflict Resolution Engine:
            Accuracy-Time Conflict discounting (+1.0 -> +0.15), "I_DONT_KNOW" (-1.0),
            Causal (ancestors) & Correlational (module leaf siblings) propagation,
            and Prerequisite-Postrequisite conflict resolution (bottom of lowest hole).
  - FR-2.3: Conditional Completion (0 < balance < 1.0) with cascading credit revocation,
            Supplemental Diagnostics (2-5 questions on newly added/rewired balance == 0 nodes),
            and "I Already Know This" Fast-Track lesson bypass (2 unassisted highest-KP questions).
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

from pydantic import BaseModel, Field

from trellis.domain.models import (
    CourseCatalogItem,
    InteractionAttempt,
    KnowledgePoint,
    StudentProfile,
    StudentTopicState,
    TopicMasteryStatus,
    TopicNode,
)
from trellis.engine.content_blueprint_solver import (
    BlueprintInstance,
    ContentBlueprintSolver,
)
from trellis.engine.topology_service import TopologyService
from trellis.ports.storage_port import TrellisStoragePort


# ---------------------------------------------------------------------------
# Diagnostic DTOs & Value Objects
# ---------------------------------------------------------------------------

class DiagnosticQuestion(BaseModel):
    """Represents a selected adaptive diagnostic or fast-track question."""
    question_index: int = Field(default=0, ge=0)
    topic_id: str
    kp_id: str
    kp_index: int
    expected_time_seconds: float = Field(default=60.0, gt=0.0)
    prior_balance: float = Field(default=0.0)
    prior_mastery_probability: float = Field(default=0.5, ge=0.0, le=1.0)
    expected_information_gain: float = Field(default=0.0, ge=0.0)
    blueprint_instance: Optional[BlueprintInstance] = None


class DiagnosticSubmission(BaseModel):
    """Input sensor payload for a diagnostic or fast-track question response."""
    student_id: str
    topic_id: str
    kp_id: str
    student_answer: str = ""
    duration_ms: int = Field(default=0, ge=0)
    expected_time_seconds: float = Field(default=60.0, gt=0.0)
    submission_flag: Literal["ANSWER_SUBMITTED", "I_DONT_KNOW"] = "ANSWER_SUBMITTED"
    reference_peeked: bool = False
    target_answer: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None


class DiagnosticPropagationReport(BaseModel):
    """Summary of plus-minus weight assignment and graph propagation for one submission."""
    topic_id: str
    is_correct: bool
    submission_flag: str
    assigned_weight: float
    accuracy_time_discounted: bool
    updated_balances: Dict[str, float]
    propagated_ancestors: List[str] = Field(default_factory=list)
    propagated_leaf_siblings: List[str] = Field(default_factory=list)
    propagated_postrequisites: List[str] = Field(default_factory=list)


class PrerequisitePostrequisiteConflict(BaseModel):
    """Audit record when a postrequisite has positive balance above an unmastered prerequisite hole."""
    hole_topic_id: str
    conflicting_postrequisite_id: str
    prior_postrequisite_balance: float
    resolved_postrequisite_balance: float


class DiagnosticPlacementResult(BaseModel):
    """Complete placement summary emitted upon finalizing a diagnostic exam."""
    student_id: str
    enrolled_course_id: str
    mastered_topic_ids: List[str]
    conditionally_completed_topic_ids: List[str]
    available_frontier_topic_ids: List[str]
    locked_topic_ids: List[str]
    mastered_floor_topic_ids: List[str]
    lowest_knowledge_hole_ids: List[str]
    conflicts_resolved: List[PrerequisitePostrequisiteConflict]
    course_progress_pct: float


class ConditionalRevocationReport(BaseModel):
    """Audit report emitted when failing a dependent task peels back conditionally completed topics."""
    student_id: str
    failed_topic_id: str
    revoked_topic_ids: List[str]
    newly_available_topic_ids: List[str]
    peeled_back_locked_topic_ids: List[str]


class FastTrackBypassResult(BaseModel):
    """Evaluation outcome for 'I Already Know This' lesson bypass."""
    student_id: str
    topic_id: str
    bypass_granted: bool
    questions_evaluated: int
    unassisted_correct_within_time: int
    updated_status: TopicMasteryStatus
    unlocked_postrequisite_ids: List[str] = Field(default_factory=list)
    failure_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Core Diagnostic Engine
# ---------------------------------------------------------------------------

class DiagnosticEngine:
    """
    Adaptive Placement Diagnostic, Plus-Minus Confidence Balance & Conditional Completion Engine.
    Communicates exclusively with TrellisStoragePort, TopologyService, and ContentBlueprintSolver.
    Zero raw SQL or Cypher queries permitted.
    """

    MIN_OVERTIME_WEIGHT: float = 0.15
    FULL_CORRECT_WEIGHT: float = 1.0
    INCORRECT_WEIGHT: float = -1.0
    ANCESTOR_DECAY_PER_HOP: float = 0.70
    POSTREQ_DECAY_PER_HOP: float = 0.75
    LEAF_SIBLING_CORRELATION_WEIGHT: float = 0.50

    def __init__(
        self,
        storage: TrellisStoragePort,
        topology: Optional[TopologyService] = None,
        blueprint_solver: Optional[ContentBlueprintSolver] = None,
    ) -> None:
        self.storage = storage
        self.topology = topology or TopologyService(storage)
        self.solver = blueprint_solver or ContentBlueprintSolver(storage)

    # =======================================================================
    # FR-2.1: Pre-Diagnostic Graph Compression & Max-Entropy Question Selector
    # =======================================================================

    def get_diagnostic_scope(
        self,
        enrolled_course_id: str,
        student_id: Optional[str] = None,
        foundations_only: bool = False,
    ) -> List[str]:
        """
        Computes the target course + prerequisite foundations subgraph to be diagnosed.
        Automatically excludes topics in mastery_floor_course_ids (or already MASTERED_FLOOR)
        and respects Non-Holistic Targeted Remediation for university courses.
        """
        catalog = self.storage.get_course_catalog()
        target_course: Optional[CourseCatalogItem] = None
        for c in catalog:
            if c.course_id == enrolled_course_id:
                target_course = c
                break

        if not target_course:
            raise KeyError(f"Course '{enrolled_course_id}' not found in course catalog.")

        floor_courses: Set[str] = set(target_course.mastery_floor_course_ids)

        # If student_id is given, ensure mastery floors are applied
        existing_states: Dict[str, StudentTopicState] = {}
        if student_id:
            if floor_courses:
                self.topology.apply_mastery_floors(student_id, enrolled_course_id)
            existing_states = self.storage.get_student_topic_states(student_id)

        # Full ancestor prerequisite subgraph of enrolled course
        ancestor_subgraph_ids = self.topology.get_course_ancestor_prerequisite_subgraph(
            enrolled_course_id
        )

        scoped_ids: List[str] = []
        for tid in sorted(ancestor_subgraph_ids):
            try:
                node = self.storage.get_topic_node(tid)
            except KeyError:
                continue

            # Exclude floor courses or topics already marked MASTERED_FLOOR
            if node.course_id in floor_courses:
                continue
            st = existing_states.get(tid)
            if st and st.status == "MASTERED_FLOOR":
                continue

            # If accelerated Foundations track requested, filter lower-course nodes
            if foundations_only and not node.in_foundations_sequence:
                continue

            scoped_ids.append(tid)

        return scoped_ids

    def compress_diagnostic_subgraph(
        self,
        enrolled_course_id: str,
        student_id: Optional[str] = None,
        foundations_only: bool = False,
        max_hops: int = 3,
    ) -> List[str]:
        """
        Compresses the target course + foundations subgraph into the minimal cover C
        where every topic t in the scoped subgraph has:
          1. An ancestor a in C within <= max_hops (3) prerequisite edges (0 <= dist(a, t) <= 3), AND
          2. A progeny p in C within <= max_hops (3) prerequisite edges (0 <= dist(t, p) <= 3).
        Uses deterministic greedy Dual-Requirement Set-Cover.
        """
        scope_ids = self.get_diagnostic_scope(
            enrolled_course_id=enrolled_course_id,
            student_id=student_id,
            foundations_only=foundations_only,
        )
        if not scope_ids:
            return []

        scope_set = set(scope_ids)

        # Compute <= max_hops progeny and ancestors within scope_set
        prog_within_k: Dict[str, Set[str]] = {}
        for u in scope_ids:
            postreqs = set(self.storage.get_postrequisites(u, max_hops=max_hops)) & scope_set
            prog_within_k[u] = {u} | postreqs

        anc_within_k: Dict[str, Set[str]] = {u: set() for u in scope_ids}
        for u in scope_ids:
            for v in prog_within_k[u]:
                anc_within_k[v].add(u)

        # Dual coverage universe: every topic t requires (t, 'anc') and (t, 'prog')
        uncovered: Set[Tuple[str, str]] = set()
        for t in scope_ids:
            uncovered.add((t, "anc"))
            uncovered.add((t, "prog"))

        # Candidate coverage sets:
        # Picking candidate c covers:
        #   - (t, 'anc') for all t in prog_within_k[c] (since c is an ancestor of t within <= k hops)
        #   - (t, 'prog') for all t in anc_within_k[c] (since c is a progeny of t within <= k hops)
        candidate_coverage: Dict[str, Set[Tuple[str, str]]] = {}
        node_degree: Dict[str, int] = {}
        node_is_core: Dict[str, int] = {}

        for c in scope_ids:
            cov_anc = {(t, "anc") for t in prog_within_k[c]}
            cov_prog = {(t, "prog") for t in anc_within_k[c]}
            candidate_coverage[c] = cov_anc | cov_prog
            node = self.storage.get_topic_node(c)
            node_is_core[c] = 1 if node.is_core else 0
            # Interior connectivity degree within scope
            node_degree[c] = len(prog_within_k[c]) + len(anc_within_k[c])

        selected_cover: List[str] = []
        remaining_candidates = set(scope_ids)

        while uncovered and remaining_candidates:
            # Deterministic greedy selection maximizing newly covered requirements
            best_cand = max(
                remaining_candidates,
                key=lambda c: (
                    len(candidate_coverage[c] & uncovered),
                    node_is_core[c],
                    node_degree[c],
                    c,
                ),
            )
            newly_covered = candidate_coverage[best_cand] & uncovered
            if not newly_covered:
                break
            selected_cover.append(best_cand)
            uncovered -= newly_covered
            remaining_candidates.remove(best_cand)

        return sorted(selected_cover)

    def verify_diagnostic_cover(
        self,
        scope_ids: List[str],
        cover_ids: List[str],
        max_hops: int = 3,
    ) -> bool:
        """
        Verifies the mathematical cover invariant:
        Every topic t in scope_ids has both an ancestor a in cover_ids within <= max_hops
        prerequisite edges AND a progeny p in cover_ids within <= max_hops prerequisite edges.
        """
        scope_set = set(scope_ids)
        cover_set = set(cover_ids)
        if not scope_set:
            return True

        prog_map: Dict[str, Set[str]] = {}
        for u in scope_ids:
            postreqs = set(self.storage.get_postrequisites(u, max_hops=max_hops)) & scope_set
            prog_map[u] = {u} | postreqs

        anc_map: Dict[str, Set[str]] = {u: set() for u in scope_ids}
        for u in scope_ids:
            for v in prog_map[u]:
                anc_map[v].add(u)

        for t in scope_ids:
            has_ancestor_in_cover = bool(anc_map[t] & cover_set)
            has_progeny_in_cover = bool(prog_map[t] & cover_set)
            if not (has_ancestor_in_cover and has_progeny_in_cover):
                return False

        return True

    @staticmethod
    def balance_to_mastery_probability(balance: float, slope: float = 1.5) -> float:
        """
        Maps signed diagnostic_plus_minus_balance in (-inf, +inf) to mastery probability in (0, 1).
        At balance == 0.0, probability == 0.50 (exact 50% accuracy boundary).
        """
        clamped_exp = max(-20.0, min(20.0, -slope * balance))
        return 1.0 / (1.0 + math.exp(clamped_exp))

    @staticmethod
    def binary_entropy(prob: float) -> float:
        """
        Computes binary Shannon entropy H(p) in [0.0, 1.0] bits.
        Maximized (1.0 bit) at p = 0.50 (the 50% accuracy boundary).
        """
        p = max(1e-9, min(1.0 - 1e-9, prob))
        return -(p * math.log2(p) + (1.0 - p) * math.log2(1.0 - p))

    def compute_expected_information_gain(
        self,
        topic_id: str,
        scope_set: Set[str],
        balances: Dict[str, float],
    ) -> float:
        """
        Computes the expected information gain (in bits of graph uncertainty resolved)
        from asking a diagnostic question on topic_id:
          IG(c) = H(p_c) * [1 + p_c * sum_{u in Anc(c)} w_pos * H(p_u)
                              + (1 - p_c) * sum_{v in Post(c)} w_neg * H(p_v)]
        Peaks along the ~50% accuracy boundary (balance ~ 0) at nodes that bisect
        the largest uncertain ancestor and postrequisite subgraphs.
        """
        b_c = balances.get(topic_id, 0.0)
        p_c = self.balance_to_mastery_probability(b_c)
        h_c = self.binary_entropy(p_c)

        # Positive propagation reach: prerequisite ancestors + encompassed ancestors + leaf siblings
        pos_reach: Set[str] = set()
        for p in self.storage.get_encompassed_ancestors(topic_id, max_hops=3):
            if p.ancestor_id in scope_set and p.ancestor_id != topic_id:
                pos_reach.add(p.ancestor_id)

        # Walk hard prerequisites up to 3 hops
        frontier = {topic_id}
        for _ in range(3):
            next_frontier: Set[str] = set()
            for curr in frontier:
                for hp in self.storage.get_hard_parents(curr):
                    if hp in scope_set and hp not in pos_reach and hp != topic_id:
                        pos_reach.add(hp)
                        next_frontier.add(hp)
            frontier = next_frontier
            if not frontier:
                break

        for sib in self.get_module_leaf_siblings(topic_id, scope_set):
            pos_reach.add(sib)

        # Negative propagation reach: postrequisites within scope
        neg_reach = (set(self.storage.get_postrequisites(topic_id, max_hops=3)) & scope_set) - {topic_id}

        pos_entropy_sum = sum(
            self.ANCESTOR_DECAY_PER_HOP * self.binary_entropy(
                self.balance_to_mastery_probability(balances.get(u, 0.0))
            )
            for u in pos_reach
        )
        neg_entropy_sum = sum(
            self.POSTREQ_DECAY_PER_HOP * self.binary_entropy(
                self.balance_to_mastery_probability(balances.get(v, 0.0))
            )
            for v in neg_reach
        )

        # Bisection bonus: rewards nodes that have both uncertain ancestors and uncertain postrequisites
        bisection_balance = 1.0 + 0.25 * min(pos_entropy_sum, neg_entropy_sum)

        expected_ig = h_c * (1.0 + p_c * pos_entropy_sum + (1.0 - p_c) * neg_entropy_sum) * bisection_balance
        return round(expected_ig, 6)

    def _select_diagnostic_kp(self, node: TopicNode) -> KnowledgePoint:
        """
        Selects the best diagnostic KnowledgePoint for a topic.
        Prefers the highest KP that has a problem_blueprint (or highest KP overall),
        as diagnostic questions must exercise the full scope and prerequisites of the topic.
        """
        if not node.knowledge_points:
            raise ValueError(f"TopicNode '{node.id}' has no knowledge_points defined.")

        sorted_kps = sorted(node.knowledge_points, key=lambda k: k.kp_index, reverse=True)
        for kp in sorted_kps:
            try:
                shell = self.storage.get_kp_content_shell(kp.kp_id)
                if shell.problem_blueprint is not None:
                    return kp
            except KeyError:
                if kp.problem_blueprint is not None:
                    return kp

        return sorted_kps[0]

    def _build_diagnostic_question(
        self,
        topic_id: str,
        question_index: int,
        prior_balance: float,
        expected_ig: float,
        seed: Optional[int] = None,
    ) -> DiagnosticQuestion:
        """Constructs a DiagnosticQuestion with an instantiated BlueprintInstance if available."""
        node = self.storage.get_topic_node(topic_id)
        kp = self._select_diagnostic_kp(node)

        expected_time_sec = 60.0
        if node.requires_automaticity and node.automaticity_Defining_latency_ms:
            expected_time_sec = max(3.0, node.automaticity_Defining_latency_ms / 1000.0)
        else:
            expected_time_sec = round(30.0 + 60.0 * node.base_difficulty, 1)

        instance: Optional[BlueprintInstance] = None
        try:
            shell = self.storage.get_kp_content_shell(kp.kp_id)
            if shell.problem_blueprint is not None:
                instance = self.solver.generate_variant(
                    kp.kp_id,
                    seed=(seed + question_index) if seed is not None else (42 + question_index),
                )
        except (KeyError, ValueError):
            instance = None

        prob = self.balance_to_mastery_probability(prior_balance)
        return DiagnosticQuestion(
            question_index=question_index,
            topic_id=topic_id,
            kp_id=kp.kp_id,
            kp_index=kp.kp_index,
            expected_time_seconds=expected_time_sec,
            prior_balance=round(prior_balance, 4),
            prior_mastery_probability=round(prob, 4),
            expected_information_gain=round(expected_ig, 6),
            blueprint_instance=instance,
        )

    def select_next_diagnostic_question(
        self,
        student_id: str,
        enrolled_course_id: str,
        asked_topic_ids: Optional[Set[str]] = None,
        candidate_pool: Optional[List[str]] = None,
        foundations_only: bool = False,
        min_questions: int = 20,
        max_questions: int = 60,
        confidence_threshold: float = 1.0,
        seed: Optional[int] = None,
    ) -> Optional[DiagnosticQuestion]:
        """
        Dynamically selects the next diagnostic question maximizing expected information gain
        along the ~50% accuracy boundary (balance ~ 0.0) across a 20-60 question budget.
        Returns None when the diagnostic is complete.
        """
        asked = set(asked_topic_ids) if asked_topic_ids else set()
        num_asked = len(asked)

        # Hard ceiling of max_questions (default 60)
        if num_asked >= max_questions:
            return None

        scope_ids = self.get_diagnostic_scope(
            enrolled_course_id=enrolled_course_id,
            student_id=student_id,
            foundations_only=foundations_only,
        )
        if not scope_ids:
            return None

        scope_set = set(scope_ids)
        states = self.storage.get_student_topic_states(student_id, scope_ids)
        balances: Dict[str, float] = {
            tid: (states[tid].diagnostic_plus_minus_balance if tid in states else 0.0)
            for tid in scope_ids
        }

        # Determine primary candidate pool (compressed cover by default)
        if candidate_pool is not None:
            primary_pool = [tid for tid in candidate_pool if tid in scope_set and tid not in asked]
        else:
            cover = self.compress_diagnostic_subgraph(
                enrolled_course_id=enrolled_course_id,
                student_id=student_id,
                foundations_only=foundations_only,
            )
            primary_pool = [tid for tid in cover if tid not in asked]

        # If we have already asked >= min_questions, check if all scope nodes meet confidence_threshold
        if num_asked >= min_questions:
            low_confidence_remaining = [
                tid for tid in scope_ids if abs(balances.get(tid, 0.0)) < confidence_threshold
            ]
            if not low_confidence_remaining:
                return None

        # If compressed cover pool is exhausted but we still have uncertain nodes (or < min_questions),
        # expand to remaining unasked topics in the full diagnostic scope
        active_candidates = primary_pool
        if not active_candidates:
            active_candidates = [
                tid for tid in scope_ids
                if tid not in asked and (
                    num_asked < min_questions or abs(balances.get(tid, 0.0)) < confidence_threshold
                )
            ]

        if not active_candidates:
            return None

        # Score each candidate by Expected Information Gain along the ~50% accuracy boundary
        scored_candidates: List[Tuple[float, float, int, str]] = []
        for cid in active_candidates:
            ig = self.compute_expected_information_gain(cid, scope_set, balances)
            b_val = balances.get(cid, 0.0)
            # Closeness to 0.0 balance (50% accuracy boundary)
            boundary_closeness = -abs(b_val)
            node = self.storage.get_topic_node(cid)
            is_core_flag = 1 if node.is_core else 0
            scored_candidates.append((ig, boundary_closeness, is_core_flag, cid))

        scored_candidates.sort(reverse=True)
        best_ig, _, _, best_topic_id = scored_candidates[0]

        return self._build_diagnostic_question(
            topic_id=best_topic_id,
            question_index=num_asked,
            prior_balance=balances.get(best_topic_id, 0.0),
            expected_ig=best_ig,
            seed=seed,
        )

    # =======================================================================
    # FR-2.2: Plus-Minus Confidence Balance & Conflict Resolution Engine
    # =======================================================================

    def compute_answer_weight(
        self,
        is_correct: bool,
        duration_ms: int,
        expected_time_seconds: float = 60.0,
        submission_flag: str = "ANSWER_SUBMITTED",
    ) -> Tuple[float, bool]:
        """
        Computes the signed diagnostic weight for a single answer:
          1. Explicit "I_DONT_KNOW" flag -> immediately -1.0 without guessing distortion.
          2. Incorrect answer -> -1.0.
          3. Correct answer within expected_time_seconds * 1000 -> +1.0.
          4. Accuracy-Time Conflict Discounting: Correct answer exceeding expected_time_seconds * 1000
             decays smoothly from +1.0 down to +0.15 floor.
        Returns (signed_weight, was_time_discounted).
        """
        if submission_flag == "I_DONT_KNOW" or not is_correct:
            return (self.INCORRECT_WEIGHT, False)

        expected_ms = max(1.0, expected_time_seconds * 1000.0)
        if duration_ms <= expected_ms:
            return (self.FULL_CORRECT_WEIGHT, False)

        # Overtime decay: linear decay from 1.0 at 1x expected_ms to 0.15 floor at >= 3x expected_ms
        overtime_ratio = (duration_ms - expected_ms) / (2.0 * expected_ms)
        discounted_weight = max(
            self.MIN_OVERTIME_WEIGHT,
            self.FULL_CORRECT_WEIGHT - (self.FULL_CORRECT_WEIGHT - self.MIN_OVERTIME_WEIGHT) * overtime_ratio,
        )
        return (round(discounted_weight, 4), True)

    def get_module_leaf_siblings(
        self,
        topic_id: str,
        scope_set: Optional[Set[str]] = None,
    ) -> List[str]:
        """
        Identifies correlational module leaf siblings:
        If topic_id is a leaf topic within its module (domain_cluster) — meaning none of its
        postrequisites belong to the same domain_cluster — returns all other leaf topics
        within that same domain_cluster.
        """
        node = self.storage.get_topic_node(topic_id)
        neigh = self.storage.get_local_neighborhood(topic_id)
        if not neigh.cluster_siblings:
            return []

        cluster_members = set(neigh.cluster_siblings) | {topic_id}
        if scope_set is not None:
            cluster_members &= scope_set

        # Check if topic_id is a leaf within its domain_cluster
        postreqs_1hop = set(self.storage.get_postrequisites(topic_id, max_hops=1))
        if postreqs_1hop & cluster_members:
            # topic_id has downstream dependents in the same module, so it is not a module leaf
            return []

        leaf_siblings: List[str] = []
        for sib_id in sorted(neigh.cluster_siblings):
            if scope_set is not None and sib_id not in scope_set:
                continue
            sib_postreqs = set(self.storage.get_postrequisites(sib_id, max_hops=1))
            if not (sib_postreqs & cluster_members):
                leaf_siblings.append(sib_id)

        return leaf_siblings

    def process_diagnostic_submission(
        self,
        submission: DiagnosticSubmission,
        scope_ids: Optional[List[str]] = None,
    ) -> DiagnosticPropagationReport:
        """
        Evaluates a diagnostic submission and propagates its signed weight across the knowledge graph:
          - "I_DONT_KNOW": immediately records -1.0 without SymPy grading.
          - Positive weight (+1.0 decayed to +0.15 on overtime): flows to target topic,
            causal ancestors (encompassed skills & prerequisite ancestors), and correlational
            module leaf siblings.
          - Negative weight (-1.0): flows to target topic and downstream post-requisites.
        Persists updated StudentTopicState records and telemetry attempt.
        """
        # 1. Determine correctness
        if submission.submission_flag == "I_DONT_KNOW":
            is_correct = False
        else:
            target_ans = submission.target_answer
            params = submission.parameters
            if target_ans is None:
                try:
                    shell = self.storage.get_kp_content_shell(submission.kp_id)
                    if shell.worked_example:
                        target_ans = shell.worked_example.final_answer
                except KeyError:
                    target_ans = None

            if target_ans is not None:
                is_correct = self.solver.grade_student_answer(
                    student_input=submission.student_answer,
                    target_answer=target_ans,
                    parameters=params,
                )
            else:
                is_correct = bool(submission.student_answer.strip())

        # 2. Compute signed weight & Accuracy-Time Conflict discount
        weight, discounted = self.compute_answer_weight(
            is_correct=is_correct,
            duration_ms=submission.duration_ms,
            expected_time_seconds=submission.expected_time_seconds,
            submission_flag=submission.submission_flag,
        )

        scope_set = set(scope_ids) if scope_ids is not None else None
        existing_states = self.storage.get_student_topic_states(submission.student_id)

        balance_deltas: Dict[str, float] = {submission.topic_id: weight}
        propagated_ancestors: List[str] = []
        propagated_leaf_siblings: List[str] = []
        propagated_postrequisites: List[str] = []

        if weight > 0.0:
            # Causal Propagation 1: Encompassed ancestors (G_enc)
            enc_paths = self.storage.get_encompassed_ancestors(submission.topic_id, max_hops=3)
            best_ancestor_factor: Dict[str, float] = {}
            for p in enc_paths:
                if scope_set is not None and p.ancestor_id not in scope_set:
                    continue
                if p.ancestor_id != submission.topic_id:
                    best_ancestor_factor[p.ancestor_id] = max(
                        best_ancestor_factor.get(p.ancestor_id, 0.0),
                        p.compound_weight,
                    )

            # Causal Propagation 2: Prerequisite ancestors (G_hard) up to 3 hops
            curr_level = {submission.topic_id}
            visited_prereqs = {submission.topic_id}
            for hop in range(1, 4):
                next_level: Set[str] = set()
                hop_factor = self.ANCESTOR_DECAY_PER_HOP ** hop
                for u in curr_level:
                    for pid in self.storage.get_hard_parents(u):
                        if pid not in visited_prereqs:
                            visited_prereqs.add(pid)
                            next_level.add(pid)
                            if scope_set is None or pid in scope_set:
                                best_ancestor_factor[pid] = max(
                                    best_ancestor_factor.get(pid, 0.0),
                                    hop_factor,
                                )
                curr_level = next_level
                if not curr_level:
                    break

            for anc_id, factor in sorted(best_ancestor_factor.items()):
                balance_deltas[anc_id] = round(weight * factor, 4)
                propagated_ancestors.append(anc_id)

            # Correlational Propagation 3: Module leaf siblings
            leaf_sibs = self.get_module_leaf_siblings(submission.topic_id, scope_set)
            for sib_id in leaf_sibs:
                if sib_id not in balance_deltas:
                    balance_deltas[sib_id] = round(weight * self.LEAF_SIBLING_CORRELATION_WEIGHT, 4)
                    propagated_leaf_siblings.append(sib_id)

        else:
            # Negative Propagation: Post-requisites up to 3 hops (and all reachable postrequisites)
            visited_post = {submission.topic_id}
            curr_level = {submission.topic_id}
            for hop in range(1, 4):
                next_level = set()
                hop_factor = self.POSTREQ_DECAY_PER_HOP ** hop
                for u in curr_level:
                    direct_post = self.storage.get_postrequisites(u, max_hops=1)
                    for vid in direct_post:
                        if vid not in visited_post:
                            visited_post.add(vid)
                            next_level.add(vid)
                            if scope_set is None or vid in scope_set:
                                balance_deltas[vid] = round(weight * hop_factor, 4)
                                propagated_postrequisites.append(vid)
                curr_level = next_level
                if not curr_level:
                    break

            # Also propagate beyond 3 hops with floor factor so higher postrequisites are dampened
            all_post = self.storage.get_postrequisites(submission.topic_id)
            deep_factor = self.POSTREQ_DECAY_PER_HOP ** 4
            for vid in all_post:
                if vid not in visited_post and (scope_set is None or vid in scope_set):
                    balance_deltas[vid] = round(weight * deep_factor, 4)
                    propagated_postrequisites.append(vid)

        # 3. Apply deltas to StudentTopicState records and persist
        updated_balances: Dict[str, float] = {}
        states_to_save: List[StudentTopicState] = []

        for tid, delta in balance_deltas.items():
            try:
                node = self.storage.get_topic_node(tid)
            except KeyError:
                continue

            curr_state = existing_states.get(tid)
            if curr_state and curr_state.status == "MASTERED_FLOOR":
                # Floor-credited courses are immutable during diagnostic
                continue

            prior_bal = curr_state.diagnostic_plus_minus_balance if curr_state else 0.0
            new_bal = round(prior_bal + delta, 4)
            updated_balances[tid] = new_bal

            if curr_state:
                new_state = curr_state.model_copy(
                    update={"diagnostic_plus_minus_balance": new_bal}
                )
            else:
                new_state = StudentTopicState(
                    student_id=submission.student_id,
                    topic_id=tid,
                    status="LOCKED",
                    topic_difficulty=node.base_difficulty,
                    diagnostic_plus_minus_balance=new_bal,
                )
            states_to_save.append(new_state)

        attempt = InteractionAttempt(
            attempt_id=f"diag_{uuid.uuid4().hex[:12]}",
            student_id=submission.student_id,
            topic_id=submission.topic_id,
            kp_id=submission.kp_id,
            submitted_answer=(
                "I_DONT_KNOW"
                if submission.submission_flag == "I_DONT_KNOW"
                else submission.student_answer
            ),
            is_correct=is_correct,
            duration_ms=submission.duration_ms,
            reference_peeked=submission.reference_peeked,
        )

        self.storage.apply_submission_and_propagate_fire(
            attempt=attempt,
            updated_states=states_to_save,
            xp_entry=None,
        )

        return DiagnosticPropagationReport(
            topic_id=submission.topic_id,
            is_correct=is_correct,
            submission_flag=submission.submission_flag,
            assigned_weight=weight,
            accuracy_time_discounted=discounted,
            updated_balances=updated_balances,
            propagated_ancestors=sorted(propagated_ancestors),
            propagated_leaf_siblings=sorted(propagated_leaf_siblings),
            propagated_postrequisites=sorted(propagated_postrequisites),
        )

    def resolve_prerequisite_postrequisite_conflicts(
        self,
        student_id: str,
        enrolled_course_id: str,
        scope_ids: Optional[List[str]] = None,
        failed_direct_topic_ids: Optional[Set[str]] = None,
    ) -> Tuple[Dict[str, float], List[PrerequisitePostrequisiteConflict], List[str]]:
        """
        Resolves Prerequisite-Postrequisite Conflicts:
        When a student answers a more advanced post-requisite correctly (balance > 0)
        but fails or lacks mastery on a simpler prerequisite (balance <= 0 or directly failed),
        this indicates a 'knowledge hole'.
        Revokes positive balances on all hard post-requisites above the hole so the student is
        placed strictly at the BOTTOM of their lowest knowledge hole.
        Returns (resolved_balances, conflicts_detected, lowest_hole_topic_ids).
        """
        if scope_ids is None:
            scope_ids = self.get_diagnostic_scope(enrolled_course_id, student_id=student_id)
        scope_set = set(scope_ids)

        states = self.storage.get_student_topic_states(student_id, scope_ids)
        balances: Dict[str, float] = {
            tid: (states[tid].diagnostic_plus_minus_balance if tid in states else 0.0)
            for tid in scope_ids
        }

        # If a topic was directly failed on the diagnostic, ensure its balance is non-positive
        if failed_direct_topic_ids:
            for fid in failed_direct_topic_ids:
                if fid in balances and balances[fid] > 0.0:
                    balances[fid] = 0.0

        conflicts: List[PrerequisitePostrequisiteConflict] = []
        candidate_holes: Set[str] = set()

        # Identify every unmastered topic u (balance <= 0.0) that has at least one hard
        # post-requisite v in scope_set with balance[v] > 0.0
        for u in sorted(scope_ids):
            if balances.get(u, 0.0) <= 0.0:
                # Traverse hard post-requisites of u within scope_set
                hard_descendants: List[str] = []
                queue = [u]
                visited = {u}
                while queue:
                    curr = queue.pop(0)
                    for post_id in self.storage.get_postrequisites(curr, max_hops=1):
                        if post_id not in scope_set or post_id in visited:
                            continue
                        hard_parents = set(self.storage.get_hard_parents(post_id))
                        if curr in hard_parents:
                            visited.add(post_id)
                            hard_descendants.append(post_id)
                            queue.append(post_id)

                for v in hard_descendants:
                    if balances.get(v, 0.0) > 0.0:
                        candidate_holes.add(u)
                        conflicts.append(
                            PrerequisitePostrequisiteConflict(
                                hole_topic_id=u,
                                conflicting_postrequisite_id=v,
                                prior_postrequisite_balance=balances[v],
                                resolved_postrequisite_balance=0.0,
                            )
                        )
                        balances[v] = 0.0

        # Find the bottom of the lowest knowledge holes:
        # Unmastered nodes u (balance <= 0.0) whose own hard prerequisites are all mastered (> 0.0 or MASTERED_FLOOR)
        all_states = self.storage.get_student_topic_states(student_id)
        lowest_holes: List[str] = []
        for u in sorted(scope_ids):
            if balances.get(u, 0.0) <= 0.0:
                hard_parents = self.storage.get_hard_parents(u)
                parents_mastered = True
                for pid in hard_parents:
                    if pid in scope_set:
                        if balances.get(pid, 0.0) <= 0.0:
                            parents_mastered = False
                            break
                    else:
                        p_st = all_states.get(pid)
                        if not p_st or p_st.status not in ("MASTERED", "MASTERED_FLOOR"):
                            parents_mastered = False
                            break
                if parents_mastered:
                    lowest_holes.append(u)

        return balances, conflicts, lowest_holes

    # =======================================================================
    # FR-2.3: Conditional Completion, Supplemental Diagnostics & Fast-Track
    # =======================================================================

    def finalize_diagnostic_placement(
        self,
        student_id: str,
        enrolled_course_id: str,
        foundations_only: bool = False,
        failed_direct_topic_ids: Optional[Set[str]] = None,
    ) -> DiagnosticPlacementResult:
        """
        Finalizes the student's knowledge profile after diagnostic completion:
          1. Applies Course-Level Mastery Floors (MASTERED_FLOOR).
          2. Resolves Prerequisite-Postrequisite Conflicts (places student at bottom of lowest hole).
          3. Topics with balance >= 1.0 -> status = 'MASTERED', is_conditionally_completed = False,
             rep_num = floor(balance).
          4. Topics with 0.0 < balance < 1.0 -> status = 'MASTERED', is_conditionally_completed = True,
             rep_num = round(balance, 4).
          5. Topics with balance <= 0.0 -> 'AVAILABLE' if all hard parents are MASTERED/MASTERED_FLOOR,
             else 'LOCKED'.
          6. Updates StudentProfile.progress_pct based on mastered course nodes / total course nodes.
        """
        # 1. Apply mastery floors
        self.topology.apply_mastery_floors(student_id, enrolled_course_id)

        scope_ids = self.get_diagnostic_scope(
            enrolled_course_id=enrolled_course_id,
            student_id=student_id,
            foundations_only=foundations_only,
        )
        scope_set = set(scope_ids)

        # 2. Resolve Prerequisite-Postrequisite conflicts
        resolved_balances, conflicts, lowest_holes = self.resolve_prerequisite_postrequisite_conflicts(
            student_id=student_id,
            enrolled_course_id=enrolled_course_id,
            scope_ids=scope_ids,
            failed_direct_topic_ids=failed_direct_topic_ids,
        )

        all_states = self.storage.get_student_topic_states(student_id)
        now = datetime.now(timezone.utc)

        mastered_ids: List[str] = []
        conditional_ids: List[str] = []
        available_ids: List[str] = []
        locked_ids: List[str] = []
        floor_ids: List[str] = [
            tid for tid, st in all_states.items() if st.status == "MASTERED_FLOOR"
        ]

        # First pass: classify MASTERED (balance >= 1.0 and 0.0 < balance < 1.0)
        effective_status: Dict[str, TopicMasteryStatus] = {
            tid: st.status for tid, st in all_states.items()
        }

        for tid in scope_ids:
            bal = resolved_balances.get(tid, 0.0)
            if bal > 0.0:
                effective_status[tid] = "MASTERED"
            else:
                effective_status[tid] = "LOCKED"

        # Second pass: unlock AVAILABLE frontier (bottom of lowest knowledge holes)
        for tid in scope_ids:
            bal = resolved_balances.get(tid, 0.0)
            node = self.storage.get_topic_node(tid)
            curr_st = all_states.get(tid)

            if bal >= 1.0:
                rep_n = float(math.floor(bal))
                interval_days = float(2 ** max(0, int(rep_n) - 1))
                new_st = StudentTopicState(
                    student_id=student_id,
                    topic_id=tid,
                    status="MASTERED",
                    rep_num=rep_n,
                    stability_interval_days=interval_days,
                    memory_strength=1.0,
                    last_reviewed_at=now,
                    due_review_at=now + timedelta(days=interval_days),
                    student_ability=1.0,
                    topic_difficulty=node.base_difficulty,
                    speed=1.0,
                    allow_implicit_fire_credit=True,
                    diagnostic_plus_minus_balance=bal,
                    is_conditionally_completed=False,
                    current_kp_index=len(node.knowledge_points),
                )
                self.storage.upsert_student_topic_state(new_st)
                mastered_ids.append(tid)

            elif 0.0 < bal < 1.0:
                new_st = StudentTopicState(
                    student_id=student_id,
                    topic_id=tid,
                    status="MASTERED",
                    rep_num=round(bal, 4),
                    stability_interval_days=1.0,
                    memory_strength=1.0,
                    last_reviewed_at=now,
                    due_review_at=now + timedelta(days=1.0),
                    student_ability=1.0,
                    topic_difficulty=node.base_difficulty,
                    speed=1.0,
                    allow_implicit_fire_credit=True,
                    diagnostic_plus_minus_balance=bal,
                    is_conditionally_completed=True,
                    current_kp_index=len(node.knowledge_points),
                )
                self.storage.upsert_student_topic_state(new_st)
                mastered_ids.append(tid)
                conditional_ids.append(tid)

            else:
                hard_parents = self.storage.get_hard_parents(tid)
                all_parents_mastered = all(
                    effective_status.get(pid, "LOCKED") in ("MASTERED", "MASTERED_FLOOR")
                    for pid in hard_parents
                )
                target_status: TopicMasteryStatus = "AVAILABLE" if all_parents_mastered else "LOCKED"
                effective_status[tid] = target_status

                new_st = StudentTopicState(
                    student_id=student_id,
                    topic_id=tid,
                    status=target_status,
                    rep_num=0.0,
                    stability_interval_days=1.0,
                    memory_strength=1.0,
                    student_ability=curr_st.student_ability if curr_st else 1.0,
                    topic_difficulty=node.base_difficulty,
                    speed=1.0,
                    allow_implicit_fire_credit=True,
                    diagnostic_plus_minus_balance=bal,
                    is_conditionally_completed=False,
                    current_kp_index=0,
                )
                self.storage.upsert_student_topic_state(new_st)
                if target_status == "AVAILABLE":
                    available_ids.append(tid)
                else:
                    locked_ids.append(tid)

        # Update course progress_pct on StudentProfile
        course_nodes = self.storage.get_course_subgraph(enrolled_course_id)
        total_course_count = len(course_nodes)
        mastered_course_count = sum(
            1 for cn in course_nodes
            if effective_status.get(cn.id) in ("MASTERED", "MASTERED_FLOOR")
        )
        progress_pct = round(
            (mastered_course_count / total_course_count) * 100.0, 2
        ) if total_course_count > 0 else 0.0

        try:
            profile = self.storage.get_student_profile(student_id)
            self.storage.upsert_student_profile(
                profile.model_copy(update={"progress_pct": progress_pct})
            )
        except KeyError:
            self.storage.upsert_student_profile(
                StudentProfile(
                    student_id=student_id,
                    enrolled_course_id=enrolled_course_id,
                    progress_pct=progress_pct,
                )
            )

        return DiagnosticPlacementResult(
            student_id=student_id,
            enrolled_course_id=enrolled_course_id,
            mastered_topic_ids=sorted(mastered_ids),
            conditionally_completed_topic_ids=sorted(conditional_ids),
            available_frontier_topic_ids=sorted(available_ids),
            locked_topic_ids=sorted(locked_ids),
            mastered_floor_topic_ids=sorted(floor_ids),
            lowest_knowledge_hole_ids=sorted(lowest_holes),
            conflicts_resolved=conflicts,
            course_progress_pct=progress_pct,
        )

    def revoke_conditional_completion(
        self,
        student_id: str,
        failed_topic_id: str,
        enrolled_course_id: Optional[str] = None,
    ) -> ConditionalRevocationReport:
        """
        Cascading Conditional Completion Revocation (FR-2.3):
        When a student fails a task on a conditionally completed topic OR fails a dependent task
        that builds on conditionally completed prerequisites:
          1. Identifies the conditionally completed topic(s) in the prerequisite ancestor chain
             (and failed_topic_id itself if conditionally completed).
          2. Immediately revokes credit for those conditionally completed ancestors AND any
             downstream post-requisites that depend on them.
          3. Peels back the knowledge profile: sets the lowest revoked hole(s) whose hard parents
             are still MASTERED to 'AVAILABLE', and locks all dependent downstream topics.
        """
        all_states = self.storage.get_student_topic_states(student_id)

        # 1. Identify conditionally completed roots connected to failed_topic_id
        revocation_roots: Set[str] = set()
        failed_st = all_states.get(failed_topic_id)
        if failed_st and failed_st.is_conditionally_completed:
            revocation_roots.add(failed_topic_id)

        # Walk backward along hard prerequisites from failed_topic_id to find conditional ancestors
        queue = [failed_topic_id]
        visited_back = {failed_topic_id}
        while queue:
            curr = queue.pop(0)
            for pid in self.storage.get_hard_parents(curr):
                if pid not in visited_back:
                    visited_back.add(pid)
                    p_st = all_states.get(pid)
                    if p_st and p_st.is_conditionally_completed:
                        revocation_roots.add(pid)
                        queue.append(pid)

        if not revocation_roots:
            return ConditionalRevocationReport(
                student_id=student_id,
                failed_topic_id=failed_topic_id,
                revoked_topic_ids=[],
                newly_available_topic_ids=[],
                peeled_back_locked_topic_ids=[],
            )

        # 2. Collect all downstream post-requisites of revocation_roots that were MASTERED or AVAILABLE
        affected_topics: Set[str] = set(revocation_roots)
        for root_id in revocation_roots:
            for post_id in self.storage.get_postrequisites(root_id):
                post_st = all_states.get(post_id)
                if post_st and post_st.status in ("MASTERED", "AVAILABLE", "IN_PROGRESS"):
                    affected_topics.add(post_id)

        revoked_ids: List[str] = []
        for tid in sorted(affected_topics):
            st = all_states.get(tid)
            if st and st.status == "MASTERED":
                revoked_ids.append(tid)

        # 3. Recompute status for all affected topics (lowest holes -> AVAILABLE, dependents -> LOCKED)
        # First mark all affected topics as LOCKED in our working map
        working_status: Dict[str, TopicMasteryStatus] = {
            tid: st.status for tid, st in all_states.items()
        }
        for tid in affected_topics:
            working_status[tid] = "LOCKED"

        newly_available: List[str] = []
        peeled_locked: List[str] = []

        for tid in sorted(affected_topics):
            hard_parents = self.storage.get_hard_parents(tid)
            all_parents_mastered = all(
                working_status.get(pid, "LOCKED") in ("MASTERED", "MASTERED_FLOOR")
                for pid in hard_parents
            )
            new_status: TopicMasteryStatus = "AVAILABLE" if all_parents_mastered else "LOCKED"
            working_status[tid] = new_status

            old_st = all_states.get(tid)
            node = self.storage.get_topic_node(tid)
            updated_st = StudentTopicState(
                student_id=student_id,
                topic_id=tid,
                status=new_status,
                rep_num=0.0,
                stability_interval_days=1.0,
                memory_strength=1.0,
                student_ability=old_st.student_ability if old_st else 1.0,
                topic_difficulty=node.base_difficulty,
                speed=old_st.speed if old_st else 1.0,
                allow_implicit_fire_credit=True,
                diagnostic_plus_minus_balance=-0.5 if tid in revocation_roots else 0.0,
                is_conditionally_completed=False,
                current_kp_index=0,
                consecutive_correct=0,
            )
            self.storage.upsert_student_topic_state(updated_st)

            if new_status == "AVAILABLE":
                newly_available.append(tid)
            else:
                peeled_locked.append(tid)

        # Update StudentProfile.progress_pct if enrolled_course_id is known
        target_course_id = enrolled_course_id
        if not target_course_id:
            try:
                target_course_id = self.storage.get_student_profile(student_id).enrolled_course_id
            except KeyError:
                target_course_id = None

        if target_course_id:
            course_nodes = self.storage.get_course_subgraph(target_course_id)
            if course_nodes:
                mastered_cnt = sum(
                    1 for cn in course_nodes
                    if working_status.get(cn.id) in ("MASTERED", "MASTERED_FLOOR")
                )
                pct = round((mastered_cnt / len(course_nodes)) * 100.0, 2)
                try:
                    prof = self.storage.get_student_profile(student_id)
                    self.storage.upsert_student_profile(prof.model_copy(update={"progress_pct": pct}))
                except KeyError:
                    pass

        return ConditionalRevocationReport(
            student_id=student_id,
            failed_topic_id=failed_topic_id,
            revoked_topic_ids=sorted(revoked_ids),
            newly_available_topic_ids=sorted(newly_available),
            peeled_back_locked_topic_ids=sorted(peeled_locked),
        )

    def build_supplemental_diagnostic(
        self,
        student_id: str,
        enrolled_course_id: str,
        structural_delta: Optional[Dict[str, Any]] = None,
        min_questions: int = 2,
        max_questions: int = 5,
        seed: Optional[int] = None,
    ) -> List[DiagnosticQuestion]:
        """
        Constructs a 2-5 question Supplemental Diagnostic (FR-2.3) for newly added or
        rewired curriculum nodes whose diagnostic_plus_minus_balance == 0.0.
        """
        scope_ids = self.get_diagnostic_scope(enrolled_course_id, student_id=student_id)
        scope_set = set(scope_ids)
        states = self.storage.get_student_topic_states(student_id, scope_ids)

        delta_candidates: Set[str] = set()
        if structural_delta:
            for nid in structural_delta.get("added_nodes", []):
                if nid in scope_set:
                    delta_candidates.add(nid)
            for edge_change in structural_delta.get("rewired_edges", []):
                tid = edge_change.get("topic_id")
                if tid in scope_set:
                    delta_candidates.add(tid)
            for nid in structural_delta.get("updated_kps", []):
                if nid in scope_set:
                    delta_candidates.add(nid)

        # Filter to topics with diagnostic_plus_minus_balance == 0.0
        zero_balance_pool: List[str] = []
        search_pool = sorted(delta_candidates) if delta_candidates else scope_ids
        for tid in search_pool:
            st = states.get(tid)
            bal = st.diagnostic_plus_minus_balance if st else 0.0
            if abs(bal) < 1e-6:
                zero_balance_pool.append(tid)

        # If delta_candidates had fewer than min_questions, supplement from any remaining balance==0 nodes in scope
        if len(zero_balance_pool) < min_questions:
            for tid in scope_ids:
                if tid not in zero_balance_pool:
                    st = states.get(tid)
                    bal = st.diagnostic_plus_minus_balance if st else 0.0
                    if abs(bal) < 1e-6:
                        zero_balance_pool.append(tid)

        if not zero_balance_pool:
            return []

        balances = {
            tid: (states[tid].diagnostic_plus_minus_balance if tid in states else 0.0)
            for tid in scope_ids
        }

        # Rank zero-balance candidates by Expected Information Gain and select up to max_questions (2..5)
        scored = [
            (self.compute_expected_information_gain(tid, scope_set, balances), tid)
            for tid in zero_balance_pool
        ]
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)

        selected_ids = [tid for _, tid in scored[:max_questions]]
        questions: List[DiagnosticQuestion] = []
        for idx, tid in enumerate(selected_ids):
            ig = self.compute_expected_information_gain(tid, scope_set, balances)
            questions.append(
                self._build_diagnostic_question(
                    topic_id=tid,
                    question_index=idx,
                    prior_balance=0.0,
                    expected_ig=ig,
                    seed=seed,
                )
            )

        return questions

    def generate_fast_track_questions(
        self,
        topic_id: str,
        count: int = 2,
        base_seed: int = 100,
    ) -> List[DiagnosticQuestion]:
        """
        Generates 2 unassisted highest-KP verification questions for the
        "I Already Know This" fast-track lesson bypass (FR-2.3).
        """
        node = self.storage.get_topic_node(topic_id)
        if not node.knowledge_points:
            raise ValueError(f"TopicNode '{topic_id}' has no knowledge points.")

        highest_kp = max(node.knowledge_points, key=lambda k: k.kp_index)
        expected_time_sec = (
            max(3.0, node.automaticity_Defining_latency_ms / 1000.0)
            if (node.requires_automaticity and node.automaticity_Defining_latency_ms)
            else round(30.0 + 60.0 * node.base_difficulty, 1)
        )

        questions: List[DiagnosticQuestion] = []
        for i in range(count):
            instance: Optional[BlueprintInstance] = None
            try:
                shell = self.storage.get_kp_content_shell(highest_kp.kp_id)
                if shell.problem_blueprint is not None:
                    instance = self.solver.generate_variant(
                        highest_kp.kp_id, seed=base_seed + i * 17
                    )
            except (KeyError, ValueError):
                instance = None

            questions.append(
                DiagnosticQuestion(
                    question_index=i,
                    topic_id=topic_id,
                    kp_id=highest_kp.kp_id,
                    kp_index=highest_kp.kp_index,
                    expected_time_seconds=expected_time_sec,
                    prior_balance=0.0,
                    prior_mastery_probability=0.5,
                    expected_information_gain=1.0,
                    blueprint_instance=instance,
                )
            )

        return questions

    def evaluate_fast_track_bypass(
        self,
        student_id: str,
        topic_id: str,
        submissions: List[DiagnosticSubmission],
        expected_time_seconds: float = 60.0,
    ) -> FastTrackBypassResult:
        """
        Evaluates the "I Already Know This" Fast-Track lesson bypass (FR-2.3):
        Requires >= 2 unassisted questions from the topic's highest Knowledge Point
        answered Mathematically Correctly AND within expected_time_seconds * 1000.
        If passed, transitions topic immediately to MASTERED and unlocks eligible postrequisites.
        """
        node = self.storage.get_topic_node(topic_id)
        if not node.knowledge_points:
            raise ValueError(f"TopicNode '{topic_id}' has no knowledge points.")

        highest_kp = max(node.knowledge_points, key=lambda k: k.kp_index)
        existing_states = self.storage.get_student_topic_states(student_id)
        curr_state = existing_states.get(topic_id)
        prior_status: TopicMasteryStatus = curr_state.status if curr_state else "AVAILABLE"

        if len(submissions) < 2:
            return FastTrackBypassResult(
                student_id=student_id,
                topic_id=topic_id,
                bypass_granted=False,
                questions_evaluated=len(submissions),
                unassisted_correct_within_time=0,
                updated_status=prior_status,
                failure_reason="Fast-track bypass requires at least 2 highest-KP submissions.",
            )

        valid_count = 0
        failure_reason: Optional[str] = None

        for sub in submissions:
            # 1. Verify highest KP
            if sub.kp_id != highest_kp.kp_id:
                failure_reason = (
                    f"Submission KP '{sub.kp_id}' does not match highest KP '{highest_kp.kp_id}'."
                )
                break

            # 2. Verify unassisted (no reference peek, not I_DONT_KNOW)
            if sub.reference_peeked or sub.submission_flag == "I_DONT_KNOW":
                failure_reason = "Fast-track bypass requires unassisted responses without reference peeking."
                break

            # 3. Verify within expected time
            limit_sec = sub.expected_time_seconds or expected_time_seconds
            if node.requires_automaticity and node.automaticity_Defining_latency_ms:
                limit_ms = node.automaticity_Defining_latency_ms
            else:
                limit_ms = int(limit_sec * 1000)

            if sub.duration_ms > limit_ms:
                failure_reason = (
                    f"Response time {sub.duration_ms}ms exceeded expected time ceiling {limit_ms}ms."
                )
                break

            # 4. Verify mathematical correctness via SymPy solver
            target_ans = sub.target_answer
            if target_ans is None:
                try:
                    shell = self.storage.get_kp_content_shell(highest_kp.kp_id)
                    if shell.worked_example:
                        target_ans = shell.worked_example.final_answer
                except KeyError:
                    target_ans = None

            if target_ans is not None:
                is_correct = self.solver.grade_student_answer(
                    student_input=sub.student_answer,
                    target_answer=target_ans,
                    parameters=sub.parameters,
                )
            else:
                is_correct = bool(sub.student_answer.strip())

            if not is_correct:
                failure_reason = "Incorrect answer submitted during fast-track verification."
                break

            valid_count += 1

        if failure_reason is not None or valid_count < 2:
            return FastTrackBypassResult(
                student_id=student_id,
                topic_id=topic_id,
                bypass_granted=False,
                questions_evaluated=len(submissions),
                unassisted_correct_within_time=valid_count,
                updated_status=prior_status,
                failure_reason=failure_reason or "Insufficient valid fast-track responses.",
            )

        # All 2+ highest-KP questions passed unassisted within expected time -> MASTERED!
        now = datetime.now(timezone.utc)
        mastered_state = StudentTopicState(
            student_id=student_id,
            topic_id=topic_id,
            status="MASTERED",
            rep_num=max(1.0, curr_state.rep_num if curr_state else 1.0),
            stability_interval_days=2.0,
            memory_strength=1.0,
            last_reviewed_at=now,
            due_review_at=now + timedelta(days=2.0),
            student_ability=curr_state.student_ability if curr_state else 1.0,
            topic_difficulty=node.base_difficulty,
            speed=curr_state.speed if curr_state else 1.0,
            allow_implicit_fire_credit=True,
            diagnostic_plus_minus_balance=max(
                1.0, curr_state.diagnostic_plus_minus_balance if curr_state else 1.0
            ),
            is_conditionally_completed=False,
            current_kp_index=len(node.knowledge_points),
            consecutive_correct=node.mastery_gate_n,
        )
        self.storage.upsert_student_topic_state(mastered_state)
        existing_states[topic_id] = mastered_state

        # Check direct postrequisites that can now unlock from LOCKED -> AVAILABLE
        unlocked_postreqs: List[str] = []
        for post_id in self.storage.get_postrequisites(topic_id, max_hops=1):
            p_st = existing_states.get(post_id)
            if p_st and p_st.status in ("MASTERED", "MASTERED_FLOOR", "AVAILABLE", "IN_PROGRESS"):
                continue
            hard_parents = self.storage.get_hard_parents(post_id)
            if all(
                existing_states.get(hp) is not None
                and existing_states[hp].status in ("MASTERED", "MASTERED_FLOOR")
                for hp in hard_parents
            ):
                post_node = self.storage.get_topic_node(post_id)
                unlocked_state = StudentTopicState(
                    student_id=student_id,
                    topic_id=post_id,
                    status="AVAILABLE",
                    topic_difficulty=post_node.base_difficulty,
                )
                self.storage.upsert_student_topic_state(unlocked_state)
                unlocked_postreqs.append(post_id)

        return FastTrackBypassResult(
            student_id=student_id,
            topic_id=topic_id,
            bypass_granted=True,
            questions_evaluated=len(submissions),
            unassisted_correct_within_time=valid_count,
            updated_status="MASTERED",
            unlocked_postrequisite_ids=sorted(unlocked_postreqs),
            failure_reason=None,
        )
