"""
src/trellis/engine/hsrs_fire_engine.py
Tier 3: Deterministic Mastery State Machine & Hierarchical Spaced Repetition Engine.

Implements:
  - FR-3.2: Student-Topic Learning Speed Calibration & Preventative Remediation Gate
    - Per-topic student_ability initialized from Local Neighborhood mean.
    - EWMA ability updates with multi-path deduplicated propagation along ENCOMPASSES edges:
      * Correct answers propagate downward to simpler encompassed skills.
      * Incorrect answers propagate upward to more advanced encompassing skills.
    - Deterministic speed = student_ability / topic_difficulty in [0.25, 2.50].
    - Preventative Remediation Gate: blocks implicit FIRe credit (allow_implicit_fire_credit = False)
      when speed < 1.0 to force explicit spaced reviews.
  - FR-3.3: HSRS Memory Decay & Bidirectional FIRe Propagation Engine
    - Half-life memory decay: memory = (0.5)^(days_elapsed / stability_interval_days).
    - Status transition: MASTERED -> DUE_FOR_REVIEW strictly when memory <= 0.50.
    - Early review discounting: rawDelta discounted when days_elapsed < stability_interval_days,
      with target interval expansion proportionally damped to prevent cramming inflation.
    - Downward Credit Trickle-Down: passing advanced node propagates discounted credit
      Delta_C = effective_delta * max_paths(prod(w_i)) down ENCOMPASSES edges to MASTERED
      ancestors with allow_implicit_fire_credit == True. Expands interval
      I_{k+1} = I_k * (1.0 + 0.5 * speed) when cumulative_fire_credit >= 1.0.
    - Upward Penalty Trickle-Up: failing simpler node (is_success == False or rawDelta < 0)
      applies overdue decay >= 1.0 ("summer slide"), resets stale fractional credit,
      and propagates penalty upward along ENCOMPASSES edges to encompassing descendants.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, ConfigDict, Field

from trellis.domain.models import (
    LocalNeighborhood,
    StudentTopicState,
    TopicNode,
    WeightedEncompassingPath,
)
from trellis.ports.storage_port import TrellisStoragePort

# Floating-point threshold tolerance to prevent IEEE-754 representation drift
# (e.g. 3 * 0.333333... = 0.9999999999999999) from missing the 1.0 credit trigger.
_CREDIT_TRIGGER_EPSILON: float = 1e-6
CREDIT_TRIGGER_EPSILON: float = _CREDIT_TRIGGER_EPSILON


# ---------------------------------------------------------------------------
# DTOs & Telemetry
# ---------------------------------------------------------------------------

class PropagatedAbilityDelta(BaseModel):
    """Telemetry record for ability updates propagated along graph edges."""
    model_config = ConfigDict(frozen=True)

    topic_id: str
    direction: str  # "DOWNWARD_ENCOMPASSED" or "UPWARD_ENCOMPASSING"
    compound_weight: float
    prior_ability: float
    new_ability: float
    prior_speed: float
    new_speed: float
    allow_implicit_fire_credit: bool


class AttemptCalibrationResult(BaseModel):
    """Deterministic result emitted after recording an interaction attempt."""
    student_id: str
    topic_id: str
    is_correct: bool
    prior_ability: float
    new_ability: float
    topic_difficulty: float
    prior_speed: float
    new_speed: float
    allow_implicit_fire_credit: bool
    propagated_updates: List[PropagatedAbilityDelta] = Field(default_factory=list)


class FireCreditTransfer(BaseModel):
    """Telemetry record for implicit credit transferred downward to an ancestor."""
    model_config = ConfigDict(frozen=True)

    ancestor_id: str
    compound_weight: float
    credit_delta: float
    cumulative_credit_before: float
    cumulative_credit_after: float
    interval_expanded: bool
    prior_interval: float
    new_interval: float
    status_transitioned: Optional[str] = None


class PenaltyTransfer(BaseModel):
    """Telemetry record for penalty transferred upward to an encompassing descendant."""
    model_config = ConfigDict(frozen=True)

    descendant_id: str
    compound_weight: float
    penalty_delta: float
    prior_interval: float
    new_interval: float
    prior_rep_num: float
    new_rep_num: float
    prior_memory: float
    new_memory: float
    status_transitioned: Optional[str] = None


class RepetitionResult(BaseModel):
    """Full deterministic result emitted after processing an explicit review or lesson completion."""
    student_id: str
    topic_id: str
    is_success: bool
    raw_delta: float
    effective_delta: float
    days_elapsed: float
    decay_factor: float
    discount_factor: float
    target_rep_num_after: float
    target_interval_after: float
    target_memory_after: float
    downward_fire_transfers: List[FireCreditTransfer] = Field(default_factory=list)
    upward_penalty_transfers: List[PenaltyTransfer] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# FR-3.2 & FR-3.3: HSRS & FIRe Engine
# ---------------------------------------------------------------------------

class HSRSFireEngine:
    """
    Hierarchical Spaced Repetition System (HSRS) &
    Fractional Implicit Repetition (FIRe) Algorithmic Engine.
    Interacts with persistence exclusively through TrellisStoragePort.
    Zero raw queries or direct database access permitted.
    """

    def __init__(self, storage: TrellisStoragePort) -> None:
        self.storage = storage

    # -----------------------------------------------------------------------
    # Internal Helpers: Multi-Path Deduplication & Timezone Normalization
    # -----------------------------------------------------------------------

    @staticmethod
    def _deduplicate_encompassing_weights(
        paths: List[WeightedEncompassingPath],
        exclude_topic_id: Optional[str] = None,
    ) -> Dict[str, float]:
        """
        Deduplicates multi-path DAG branches using the (max, *) algebraic semiring:
          w(u) = max_{paths p: v ~> u} (compound_weight(p))
        Prevents redundant double-counting across parallel diamond branches or
        coexisting 1-hop explicit and 2-hop transitive encompassing edges.
        Clamps resulting weight strictly to (0.0, 1.0].
        """
        max_weights: Dict[str, float] = {}
        for path in paths:
            target_id = path.ancestor_id
            if exclude_topic_id and target_id == exclude_topic_id:
                continue
            clamped_w = max(0.0, min(1.0, float(path.compound_weight)))
            if clamped_w > 0.0:
                max_weights[target_id] = max(
                    max_weights.get(target_id, 0.0), clamped_w
                )
        return max_weights

    @staticmethod
    def _compute_days_elapsed(
        last_reviewed_at: Optional[datetime],
        now: datetime,
        default_days: float,
    ) -> float:
        """
        Safely computes elapsed days between last_reviewed_at and now,
        normalizing offset-naive and offset-aware UTC timestamps.
        """
        if last_reviewed_at is None:
            return max(0.0, float(default_days))

        last_dt = last_reviewed_at
        curr_dt = now
        if last_dt.tzinfo is None and curr_dt.tzinfo is not None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        elif last_dt.tzinfo is not None and curr_dt.tzinfo is None:
            curr_dt = curr_dt.replace(tzinfo=timezone.utc)

        return max(0.0, (curr_dt - last_dt).total_seconds() / 86400.0)

    # Public static aliases for tier engine interoperability
    CREDIT_TRIGGER_EPSILON: float = _CREDIT_TRIGGER_EPSILON
    deduplicate_encompassing_weights = staticmethod(_deduplicate_encompassing_weights)
    compute_days_elapsed = staticmethod(_compute_days_elapsed)

    # -----------------------------------------------------------------------
    # FR-3.2: Learning Speed Calibration & Preventative Remediation Gate
    # -----------------------------------------------------------------------

    def calibrate_learning_speed(
        self, student_ability: float, topic_difficulty: float
    ) -> float:
        """
        Calculates deterministic student-topic learning speed:
          speed = student_ability / max(0.1, topic_difficulty)
        Clamped strictly to [0.25, 2.50] as defined in PRD FR-3.2.
        """
        eff_diff = max(0.1, float(topic_difficulty))
        raw_speed = float(student_ability) / eff_diff
        clamped = max(0.25, min(2.50, round(raw_speed, 4)))
        return clamped

    def evaluate_preventative_gate(self, speed: float) -> bool:
        """
        Preventative Remediation Gating Rule:
        If speed < 1.0, returns False (allow_implicit_fire_credit = False),
        shutting down incoming implicit FIRe credit and forcing explicit reviews.
        If speed >= 1.0, returns True.
        """
        return speed >= 1.0

    def initialize_student_ability(self, student_id: str, topic_id: str) -> float:
        """
        Initializes student ability for a topic from its Local Neighborhood mean:
        consisting of direct prerequisites, key prerequisite, encompassings,
        and cluster siblings (The Math Academy Way, Ch. 29).
        Falls back to baseline 1.0 if no neighbors have recorded ability.
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
            student_id, sorted(neighbor_ids)
        )
        # Prioritize neighbors that have active mastery/practice history or non-default ability
        calibrated_abilities = [
            st.student_ability
            for st in neighbor_states.values()
            if st.student_ability is not None
            and st.student_ability > 0.0
            and (
                st.status in ("MASTERED", "MASTERED_FLOOR", "DUE_FOR_REVIEW", "IN_PROGRESS", "TIER_3_REMEDIATION")
                or abs(st.student_ability - 1.0) > 1e-6
            )
        ]

        abilities = calibrated_abilities or [
            st.student_ability
            for st in neighbor_states.values()
            if st.student_ability is not None and st.student_ability > 0.0
        ]

        if not abilities:
            return 1.0

        mean_ability = sum(abilities) / len(abilities)
        return max(0.1, min(3.0, round(mean_ability, 4)))

    def get_or_create_student_topic_state(
        self, student_id: str, topic_id: str
    ) -> StudentTopicState:
        """
        Retrieves existing StudentTopicState or instantiates a fresh state
        with ability initialized from the Local Neighborhood mean.
        """
        states = self.storage.get_student_topic_states(student_id, [topic_id])
        st = states.get(topic_id)
        if st is not None:
            return st

        try:
            node = self.storage.get_topic_node(topic_id)
            diff = node.base_difficulty
        except KeyError:
            diff = 0.5

        init_ability = self.initialize_student_ability(student_id, topic_id)
        speed = self.calibrate_learning_speed(init_ability, diff)
        allow_fire = self.evaluate_preventative_gate(speed)

        new_st = StudentTopicState(
            student_id=student_id,
            topic_id=topic_id,
            status="LOCKED",
            student_ability=init_ability,
            topic_difficulty=diff,
            speed=speed,
            allow_implicit_fire_credit=allow_fire,
        )
        self.storage.upsert_student_topic_state(new_st)
        return new_st

    def record_attempt(
        self,
        student_id: str,
        topic_id: str,
        is_correct: bool,
        alpha: float = 0.20,
        propagate: bool = True,
        max_hops: int = 2,
    ) -> AttemptCalibrationResult:
        """
        Updates student_ability via Exponentially Weighted Moving Average (EWMA),
        recalibrates speed, evaluates preventative gate, and propagates updates
        across deduplicated multi-hop ENCOMPASSES paths:
          - Correct answer: propagates downward to simpler encompassed skills.
          - Incorrect answer: propagates upward to more advanced encompassing skills.
        """
        eff_alpha = max(0.01, min(1.0, float(alpha)))
        state = self.get_or_create_student_topic_state(student_id, topic_id)
        prior_ability = state.student_ability
        prior_speed = state.speed

        target_obs = 1.0 if is_correct else 0.0
        new_ability = (1.0 - eff_alpha) * prior_ability + eff_alpha * target_obs
        new_ability = max(0.1, min(3.0, round(new_ability, 4)))

        new_speed = self.calibrate_learning_speed(new_ability, state.topic_difficulty)
        allow_fire = self.evaluate_preventative_gate(new_speed)

        # Update primary topic state
        state.student_ability = new_ability
        state.speed = new_speed
        state.allow_implicit_fire_credit = allow_fire
        self.storage.upsert_student_topic_state(state)

        propagated_deltas: List[PropagatedAbilityDelta] = []

        if propagate:
            if is_correct:
                # Downward propagation to simpler encompassed skills (deduplicated by max weight)
                encompassed_paths = self.storage.get_encompassed_ancestors(
                    topic_id, max_hops=max_hops
                )
                max_weights = self._deduplicate_encompassing_weights(
                    encompassed_paths, exclude_topic_id=topic_id
                )
                for anc_id in sorted(max_weights.keys()):
                    w = max_weights[anc_id]
                    delta = self._apply_propagated_update(
                        student_id=student_id,
                        target_id=anc_id,
                        observation=1.0,
                        effective_alpha=eff_alpha * w,
                        direction="DOWNWARD_ENCOMPASSED",
                        compound_weight=w,
                    )
                    if delta:
                        propagated_deltas.append(delta)
            else:
                # Upward penalty propagation to encompassing descendants (deduplicated by max weight)
                encompassing_paths = self.storage.get_encompassing_descendants(
                    topic_id, max_hops=max_hops
                )
                max_weights = self._deduplicate_encompassing_weights(
                    encompassing_paths, exclude_topic_id=topic_id
                )
                for desc_id in sorted(max_weights.keys()):
                    w = max_weights[desc_id]
                    delta = self._apply_propagated_update(
                        student_id=student_id,
                        target_id=desc_id,
                        observation=0.0,
                        effective_alpha=eff_alpha * w,
                        direction="UPWARD_ENCOMPASSING",
                        compound_weight=w,
                    )
                    if delta:
                        propagated_deltas.append(delta)

        return AttemptCalibrationResult(
            student_id=student_id,
            topic_id=topic_id,
            is_correct=is_correct,
            prior_ability=prior_ability,
            new_ability=new_ability,
            topic_difficulty=state.topic_difficulty,
            prior_speed=prior_speed,
            new_speed=new_speed,
            allow_implicit_fire_credit=allow_fire,
            propagated_updates=propagated_deltas,
        )

    def _apply_propagated_update(
        self,
        student_id: str,
        target_id: str,
        observation: float,
        effective_alpha: float,
        direction: str,
        compound_weight: float,
    ) -> Optional[PropagatedAbilityDelta]:
        """Helper to apply attenuated EWMA update to a neighboring topic."""
        states = self.storage.get_student_topic_states(student_id, [target_id])
        target_state = states.get(target_id)
        if target_state is None:
            return None

        p_ability = target_state.student_ability
        p_speed = target_state.speed

        updated_ability = (1.0 - effective_alpha) * p_ability + effective_alpha * observation
        updated_ability = max(0.1, min(3.0, round(updated_ability, 4)))

        updated_speed = self.calibrate_learning_speed(
            updated_ability, target_state.topic_difficulty
        )
        new_allow_fire = self.evaluate_preventative_gate(updated_speed)

        target_state.student_ability = updated_ability
        target_state.speed = updated_speed
        target_state.allow_implicit_fire_credit = new_allow_fire
        self.storage.upsert_student_topic_state(target_state)

        return PropagatedAbilityDelta(
            topic_id=target_id,
            direction=direction,
            compound_weight=round(compound_weight, 4),
            prior_ability=p_ability,
            new_ability=updated_ability,
            prior_speed=p_speed,
            new_speed=updated_speed,
            allow_implicit_fire_credit=new_allow_fire,
        )

    def calibrate_and_persist_topic_speed(
        self, student_id: str, topic_id: str
    ) -> StudentTopicState:
        """
        Recalibrates speed and updates allow_implicit_fire_credit for an existing state.
        """
        state = self.get_or_create_student_topic_state(student_id, topic_id)
        speed = self.calibrate_learning_speed(
            state.student_ability, state.topic_difficulty
        )
        allow_fire = self.evaluate_preventative_gate(speed)

        state.speed = speed
        state.allow_implicit_fire_credit = allow_fire
        self.storage.upsert_student_topic_state(state)
        return state

    # -----------------------------------------------------------------------
    # FR-3.3: HSRS Memory Decay & Bidirectional FIRe Propagation Engine
    # -----------------------------------------------------------------------

    def calculate_memory_strength(
        self, days_elapsed: float, stability_interval_days: float
    ) -> float:
        """
        Calculates half-life memory decay:
          memory = (0.5)^(days_elapsed / stability_interval_days)
        Clamped strictly to [0.0, 1.0].
        """
        if stability_interval_days <= 0:
            return 0.0
        if days_elapsed <= 0:
            return 1.0

        exponent = float(days_elapsed) / float(stability_interval_days)
        decay = math.pow(0.5, exponent)
        return max(0.0, min(1.0, round(decay, 4)))

    def is_due_for_review(
        self, memory_strength: float, threshold: float = 0.50
    ) -> bool:
        """
        Flags whether a topic is due for review based on memory strength:
          memory_strength <= 0.50
        """
        return memory_strength <= threshold

    def calculate_early_review_discount(
        self, days_elapsed: float, stability_interval_days: float
    ) -> float:
        """
        Discounts positive rawDelta if repetition occurred early:
        If days_elapsed >= stability_interval_days: discount factor = 1.0 (full credit).
        If days_elapsed < stability_interval_days: discount factor = days_elapsed / interval.
        Clamped to [0.0, 1.0].
        """
        if stability_interval_days <= 0:
            return 1.0
        ratio = float(days_elapsed) / float(stability_interval_days)
        return max(0.0, min(1.0, round(ratio, 4)))

    def calculate_overdue_decay(
        self, days_elapsed: float, stability_interval_days: float
    ) -> float:
        """
        Calculates the overdue decay factor for failed repetitions ("summer slide"):
          decay = max(1.0, days_elapsed / stability_interval_days)
        Starts at 1.0 and grows larger as repetition becomes severely overdue.
        Clamped to [1.0, 5.0] to maintain numerical stability.
        """
        eff_interval = max(0.5, float(stability_interval_days))
        ratio = float(days_elapsed) / eff_interval
        decay = max(1.0, min(5.0, round(ratio, 4)))
        return decay

    def sweep_and_update_memory_decay(
        self, student_id: str, as_of: Optional[datetime] = None
    ) -> List[StudentTopicState]:
        """
        Evaluates memory decay for all MASTERED topics of a student:
          memory = (0.5)^(days_elapsed / stability_interval_days)
        Transitions MASTERED -> DUE_FOR_REVIEW strictly when memory <= 0.50.
        Returns the deterministically ordered list of states transitioned to DUE_FOR_REVIEW.
        """
        now = as_of or datetime.now(timezone.utc)
        all_states = self.storage.get_student_topic_states(student_id)

        transitioned: List[StudentTopicState] = []

        for tid in sorted(all_states.keys()):
            state = all_states[tid]
            if state.status not in ("MASTERED", "MASTERED_FLOOR"):
                continue

            days_elapsed = self._compute_days_elapsed(
                state.last_reviewed_at, now, default_days=0.0
            )
            mem = self.calculate_memory_strength(
                days_elapsed, state.stability_interval_days
            )
            state.memory_strength = mem

            if mem <= 0.50 and state.status == "MASTERED":
                state.status = "DUE_FOR_REVIEW"
                state.due_review_at = now
                self.storage.upsert_student_topic_state(state)
                transitioned.append(state)
            else:
                self.storage.upsert_student_topic_state(state)

        return transitioned

    def process_explicit_repetition(
        self,
        student_id: str,
        topic_id: str,
        is_success: bool,
        raw_delta: float = 1.0,
        as_of: Optional[datetime] = None,
        max_hops: int = 2,
    ) -> RepetitionResult:
        """
        Executes a complete HSRS review or practice pass/fail event:
          - If passed (is_success == True and raw_delta >= 0):
            * Discounts rawDelta if repetition was early (days_elapsed < interval).
            * Increases rep_num and expands stability interval proportionally:
              I_{k+1} = I_k * (1.0 + 0.5 * speed * min(1.0, effective_delta)).
            * Trickles implicit credit Delta_C = effective_delta * max_paths(prod(w_i))
              downward to MASTERED/DUE_FOR_REVIEW ancestors with allow_implicit_fire_credit == True.
            * Expands ancestor stability intervals when cumulative_fire_credit >= 1.0.
          - If failed (is_success == False or raw_delta < 0):
            * Applies overdue decay factor >= 1.0 ("summer slide").
            * Reduces rep_num, contracts interval (floor >= 0.5 days), resets stale
              cumulative_fire_credit, and sets status = DUE_FOR_REVIEW.
            * Trickles penalty upward to encompassing descendants, decaying their memory
              and flagging DUE_FOR_REVIEW if memory <= 0.50.
        """
        now = as_of or datetime.now(timezone.utc)
        state = self.get_or_create_student_topic_state(student_id, topic_id)

        # Negative raw_delta unconditionally routes to the failed repetition branch
        passed = bool(is_success and raw_delta >= 0.0)

        # 1. Calculate days elapsed since last review
        days_elapsed = self._compute_days_elapsed(
            state.last_reviewed_at,
            now,
            default_days=state.stability_interval_days,
        )

        downward_transfers: List[FireCreditTransfer] = []
        upward_transfers: List[PenaltyTransfer] = []

        if passed:
            # Positive repetition: apply early-repetition discount
            discount = self.calculate_early_review_discount(
                days_elapsed, state.stability_interval_days
            )
            decay = 1.0
            effective_delta = round(float(raw_delta) * discount, 4)

            # Update target node
            rep_delta = round(state.speed * effective_delta, 4)
            state.rep_num = round(state.rep_num + rep_delta, 4)

            # Interval expansion scaled by min(1.0, effective_delta) so premature
            # cramming (effective_delta == 0) cannot artificially inflate intervals,
            # while on-time reviews (effective_delta >= 1.0) expand by (1.0 + 0.5 * speed).
            expansion_scale = min(1.0, effective_delta)
            expansion_mult = 1.0 + 0.5 * state.speed * expansion_scale
            new_interval = round(state.stability_interval_days * expansion_mult, 4)
            state.stability_interval_days = max(1.0, new_interval)
            state.memory_strength = 1.0
            state.last_reviewed_at = now
            state.due_review_at = now + timedelta(days=state.stability_interval_days)

            if state.status in ("DUE_FOR_REVIEW", "IN_PROGRESS", "AVAILABLE", "TIER_3_REMEDIATION", "LOCKED"):
                state.status = "MASTERED"

            self.storage.upsert_student_topic_state(state)

            # Downward FIRe Credit Trickle-Down along ENCOMPASSES edges
            encompassed_paths = self.storage.get_encompassed_ancestors(
                topic_id, max_hops=max_hops
            )
            max_weights_by_ancestor = self._deduplicate_encompassing_weights(
                encompassed_paths, exclude_topic_id=topic_id
            )

            if max_weights_by_ancestor:
                ancestor_states = self.storage.get_student_topic_states(
                    student_id, sorted(max_weights_by_ancestor.keys())
                )

                for anc_id in sorted(max_weights_by_ancestor.keys()):
                    weight = max_weights_by_ancestor[anc_id]
                    anc_state = ancestor_states.get(anc_id)
                    # Implicit credit only trickles to MASTERED or DUE_FOR_REVIEW topics
                    if not anc_state or anc_state.status not in ("MASTERED", "MASTERED_FLOOR", "DUE_FOR_REVIEW"):
                        continue

                    # Preventative remediation gate: strictly block implicit credit if speed < 1.0
                    if not anc_state.allow_implicit_fire_credit or anc_state.speed < 1.0:
                        continue

                    credit_delta = round(effective_delta * weight, 4)
                    prior_cred = anc_state.cumulative_fire_credit
                    new_cred = round(prior_cred + credit_delta, 4)
                    anc_state.cumulative_fire_credit = new_cred

                    prior_int = anc_state.stability_interval_days
                    expanded = False
                    status_trans = None

                    # If cumulative credit reaches >= 1.0 (within float epsilon), expand interval
                    while anc_state.cumulative_fire_credit + _CREDIT_TRIGGER_EPSILON >= 1.0:
                        expanded = True
                        mult = 1.0 + 0.5 * anc_state.speed
                        anc_state.stability_interval_days = round(
                            anc_state.stability_interval_days * mult, 4
                        )
                        anc_state.rep_num = round(
                            anc_state.rep_num + 1.0 * anc_state.speed, 4
                        )
                        anc_state.cumulative_fire_credit = max(
                            0.0, round(anc_state.cumulative_fire_credit - 1.0, 4)
                        )
                        anc_state.memory_strength = 1.0
                        anc_state.last_reviewed_at = now
                        anc_state.due_review_at = now + timedelta(
                            days=anc_state.stability_interval_days
                        )

                        # If topic was due for review, implicit credit knocks it out!
                        if anc_state.status == "DUE_FOR_REVIEW":
                            anc_state.status = "MASTERED"
                            status_trans = "MASTERED"

                    # Even if not expanded to 1.0, positive implicit credit partially replenishes memory
                    if not expanded and credit_delta > 0.0:
                        anc_state.memory_strength = min(
                            1.0, round(anc_state.memory_strength + (0.5 * credit_delta), 4)
                        )

                    self.storage.upsert_student_topic_state(anc_state)

                    downward_transfers.append(
                        FireCreditTransfer(
                            ancestor_id=anc_id,
                            compound_weight=round(weight, 4),
                            credit_delta=credit_delta,
                            cumulative_credit_before=prior_cred,
                            cumulative_credit_after=anc_state.cumulative_fire_credit,
                            interval_expanded=expanded,
                            prior_interval=prior_int,
                            new_interval=anc_state.stability_interval_days,
                            status_transitioned=status_trans,
                        )
                    )

        else:
            # Failed repetition: apply overdue summer slide decay
            discount = 1.0
            raw_magnitude = abs(float(raw_delta)) if abs(float(raw_delta)) > 0.0 else 1.0
            decay = self.calculate_overdue_decay(days_elapsed, state.stability_interval_days)
            effective_delta = -round(raw_magnitude * decay, 4)

            # Update target node: apply penalty, contract interval (floor >= 0.5d), reset fractional credit
            penalty = round(state.speed * decay * raw_magnitude, 4)
            state.rep_num = max(0.0, round(state.rep_num - penalty, 4))

            contraction_div = 1.0 + 0.5 * state.speed
            contracted_int = round(state.stability_interval_days / contraction_div, 4)
            state.stability_interval_days = max(0.5, contracted_int)
            state.cumulative_fire_credit = 0.0
            state.memory_strength = 0.25  # Severely decayed
            state.status = "DUE_FOR_REVIEW"
            state.due_review_at = now
            self.storage.upsert_student_topic_state(state)

            # Upward Penalty Trickle-Up along ENCOMPASSES edges
            encompassing_paths = self.storage.get_encompassing_descendants(
                topic_id, max_hops=max_hops
            )
            max_weights_by_descendant = self._deduplicate_encompassing_weights(
                encompassing_paths, exclude_topic_id=topic_id
            )

            if max_weights_by_descendant:
                descendant_states = self.storage.get_student_topic_states(
                    student_id, sorted(max_weights_by_descendant.keys())
                )

                for desc_id in sorted(max_weights_by_descendant.keys()):
                    weight = max_weights_by_descendant[desc_id]
                    desc_state = descendant_states.get(desc_id)
                    if not desc_state or desc_state.status not in ("MASTERED", "DUE_FOR_REVIEW"):
                        continue

                    upward_penalty = round(penalty * weight, 4)
                    prior_rep = desc_state.rep_num
                    prior_int = desc_state.stability_interval_days

                    # Account for any elapsed time decay on the descendant prior to penalty
                    if desc_state.last_reviewed_at is not None:
                        desc_days = self._compute_days_elapsed(
                            desc_state.last_reviewed_at, now, default_days=0.0
                        )
                        time_decayed_mem = self.calculate_memory_strength(desc_days, prior_int)
                        prior_mem = min(desc_state.memory_strength, time_decayed_mem)
                    else:
                        prior_mem = desc_state.memory_strength

                    desc_state.rep_num = max(0.0, round(prior_rep - upward_penalty, 4))
                    new_int = round(prior_int / (1.0 + 0.25 * weight), 4)
                    desc_state.stability_interval_days = max(0.5, new_int)
                    desc_state.cumulative_fire_credit = max(
                        0.0, round(desc_state.cumulative_fire_credit - weight, 4)
                    )
                    new_mem = max(0.0, round(prior_mem - 0.25 * weight, 4))
                    desc_state.memory_strength = new_mem

                    status_trans = None
                    if new_mem <= 0.50 and desc_state.status == "MASTERED":
                        desc_state.status = "DUE_FOR_REVIEW"
                        desc_state.due_review_at = now
                        status_trans = "DUE_FOR_REVIEW"

                    self.storage.upsert_student_topic_state(desc_state)

                    upward_transfers.append(
                        PenaltyTransfer(
                            descendant_id=desc_id,
                            compound_weight=round(weight, 4),
                            penalty_delta=upward_penalty,
                            prior_interval=prior_int,
                            new_interval=desc_state.stability_interval_days,
                            prior_rep_num=prior_rep,
                            new_rep_num=desc_state.rep_num,
                            prior_memory=prior_mem,
                            new_memory=new_mem,
                            status_transitioned=status_trans,
                        )
                    )

        return RepetitionResult(
            student_id=student_id,
            topic_id=topic_id,
            is_success=passed,
            raw_delta=raw_delta,
            effective_delta=effective_delta,
            days_elapsed=round(days_elapsed, 4),
            decay_factor=decay,
            discount_factor=discount,
            target_rep_num_after=state.rep_num,
            target_interval_after=state.stability_interval_days,
            target_memory_after=state.memory_strength,
            downward_fire_transfers=downward_transfers,
            upward_penalty_transfers=upward_transfers,
        )
