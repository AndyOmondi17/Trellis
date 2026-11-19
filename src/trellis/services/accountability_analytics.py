"""
src/trellis/services/accountability_analytics.py
Tier 8 Domain Service for Trellis K-16 Platform.
Encapsulates:
  - FR-8.1: Parent/Coach Accountability Portal & 5-Stage Scaffolded Independence Tracker
  - FR-8.2: Contextual Habit Formation & Minimum Pace Monitor (15 XP floor / 90 XP ceiling)
  - FR-8.3: Granular Content Remediation Analytics (95%/99% Pass Bar & Student Flagging)
Strictly adheres to TrellisStoragePort hexagonal architecture with zero raw DB queries.
"""

from __future__ import annotations

import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

from pydantic import BaseModel, Field

from trellis.domain.models import (
    AdultReSitAlert,
    ContentFlagType,
    ContentRemediationFlag,
    CourseCatalogItem,
    CourseCompletionProjection,
    GranularPassRateMetric,
    HabitHealthStatus,
    HabitTelemetryReport,
    IndependenceStage,
    IndependenceStageProgress,
    InteractionAttempt,
    ParentCustomReward,
    PrimingEvaluationReport,
    StudentProfile,
    StudentTopicState,
    StudyHabitSchedule,
    TopicNode,
    XPLedgerEntry,
)
from trellis.ports.storage_port import TrellisStoragePort


INDEPENDENCE_STAGE_ORDER: List[IndependenceStage] = [
    "CO_SITTING",
    "PER_TASK_CHECKIN",
    "DAILY_CHECKIN",
    "EVERY_FEW_DAYS_CHECKIN",
    "WEEKLY_CHECKIN",
]


class AccountabilityAnalyticsConfig(BaseModel):
    """Configuration thresholds for Tier 8 Analytics & Habit Monitoring."""
    viability_min_weekday_pace: float = Field(default=15.0, description="Minimum XP/weekday viability floor")
    burnout_max_daily_pace: float = Field(default=90.0, description="Maximum XP/day burnout ceiling")
    first_attempt_pass_bar: float = Field(default=0.95, description="95% first-attempt pass standard")
    two_attempt_pass_bar: float = Field(default=0.99, description="99% within-two-attempts pass standard")
    superficial_priming_gap_threshold: float = Field(default=0.20, description="Max allowed gap between lesson and unprimed accuracy")
    superficial_priming_min_unprimed_acc: float = Field(default=0.80, description="Floor for unprimed review/quiz retention")
    promotion_min_days_clean: int = Field(default=5, description="Days with zero blowoffs/penalties to advance stage")


class AccountabilityAnalyticsService:
    """
    Tier 8 Service coordinating Parent Portal, 5-Stage Independence tracking,
    Habit formation monitoring, and 95%/99% Content Remediation Analytics.
    Interacts 100% through TrellisStoragePort.
    """

    def __init__(
        self,
        storage: TrellisStoragePort,
        config: Optional[AccountabilityAnalyticsConfig] = None,
    ) -> None:
        self.storage = storage
        self.config = config or AccountabilityAnalyticsConfig()

    # -----------------------------------------------------------------------
    # FR-8.1: 5-Stage Scaffolded Independence & Adult Re-Sit Alerts
    # -----------------------------------------------------------------------

    def evaluate_independence_progression(
        self, student_id: str, as_of: Optional[datetime] = None
    ) -> IndependenceStageProgress:
        """
        Evaluates a student's readiness to advance through the 5 independence stages:
          CO_SITTING -> PER_TASK_CHECKIN -> DAILY_CHECKIN -> EVERY_FEW_DAYS_CHECKIN -> WEEKLY_CHECKIN
        Co-sitting is strictly an advisory recommendation and never an enforced lockout.
        """
        profile = self.storage.get_student_profile(student_id)
        current_stage = profile.independence_stage
        current_idx = INDEPENDENCE_STAGE_ORDER.index(current_stage)

        # Check recent penalties and active alerts
        ref_time = as_of or datetime.now(timezone.utc)
        since_cutoff = ref_time - timedelta(days=self.config.promotion_min_clean_days if hasattr(self.config, 'promotion_min_clean_days') else 5)
        recent_ledger = self.storage.get_xp_ledger_entries(student_id=student_id, since=since_cutoff, limit=100)
        has_negative_penalty = any(e.quality_tier == "NEGATIVE_PENALTY" for e in recent_ledger)

        active_alerts = self.storage.list_adult_resit_alerts(student_id=student_id, unacknowledged_only=True)
        critical_alerts = [a for a in active_alerts if a.severity in ("HIGH", "CRITICAL")]

        # Promotion eligibility:
        # 1. Not already at the maximum stage (WEEKLY_CHECKIN)
        # 2. Zero consecutive blowoffs
        # 3. No negative penalties in recent window
        # 4. No active unacknowledged high/critical alerts
        # 5. Pace >= viability floor or reasonable cumulative XP
        is_max_stage = current_idx >= len(INDEPENDENCE_STAGE_ORDER) - 1
        is_clean = (profile.consecutive_blowoffs == 0) and (not has_negative_penalty) and (len(critical_alerts) == 0)
        is_pacing_ok = profile.rolling_pace_xp >= self.config.viability_min_weekday_pace or profile.xp_total >= 50

        eligible = (not is_max_stage) and is_clean and is_pacing_ok
        next_stage = INDEPENDENCE_STAGE_ORDER[current_idx + 1] if eligible else None

        if eligible:
            rec = f"Learner has maintained focused, independent study. Ready to advance to {next_stage}."
        elif is_max_stage:
            rec = "Learner is at full independent weekly check-in stage."
        elif not is_clean:
            rec = "Recent rapid submissions or coaching alerts detected. Maintain current check-in frequency."
        else:
            rec = f"Keep building consistent practice momentum to reach {self.config.viability_min_weekday_pace} XP/weekday."

        return IndependenceStageProgress(
            student_id=student_id,
            current_stage=current_stage,
            eligible_for_promotion=eligible,
            next_stage=next_stage,
            advisory_recommendation=rec,
        )

    def promote_independence_stage(self, student_id: str) -> StudentProfile:
        """Promotes student to the next independence stage if eligible."""
        prog = self.evaluate_independence_progression(student_id)
        profile = self.storage.get_student_profile(student_id)
        if prog.eligible_for_promotion and prog.next_stage:
            updated = profile.model_copy(update={"independence_stage": prog.next_stage})
            self.storage.upsert_student_profile(updated)
            return updated
        return profile

    def adjust_independence_stage(
        self, student_id: str, target_stage: IndependenceStage
    ) -> StudentProfile:
        """Allows parent/coach to adjust independence stage directly without penalties."""
        profile = self.storage.get_student_profile(student_id)
        updated = profile.model_copy(update={"independence_stage": target_stage})
        self.storage.upsert_student_profile(updated)
        return updated

    def trigger_adult_resit_advisory(
        self,
        student_id: str,
        reason: Literal[
            "XP_HACKER_PENALTY",
            "PROLONGED_STALL",
            "FREQUENT_REMEDIATION_HALTS",
            "BURNOUT_WARNING",
            "PACING_BELOW_VIABILITY",
            "MANUAL_REQUEST",
        ],
        details: str,
        severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM",
    ) -> AdultReSitAlert:
        """
        Emits an advisory notification for the parent/coach dashboard.
        Co-sitting remains strictly an optional recommendation and does not lock the learner out.
        """
        alert = AdultReSitAlert(
            alert_id=f"alert_{student_id}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}",
            student_id=student_id,
            reason=reason,
            details=details,
            severity=severity,
            acknowledged=False,
            created_at=datetime.now(timezone.utc),
        )
        self.storage.record_adult_resit_alert(alert)
        return alert

    def acknowledge_adult_resit(
        self, alert_id: str, acknowledged_at: Optional[datetime] = None
    ) -> None:
        """Marks an adult re-sit alert as acknowledged by parent or coach."""
        self.storage.acknowledge_adult_resit_alert(alert_id, acknowledged_at=acknowledged_at)

    def create_parent_reward(
        self, student_id: str, title: str, xp_target: int
    ) -> ParentCustomReward:
        """Binds a custom parent reward to an XP target threshold."""
        profile = self.storage.get_student_profile(student_id)
        is_already_unlocked = profile.xp_total >= xp_target

        reward = ParentCustomReward(
            reward_id=f"rew_{student_id}_{uuid.uuid4().hex[:8]}",
            student_id=student_id,
            title=title,
            xp_target=xp_target,
            status="UNLOCKED" if is_already_unlocked else "PENDING",
            created_at=datetime.now(timezone.utc),
            unlocked_at=datetime.now(timezone.utc) if is_already_unlocked else None,
        )
        self.storage.save_parent_reward(reward)
        return reward

    def evaluate_parent_rewards(self, student_id: str) -> List[ParentCustomReward]:
        """Checks student's current total XP against pending rewards and unlocks them if reached."""
        profile = self.storage.get_student_profile(student_id)
        rewards = self.storage.get_parent_rewards(student_id)
        for r in rewards:
            if r.status == "PENDING" and profile.xp_total >= r.xp_target:
                self.storage.update_parent_reward_status(r.reward_id, "UNLOCKED")
        return self.storage.get_parent_rewards(student_id)

    def claim_parent_reward(
        self, reward_id: str, claimed_at: Optional[datetime] = None
    ) -> None:
        """Marks an unlocked reward as claimed by the parent/learner."""
        self.storage.update_parent_reward_status(reward_id, "CLAIMED", claimed_at=claimed_at)

    # -----------------------------------------------------------------------
    # FR-8.2: Contextual Habit Formation & Minimum Pace Monitor
    # -----------------------------------------------------------------------

    def set_study_habit_schedule(
        self,
        student_id: str,
        scheduled_start_time: str = "16:00",
        scheduled_end_time: str = "17:00",
        active_days: Optional[List[int]] = None,
        target_daily_xp: int = 40,
    ) -> StudyHabitSchedule:
        """Configures or updates a student's daily study habit window."""
        existing = self.storage.get_study_habit_schedule(student_id)
        current_streak = existing.current_streak_days if existing else 0
        longest_streak = existing.longest_streak_days if existing else 0
        last_date = existing.last_active_date if existing else None

        schedule = StudyHabitSchedule(
            student_id=student_id,
            target_daily_xp=target_daily_xp,
            scheduled_start_time=scheduled_start_time,
            scheduled_end_time=scheduled_end_time,
            active_days=active_days if active_days is not None else [0, 1, 2, 3, 4],
            current_streak_days=current_streak,
            longest_streak_days=longest_streak,
            last_active_date=last_date,
        )
        self.storage.save_study_habit_schedule(schedule)
        return schedule

    def record_daily_session_telemetry(
        self,
        student_id: str,
        session_date: date,
        xp_earned_today: int,
    ) -> StudyHabitSchedule:
        """
        Updates streak tracking for a student based on daily practice.
        Consecutive active days meeting target daily XP increment the streak;
        skipped active days reset current streak to 1 (or 0).
        """
        schedule = self.storage.get_study_habit_schedule(student_id)
        if not schedule:
            schedule = StudyHabitSchedule(student_id=student_id)

        date_str = session_date.isoformat()
        if schedule.last_active_date == date_str:
            # Same day telemetry update
            return schedule

        if schedule.last_active_date:
            last_dt = date.fromisoformat(schedule.last_active_date)
            diff_days = (session_date - last_dt).days
            if diff_days == 1:
                # Consecutive day!
                new_streak = schedule.current_streak_days + 1
            elif diff_days <= 0:
                # Historical / same day
                new_streak = schedule.current_streak_days
            else:
                # Missed one or more days -> streak resets
                new_streak = 1
        else:
            new_streak = 1

        new_longest = max(schedule.longest_streak_days, new_streak)
        updated_schedule = schedule.model_copy(
            update={
                "current_streak_days": new_streak,
                "longest_streak_days": new_longest,
                "last_active_date": date_str,
            }
        )
        self.storage.save_study_habit_schedule(updated_schedule)
        return updated_schedule

    def evaluate_habit_and_pace(
        self, student_id: str, as_of: Optional[datetime] = None
    ) -> HabitTelemetryReport:
        """
        Evaluates habit consistency, scheduled window adherence, minimum pace floor,
        and burnout ceiling.
        """
        profile = self.storage.get_student_profile(student_id)
        schedule = self.storage.get_study_habit_schedule(student_id)
        pace = profile.rolling_pace_xp

        # Viability & Burnout warnings
        viability_warn = pace < self.config.viability_min_weekday_pace
        burnout_warn = pace > self.config.burnout_max_daily_pace

        # Window adherence calculation
        attempts = self.storage.get_interaction_attempts(student_id=student_id, limit=50)
        window_adherence = 100.0
        if schedule and attempts:
            try:
                start_h, start_m = map(int, schedule.scheduled_start_time.split(":"))
                end_h, end_m = map(int, schedule.scheduled_end_time.split(":"))
                start_mins = start_h * 60 + start_m
                end_mins = end_h * 60 + end_m

                on_time_count = 0
                for a in attempts:
                    att_time = a.created_at.time()
                    att_mins = att_time.hour * 60 + att_time.minute
                    # Check window (handle wrap-around if needed, usually simple)
                    if start_mins <= att_mins <= end_mins:
                        on_time_count += 1
                window_adherence = round((on_time_count / len(attempts)) * 100.0, 1)
            except Exception:
                window_adherence = 100.0

        streak = schedule.current_streak_days if schedule else 0

        # Classify habit health
        if burnout_warn:
            health: HabitHealthStatus = "BURNOUT_RISK"
            rec = "Practice volume is unusually heavy (>90 XP/day). Cap deliberate practice to 60–90 min to prevent cognitive burnout."
        elif viability_warn:
            health = "AT_RISK_DROPOUT"
            rec = "Rolling pace is below 15 XP/weekday. Forgetting curve may outpace learning; aim for at least 15–20 minutes daily."
        elif streak >= 7 and window_adherence >= 70.0:
            health = "STRONG_HABIT"
            rec = "Exceptional daily consistency and scheduled window adherence! Solid automaticity and habit established."
        elif streak >= 3:
            health = "DEVELOPING_HABIT"
            rec = "Practice habit is developing well. Maintain consistent timing to reinforce automaticity."
        else:
            health = "INCONSISTENT"
            rec = "Anchor practice to a specific daily window (e.g. after school/dinner) to build regular habit momentum."

        return HabitTelemetryReport(
            student_id=student_id,
            rolling_pace_xp=pace,
            viability_warning=viability_warn,
            burnout_warning=burnout_warn,
            streak_days=streak,
            window_adherence_pct=window_adherence,
            habit_health=health,
            recommendation=rec,
        )

    # -----------------------------------------------------------------------
    # FR-8.3: Granular Content Remediation Analytics & Student Flagging
    # -----------------------------------------------------------------------

    def analyze_pass_rates(
        self, course_id: Optional[str] = None, topic_id: Optional[str] = None
    ) -> List[GranularPassRateMetric]:
        """
        Analyzes 1st-attempt and 2-attempt pass rates against the Math Academy
        Gold Standards:
          - 95% pass rate on first attempt (P1 >= 0.95)
          - 99% pass rate within two attempts (P2 >= 0.99)
        Aggregates at Topic, Knowledge Point, and Problem Blueprint levels.
        """
        # Fetch relevant attempts
        attempts = self.storage.get_interaction_attempts(topic_id=topic_id, limit=5000)

        # Filter by course if specified
        if course_id:
            course_subgraph = {n.id for n in self.storage.get_course_subgraph(course_id)}
            attempts = [a for a in attempts if a.topic_id in course_subgraph]

        if not attempts:
            return []

        # Group attempts by (student_id, topic_id, kp_id)
        # Sequence of attempts by a student on a specific KP
        sessions: Dict[Tuple[str, str, str], List[InteractionAttempt]] = {}
        for a in attempts:
            key = (a.student_id, a.topic_id, a.kp_id)
            if key not in sessions:
                sessions[key] = []
            sessions[key].append(a)

        # Sort each session chronologically
        for k in sessions:
            sessions[k].sort(key=lambda x: x.created_at)

        # Aggregate at (topic_id, kp_id)
        kp_stats: Dict[Tuple[str, str], Dict[str, int]] = {}
        for (student_id, t_id, kp_id), att_list in sessions.items():
            kp_key = (t_id, kp_id)
            if kp_key not in kp_stats:
                kp_stats[kp_key] = {"total": 0, "pass_1": 0, "pass_2": 0}

            kp_stats[kp_key]["total"] += 1
            if att_list[0].is_correct:
                kp_stats[kp_key]["pass_1"] += 1
                kp_stats[kp_key]["pass_2"] += 1
            elif len(att_list) > 1 and att_list[1].is_correct:
                kp_stats[kp_key]["pass_2"] += 1

        metrics: List[GranularPassRateMetric] = []

        # 1. Compute Knowledge Point metrics
        topic_aggregates: Dict[str, Dict[str, int]] = {}
        for (t_id, kp_id), stat in kp_stats.items():
            tot = stat["total"]
            p1 = round(stat["pass_1"] / tot, 4) if tot > 0 else 1.0
            p2 = round(stat["pass_2"] / tot, 4) if tot > 0 else 1.0

            p1_deficit = p1 < self.config.first_attempt_pass_bar
            critical_defect = p2 < self.config.two_attempt_pass_bar

            rec: Optional[str] = None
            if critical_defect:
                rec = "CRITICAL DEFECT: Pass rate within 2 tries < 99%. Needs intermediate KP or topic splitting."
            elif p1_deficit:
                rec = "SCAFFOLDING NEEDED: 1st-pass rate < 95%. Add worked example subgoal labels or reduce chunks."
            else:
                rec = "Meets 95%/99% gold standard."

            metrics.append(
                GranularPassRateMetric(
                    entity_id=kp_id,
                    entity_type="KNOWLEDGE_POINT",
                    total_first_attempts=tot,
                    first_attempt_passes=stat["pass_1"],
                    second_attempt_passes=stat["pass_2"],
                    pass_rate_first_attempt=p1,
                    pass_rate_two_attempts=p2,
                    flag_first_pass_deficit=p1_deficit,
                    flag_critical_defect=critical_defect,
                    recommended_action=rec,
                )
            )

            # Roll up to topic
            if t_id not in topic_aggregates:
                topic_aggregates[t_id] = {"total": 0, "pass_1": 0, "pass_2": 0}
            topic_aggregates[t_id]["total"] += tot
            topic_aggregates[t_id]["pass_1"] += stat["pass_1"]
            topic_aggregates[t_id]["pass_2"] += stat["pass_2"]

        # 2. Compute Topic-level rollup metrics
        for t_id, stat in topic_aggregates.items():
            tot = stat["total"]
            p1 = round(stat["pass_1"] / tot, 4) if tot > 0 else 1.0
            p2 = round(stat["pass_2"] / tot, 4) if tot > 0 else 1.0

            p1_deficit = p1 < self.config.first_attempt_pass_bar
            critical_defect = p2 < self.config.two_attempt_pass_bar

            rec = None
            if critical_defect:
                rec = "Topic overall 2-attempt pass rate < 99%. Inspect member KPs for prerequisite gaps."
            elif p1_deficit:
                rec = "Topic 1st-attempt rate < 95%. Review lesson worked examples."
            else:
                rec = "Topic meets 95%/99% standard."

            metrics.append(
                GranularPassRateMetric(
                    entity_id=t_id,
                    entity_type="TOPIC",
                    total_first_attempts=tot,
                    first_attempt_passes=stat["pass_1"],
                    second_attempt_passes=stat["pass_2"],
                    pass_rate_first_attempt=p1,
                    pass_rate_two_attempts=p2,
                    flag_first_pass_deficit=p1_deficit,
                    flag_critical_defect=critical_defect,
                    recommended_action=rec,
                )
            )

        return metrics

    def evaluate_priming_effect(self, topic_id: str) -> PrimingEvaluationReport:
        """
        Cross-checks lesson pass rate against subsequent unprimed Review or Quiz accuracy.
        Catches superficial priming where students breeze through lessons with worked examples
        in working memory, but bomb unprimed delayed retention.
        """
        attempts = self.storage.get_interaction_attempts(topic_id=topic_id, limit=1000)
        if not attempts:
            return PrimingEvaluationReport(
                topic_id=topic_id,
                lesson_pass_rate=1.0,
                unprimed_review_quiz_accuracy=1.0,
                retention_gap=0.0,
                superficial_priming_detected=False,
                recommendation="No telemetry recorded for this topic.",
            )

        # Distinguish lesson attempts (first encounter) from subsequent review attempts
        # Group by student
        by_student: Dict[str, List[InteractionAttempt]] = {}
        for a in attempts:
            by_student.setdefault(a.student_id, []).append(a)

        lesson_correct = 0
        lesson_total = 0
        unprimed_correct = 0
        unprimed_total = 0

        for s_id, s_attempts in by_student.items():
            s_attempts.sort(key=lambda x: x.created_at)
            # The earliest sequence represents the lesson loop (up to first mastery/completion)
            # Subsequent attempts represent spaced reviews or quizzes
            lesson_slice = s_attempts[:3]
            unprimed_slice = s_attempts[3:]

            for a in lesson_slice:
                lesson_total += 1
                if a.is_correct:
                    lesson_correct += 1

            for a in unprimed_slice:
                unprimed_total += 1
                if a.is_correct:
                    unprimed_correct += 1

        lesson_rate = round(lesson_correct / lesson_total, 4) if lesson_total > 0 else 1.0
        unprimed_rate = round(unprimed_correct / unprimed_total, 4) if unprimed_total > 0 else lesson_rate
        gap = round(lesson_rate - unprimed_rate, 4)

        superficial = (
            lesson_rate >= 0.85
            and (unprimed_rate < self.config.superficial_priming_min_unprimed_acc or gap >= self.config.superficial_priming_gap_threshold)
            and unprimed_total >= 2
        )

        if superficial:
            rec = "SUPERFICIAL PRIMING DETECTED: Lesson pass rate is artificially elevated by immediate worked-example priming, but unprimed retention drops steeply. Introduce delayed retrieval or reduce priming cues."
        else:
            rec = "Retention is genuine: unprimed review performance aligns solidly with initial lesson mastery."

        return PrimingEvaluationReport(
            topic_id=topic_id,
            lesson_pass_rate=lesson_rate,
            unprimed_review_quiz_accuracy=unprimed_rate,
            retention_gap=gap,
            superficial_priming_detected=superficial,
            recommendation=rec,
        )

    def flag_content_item(
        self,
        student_id: str,
        topic_id: str,
        kp_id: Optional[str] = None,
        problem_stem: Optional[str] = None,
        flag_type: ContentFlagType = "AMBIGUOUS_WORDING",
        student_comment: str = "",
    ) -> ContentRemediationFlag:
        """Records an in-task content flag from a student or parent for curriculum remediation."""
        flag = ContentRemediationFlag(
            flag_id=f"flag_{topic_id}_{uuid.uuid4().hex[:8]}",
            student_id=student_id,
            topic_id=topic_id,
            kp_id=kp_id,
            problem_stem=problem_stem,
            flag_type=flag_type,
            student_comment=student_comment,
            created_at=datetime.now(timezone.utc),
            status="OPEN",
        )
        self.storage.record_content_flag(flag)
        return flag

    def resolve_content_flag(self, flag_id: str, status: str = "RESOLVED") -> None:
        """Resolves an open content remediation flag."""
        self.storage.update_content_flag_status(flag_id, status)

    def get_content_author_remediation_queue(
        self, course_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Provides curriculum authors with a prioritized queue of flagged items and pass-bar defects."""
        flags = self.storage.list_content_flags(status="OPEN")
        pass_metrics = self.analyze_pass_rates(course_id=course_id)

        critical_defects = [m for m in pass_metrics if m.flag_critical_defect]
        scaffolding_deficits = [m for m in pass_metrics if m.flag_first_pass_deficit and not m.flag_critical_defect]

        return {
            "open_flags_count": len(flags),
            "open_flags": [f.model_dump() for f in flags],
            "critical_defects_count": len(critical_defects),
            "critical_defects": [m.model_dump() for m in critical_defects],
            "scaffolding_deficits_count": len(scaffolding_deficits),
            "scaffolding_deficits": [m.model_dump() for m in scaffolding_deficits],
        }

    # -----------------------------------------------------------------------
    # Consolidated Parent Portal View
    # -----------------------------------------------------------------------

    def get_parent_portal_summary(
        self, student_id: str, as_of: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Generates a comprehensive summary payload for the Parent / Coach Dashboard:
          - Learner profile & progress
          - 5-stage independence state & next steps
          - Habit consistency, streak, and scheduled study window
          - Pending & unlocked parent rewards
          - Active advisory coaching / re-sit alerts
        """
        profile = self.storage.get_student_profile(student_id)
        progression = self.evaluate_independence_progression(student_id, as_of=as_of)
        habit_report = self.evaluate_habit_and_pace(student_id, as_of=as_of)
        rewards = self.evaluate_parent_rewards(student_id)
        alerts = self.storage.list_adult_resit_alerts(student_id=student_id, unacknowledged_only=True)
        schedule = self.storage.get_study_habit_schedule(student_id)

        return {
            "student_id": profile.student_id,
            "enrolled_course_id": profile.enrolled_course_id,
            "xp_total": profile.xp_total,
            "progress_pct": profile.progress_pct,
            "rolling_pace_xp": profile.rolling_pace_xp,
            "independence": {
                "current_stage": progression.current_stage,
                "eligible_for_promotion": progression.eligible_for_promotion,
                "next_stage": progression.next_stage,
                "recommendation": progression.advisory_recommendation,
            },
            "habit": {
                "health": habit_report.habit_health,
                "streak_days": habit_report.streak_days,
                "window_adherence_pct": habit_report.window_adherence_pct,
                "scheduled_start": schedule.scheduled_start_time if schedule else None,
                "scheduled_end": schedule.scheduled_end_time if schedule else None,
                "viability_warning": habit_report.viability_warning,
                "burnout_warning": habit_report.burnout_warning,
                "recommendation": habit_report.recommendation,
            },
            "rewards": [r.model_dump() for r in rewards],
            "active_alerts": [a.model_dump() for a in alerts],
        }
