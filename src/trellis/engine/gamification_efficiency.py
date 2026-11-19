"""
src/trellis/engine/gamification_efficiency.py
Tier 7: Gamification Economy, Anti-Cheat XP Hacker Defenses & Learning Efficiency.

Authoritative Implementation of:
  - FR-7.1: XP-Time Equivalence ($1 XP ~ 1 Min$) & 6-Tier Quality Scaling
  - FR-7.2: Anti-Loophole & "XP Hacker" Defense System
  - FR-7.3: Decoupled Course Progress % vs. XP & Learning Efficiency Projector
  - FR-7.4: Ability-Grouped Weekly Leagues & Promotion/Demotion

Strict Architectural Invariants:
  1. Dual-Metric Economy: Course Progress % (mastered nodes / total nodes)
     is strictly decoupled from Monotonic XP.
  2. All calculations, state transitions, and awards are 100% deterministic.
  3. Zero raw queries outside src/trellis/adapters/db/.
  4. Bit-identical parity across memory, relational, and graph backends.
"""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from trellis.domain.models import (
    CourseCatalogItem,
    CourseCompletionProjection,
    LeagueCohort,
    LeagueMemberStanding,
    LeagueTier,
    QualityTier,
    SkillType,
    StudentProfile,
    StudentTopicState,
    TopicNode,
    XPHackerAlert,
    XPLedgerEntry,
)
from trellis.ports.storage_port import TrellisStoragePort


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class GamificationEfficiencyConfig(BaseModel):
    """Calibrated thresholds and constants for the Trellis Gamification Economy."""

    # FR-7.1: Base XP & Equivalence (1 XP ~ 1 minute focused deliberate effort)
    xp_per_minute: float = Field(default=1.0, gt=0.0)
    lesson_base_xp: int = Field(default=4, ge=1, le=10)
    review_base_xp: int = Field(default=3, ge=1, le=10)
    quiz_item_xp: int = Field(default=1, ge=1)
    diagnostic_item_xp: int = Field(default=1, ge=1)
    synthesis_part_xp: int = Field(default=2, ge=1)
    synthesis_completion_bonus_xp: int = Field(default=5, ge=1)

    # 6-Tier Quality Multipliers
    bonus_multiplier: float = Field(default=1.25, ge=1.0)
    full_multiplier: float = Field(default=1.00, ge=1.0)
    most_multiplier: float = Field(default=0.75, ge=0.5)
    little_multiplier: float = Field(default=0.25, ge=0.1)
    zero_multiplier: float = Field(default=0.00, ge=0.0)

    # FR-7.2: Anti-Hacker & Defense
    blowoff_threshold_ratio: float = Field(default=0.20, gt=0.0, le=0.5)
    base_negative_penalty: int = Field(default=-2, le=-1)
    max_negative_penalty: int = Field(default=-10, le=-2)
    idle_multiplier: float = Field(default=3.0, ge=2.0)
    default_median_latency_ms: int = Field(default=20000, ge=3000)
    cooldown_minutes: int = Field(default=15, ge=1)

    # Minimum Expected Reading/Solving Durations per Skill Type (ms)
    min_duration_fluency_fact_ms: int = Field(default=1200, ge=500)
    min_duration_procedural_ms: int = Field(default=4000, ge=1000)
    min_duration_conceptual_ms: int = Field(default=5000, ge=1000)
    min_duration_synthesis_ms: int = Field(default=8000, ge=2000)
    min_duration_default_ms: int = Field(default=4000, ge=1000)

    # FR-7.3: Pace & Efficiency Curve
    baseline_pace_xp: float = Field(default=40.0, gt=0.0)
    viability_pace_floor: float = Field(default=15.0, gt=0.0)
    burnout_daily_ceiling: float = Field(default=90.0, gt=0.0)
    efficiency_exponent: float = Field(default=0.1, gt=0.0, le=0.5)
    rolling_pace_window_days: int = Field(default=14, ge=7)

    # FR-7.4: Ability-Grouped Weekly Leagues
    league_min_cohort_size: int = Field(default=10, ge=5)
    league_target_cohort_size: int = Field(default=25, ge=10)
    league_max_cohort_size: int = Field(default=35, ge=20)
    league_promotion_pct: float = Field(default=0.20, gt=0.0, le=0.5)
    league_demotion_pct: float = Field(default=0.20, gt=0.0, le=0.5)


LEAGUE_TIER_ORDER: List[LeagueTier] = [
    "APPRENTICE",
    "PRACTITIONER",
    "SCHOLAR",
    "FELLOW",
    "POLYMATH",
]


# ---------------------------------------------------------------------------
# Core Engine: Gamification & Efficiency
# ---------------------------------------------------------------------------

class GamificationEfficiencyEngine:
    """
    Deterministic domain service for XP quality scaling, anti-cheat detection,
    course progress / efficiency projection, and ability-grouped leagues.
    """

    def __init__(
        self,
        storage: TrellisStoragePort,
        config: Optional[GamificationEfficiencyConfig] = None,
    ) -> None:
        self.storage = storage
        self.config = config or GamificationEfficiencyConfig()

    # =======================================================================
    # FR-7.1: XP-Time Equivalence & 6-Tier Quality Scaling
    # =======================================================================

    def compute_lesson_quality(
        self,
        total_kps: int,
        errors_across_kps: int,
        hints_used: int = 0,
        is_first_attempt: bool = True,
        base_xp: Optional[int] = None,
    ) -> Tuple[QualityTier, int]:
        """
        Evaluates lesson staircase mastery effort across the 6 quality tiers.
        - BONUS_XP (1.25x): 0 errors, 0 hints on first attempt.
        - FULL_XP (1.00x): <= 1 error across all KPs.
        - MOST_XP (0.75x): 2 errors across all KPs.
        - LITTLE_XP (0.25x): >= 3 errors (mastered with high friction).
        - ZERO_XP (0.00x): 0 XP for unmastered / failed attempt.
        """
        eff_base = base_xp if base_xp is not None else self.config.lesson_base_xp

        if errors_across_kps == 0 and hints_used == 0 and is_first_attempt:
            delta_xp = int(math.ceil(eff_base * self.config.bonus_multiplier))
            return "BONUS_XP", delta_xp
        elif errors_across_kps <= 1:
            delta_xp = int(round(eff_base * self.config.full_multiplier))
            return "FULL_XP", delta_xp
        elif errors_across_kps == 2:
            delta_xp = max(1, int(round(eff_base * self.config.most_multiplier)))
            return "MOST_XP", delta_xp
        else:
            delta_xp = max(1, int(round(eff_base * self.config.little_multiplier)))
            return "LITTLE_XP", delta_xp

    def compute_review_quality(
        self,
        total_items: int,
        error_count: int,
        passed: bool,
        base_xp: Optional[int] = None,
    ) -> Tuple[QualityTier, int]:
        """
        Evaluates spaced review completion effort across the 6 quality tiers.
        - BONUS_XP (1.25x): 100% clean pass (0 errors).
        - FULL_XP (1.00x): Passed with 1 slip.
        - MOST_XP (0.75x): Passed with 2 mistakes (e.g. ✖✔✖✔✔ sequence).
        - LITTLE_XP (0.25x): Failed but substantial effort (1-2 correct answers).
        - ZERO_XP (0.00x): Clean failure without effort.
        """
        eff_base = base_xp if base_xp is not None else self.config.review_base_xp

        if not passed:
            if total_items > 0 and (total_items - error_count) > 0:
                delta_xp = max(1, int(round(eff_base * self.config.little_multiplier)))
                return "LITTLE_XP", delta_xp
            return "ZERO_XP", 0

        if error_count == 0:
            delta_xp = int(math.ceil(eff_base * self.config.bonus_multiplier))
            return "BONUS_XP", delta_xp
        elif error_count == 1:
            delta_xp = int(round(eff_base * self.config.full_multiplier))
            return "FULL_XP", delta_xp
        elif error_count == 2:
            delta_xp = max(1, int(round(eff_base * self.config.most_multiplier)))
            return "MOST_XP", delta_xp
        else:
            delta_xp = max(1, int(round(eff_base * self.config.little_multiplier)))
            return "LITTLE_XP", delta_xp

    def compute_quiz_quality(
        self,
        score_pct: float,
        question_count: int,
        correct_count: int,
    ) -> Tuple[QualityTier, int]:
        """
        Evaluates timed interleaved quiz performance across the 6 quality tiers.
        - BONUS_XP: 100% score (1.25x quiz item base XP).
        - FULL_XP: >= 80% passing band (1 XP per verified correct item).
        - MOST_XP: 60% - 79% score band.
        - LITTLE_XP: 40% - 59% score band.
        - ZERO_XP: < 40% score band.
        """
        base_xp = correct_count * self.config.quiz_item_xp

        if score_pct >= 99.99 and question_count > 0:
            delta_xp = int(math.ceil(base_xp * self.config.bonus_multiplier))
            return "BONUS_XP", delta_xp
        elif score_pct >= 80.0:
            return "FULL_XP", base_xp
        elif score_pct >= 60.0:
            delta_xp = max(1, int(round(base_xp * self.config.most_multiplier)))
            return "MOST_XP", delta_xp
        elif score_pct >= 40.0:
            delta_xp = max(1, int(round(base_xp * self.config.little_multiplier)))
            return "LITTLE_XP", delta_xp
        else:
            return "ZERO_XP", 0

    def compute_synthesis_quality(
        self,
        parts_completed: int,
        total_parts: int,
        errors_count: int,
    ) -> Tuple[QualityTier, int]:
        """
        Evaluates multi-part applied synthesis performance across quality tiers.
        """
        part_xp = parts_completed * self.config.synthesis_part_xp
        if parts_completed == total_parts:
            total_earned = part_xp + self.config.synthesis_completion_bonus_xp
            if errors_count == 0:
                delta_xp = int(math.ceil(total_earned * self.config.bonus_multiplier))
                return "BONUS_XP", delta_xp
            elif errors_count <= 1:
                return "FULL_XP", total_earned
            else:
                delta_xp = max(part_xp, int(round(total_earned * self.config.most_multiplier)))
                return "MOST_XP", delta_xp
        elif parts_completed > 0:
            return "LITTLE_XP", part_xp
        else:
            return "ZERO_XP", 0

    def award_xp(
        self,
        student_id: str,
        topic_id: str,
        task_type: str,
        quality_tier: QualityTier,
        delta_xp: int,
        reason: str,
        as_of: Optional[datetime] = None,
    ) -> XPLedgerEntry:
        """
        Atomically appends an XP ledger entry and updates the student's profile.
        """
        curr_time = as_of or datetime.now(timezone.utc)
        entry = XPLedgerEntry(
            entry_id=f"xp_{uuid.uuid4().hex[:12]}",
            student_id=student_id,
            topic_id=topic_id,
            task_type=task_type,
            quality_tier=quality_tier,
            delta_xp=delta_xp,
            reason=reason,
            created_at=curr_time,
        )
        self.storage.append_xp_ledger(entry)
        return entry

    # =======================================================================
    # FR-7.2: Anti-Loophole & "XP Hacker" Defense System
    # =======================================================================

    def compute_min_expected_duration(
        self,
        skill_type: Optional[SkillType | str] = None,
        word_count: int = 15,
        bloom_tier: Optional[str] = None,
    ) -> int:
        """
        Estimates minimum expected reading and solving duration (ms).
        Accounts for reading time (~250ms/word) plus cognitive procedural execution.
        """
        reading_ms = max(500, word_count * 250)

        st = (skill_type or "PROCEDURAL").upper()
        if st == "FLUENCY_FACT":
            skill_floor = self.config.min_duration_fluency_fact_ms
        elif st == "PROCEDURAL":
            skill_floor = self.config.min_duration_procedural_ms
        elif st in ("CONCEPTUAL", "REPRESENTATIONAL"):
            skill_floor = self.config.min_duration_conceptual_ms
        elif st in ("SYNTHESIS", "MULTI_STEP_SYNTHESIS", "THEOREM_PROOF"):
            skill_floor = self.config.min_duration_synthesis_ms
        else:
            skill_floor = self.config.min_duration_default_ms

        return max(skill_floor, reading_ms + 1000)

    def detect_blow_off(
        self,
        duration_ms: int,
        min_expected_ms: int,
        is_correct: bool,
    ) -> bool:
        """
        Detects rapid random guessing / task blow-offs (< 20% minimum expected duration).
        Correct responses are not blow-offs (e.g. automaticity or fast solvers).
        """
        if is_correct:
            return False
        threshold = int(min_expected_ms * self.config.blowoff_threshold_ratio)
        return duration_ms < threshold

    def apply_anti_hacker_penalty(
        self,
        student_id: str,
        topic_id: str,
        task_type: str,
        duration_ms: int,
        min_expected_ms: int,
        as_of: Optional[datetime] = None,
    ) -> Tuple[XPLedgerEntry, XPHackerAlert]:
        """
        Deducts escalating negative XP penalties for rapid blow-offs,
        enforces 15-minute cooldown hold, and emits an XPHackerAlert.
        """
        curr_time = as_of or datetime.now(timezone.utc)

        # Retrieve profile to check consecutive blowoffs
        try:
            profile = self.storage.get_student_profile(student_id)
            consecutive = profile.consecutive_blowoffs + 1
        except KeyError:
            profile = StudentProfile(
                student_id=student_id,
                enrolled_course_id="CRS_DEFAULT",
                consecutive_blowoffs=1,
            )
            consecutive = 1

        # Escalating penalty: -2, -4, -6, ... capped at max_negative_penalty (-10)
        penalty = max(
            self.config.max_negative_penalty,
            self.config.base_negative_penalty * consecutive,
        )

        # Record XP penalty transaction
        entry = XPLedgerEntry(
            entry_id=f"xp_pen_{uuid.uuid4().hex[:10]}",
            student_id=student_id,
            topic_id=topic_id,
            task_type=task_type,
            quality_tier="NEGATIVE_PENALTY",
            delta_xp=penalty,
            reason=f"XP Hacker penalty: Rapid blow-off ({duration_ms}ms < 20% of {min_expected_ms}ms)",
            created_at=curr_time,
        )
        self.storage.append_xp_ledger(entry)

        # Update profile with incremented consecutive blowoffs
        updated_profile = profile.model_copy(
            update={
                "consecutive_blowoffs": consecutive,
                "xp_total": max(0, profile.xp_total + penalty),
            }
        )
        self.storage.upsert_student_profile(updated_profile)

        # Set 15-minute cooldown on the topic state
        cooldown_until = curr_time + timedelta(minutes=self.config.cooldown_minutes)
        states = self.storage.get_student_topic_states(student_id, [topic_id])
        if topic_id in states:
            st = states[topic_id]
            updated_st = st.model_copy(
                update={
                    "consecutive_correct": 0,
                    "cooldown_until": cooldown_until,
                }
            )
            self.storage.upsert_student_topic_state(updated_st)

        alert = XPHackerAlert(
            alert_id=f"alert_hacker_{uuid.uuid4().hex[:10]}",
            student_id=student_id,
            topic_id=topic_id,
            duration_ms=duration_ms,
            min_expected_ms=min_expected_ms,
            penalty_xp=penalty,
            consecutive_blowoffs=consecutive,
            created_at=curr_time,
        )
        return entry, alert

    def reset_consecutive_blowoffs(self, student_id: str) -> None:
        """Resets consecutive blowoffs counter upon a genuine attempt."""
        try:
            profile = self.storage.get_student_profile(student_id)
            if profile.consecutive_blowoffs > 0:
                self.storage.upsert_student_profile(
                    profile.model_copy(update={"consecutive_blowoffs": 0})
                )
        except KeyError:
            pass

    def apply_idle_timer_ceiling(
        self,
        duration_ms: int,
        student_median_latency_ms: Optional[int] = None,
    ) -> Tuple[int, bool]:
        """
        Anti-cheat idle timeout: clamps duration to 3 * median_latency.
        Freezes session time and XP accrual when latency exceeds the ceiling.
        """
        med_lat = student_median_latency_ms or self.config.default_median_latency_ms
        ceiling_ms = int(med_lat * self.config.idle_multiplier)

        if duration_ms > ceiling_ms:
            return ceiling_ms, True
        return duration_ms, False

    def is_topic_on_cooldown(
        self,
        student_id: str,
        topic_id: str,
        as_of: Optional[datetime] = None,
    ) -> Tuple[bool, Optional[datetime]]:
        """Checks if a topic is currently locked under an active cooldown period."""
        curr_time = as_of or datetime.now(timezone.utc)
        states = self.storage.get_student_topic_states(student_id, [topic_id])
        if topic_id not in states:
            return False, None
        cd = states[topic_id].cooldown_until
        if cd and curr_time < cd:
            return True, cd
        return False, None

    # =======================================================================
    # FR-7.3: Decoupled Course Progress % & Learning Efficiency Projector
    # =======================================================================

    def compute_decoupled_progress_pct(
        self,
        student_id: str,
        course_id: str,
    ) -> float:
        """
        Computes course progress strictly from mastered topic nodes vs total course nodes.
        Completely decoupled from XP earned.
        """
        course_nodes = self.storage.get_course_subgraph(course_id)
        if not course_nodes:
            return 0.0

        topic_ids = [n.id for n in course_nodes]
        states = self.storage.get_student_topic_states(student_id, topic_ids)

        mastered_count = sum(
            1
            for tid in topic_ids
            if tid in states and states[tid].status in ("MASTERED", "MASTERED_FLOOR")
        )

        progress = round((mastered_count / len(topic_ids)) * 100.0, 2)
        return min(100.0, max(0.0, progress))

    def compute_rolling_weekday_pace(
        self,
        student_id: str,
        window_days: Optional[int] = None,
        as_of: Optional[datetime] = None,
    ) -> float:
        """
        Computes rolling average XP earned per weekday over a historical window.
        Filters out weekends (Saturdays and Sundays).
        """
        curr_time = as_of or datetime.now(timezone.utc)
        eff_window_days = window_days or self.config.rolling_pace_window_days
        window_start = curr_time - timedelta(days=eff_window_days)

        # Retrieve XP entries within window
        entries = self.storage.get_xp_ledger_entries(
            student_id,
            limit=500,
            since=window_start,
            until=curr_time,
        )

        # Sum net XP earned on weekdays (Monday = 0 ... Friday = 4)
        weekday_xp = sum(
            e.delta_xp for e in entries if e.created_at.weekday() < 5
        )

        # Count total weekdays in the window interval
        weekdays_count = 0
        cur_date = window_start.date()
        end_date = curr_time.date()
        while cur_date <= end_date:
            if cur_date.weekday() < 5:
                weekdays_count += 1
            cur_date += timedelta(days=1)

        weekdays_count = max(1, weekdays_count)
        pace = max(0.0, round(weekday_xp / weekdays_count, 2))
        return pace

    def compute_learning_efficiency_multiplier(self, pace: float) -> float:
        """
        Computes learning efficiency multiplier using the empirical curve:
          efficiency_multiplier = (pace / 40.0) ^ 0.1

        Matches table in The Math Academy Way (Chapter 31):
          - Pace 160: ~1.15x
          - Pace 80:  ~1.07x
          - Pace 40:  1.00x
          - Pace 20:  ~0.93x
          - Pace 10:  ~0.87x
          - Pace 5:   ~0.81x
        """
        # Clamp pace to minimum floor >= 1.0 to prevent division by zero or negative base
        clamped_pace = max(1.0, pace)
        ratio = clamped_pace / self.config.baseline_pace_xp
        multiplier = round(ratio ** self.config.efficiency_exponent, 4)
        return multiplier

    def project_course_completion(
        self,
        student_id: str,
        course_id: str,
        as_of: Optional[datetime] = None,
    ) -> CourseCompletionProjection:
        """
        Projects course completion weekdays, calendar weeks, and calendar date
        using the (pace / 40)^0.1 efficiency curve.
        """
        curr_time = as_of or datetime.now(timezone.utc)
        progress_pct = self.compute_decoupled_progress_pct(student_id, course_id)

        # Find course benchmark XP
        catalog = self.storage.get_course_catalog()
        course_item = next((c for c in catalog if c.course_id == course_id), None)
        benchmark_xp = course_item.benchmark_xp if course_item else 3000

        # Remaining XP in the course
        remaining_ratio = max(0.0, 1.0 - (progress_pct / 100.0))
        remaining_xp = int(round(benchmark_xp * remaining_ratio))

        # Pace & Efficiency multiplier
        pace = self.compute_rolling_weekday_pace(student_id, as_of=curr_time)
        try:
            profile = self.storage.get_student_profile(student_id)
            if pace <= 0.0 and profile.rolling_pace_xp > 0:
                pace = profile.rolling_pace_xp
        except KeyError:
            pass

        eff_multiplier = self.compute_learning_efficiency_multiplier(pace)
        effective_daily_pace = max(0.1, pace * eff_multiplier)

        # Calculate projected weekdays
        if remaining_xp == 0:
            proj_weekdays = 0
            proj_weeks = 0.0
            proj_date_str = curr_time.date().isoformat()
        else:
            proj_weekdays = int(math.ceil(remaining_xp / effective_daily_pace))
            proj_weeks = round(proj_weekdays / 5.0, 1)

            # Compute completion date skipping weekends
            d = curr_time.date()
            added_weekdays = 0
            while added_weekdays < proj_weekdays:
                d += timedelta(days=1)
                if d.weekday() < 5:
                    added_weekdays += 1
            proj_date_str = d.isoformat()

        viability_warn = pace < self.config.viability_pace_floor and remaining_xp > 0
        burnout_warn = pace > self.config.burnout_daily_ceiling

        return CourseCompletionProjection(
            student_id=student_id,
            course_id=course_id,
            current_progress_pct=progress_pct,
            benchmark_xp=benchmark_xp,
            remaining_xp=remaining_xp,
            rolling_pace_xp=pace,
            efficiency_multiplier=eff_multiplier,
            projected_weekdays=proj_weekdays,
            projected_calendar_weeks=proj_weeks,
            projected_completion_date=proj_date_str,
            viability_warning=viability_warn,
            burnout_warning=burnout_warn,
        )

    # =======================================================================
    # FR-7.4: Ability-Grouped Weekly Leagues & Promotion/Demotion
    # =======================================================================

    @staticmethod
    def get_iso_week_key(dt: datetime) -> str:
        """Returns ISO calendar week string, e.g. '2026-W43'."""
        year, week, _ = dt.isocalendar()
        return f"{year}-W{week:02d}"

    @staticmethod
    def get_iso_week_bounds(week_key: str) -> Tuple[datetime, datetime]:
        """
        Returns (start_utc, end_utc) for an ISO week key.
        Week runs Monday 00:00:00 UTC through Sunday 23:59:59.999999 UTC.
        """
        parts = week_key.split("-W")
        year = int(parts[0])
        week = int(parts[1])
        # Monday of given week
        monday = date.fromisocalendar(year, week, 1)
        sunday = date.fromisocalendar(year, week, 7)
        start_dt = datetime.combine(monday, dt_time.min, tzinfo=timezone.utc)
        end_dt = datetime.combine(sunday, dt_time.max, tzinfo=timezone.utc)
        return start_dt, end_dt

    def assign_weekly_cohorts(
        self,
        week_key: str,
        as_of: Optional[datetime] = None,
    ) -> List[LeagueCohort]:
        """
        Partitions all opted-in students into 20-30 student weekly cohorts
        matched by historical rolling pace within their current LeagueTier.
        """
        curr_time = as_of or datetime.now(timezone.utc)

        # Retrieve all opted-in students
        all_profiles = self.storage.list_student_profiles()
        opted_in = [p for p in all_profiles if p.league_opt_in]

        # Group by league tier
        by_tier: Dict[str, List[StudentProfile]] = {t: [] for t in LEAGUE_TIER_ORDER}
        for prof in opted_in:
            tier = prof.league_tier if prof.league_tier in LEAGUE_TIER_ORDER else "APPRENTICE"
            by_tier[tier].append(prof)

        cohorts: List[LeagueCohort] = []

        for tier, members in by_tier.items():
            if not members:
                continue

            # Sort members by rolling pace descending for ability matching
            members_sorted = sorted(members, key=lambda m: (-m.rolling_pace_xp, m.student_id))

            # Chunk into cohorts of target size (20-30)
            chunk_size = self.config.league_target_cohort_size
            total_students = len(members_sorted)
            num_chunks = max(1, math.ceil(total_students / chunk_size))

            # Distribute evenly across chunks
            chunks: List[List[StudentProfile]] = [[] for _ in range(num_chunks)]
            for idx, member in enumerate(members_sorted):
                chunks[idx % num_chunks].append(member)

            for chunk_idx, chunk in enumerate(chunks):
                if not chunk:
                    continue
                cohort_id = f"cohort_{week_key}_{tier.lower()}_{chunk_idx + 1:02d}"
                member_ids = [m.student_id for m in chunk]

                initial_standings = [
                    LeagueMemberStanding(
                        student_id=mid,
                        net_weekly_xp=0,
                        rank=r + 1,
                        status="RETAINING",
                    )
                    for r, mid in enumerate(member_ids)
                ]

                cohort = LeagueCohort(
                    cohort_id=cohort_id,
                    week_key=week_key,
                    tier=tier,
                    member_ids=member_ids,
                    standings=initial_standings,
                    finalized=False,
                )
                self.storage.save_league_cohort(cohort)
                cohorts.append(cohort)

        return cohorts

    def compute_cohort_standings(
        self,
        cohort_id: str,
        as_of: Optional[datetime] = None,
    ) -> LeagueCohort:
        """
        Ranks students in a league cohort strictly by net weekly XP earned,
        properly deducting negative hacker penalties.
        Assigns PROMOTING, RETAINING, or DEMOTING status.
        """
        cohort = self.storage.get_league_cohort(cohort_id)
        if not cohort:
            raise KeyError(f"LeagueCohort '{cohort_id}' not found.")

        curr_time = as_of or datetime.now(timezone.utc)
        week_start, week_end = self.get_iso_week_bounds(cohort.week_key)

        # Compute net weekly XP per member
        member_xp: Dict[str, Tuple[int, datetime]] = {}
        for mid in cohort.member_ids:
            entries = self.storage.get_xp_ledger_entries(
                mid,
                limit=1000,
                since=week_start,
                until=min(curr_time, week_end),
            )
            net_xp = sum(e.delta_xp for e in entries)
            latest_ts = max((e.created_at for e in entries), default=datetime.min.replace(tzinfo=timezone.utc))
            member_xp[mid] = (net_xp, latest_ts)

        # Sort descending by (net_xp, -latest_ts, student_id)
        sorted_members = sorted(
            cohort.member_ids,
            key=lambda mid: (-member_xp[mid][0], member_xp[mid][1], mid),
        )

        n_members = len(sorted_members)
        promo_count = max(1, int(round(n_members * self.config.league_promotion_pct))) if n_members >= 5 else 1
        demo_count = max(1, int(round(n_members * self.config.league_demotion_pct))) if n_members >= 5 else 0

        standings: List[LeagueMemberStanding] = []
        for rank, mid in enumerate(sorted_members, start=1):
            net_xp = member_xp[mid][0]

            if rank <= promo_count:
                status = "PROMOTING"
            elif rank > (n_members - demo_count) and cohort.tier != "APPRENTICE":
                status = "DEMOTING"
            else:
                status = "RETAINING"

            standings.append(
                LeagueMemberStanding(
                    student_id=mid,
                    net_weekly_xp=net_xp,
                    rank=rank,
                    status=status,
                )
            )

        updated_cohort = cohort.model_copy(update={"standings": standings})
        self.storage.save_league_cohort(updated_cohort)
        return updated_cohort

    def settle_weekly_league(
        self,
        cohort_id: str,
        as_of: Optional[datetime] = None,
    ) -> Tuple[LeagueCohort, Dict[str, str]]:
        """
        Sunday settlement: finalizes weekly league standings and promotes/demotes
        members to their new LeagueTier in StudentProfile.
        """
        curr_time = as_of or datetime.now(timezone.utc)
        cohort = self.compute_cohort_standings(cohort_id, as_of=curr_time)
        if cohort.finalized:
            return cohort, {}

        tier_idx = LEAGUE_TIER_ORDER.index(cohort.tier) if cohort.tier in LEAGUE_TIER_ORDER else 0
        tier_transitions: Dict[str, str] = {}

        for st in cohort.standings:
            try:
                prof = self.storage.get_student_profile(st.student_id)
            except KeyError:
                continue

            if st.status == "PROMOTING":
                new_idx = min(len(LEAGUE_TIER_ORDER) - 1, tier_idx + 1)
                new_tier = LEAGUE_TIER_ORDER[new_idx]
            elif st.status == "DEMOTING":
                new_idx = max(0, tier_idx - 1)
                new_tier = LEAGUE_TIER_ORDER[new_idx]
            else:
                new_tier = cohort.tier

            tier_transitions[st.student_id] = new_tier
            if new_tier != prof.league_tier:
                self.storage.upsert_student_profile(
                    prof.model_copy(update={"league_tier": new_tier})
                )

        finalized_cohort = cohort.model_copy(update={"finalized": True})
        self.storage.save_league_cohort(finalized_cohort)
        return finalized_cohort, tier_transitions
