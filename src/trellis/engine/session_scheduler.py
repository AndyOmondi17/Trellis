"""
src/trellis/engine/session_scheduler.py
Tier 4: Spaced Repetition Compression, Macro-Interleaving & Sticky Dashboard.

Implements:
  - FR-4.1: Spaced Repetition Compression Set-Cover Optimizer (ReviewCompressionOptimizer)
    - Submodular marginal credit gain: Delta f(c | acc) = sum min(1.0 - acc[r], flow(c -> r))
    - Exact HSRS early-review discounting and knock-out thresholds
    - Look-ahead fending for upcoming reviews (0.50 < memory <= 0.70)
    - Reverse redundancy-pruning pass
    - 3-tier candidate kind priority: FRONTIER_LESSON > DUE_REVIEW > MASTERED_REVIEW
    - Invalidation of ancestor candidates when cumulative credit crosses 1.0
  - FR-4.2: Macro-Interleaving & Review-Tsunami Throttle (InterleavingComposer)
    - 70% Frontier Lesson / 30% Compressed Review split at baseline pace
    - 25% minimum lesson floor (>= 1 lesson per 3 reviews) during review waves
    - Simultaneous dashboard cluster non-interference (MENU mode)
    - Consecutive sequence non-interference with Pigeonhole majority rule (SEQUENCE mode)
    - Controlled bottleneck relaxation when tasks are confined to a single prerequisite cluster
  - FR-4.3: Sticky 'Math Buffet' Task Dashboard Manager (SessionScheduler)
    - Composite Importance ranking: knockout + fending + dependents + enrolled + core + urgency
    - Deterministic sticky task reconciliation: lessons stay until completed or revoked;
      reviews stay until completed or implicitly knocked out
    - Opaque retention of Tier-5 task types (TARGETED_REMEDIATION, FACT_FLUENCY, TIMED_QUIZ)
    - Pure core algorithms over immutable ScheduleSnapshot for bit-identical cross-backend parity
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Set, Tuple
from pydantic import BaseModel, ConfigDict, Field

from trellis.domain.models import (
    DashboardTaskItem,
    ReviewCompressionPlan,
    StudentProfile,
    StudentTopicState,
    TaskType,
    TopicNode,
)
from trellis.engine.frontier_service import FrontierService
from trellis.engine.hsrs_fire_engine import CREDIT_TRIGGER_EPSILON, HSRSFireEngine
from trellis.ports.storage_port import TrellisStoragePort

# Candidate kinds within the scheduler
CandidateKind = Literal["FRONTIER_LESSON", "DUE_REVIEW", "MASTERED_REVIEW"]

# Explicit removal reasons for evicted dashboard items
RemovalReason = Literal[
    "COMPLETED",
    "REVIEW_RESOLVED",
    "INVALIDATED_RELOCKED",
    "INVALIDATED_HOLD",
]

_KIND_RANK: Dict[CandidateKind, int] = {
    "FRONTIER_LESSON": 2,
    "DUE_REVIEW": 1,
    "MASTERED_REVIEW": 0,
}


# ===========================================================================
# Section 1: Scheduler Configuration
# ===========================================================================

class SchedulerConfig(BaseModel):
    """Configuration parameters for Tier-4 scheduling, compression, and ranking."""
    model_config = ConfigDict(frozen=True)

    dashboard_capacity: int = Field(default=6, ge=1)
    baseline_lesson_pct: int = Field(default=70, ge=0, le=100)  # FR-4.2 70/30 baseline
    min_lesson_pct: int = Field(default=25, ge=0, le=100)       # FR-4.2 25% hard floor
    max_consecutive_reviews: int = Field(default=3, ge=1)       # Sequence interleaving cap
    fire_max_hops: int = Field(default=2, ge=1, le=3)           # FIRe propagation horizon
    fending_memory_low: float = Field(default=0.50, ge=0.0, le=1.0)
    fending_memory_high: float = Field(default=0.70, ge=0.0, le=1.0)
    lesson_raw_delta: float = Field(default=1.0, gt=0.0)
    review_raw_delta: float = Field(default=1.0, gt=0.0)

    # Composite Importance Weights (FR-4.3)
    w_knockout: float = 10.0
    w_fending: float = 3.0
    w_dependents: float = 1.0     # multiplied by log2(1 + in_scope_dependents_count)
    w_enrolled: float = 2.0       # bonus for enrolled course topics vs foundational gaps
    w_core: float = 1.0           # bonus for is_core == True
    w_urgency: float = 2.0        # reviews: multiplied by (1.0 - memory_strength)


# ===========================================================================
# Section 2: DTOs & Result Contracts
# ===========================================================================

class CoverageCandidate(BaseModel):
    """An eligible task candidate evaluated for review compression and scheduling."""
    model_config = ConfigDict(frozen=True)

    topic_id: str
    kind: CandidateKind
    domain_cluster: str
    course_id: str
    effective_delta: float
    flows: Dict[str, float]  # target_id -> round(effective_delta * compound_weight, 4)
    is_enrolled: bool
    is_core: bool
    dependents_count: int
    memory_strength: float


class ScheduleSnapshot(BaseModel):
    """Immutable point-in-time snapshot of graph topology and student state."""
    model_config = ConfigDict(frozen=True)

    student_id: str
    enrolled_course_id: str
    as_of: datetime
    states: Dict[str, StudentTopicState]
    due_review_ids: List[str]
    upcoming_review_ids: List[str]
    credit_targets: Set[str]
    candidates: Dict[str, CoverageCandidate]


class CompressionStep(BaseModel):
    """One step executed in greedy review compression."""
    model_config = ConfigDict(frozen=True)

    topic_id: str
    kind: CandidateKind
    knocked_out_review_ids: List[str]
    marginal_credit_gain: float
    fending_credit_gain: float


class CompressionResult(BaseModel):
    """Deterministic result of the review compression set-cover solver."""
    model_config = ConfigDict(frozen=True)

    steps: List[CompressionStep]
    selected_task_ids: List[str]
    covered_review_ids: List[str]
    fended_review_ids: List[str]
    uncovered_review_ids: List[str]
    efficiency_ratio: float
    task_knockout_attribution: Dict[str, int]

    def to_plan(self) -> ReviewCompressionPlan:
        return ReviewCompressionPlan(
            selected_task_ids=list(self.selected_task_ids),
            covered_review_ids=list(self.covered_review_ids),
            fended_review_ids=list(self.fended_review_ids),
            efficiency_ratio=self.efficiency_ratio,
        )


class ScheduledTask(BaseModel):
    """A task selected and ranked for presentation to the student."""
    model_config = ConfigDict(frozen=True)

    topic_id: str
    task_type: TaskType
    candidate_kind: Optional[CandidateKind] = None
    domain_cluster: str
    rank_score: float
    is_sticky: bool
    assigned_at: datetime
    knockout_count: int = 0
    fending_credit: float = 0.0


class SessionPlan(BaseModel):
    """Scheduled task bundle composed for a menu or ordered session queue."""
    model_config = ConfigDict(frozen=True)

    student_id: str
    mode: Literal["MENU", "SEQUENCE"]
    tasks: List[ScheduledTask]
    lesson_count: int
    review_count: int
    is_review_wave: bool
    lesson_floor_satisfied: bool
    interference_relaxed: bool
    relaxation_reason: Optional[str] = None


class DashboardRefreshResult(BaseModel):
    """Telemetry report emitted after refreshing a student's sticky dashboard."""
    model_config = ConfigDict(frozen=True)

    student_id: str
    menu: List[DashboardTaskItem]
    retained_ids: List[str]
    added_ids: List[str]
    removed: Dict[str, RemovalReason]
    compression: CompressionResult
    plan: SessionPlan


# ===========================================================================
# Section 3: Composite Importance Scoring
# ===========================================================================

def compute_composite_importance(
    cand: CoverageCandidate,
    config: SchedulerConfig,
    knockout_count: int,
    fending_credit: float,
) -> float:
    """
    Computes composite importance score for ranking dashboard candidates:
      Lessons: w_ko * KO + w_fend * F + w_dep * log2(1 + D) + w_enr * 1_enr + w_core * 1_core
      Reviews: w_ko * KO + w_fend * F + w_urg * (1.0 - memory_strength)
    """
    if cand.kind == "FRONTIER_LESSON":
        dep_score = math.log2(1.0 + max(0, cand.dependents_count))
        enr_score = 1.0 if cand.is_enrolled else 0.0
        core_score = 1.0 if cand.is_core else 0.0
        imp = (
            config.w_knockout * knockout_count
            + config.w_fending * fending_credit
            + config.w_dependents * dep_score
            + config.w_enrolled * enr_score
            + config.w_core * core_score
        )
    else:  # DUE_REVIEW or MASTERED_REVIEW
        urgency = max(0.0, 1.0 - cand.memory_strength)
        imp = (
            config.w_knockout * knockout_count
            + config.w_fending * fending_credit
            + config.w_urgency * urgency
        )
    return round(imp, 4)


# ===========================================================================
# Section 4: FR-4.1 Review Compression Optimizer (Pure Core)
# ===========================================================================

class ReviewCompressionOptimizer:
    """
    Greedy Submodular Set-Cover Optimizer with Wolsey marginal credit gain,
    look-ahead fending, reverse redundancy pruning, and exact HSRS parity.
    """

    def __init__(self, config: SchedulerConfig = SchedulerConfig()) -> None:
        self.config = config

    @staticmethod
    def _effective_flow(c: CoverageCandidate, r: str, acc_r: float) -> float:
        remaining = max(0.0, round(1.0 - acc_r, 4))
        if remaining <= CREDIT_TRIGGER_EPSILON:
            return 0.0
        if c.topic_id == r:
            return remaining  # Explicit review of r completes r
        return min(remaining, c.flows.get(r, 0.0))

    def _knockouts(
        self, c: CoverageCandidate, acc: Dict[str, float], uncovered: Set[str]
    ) -> Set[str]:
        hit: Set[str] = set()
        if c.topic_id in uncovered:
            hit.add(c.topic_id)
        for r, flow in c.flows.items():
            if r in uncovered and round(acc.get(r, 0.0) + flow, 4) + CREDIT_TRIGGER_EPSILON >= 1.0:
                hit.add(r)
        return hit

    def _marginal_gain(
        self, c: CoverageCandidate, acc: Dict[str, float], uncovered: Set[str]
    ) -> float:
        return round(
            sum(self._effective_flow(c, r, acc.get(r, 0.0)) for r in uncovered),
            4,
        )

    def _fending_gain(
        self, c: CoverageCandidate, upcoming: Set[str]
    ) -> float:
        return round(sum(f for r, f in c.flows.items() if r in upcoming), 4)

    def _is_eligible_mastered_review(
        self,
        c: CoverageCandidate,
        acc: Dict[str, float],
        uncovered: Set[str],
        upcoming: Set[str],
    ) -> bool:
        """
        Fix 6: A non-due MASTERED_REVIEW candidate may only be selected if it
        genuinely compresses (> 1.0 marginal gain or flows into >= 2 uncovered
        due reviews) or knocks out a due review while also fending an upcoming review.
        Never replaces a single due review 1-for-1 with zero extra benefit.
        """
        if c.kind != "MASTERED_REVIEW":
            return True
        if c.effective_delta <= 0.0:
            return False
        touched_due = [r for r in uncovered if c.flows.get(r, 0.0) > 0.0]
        if len(touched_due) >= 2:
            return True
        ko = self._knockouts(c, acc, uncovered)
        fend = self._fending_gain(c, upcoming)
        return len(ko) >= 1 and fend > 0.0

    def _greedy_key(
        self,
        c: CoverageCandidate,
        acc: Dict[str, float],
        uncovered: Set[str],
        upcoming: Set[str],
    ) -> Tuple[float, int, float, int, str]:
        gain = self._marginal_gain(c, acc, uncovered)
        ko = self._knockouts(c, acc, uncovered)
        fend = self._fending_gain(c, upcoming)
        return (
            -gain,                        # 1. Wolsey submodular marginal credit gain
            -len(ko),                     # 2. Immediate full knockouts on this step
            -fend,                        # 3. Look-ahead fending flow into U_up
            -_KIND_RANK[c.kind],          # 4. FRONTIER_LESSON (2) > DUE_REVIEW (1) > MASTERED_REVIEW (0)
            c.topic_id,                   # 5. Deterministic lexicographical tie-breaker
        )

    def _apply(
        self,
        c: CoverageCandidate,
        acc: Dict[str, float],
        credit_received: Dict[str, float],
        uncovered: Set[str],
        pool: Dict[str, CoverageCandidate],
    ) -> Set[str]:
        """
        Applies candidate c to simulated accumulation acc and credit_received.
        Removes knocked out reviews from uncovered.
        Removes any candidate from pool whose acc crosses 1.0 (Fix 5: HSRS invalidation).
        """
        knocked_out: Set[str] = set()
        if c.topic_id in uncovered:
            uncovered.discard(c.topic_id)
            knocked_out.add(c.topic_id)
            acc[c.topic_id] = 1.0
            credit_received[c.topic_id] = round(credit_received.get(c.topic_id, 0.0) + 1.0, 4)

        for target_id, flow in c.flows.items():
            prior_acc = acc.get(target_id, 0.0)
            new_acc = round(prior_acc + flow, 4)
            acc[target_id] = new_acc
            credit_received[target_id] = round(credit_received.get(target_id, 0.0) + flow, 4)
            if new_acc + CREDIT_TRIGGER_EPSILON >= 1.0:
                if target_id in uncovered:
                    uncovered.discard(target_id)
                    knocked_out.add(target_id)
                # Fix 5: remove any candidate in pool whose simulated credit crosses 1.0
                if target_id in pool:
                    del pool[target_id]

        if c.topic_id in pool:
            del pool[c.topic_id]

        return knocked_out

    def compute_fended_reviews(
        self,
        snapshot: ScheduleSnapshot,
        active_task_ids: List[str],
    ) -> List[str]:
        """
        Evaluates which upcoming reviews (0.50 < memory <= 0.70) are fended by
        the active tasks via acc >= 1.0 OR memory + 0.5 * credit_received > 0.70.
        """
        if not snapshot.upcoming_review_ids:
            return []

        acc: Dict[str, float] = {
            tid: round(st.cumulative_fire_credit, 4)
            for tid, st in snapshot.states.items()
            if tid in snapshot.credit_targets
        }
        credit_received: Dict[str, float] = {
            tid: 0.0 for tid in snapshot.credit_targets
        }

        for tid in active_task_ids:
            cand = snapshot.candidates.get(tid)
            if cand:
                if cand.topic_id in acc:
                    acc[cand.topic_id] = 1.0
                    credit_received[cand.topic_id] = round(
                        credit_received.get(cand.topic_id, 0.0) + 1.0, 4
                    )
                for target_id, flow in cand.flows.items():
                    acc[target_id] = round(acc.get(target_id, 0.0) + flow, 4)
                    credit_received[target_id] = round(
                        credit_received.get(target_id, 0.0) + flow, 4
                    )

        fended: List[str] = []
        for u in snapshot.upcoming_review_ids:
            st = snapshot.states.get(u)
            if not st:
                continue
            is_knockout = round(acc.get(u, 0.0), 4) + CREDIT_TRIGGER_EPSILON >= 1.0
            replenished = (
                round(st.memory_strength + 0.5 * credit_received.get(u, 0.0), 4)
                > self.config.fending_memory_high
            )
            if is_knockout or replenished:
                fended.append(u)
        return sorted(fended)

    def solve(
        self,
        snapshot: ScheduleSnapshot,
        preselected_task_ids: Optional[List[str]] = None,
    ) -> CompressionResult:
        """
        Finds the minimal candidate task set covering snapshot.due_review_ids,
        crediting any preselected sticky tasks first.
        """
        acc: Dict[str, float] = {
            tid: round(st.cumulative_fire_credit, 4)
            for tid, st in snapshot.states.items()
            if tid in snapshot.credit_targets
        }
        credit_received: Dict[str, float] = {
            tid: 0.0 for tid in snapshot.credit_targets
        }
        uncovered: Set[str] = set(snapshot.due_review_ids)
        upcoming: Set[str] = set(snapshot.upcoming_review_ids)
        pool: Dict[str, CoverageCandidate] = dict(snapshot.candidates)

        selected_steps: List[CompressionStep] = []
        preselected_applied: List[str] = []

        # 1. Apply preselected tasks first (retained sticky items)
        if preselected_task_ids:
            for pid in preselected_task_ids:
                cand = snapshot.candidates.get(pid)
                if cand:
                    gain = self._marginal_gain(cand, acc, uncovered)
                    fend = self._fending_gain(cand, upcoming)
                    ko = self._apply(cand, acc, credit_received, uncovered, pool)
                    preselected_applied.append(pid)

        # 2. Greedy selection while due reviews remain uncovered and candidates exist
        while uncovered and pool:
            candidates_eval: List[CoverageCandidate] = []
            for cid, cand in pool.items():
                if not self._is_eligible_mastered_review(cand, acc, uncovered, upcoming):
                    continue
                gain = self._marginal_gain(cand, acc, uncovered)
                if gain > 0.0 or cand.topic_id in uncovered:
                    candidates_eval.append(cand)

            if not candidates_eval:
                break

            best_cand = min(
                candidates_eval,
                key=lambda c: self._greedy_key(c, acc, uncovered, upcoming),
            )

            gain = self._marginal_gain(best_cand, acc, uncovered)
            fend = self._fending_gain(best_cand, upcoming)
            ko = self._apply(best_cand, acc, credit_received, uncovered, pool)

            selected_steps.append(
                CompressionStep(
                    topic_id=best_cand.topic_id,
                    kind=best_cand.kind,
                    knocked_out_review_ids=sorted(list(ko)),
                    marginal_credit_gain=gain,
                    fending_credit_gain=fend,
                )
            )

        # 3. Reverse redundancy-pruning pass
        covered_so_far = set(snapshot.due_review_ids) - uncovered
        if selected_steps and len(selected_steps) > 1:
            for i in range(len(selected_steps) - 1, -1, -1):
                remaining_steps = [s for j, s in enumerate(selected_steps) if j != i]

                sim_acc: Dict[str, float] = {
                    tid: round(st.cumulative_fire_credit, 4)
                    for tid, st in snapshot.states.items()
                    if tid in snapshot.credit_targets
                }
                for pid in preselected_applied:
                    pcand = snapshot.candidates.get(pid)
                    if pcand:
                        if pcand.topic_id in sim_acc:
                            sim_acc[pcand.topic_id] = 1.0
                        for target_id, flow in pcand.flows.items():
                            sim_acc[target_id] = round(sim_acc.get(target_id, 0.0) + flow, 4)
                for step in remaining_steps:
                    scand = snapshot.candidates.get(step.topic_id)
                    if scand:
                        if scand.topic_id in sim_acc:
                            sim_acc[scand.topic_id] = 1.0
                        for target_id, flow in scand.flows.items():
                            sim_acc[target_id] = round(sim_acc.get(target_id, 0.0) + flow, 4)

                still_fully_covered = True
                for r in covered_so_far:
                    if round(sim_acc.get(r, 0.0), 4) + CREDIT_TRIGGER_EPSILON < 1.0:
                        still_fully_covered = False
                        break

                if still_fully_covered:
                    selected_steps.pop(i)

        # 4. Final results calculation
        all_selected_ids = [s.topic_id for s in selected_steps]
        covered_list = sorted(list(set(snapshot.due_review_ids) - uncovered))
        uncovered_list = sorted(list(uncovered))

        fended_reviews = self.compute_fended_reviews(
            snapshot=snapshot,
            active_task_ids=preselected_applied + all_selected_ids,
        )

        attribution: Dict[str, int] = {}
        for s in selected_steps:
            cand = snapshot.candidates.get(s.topic_id)
            count = 0
            if cand:
                if cand.topic_id in covered_list:
                    count += 1
                for r in covered_list:
                    if r != cand.topic_id and cand.flows.get(r, 0.0) > 0.0:
                        count += 1
            attribution[s.topic_id] = max(1, count)

        eff = (
            round(len(covered_list) / max(1, len(all_selected_ids)), 2)
            if all_selected_ids
            else 1.0
        )

        return CompressionResult(
            steps=selected_steps,
            selected_task_ids=all_selected_ids,
            covered_review_ids=covered_list,
            fended_review_ids=fended_reviews,
            uncovered_review_ids=uncovered_list,
            efficiency_ratio=eff,
            task_knockout_attribution=attribution,
        )


# ===========================================================================
# Section 5: FR-4.2 Macro-Interleaving & Tsunami Throttle (Pure Core)
# ===========================================================================

class InterleavingComposer:
    """
    Composes balanced, non-interfering task schedules in MENU and SEQUENCE modes.
    Enforces the 70/30 baseline split, 25% minimum lesson floor, and Pigeonhole
    majority-cluster interleaving.
    """

    def __init__(self, config: SchedulerConfig = SchedulerConfig()) -> None:
        self.config = config

    def compose_menu(
        self,
        snapshot: ScheduleSnapshot,
        compression: CompressionResult,
        retained_items: List[DashboardTaskItem],
        cluster_map: Dict[str, str],
        importance_map: Dict[str, float],
    ) -> SessionPlan:
        """
        Composes a 6-slot (or capacity N) simultaneous dashboard menu adhering to:
          - 70/30 baseline split (4 lessons / 2 reviews for N=6)
          - 25% lesson floor (>= 2 lessons for N=6) during review waves
          - Simultaneous domain cluster non-interference
        """
        capacity = self.config.dashboard_capacity
        baseline_pct = self.config.baseline_lesson_pct
        min_lesson_pct = self.config.min_lesson_pct

        # Slot arithmetic
        review_baseline = max(1, round(capacity * (100 - baseline_pct) / 100))
        lesson_floor = math.ceil(capacity * min_lesson_pct / 100)
        review_max = max(review_baseline, capacity - lesson_floor)

        retained_ids = {it.topic_id for it in retained_items}
        retained_lessons = [it for it in retained_items if it.task_type == "FRONTIER_LESSON"]
        retained_reviews = [it for it in retained_items if it.task_type == "SPACED_REVIEW"]

        # Track occupied domain clusters from retained items
        occupied_clusters: Set[str] = {
            cluster_map.get(it.topic_id, "DEFAULT") for it in retained_items
        }

        # Build candidate review pool from compression steps (plus uncovered due reviews safeguard)
        review_task_ids: List[str] = []
        for step in compression.steps:
            if step.kind in ("DUE_REVIEW", "MASTERED_REVIEW") and step.topic_id not in retained_ids:
                if step.topic_id not in review_task_ids:
                    review_task_ids.append(step.topic_id)

        # Refinement 2 Safeguard: Any due review uncovered by retained items & selected steps
        for dr in snapshot.due_review_ids:
            if dr not in retained_ids and dr not in review_task_ids:
                if dr not in compression.covered_review_ids:
                    review_task_ids.append(dr)

        # Build candidate lesson pool from snapshot
        lesson_task_ids: List[str] = [
            cid
            for cid, c in snapshot.candidates.items()
            if c.kind == "FRONTIER_LESSON" and cid not in retained_ids
        ]

        total_pending_reviews = len(retained_reviews) + len(review_task_ids)
        is_review_wave = total_pending_reviews > review_baseline

        target_r = min(review_max, total_pending_reviews) if is_review_wave else min(review_baseline, total_pending_reviews)
        target_l = max(lesson_floor, capacity - target_r)

        free_slots = max(0, capacity - len(retained_items))
        needed_r = max(0, min(free_slots, target_r - len(retained_reviews)))
        needed_l = max(0, min(free_slots - needed_r, target_l - len(retained_lessons)))

        # Candidate queues sorted by (-rank_score, topic_id)
        avail_lessons = sorted(
            lesson_task_ids,
            key=lambda tid: (-importance_map.get(tid, 0.0), tid),
        )
        avail_reviews = sorted(
            review_task_ids,
            key=lambda tid: (-importance_map.get(tid, 0.0), tid),
        )

        selected_new_tasks: List[ScheduledTask] = []
        interference_relaxed = False
        relaxation_reason: Optional[str] = None

        def pick_next(
            cand_pool: List[str],
            occupied: Set[str],
            task_type: TaskType,
            cand_kind: CandidateKind,
        ) -> Optional[ScheduledTask]:
            nonlocal interference_relaxed, relaxation_reason
            if not cand_pool:
                return None
            # 1. Try non-colliding candidate
            for idx, tid in enumerate(cand_pool):
                cluster = cluster_map.get(tid, "DEFAULT")
                if cluster not in occupied:
                    cand_pool.pop(idx)
                    occupied.add(cluster)
                    return ScheduledTask(
                        topic_id=tid,
                        task_type=task_type,
                        candidate_kind=cand_kind,
                        domain_cluster=cluster,
                        rank_score=importance_map.get(tid, 0.0),
                        is_sticky=True,
                        assigned_at=snapshot.as_of,
                    )
            return None

        def pick_any(
            cand_pool: List[str],
            occupied: Set[str],
            task_type: TaskType,
            cand_kind: CandidateKind,
        ) -> Optional[ScheduledTask]:
            nonlocal interference_relaxed, relaxation_reason
            if not cand_pool:
                return None
            tid = cand_pool.pop(0)
            cluster = cluster_map.get(tid, "DEFAULT")
            occupied.add(cluster)
            interference_relaxed = True
            relaxation_reason = "SINGLE_CLUSTER_BOTTLENECK"
            return ScheduledTask(
                topic_id=tid,
                task_type=task_type,
                candidate_kind=cand_kind,
                domain_cluster=cluster,
                rank_score=importance_map.get(tid, 0.0),
                is_sticky=True,
                assigned_at=snapshot.as_of,
            )

        # Fill needed lesson slots
        slots_to_fill_l = needed_l
        while slots_to_fill_l > 0 and avail_lessons:
            task = pick_next(avail_lessons, occupied_clusters, "FRONTIER_LESSON", "FRONTIER_LESSON")
            if task:
                selected_new_tasks.append(task)
                slots_to_fill_l -= 1
            else:
                break

        # Fill needed review slots
        slots_to_fill_r = needed_r
        while slots_to_fill_r > 0 and avail_reviews:
            cand_id = avail_reviews[0]
            cand = snapshot.candidates.get(cand_id)
            kind: CandidateKind = cand.kind if cand else "DUE_REVIEW"
            task = pick_next(avail_reviews, occupied_clusters, "SPACED_REVIEW", kind)
            if task:
                selected_new_tasks.append(task)
                slots_to_fill_r -= 1
            else:
                break

        # Backfill remaining free slots
        has_available_lessons = bool(avail_lessons or len(retained_lessons) > 0)
        remaining_slots = free_slots - len(selected_new_tasks)
        while remaining_slots > 0:
            current_total_reviews = len(retained_reviews) + sum(
                1 for t in selected_new_tasks if t.task_type == "SPACED_REVIEW"
            )
            # If lessons exist or are retained, throttle reviews at review_max to protect lesson space.
            # If frontier lessons are completely exhausted, reviews can backfill all slots up to capacity.
            can_add_review = (
                current_total_reviews < review_max if has_available_lessons else current_total_reviews < capacity
            )

            task = None
            if can_add_review and avail_reviews:
                cand_id = avail_reviews[0]
                cand = snapshot.candidates.get(cand_id)
                kind = cand.kind if cand else "DUE_REVIEW"
                task = pick_next(avail_reviews, occupied_clusters, "SPACED_REVIEW", kind)
            if not task and avail_lessons:
                task = pick_next(avail_lessons, occupied_clusters, "FRONTIER_LESSON", "FRONTIER_LESSON")
            if not task and can_add_review and avail_reviews:
                # Relaxed review pick
                cand_id = avail_reviews[0]
                cand = snapshot.candidates.get(cand_id)
                kind = cand.kind if cand else "DUE_REVIEW"
                task = pick_any(avail_reviews, occupied_clusters, "SPACED_REVIEW", kind)
            if not task and avail_lessons:
                # Relaxed lesson pick
                task = pick_any(avail_lessons, occupied_clusters, "FRONTIER_LESSON", "FRONTIER_LESSON")

            if task:
                selected_new_tasks.append(task)
                remaining_slots -= 1
            else:
                break

        # Build final scheduled task objects combining retained and new
        final_scheduled: List[ScheduledTask] = []
        for it in retained_items:
            cand = snapshot.candidates.get(it.topic_id)
            kind = cand.kind if cand else ("FRONTIER_LESSON" if it.task_type == "FRONTIER_LESSON" else "DUE_REVIEW")
            final_scheduled.append(
                ScheduledTask(
                    topic_id=it.topic_id,
                    task_type=it.task_type,
                    candidate_kind=kind,
                    domain_cluster=cluster_map.get(it.topic_id, "DEFAULT"),
                    rank_score=importance_map.get(it.topic_id, it.rank_score),
                    is_sticky=it.is_sticky,
                    assigned_at=it.assigned_at,
                )
            )
        final_scheduled.extend(selected_new_tasks)

        final_lessons = sum(1 for t in final_scheduled if t.task_type == "FRONTIER_LESSON")
        final_reviews = sum(1 for t in final_scheduled if t.task_type == "SPACED_REVIEW")

        # Lesson floor satisfied: if there are reviews, final_lessons must satisfy min(lesson_floor, capacity)
        lesson_floor_satisfied = True
        if final_reviews > 0:
            lesson_floor_satisfied = final_lessons >= min(lesson_floor, capacity)

        return SessionPlan(
            student_id=snapshot.student_id,
            mode="MENU",
            tasks=final_scheduled,
            lesson_count=final_lessons,
            review_count=final_reviews,
            is_review_wave=is_review_wave,
            lesson_floor_satisfied=lesson_floor_satisfied,
            interference_relaxed=interference_relaxed,
            relaxation_reason=relaxation_reason,
        )

    def build_session_queue(
        self,
        tasks: List[ScheduledTask],
        max_tasks: int,
        config: Optional[SchedulerConfig] = None,
    ) -> SessionPlan:
        """
        Orders a session queue of up to max_tasks with:
          - Consecutive cluster non-interference
          - Exact Pigeonhole majority-cluster rule (Refinement 1)
          - max_consecutive_reviews cap
        """
        cfg = config or self.config
        if not tasks or max_tasks <= 0:
            return SessionPlan(
                student_id="",
                mode="SEQUENCE",
                tasks=[],
                lesson_count=0,
                review_count=0,
                is_review_wave=False,
                lesson_floor_satisfied=True,
                interference_relaxed=False,
            )

        student_id = ""
        pool = list(tasks)
        sequence: List[ScheduledTask] = []
        consec_reviews = 0
        prev_cluster: Optional[str] = None
        interference_relaxed = False
        relaxation_reason: Optional[str] = None

        limit = min(max_tasks, len(pool))

        for step in range(limit):
            rem_slots = limit - step
            has_lessons = any(t.task_type == "FRONTIER_LESSON" for t in pool)
            force_lesson = (consec_reviews >= cfg.max_consecutive_reviews) and has_lessons

            # Filter candidates by consecutive review cap
            type_eligible = [
                t for t in pool
                if (t.task_type == "FRONTIER_LESSON" if force_lesson else True)
            ]

            # Cluster non-interference candidates
            non_interfering = [
                t for t in type_eligible
                if t.domain_cluster != prev_cluster
            ]

            chosen: Optional[ScheduledTask] = None

            if non_interfering:
                # Pigeonhole Check: Count remaining tasks by cluster in non_interfering
                cluster_counts: Dict[str, int] = {}
                for t in pool:
                    cluster_counts[t.domain_cluster] = cluster_counts.get(t.domain_cluster, 0) + 1

                # If any cluster C != prev_cluster has count > floor(rem_slots / 2), force it
                pigeonhole_cluster: Optional[str] = None
                pigeonhole_thresh = math.floor(rem_slots / 2)
                for c, count in sorted(cluster_counts.items(), key=lambda kv: -kv[1]):
                    if c != prev_cluster and count > pigeonhole_thresh:
                        pigeonhole_cluster = c
                        break

                eligible_for_step = non_interfering
                if pigeonhole_cluster:
                    pigeonhole_cands = [t for t in non_interfering if t.domain_cluster == pigeonhole_cluster]
                    if pigeonhole_cands:
                        eligible_for_step = pigeonhole_cands

                # Tie-breaker: (-rank_score, assigned_at, topic_id)
                chosen = min(
                    eligible_for_step,
                    key=lambda t: (-t.rank_score, t.assigned_at, t.topic_id),
                )
            else:
                # Relaxed pick
                interference_relaxed = True
                relaxation_reason = "SINGLE_CLUSTER_BOTTLENECK"
                fallback_pool = type_eligible if type_eligible else pool
                chosen = min(
                    fallback_pool,
                    key=lambda t: (-t.rank_score, t.assigned_at, t.topic_id),
                )

            pool.remove(chosen)
            sequence.append(chosen)
            prev_cluster = chosen.domain_cluster
            if chosen.task_type == "SPACED_REVIEW":
                consec_reviews += 1
            else:
                consec_reviews = 0

        lesson_count = sum(1 for t in sequence if t.task_type == "FRONTIER_LESSON")
        review_count = sum(1 for t in sequence if t.task_type == "SPACED_REVIEW")

        return SessionPlan(
            student_id=student_id,
            mode="SEQUENCE",
            tasks=sequence,
            lesson_count=lesson_count,
            review_count=review_count,
            is_review_wave=(review_count > lesson_count),
            lesson_floor_satisfied=(lesson_count >= 1 if review_count > 0 else True),
            interference_relaxed=interference_relaxed,
            relaxation_reason=relaxation_reason,
        )


# ===========================================================================
# Section 6: FR-4.3 SessionScheduler Facade & Sticky Reconciliation
# ===========================================================================

class SessionScheduler:
    """
    Tier-4 Session Scheduler Facade. Coordinates snapshot construction,
    sticky dashboard reconciliation, set-cover review compression, macro-interleaving,
    and composite importance ranking through TrellisStoragePort.
    """

    def __init__(
        self,
        storage: TrellisStoragePort,
        frontier_service: Optional[FrontierService] = None,
        hsrs_engine: Optional[HSRSFireEngine] = None,
        config: SchedulerConfig = SchedulerConfig(),
    ) -> None:
        self.storage = storage
        self.frontier_service = frontier_service or FrontierService(storage)
        self.hsrs_engine = hsrs_engine or HSRSFireEngine(storage)
        self.config = config
        self.optimizer = ReviewCompressionOptimizer(config)
        self.composer = InterleavingComposer(config)

        # Static topology caches for sub-50ms refresh performance
        self._course_scope_cache: Dict[str, Set[str]] = {}
        self._dependents_count_cache: Dict[Tuple[str, str], int] = {}
        self._cluster_cache: Dict[str, str] = {}
        self._node_cache: Dict[str, TopicNode] = {}

    def _get_topic_node(self, topic_id: str) -> TopicNode:
        if topic_id not in self._node_cache:
            node = self.storage.get_topic_node(topic_id)
            self._node_cache[topic_id] = node
            self._cluster_cache[topic_id] = node.domain_cluster
        return self._node_cache[topic_id]

    def _get_course_scope(self, course_id: str) -> Set[str]:
        if course_id not in self._course_scope_cache:
            nodes = self.storage.get_course_subgraph(course_id)
            self._course_scope_cache[course_id] = {n.id for n in nodes}
            for n in nodes:
                self._node_cache[n.id] = n
                self._cluster_cache[n.id] = n.domain_cluster
        return self._course_scope_cache[course_id]

    def _get_dependents_count(self, course_id: str, topic_id: str) -> int:
        key = (course_id, topic_id)
        if key not in self._dependents_count_cache:
            postreqs = self.storage.get_postrequisites(topic_id)
            scope = self._get_course_scope(course_id)
            self._dependents_count_cache[key] = len(set(postreqs) & scope)
        return self._dependents_count_cache[key]

    def build_snapshot(
        self,
        student_id: str,
        as_of: datetime,
        enrolled_course_id: Optional[str] = None,
    ) -> ScheduleSnapshot:
        """
        Builds the immutable point-in-time ScheduleSnapshot by reading current
        student states and calculating exact HSRS effective flows for all candidates.
        """
        if enrolled_course_id is None:
            profile = self.storage.get_student_profile(student_id)
            enrolled_course_id = profile.enrolled_course_id

        course_scope = self._get_course_scope(enrolled_course_id)

        # 1. Available frontier lessons (with due review seam enabled)
        frontier_res = self.frontier_service.get_sequenced_frontier_result(
            student_id=student_id,
            enrolled_course_id=enrolled_course_id,
            include_due_reviews=True,
        )

        all_states = self.storage.get_student_topic_states(student_id)

        # 2. Identify due reviews R_due and upcoming reviews U_up
        due_review_ids: List[str] = []
        upcoming_review_ids: List[str] = []
        credit_targets: Set[str] = set()

        for tid, st in all_states.items():
            if st.status in ("MASTERED", "MASTERED_FLOOR", "DUE_FOR_REVIEW"):
                if st.allow_implicit_fire_credit and st.speed >= 1.0:
                    credit_targets.add(tid)

            is_on_hold = (
                st.status == "TIER_3_REMEDIATION"
                or (st.cooldown_until is not None and st.cooldown_until > as_of)
            )
            if not is_on_hold:
                if st.status == "DUE_FOR_REVIEW":
                    due_review_ids.append(tid)
                elif st.status == "MASTERED":
                    if self.config.fending_memory_low < st.memory_strength <= self.config.fending_memory_high:
                        upcoming_review_ids.append(tid)

        due_review_ids.sort()
        upcoming_review_ids.sort()

        candidates: Dict[str, CoverageCandidate] = {}

        # 3. Frontier Lesson Candidates F
        for node in frontier_res.prioritized_sequence:
            st = all_states.get(node.id)
            if st:
                if st.status == "TIER_3_REMEDIATION" or (st.cooldown_until and st.cooldown_until > as_of):
                    continue

            eff_delta = round(float(self.config.lesson_raw_delta), 4)
            enc_paths = self.storage.get_encompassed_ancestors(node.id, max_hops=self.config.fire_max_hops)
            deduped_weights = HSRSFireEngine.deduplicate_encompassing_weights(enc_paths, exclude_topic_id=node.id)

            flows: Dict[str, float] = {}
            for t_id, weight in deduped_weights.items():
                if t_id in credit_targets:
                    flow = round(eff_delta * weight, 4)
                    if flow > 0.0:
                        flows[t_id] = flow

            deps = self._get_dependents_count(enrolled_course_id, node.id)
            mem = st.memory_strength if st else 0.0

            candidates[node.id] = CoverageCandidate(
                topic_id=node.id,
                kind="FRONTIER_LESSON",
                domain_cluster=node.domain_cluster,
                course_id=node.course_id,
                effective_delta=eff_delta,
                flows=flows,
                is_enrolled=(node.id in course_scope),
                is_core=node.is_core,
                dependents_count=deps,
                memory_strength=mem,
            )

        # 4. Due Review Candidates R_due
        for rid in due_review_ids:
            st = all_states[rid]
            node = self._get_topic_node(rid)

            days_elapsed = HSRSFireEngine.compute_days_elapsed(
                st.last_reviewed_at, as_of, default_days=st.stability_interval_days
            )
            discount = self.hsrs_engine.calculate_early_review_discount(days_elapsed, st.stability_interval_days)
            eff_delta = round(float(self.config.review_raw_delta) * discount, 4)

            enc_paths = self.storage.get_encompassed_ancestors(rid, max_hops=self.config.fire_max_hops)
            deduped_weights = HSRSFireEngine.deduplicate_encompassing_weights(enc_paths, exclude_topic_id=rid)

            flows = {}
            for t_id, weight in deduped_weights.items():
                if t_id in credit_targets:
                    flow = round(eff_delta * weight, 4)
                    if flow > 0.0:
                        flows[t_id] = flow

            deps = self._get_dependents_count(enrolled_course_id, rid)

            candidates[rid] = CoverageCandidate(
                topic_id=rid,
                kind="DUE_REVIEW",
                domain_cluster=node.domain_cluster,
                course_id=node.course_id,
                effective_delta=eff_delta,
                flows=flows,
                is_enrolled=(rid in course_scope),
                is_core=node.is_core,
                dependents_count=deps,
                memory_strength=st.memory_strength,
            )

        # 5. Inverse Encompassing Discovery of M (Mastered Reviews)
        discovered_m: Set[str] = set()
        for r_target in due_review_ids + upcoming_review_ids:
            desc_paths = self.storage.get_encompassing_descendants(r_target, max_hops=self.config.fire_max_hops)
            deduped_desc = HSRSFireEngine.deduplicate_encompassing_weights(desc_paths, exclude_topic_id=r_target)
            for mid in deduped_desc.keys():
                if mid not in candidates and mid not in discovered_m:
                    st_m = all_states.get(mid)
                    if st_m and st_m.status in ("MASTERED", "MASTERED_FLOOR"):
                        is_on_hold = (
                            st_m.status == "TIER_3_REMEDIATION"
                            or (st_m.cooldown_until is not None and st_m.cooldown_until > as_of)
                        )
                        if not is_on_hold:
                            discovered_m.add(mid)

        for mid in sorted(discovered_m):
            st_m = all_states[mid]
            days_elapsed = HSRSFireEngine.compute_days_elapsed(
                st_m.last_reviewed_at, as_of, default_days=st_m.stability_interval_days
            )
            discount = self.hsrs_engine.calculate_early_review_discount(days_elapsed, st_m.stability_interval_days)
            eff_delta = round(float(self.config.review_raw_delta) * discount, 4)

            if eff_delta <= 0.0:
                continue

            node = self._get_topic_node(mid)
            enc_paths = self.storage.get_encompassed_ancestors(mid, max_hops=self.config.fire_max_hops)
            deduped_weights = HSRSFireEngine.deduplicate_encompassing_weights(enc_paths, exclude_topic_id=mid)

            flows = {}
            for t_id, weight in deduped_weights.items():
                if t_id in credit_targets:
                    flow = round(eff_delta * weight, 4)
                    if flow > 0.0:
                        flows[t_id] = flow

            deps = self._get_dependents_count(enrolled_course_id, mid)

            candidates[mid] = CoverageCandidate(
                topic_id=mid,
                kind="MASTERED_REVIEW",
                domain_cluster=node.domain_cluster,
                course_id=node.course_id,
                effective_delta=eff_delta,
                flows=flows,
                is_enrolled=(mid in course_scope),
                is_core=node.is_core,
                dependents_count=deps,
                memory_strength=st_m.memory_strength,
            )

        return ScheduleSnapshot(
            student_id=student_id,
            enrolled_course_id=enrolled_course_id,
            as_of=as_of,
            states=all_states,
            due_review_ids=due_review_ids,
            upcoming_review_ids=upcoming_review_ids,
            credit_targets=credit_targets,
            candidates=candidates,
        )

    def refresh_dashboard(
        self,
        student_id: str,
        as_of: Optional[datetime] = None,
    ) -> DashboardRefreshResult:
        """
        Executes the full FR-4.1-4.3 refresh pipeline:
          1. Sweep memory decay (MASTERED -> DUE_FOR_REVIEW)
          2. Build ScheduleSnapshot with active candidates and exact flows
          3. Reconcile existing sticky dashboard items
          4. Solve Set-Cover review compression with retained items preselected
          5. Rank candidates by Composite Importance
          6. Compose non-interfering 70/30 (or review wave) menu
          7. Persist updated menu to storage port
        """
        curr_as_of = as_of or datetime.now(timezone.utc)

        # 1. Sweep memory decay
        self.hsrs_engine.sweep_and_update_memory_decay(student_id, as_of=curr_as_of)

        # 2. Build point-in-time snapshot
        snapshot = self.build_snapshot(student_id, as_of=curr_as_of)

        # 3. Reconcile existing dashboard items
        existing_items = self.storage.get_dashboard_menu(student_id)
        retained: List[DashboardTaskItem] = []
        removed: Dict[str, RemovalReason] = {}

        for item in existing_items:
            st = snapshot.states.get(item.topic_id)
            if item.task_type == "FRONTIER_LESSON":
                if st is None or st.status in ("MASTERED", "MASTERED_FLOOR", "DUE_FOR_REVIEW"):
                    removed[item.topic_id] = "COMPLETED"
                elif st.status == "LOCKED" or not self.frontier_service.is_topic_unlocked(
                    student_id, item.topic_id, include_due_reviews=True
                ):
                    removed[item.topic_id] = "INVALIDATED_RELOCKED"
                elif st.status == "TIER_3_REMEDIATION" or (st.cooldown_until and st.cooldown_until > curr_as_of):
                    removed[item.topic_id] = "INVALIDATED_HOLD"
                else:
                    retained.append(item)
            elif item.task_type == "SPACED_REVIEW":
                if st is None:
                    removed[item.topic_id] = "REVIEW_RESOLVED"
                elif st.status == "DUE_FOR_REVIEW":
                    if st.status == "TIER_3_REMEDIATION" or (st.cooldown_until and st.cooldown_until > curr_as_of):
                        removed[item.topic_id] = "INVALIDATED_HOLD"
                    else:
                        retained.append(item)
                elif st.status in ("MASTERED", "MASTERED_FLOOR"):
                    # Case B: Sticky MASTERED_REVIEW compressor
                    last_rev = st.last_reviewed_at
                    # Normalize timezones for comparison
                    last_rev_dt = last_rev
                    item_dt = item.assigned_at
                    if last_rev_dt is not None:
                        if last_rev_dt.tzinfo is None and item_dt.tzinfo is not None:
                            last_rev_dt = last_rev_dt.replace(tzinfo=timezone.utc)
                        elif last_rev_dt.tzinfo is not None and item_dt.tzinfo is None:
                            item_dt = item_dt.replace(tzinfo=timezone.utc)

                    if last_rev_dt is not None and last_rev_dt >= item_dt:
                        removed[item.topic_id] = "REVIEW_RESOLVED"
                    elif st.cooldown_until and st.cooldown_until > curr_as_of:
                        removed[item.topic_id] = "INVALIDATED_HOLD"
                    else:
                        # Retain if it still flows positive FIRe credit into >= 1 active due review
                        cand = snapshot.candidates.get(item.topic_id)
                        flows_into_due = False
                        if cand:
                            for r_id in snapshot.due_review_ids:
                                if cand.flows.get(r_id, 0.0) > 0.0:
                                    flows_into_due = True
                                    break
                        if flows_into_due:
                            retained.append(item)
                        else:
                            removed[item.topic_id] = "REVIEW_RESOLVED"
                else:
                    removed[item.topic_id] = "INVALIDATED_RELOCKED"
            else:
                # Opaque Tier-5 tasks: TARGETED_REMEDIATION, FACT_FLUENCY, TIMED_QUIZ
                retained.append(item)

        retained_ids = [it.topic_id for it in retained]

        # 4. Solve Set-Cover Review Compression
        compression = self.optimizer.solve(
            snapshot=snapshot,
            preselected_task_ids=retained_ids,
        )

        # 5. Build Cluster & Importance Maps
        cluster_map: Dict[str, str] = {}
        importance_map: Dict[str, float] = {}

        for tid, cand in snapshot.candidates.items():
            cluster_map[tid] = cand.domain_cluster
            ko_count = compression.task_knockout_attribution.get(tid, 0)
            fend_flow = sum(cand.flows.get(u, 0.0) for u in snapshot.upcoming_review_ids)
            importance_map[tid] = compute_composite_importance(
                cand=cand,
                config=self.config,
                knockout_count=ko_count,
                fending_credit=round(fend_flow, 4),
            )

        for it in retained:
            if it.topic_id not in cluster_map:
                try:
                    node = self._get_topic_node(it.topic_id)
                    cluster_map[it.topic_id] = node.domain_cluster
                except KeyError:
                    cluster_map[it.topic_id] = "DEFAULT"

        # 6. Compose Balanced Menu
        plan = self.composer.compose_menu(
            snapshot=snapshot,
            compression=compression,
            retained_items=retained,
            cluster_map=cluster_map,
            importance_map=importance_map,
        )

        # Build persisted dashboard task items
        # Canonical order: (-rank_score, assigned_at, topic_id)
        final_menu: List[DashboardTaskItem] = []
        for t in plan.tasks:
            final_menu.append(
                DashboardTaskItem(
                    student_id=student_id,
                    topic_id=t.topic_id,
                    task_type=t.task_type,
                    rank_score=t.rank_score,
                    is_sticky=t.is_sticky,
                    assigned_at=t.assigned_at,
                )
            )

        final_menu.sort(
            key=lambda it: (-it.rank_score, it.assigned_at, it.topic_id)
        )

        # 7. Persist to storage port
        self.storage.save_dashboard_menu(student_id, final_menu)

        final_ids = [it.topic_id for it in final_menu]
        retained_kept_ids = [tid for tid in retained_ids if tid in final_ids]
        added_ids = [tid for tid in final_ids if tid not in retained_ids]

        return DashboardRefreshResult(
            student_id=student_id,
            menu=final_menu,
            retained_ids=retained_kept_ids,
            added_ids=added_ids,
            removed=removed,
            compression=compression,
            plan=plan,
        )
