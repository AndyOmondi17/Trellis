"""
tests/test_tier8_accountability.py
Deterministic Verification Suite for Tier 8:
Parent / Coach Accountability Portal, Contextual Habit Formation & Content Remediation Analytics.

Tests all functional requirements across:
  - MemoryReferenceAdapter (Adapter C)
  - StandaloneRelationalAdapter (Adapter A)
  - StandaloneGraphAdapter (Adapter B)
  - Cross-Backend Bit-Identical Parity
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Generator

import pytest

from trellis.domain.models import (
    AdultReSitAlert,
    CourseCatalogItem,
    InteractionAttempt,
    KnowledgePoint,
    ParentCustomReward,
    StudentProfile,
    StudentTopicState,
    StudyHabitSchedule,
    TopicNode,
    XPLedgerEntry,
)
from trellis.ports.storage_port import TrellisStoragePort, get_storage_adapter
from trellis.services.accountability_analytics import (
    INDEPENDENCE_STAGE_ORDER,
    AccountabilityAnalyticsConfig,
    AccountabilityAnalyticsService,
)


# ---------------------------------------------------------------------------
# Test Fixtures (Parameterized Across Memory, Relational, and Graph Backends)
# ---------------------------------------------------------------------------

@pytest.fixture(params=["memory", "relational", "graph"])
def adapter(request) -> Generator[TrellisStoragePort, None, None]:
    """Provides a clean, standalone instance of each persistence backend."""
    backend = request.param
    ad = get_storage_adapter(backend)
    yield ad
    if hasattr(ad, "close"):
        ad.close()


@pytest.fixture
def analytics_service(adapter) -> AccountabilityAnalyticsService:
    """Provides an AccountabilityAnalyticsService wired to the parameterized adapter."""
    # Register test course catalog
    course = CourseCatalogItem(
        course_id="CRS_TIER8_TEST",
        course_code="t8",
        title="Tier 8 Analytics Test Track",
        grade_band="8-9",
        benchmark_xp=2000,
    )
    # Register sample topics
    topics = []
    for i in range(1, 6):
        tid = f"trl_t8_topic_{i:03d}"
        kps = [
            KnowledgePoint(
                kp_index=0,
                kp_id=f"{tid}#kp_0",
                scaffolding_tier="KP_1_BASELINE",
                kp_focus=f"Baseline operation for {tid}",
                novel_chunk_count=2,
            ),
            KnowledgePoint(
                kp_index=1,
                kp_id=f"{tid}#kp_1",
                scaffolding_tier="KP_2_EDGE",
                kp_focus=f"Edge case operation for {tid}",
                novel_chunk_count=3,
            ),
        ]
        node = TopicNode(
            id=tid,
            title=f"Tier 8 Topic {i}",
            course_id="CRS_TIER8_TEST",
            canonical_domain="06_prealgebra_and_elementary_algebra",
            domain_cluster="CRS_TIER8_TEST::alg",
            skill_type="PROCEDURAL",
            grade_level_min=8,
            grade_level_max=9,
            estimated_xp=4,
            knowledge_points=kps,
        )
        topics.append(node)

    payload = {
        "schema_version": "2.0.0-trellis",
        "graph_id": "tier8-analytics-dag",
        "course_catalog": [course.model_dump()],
        "nodes": [t.model_dump() for t in topics],
    }
    adapter.ingest_knowledge_graph(payload)
    return AccountabilityAnalyticsService(adapter)


# ---------------------------------------------------------------------------
# FR-8.1: 5-Stage Independence & Adult Re-Sit Tests
# ---------------------------------------------------------------------------

def test_fr8_1_independence_stage_progression_ladder(analytics_service: AccountabilityAnalyticsService):
    """Verifies stepwise progression across the 5 scaffolded independence stages."""
    storage = analytics_service.storage
    student_id = "std_indep_001"

    # Register learner starting at CO_SITTING
    profile = StudentProfile(
        student_id=student_id,
        enrolled_course_id="CRS_TIER8_TEST",
        xp_total=100,
        rolling_pace_xp=25.0,
        independence_stage="CO_SITTING",
    )
    storage.upsert_student_profile(profile)

    # 1. Step: CO_SITTING -> PER_TASK_CHECKIN
    prog = analytics_service.evaluate_independence_progression(student_id)
    assert prog.eligible_for_promotion is True
    assert prog.next_stage == "PER_TASK_CHECKIN"
    updated = analytics_service.promote_independence_stage(student_id)
    assert updated.independence_stage == "PER_TASK_CHECKIN"

    # 2. Step: PER_TASK_CHECKIN -> DAILY_CHECKIN
    prog = analytics_service.evaluate_independence_progression(student_id)
    assert prog.eligible_for_promotion is True
    assert prog.next_stage == "DAILY_CHECKIN"
    updated = analytics_service.promote_independence_stage(student_id)
    assert updated.independence_stage == "DAILY_CHECKIN"

    # 3. Step: DAILY_CHECKIN -> EVERY_FEW_DAYS_CHECKIN
    prog = analytics_service.evaluate_independence_progression(student_id)
    assert prog.eligible_for_promotion is True
    assert prog.next_stage == "EVERY_FEW_DAYS_CHECKIN"
    updated = analytics_service.promote_independence_stage(student_id)
    assert updated.independence_stage == "EVERY_FEW_DAYS_CHECKIN"

    # 4. Step: EVERY_FEW_DAYS_CHECKIN -> WEEKLY_CHECKIN
    prog = analytics_service.evaluate_independence_progression(student_id)
    assert prog.eligible_for_promotion is True
    assert prog.next_stage == "WEEKLY_CHECKIN"
    updated = analytics_service.promote_independence_stage(student_id)
    assert updated.independence_stage == "WEEKLY_CHECKIN"

    # 5. At WEEKLY_CHECKIN, promotion is capped (max stage reached)
    prog = analytics_service.evaluate_independence_progression(student_id)
    assert prog.eligible_for_promotion is False
    assert prog.next_stage is None


def test_fr8_1_independence_promotion_blocked_by_blowoffs_or_alerts(analytics_service: AccountabilityAnalyticsService):
    """Verifies that promotion is held when consecutive blowoffs or negative penalties occur."""
    storage = analytics_service.storage
    student_id = "std_indep_blocked"

    profile = StudentProfile(
        student_id=student_id,
        enrolled_course_id="CRS_TIER8_TEST",
        xp_total=120,
        rolling_pace_xp=30.0,
        independence_stage="DAILY_CHECKIN",
        consecutive_blowoffs=2,  # Blow-off detected
    )
    storage.upsert_student_profile(profile)

    prog = analytics_service.evaluate_independence_progression(student_id)
    assert prog.eligible_for_promotion is False
    assert "rapid submissions" in prog.advisory_recommendation.lower() or "recent" in prog.advisory_recommendation.lower()


def test_fr8_1_parent_coach_stage_override(analytics_service: AccountabilityAnalyticsService):
    """Verifies direct, non-punitive stage adjustment by parent or coach."""
    storage = analytics_service.storage
    student_id = "std_override_001"

    profile = StudentProfile(
        student_id=student_id,
        enrolled_course_id="CRS_TIER8_TEST",
        independence_stage="DAILY_CHECKIN",
    )
    storage.upsert_student_profile(profile)

    # Parent adjusts stage directly to WEEKLY_CHECKIN
    updated = analytics_service.adjust_independence_stage(student_id, "WEEKLY_CHECKIN")
    assert updated.independence_stage == "WEEKLY_CHECKIN"

    # Parent adjusts stage to CO_SITTING as a temporary coaching arrangement
    updated2 = analytics_service.adjust_independence_stage(student_id, "CO_SITTING")
    assert updated2.independence_stage == "CO_SITTING"


def test_fr8_1_adult_resit_alerts_advisory_lifecycle(analytics_service: AccountabilityAnalyticsService):
    """Verifies that Adult Re-Sit alerts are recorded and acknowledged without blocking student practice."""
    storage = analytics_service.storage
    student_id = "std_resit_001"

    profile = StudentProfile(
        student_id=student_id,
        enrolled_course_id="CRS_TIER8_TEST",
        independence_stage="DAILY_CHECKIN",
    )
    storage.upsert_student_profile(profile)

    # Trigger advisory alert
    alert = analytics_service.trigger_adult_resit_advisory(
        student_id=student_id,
        reason="XP_HACKER_PENALTY",
        details="Student completed 3 questions in < 2 seconds. Suggest observing worked example engagement.",
        severity="HIGH",
    )

    assert alert.acknowledged is False
    assert alert.severity == "HIGH"

    # Verify alert appears in unacknowledged list
    active = storage.list_adult_resit_alerts(student_id, unacknowledged_only=True)
    assert len(active) == 1
    assert active[0].alert_id == alert.alert_id

    # Acknowledge alert
    analytics_service.acknowledge_adult_resit(alert.alert_id)
    active_after = storage.list_adult_resit_alerts(student_id, unacknowledged_only=True)
    assert len(active_after) == 0

    all_alerts = storage.list_adult_resit_alerts(student_id, unacknowledged_only=False)
    assert len(all_alerts) == 1
    assert all_alerts[0].acknowledged is True


def test_fr8_1_custom_parent_rewards_binding_and_unlocking(analytics_service: AccountabilityAnalyticsService):
    """Verifies that parent rewards unlock automatically when student XP reaches the target."""
    storage = analytics_service.storage
    student_id = "std_rewards_001"

    profile = StudentProfile(
        student_id=student_id,
        enrolled_course_id="CRS_TIER8_TEST",
        xp_total=40,
    )
    storage.upsert_student_profile(profile)

    # 1. Create a 50 XP reward (PENDING) and a 100 XP reward (PENDING)
    r50 = analytics_service.create_parent_reward(student_id, "30 mins extra gaming", xp_target=50)
    r100 = analytics_service.create_parent_reward(student_id, "Weekend trip to ice cream parlor", xp_target=100)

    assert r50.status == "PENDING"
    assert r100.status == "PENDING"

    # 2. Student earns 20 XP -> Total = 60 XP
    updated_profile = profile.model_copy(update={"xp_total": 60})
    storage.upsert_student_profile(updated_profile)

    rewards = analytics_service.evaluate_parent_rewards(student_id)
    r50_eval = next(r for r in rewards if r.reward_id == r50.reward_id)
    r100_eval = next(r for r in rewards if r.reward_id == r100.reward_id)

    assert r50_eval.status == "UNLOCKED"
    assert r100_eval.status == "PENDING"

    # 3. Parent claims the unlocked reward
    analytics_service.claim_parent_reward(r50.reward_id)
    rewards_after_claim = storage.get_parent_rewards(student_id)
    r50_claimed = next(r for r in rewards_after_claim if r.reward_id == r50.reward_id)
    assert r50_claimed.status == "CLAIMED"
    assert r50_claimed.claimed_at is not None


# ---------------------------------------------------------------------------
# FR-8.2: Contextual Habit Formation & Minimum Pace Monitor Tests
# ---------------------------------------------------------------------------

def test_fr8_2_study_habit_schedule_and_streak_tracking(analytics_service: AccountabilityAnalyticsService):
    """Verifies setting study habit schedule and tracking daily streaks with resets on skipped days."""
    storage = analytics_service.storage
    student_id = "std_habit_streak"

    profile = StudentProfile(
        student_id=student_id,
        enrolled_course_id="CRS_TIER8_TEST",
        rolling_pace_xp=35.0,
    )
    storage.upsert_student_profile(profile)

    # 1. Set schedule: 16:00 to 17:00, weekdays
    sched = analytics_service.set_study_habit_schedule(
        student_id=student_id,
        scheduled_start_time="16:00",
        scheduled_end_time="17:00",
        active_days=[0, 1, 2, 3, 4],
        target_daily_xp=40,
    )
    assert sched.scheduled_start_time == "16:00"
    assert sched.current_streak_days == 0

    # 2. Day 1 Practice
    d1 = date(2026, 10, 19)  # Monday
    s1 = analytics_service.record_daily_session_telemetry(student_id, d1, xp_earned_today=45)
    assert s1.current_streak_days == 1
    assert s1.longest_streak_days == 1

    # 3. Day 2 Practice (Consecutive day)
    d2 = date(2026, 10, 20)  # Tuesday
    s2 = analytics_service.record_daily_session_telemetry(student_id, d2, xp_earned_today=40)
    assert s2.current_streak_days == 2
    assert s2.longest_streak_days == 2

    # 4. Day 3 Practice (Consecutive day)
    d3 = date(2026, 10, 21)  # Wednesday
    s3 = analytics_service.record_daily_session_telemetry(student_id, d3, xp_earned_today=50)
    assert s3.current_streak_days == 3
    assert s3.longest_streak_days == 3

    # 5. Missed Thursday & Friday, practiced on Saturday (Gap of 3 days -> streak reset to 1)
    d4 = date(2026, 10, 24)  # Saturday
    s4 = analytics_service.record_daily_session_telemetry(student_id, d4, xp_earned_today=40)
    assert s4.current_streak_days == 1
    assert s4.longest_streak_days == 3  # Longest streak preserved!


def test_fr8_2_window_adherence_telemetry(analytics_service: AccountabilityAnalyticsService):
    """Verifies calculation of scheduled study window adherence percentage."""
    storage = analytics_service.storage
    student_id = "std_window_001"

    profile = StudentProfile(
        student_id=student_id,
        enrolled_course_id="CRS_TIER8_TEST",
        rolling_pace_xp=30.0,
    )
    storage.upsert_student_profile(profile)

    analytics_service.set_study_habit_schedule(
        student_id=student_id,
        scheduled_start_time="16:00",
        scheduled_end_time="17:00",
    )

    # Record 4 attempts: 3 inside window (16:15, 16:30, 16:45), 1 outside (20:00)
    base_date = datetime(2026, 10, 24, 16, 15, tzinfo=timezone.utc)
    for i, minutes in enumerate([15, 30, 45]):
        storage.record_attempt(
            InteractionAttempt(
                attempt_id=f"att_in_{i}",
                student_id=student_id,
                topic_id="trl_t8_topic_001",
                kp_id="trl_t8_topic_001#kp_0",
                submitted_answer="x=2",
                is_correct=True,
                duration_ms=4500,
                created_at=datetime(2026, 10, 24, 16, minutes, tzinfo=timezone.utc),
            )
        )
    storage.record_attempt(
        InteractionAttempt(
            attempt_id="att_out_0",
            student_id=student_id,
            topic_id="trl_t8_topic_001",
            kp_id="trl_t8_topic_001#kp_0",
            submitted_answer="x=2",
            is_correct=True,
            duration_ms=4500,
            created_at=datetime(2026, 10, 24, 20, 0, tzinfo=timezone.utc),
        )
    )

    report = analytics_service.evaluate_habit_and_pace(student_id)
    # 3 out of 4 attempts inside window = 75.0%
    assert report.window_adherence_pct == 75.0


def test_fr8_2_minimum_pace_viability_and_burnout_warnings(analytics_service: AccountabilityAnalyticsService):
    """Verifies viability floor (< 15 XP/weekday) and burnout ceiling (> 90 XP/day) triggers."""
    storage = analytics_service.storage

    # Case A: Below viability floor (10 XP/weekday)
    p_low = StudentProfile(
        student_id="std_low_pace",
        enrolled_course_id="CRS_TIER8_TEST",
        rolling_pace_xp=10.0,
    )
    storage.upsert_student_profile(p_low)
    rep_low = analytics_service.evaluate_habit_and_pace("std_low_pace")
    assert rep_low.viability_warning is True
    assert rep_low.burnout_warning is False
    assert rep_low.habit_health == "AT_RISK_DROPOUT"
    assert "below 15 xp/weekday" in rep_low.recommendation.lower()

    # Case B: Above burnout ceiling (105 XP/day)
    p_high = StudentProfile(
        student_id="std_high_pace",
        enrolled_course_id="CRS_TIER8_TEST",
        rolling_pace_xp=105.0,
    )
    storage.upsert_student_profile(p_high)
    rep_high = analytics_service.evaluate_habit_and_pace("std_high_pace")
    assert rep_high.viability_warning is False
    assert rep_high.burnout_warning is True
    assert rep_high.habit_health == "BURNOUT_RISK"
    assert "burnout" in rep_high.recommendation.lower()

    # Case C: Optimal healthy pace (45 XP/day)
    p_opt = StudentProfile(
        student_id="std_opt_pace",
        enrolled_course_id="CRS_TIER8_TEST",
        rolling_pace_xp=45.0,
    )
    storage.upsert_student_profile(p_opt)
    rep_opt = analytics_service.evaluate_habit_and_pace("std_opt_pace")
    assert rep_opt.viability_warning is False
    assert rep_opt.burnout_warning is False


# ---------------------------------------------------------------------------
# FR-8.3: Granular Content Remediation Analytics & 95%/99% Bar Tests
# ---------------------------------------------------------------------------

def test_fr8_3_pass_rate_gold_standard_calculation(analytics_service: AccountabilityAnalyticsService):
    """
    Verifies that 1st-attempt and 2-attempt pass rates are computed correctly
    and flagged against the 95%/99% standard:
      - Pass if P1 >= 0.95 and P2 >= 0.99
      - Flag P1 deficit if P1 < 0.95
      - Flag critical defect if P2 < 0.99
    """
    storage = analytics_service.storage
    topic_id = "trl_t8_topic_002"
    kp_id = f"{topic_id}#kp_0"

    # Simulate 100 students on kp_0:
    # 96 students pass on attempt 1 (P1 = 96%)
    # 3 students fail attempt 1, pass attempt 2 (P2 = 99%)
    # 1 student fails both attempt 1 and attempt 2
    base_time = datetime(2026, 10, 24, 10, 0, 0, tzinfo=timezone.utc)
    for s_idx in range(100):
        s_id = f"sim_std_{s_idx:03d}"
        t1 = base_time + timedelta(seconds=s_idx)
        t2 = base_time + timedelta(seconds=s_idx, minutes=1)
        if s_idx < 96:
            # 1st attempt pass
            storage.record_attempt(
                InteractionAttempt(
                    attempt_id=f"att_{s_id}_1",
                    student_id=s_id,
                    topic_id=topic_id,
                    kp_id=kp_id,
                    submitted_answer="ans",
                    is_correct=True,
                    duration_ms=3000,
                    created_at=t1,
                )
            )
        elif s_idx < 99:
            # 1st attempt fail, 2nd attempt pass
            storage.record_attempt(
                InteractionAttempt(
                    attempt_id=f"att_{s_id}_1",
                    student_id=s_id,
                    topic_id=topic_id,
                    kp_id=kp_id,
                    submitted_answer="wrong",
                    is_correct=False,
                    duration_ms=4000,
                    created_at=t1,
                )
            )
            storage.record_attempt(
                InteractionAttempt(
                    attempt_id=f"att_{s_id}_2",
                    student_id=s_id,
                    topic_id=topic_id,
                    kp_id=kp_id,
                    submitted_answer="ans",
                    is_correct=True,
                    duration_ms=3500,
                    created_at=t2,
                )
            )
        else:
            # Fails both
            storage.record_attempt(
                InteractionAttempt(
                    attempt_id=f"att_{s_id}_1",
                    student_id=s_id,
                    topic_id=topic_id,
                    kp_id=kp_id,
                    submitted_answer="wrong1",
                    is_correct=False,
                    duration_ms=4000,
                    created_at=t1,
                )
            )
            storage.record_attempt(
                InteractionAttempt(
                    attempt_id=f"att_{s_id}_2",
                    student_id=s_id,
                    topic_id=topic_id,
                    kp_id=kp_id,
                    submitted_answer="wrong2",
                    is_correct=False,
                    duration_ms=4200,
                    created_at=t2,
                )
            )

    metrics = analytics_service.analyze_pass_rates(topic_id=topic_id)
    kp_metric = next(m for m in metrics if m.entity_id == kp_id)

    assert kp_metric.total_first_attempts == 100
    assert kp_metric.first_attempt_passes == 96
    assert kp_metric.second_attempt_passes == 99
    assert kp_metric.pass_rate_first_attempt == 0.96
    assert kp_metric.pass_rate_two_attempts == 0.99
    assert kp_metric.flag_first_pass_deficit is False
    assert kp_metric.flag_critical_defect is False


def test_fr8_3_pass_rate_deficit_flagging(analytics_service: AccountabilityAnalyticsService):
    """Verifies that pass rates below 95% and 99% trigger the appropriate diagnostic flags."""
    storage = analytics_service.storage
    topic_id = "trl_t8_topic_003"
    kp_id = f"{topic_id}#kp_0"

    # Simulate 100 students on kp_0:
    # Only 90 pass first attempt (90% < 95% -> First Pass Deficit)
    # 7 pass second attempt -> Total = 97% (< 99% -> Critical Defect)
    base_time = datetime(2026, 10, 24, 10, 0, 0, tzinfo=timezone.utc)
    for s_idx in range(100):
        s_id = f"sim_substd_{s_idx:03d}"
        t1 = base_time + timedelta(seconds=s_idx)
        t2 = base_time + timedelta(seconds=s_idx, minutes=1)
        if s_idx < 90:
            storage.record_attempt(
                InteractionAttempt(
                    attempt_id=f"att_{s_id}_1",
                    student_id=s_id,
                    topic_id=topic_id,
                    kp_id=kp_id,
                    submitted_answer="ans",
                    is_correct=True,
                    duration_ms=3000,
                    created_at=t1,
                )
            )
        elif s_idx < 97:
            storage.record_attempt(
                InteractionAttempt(
                    attempt_id=f"att_{s_id}_1",
                    student_id=s_id,
                    topic_id=topic_id,
                    kp_id=kp_id,
                    submitted_answer="wrong",
                    is_correct=False,
                    duration_ms=3000,
                    created_at=t1,
                )
            )
            storage.record_attempt(
                InteractionAttempt(
                    attempt_id=f"att_{s_id}_2",
                    student_id=s_id,
                    topic_id=topic_id,
                    kp_id=kp_id,
                    submitted_answer="ans",
                    is_correct=True,
                    duration_ms=3000,
                    created_at=t2,
                )
            )
        else:
            storage.record_attempt(
                InteractionAttempt(
                    attempt_id=f"att_{s_id}_1",
                    student_id=s_id,
                    topic_id=topic_id,
                    kp_id=kp_id,
                    submitted_answer="wrong",
                    is_correct=False,
                    duration_ms=3000,
                    created_at=t1,
                )
            )

    metrics = analytics_service.analyze_pass_rates(topic_id=topic_id)
    kp_metric = next(m for m in metrics if m.entity_id == kp_id)

    assert kp_metric.pass_rate_first_attempt == 0.90
    assert kp_metric.pass_rate_two_attempts == 0.97
    assert kp_metric.flag_first_pass_deficit is True
    assert kp_metric.flag_critical_defect is True
    assert "critical defect" in kp_metric.recommended_action.lower()


def test_fr8_3_superficial_priming_detection(analytics_service: AccountabilityAnalyticsService):
    """
    Verifies that superficial priming is detected when high lesson pass rate
    is accompanied by steep drop in subsequent unprimed review/quiz retention.
    """
    storage = analytics_service.storage
    topic_id = "trl_t8_topic_004"
    kp_id = f"{topic_id}#kp_0"

    # Simulate 5 students:
    # In lesson (first 3 attempts): 100% correct (primed by worked examples)
    # In subsequent unprimed reviews: mostly incorrect
    for s_idx in range(5):
        s_id = f"prime_std_{s_idx}"
        # Lesson attempts (100% correct)
        for a_idx in range(3):
            storage.record_attempt(
                InteractionAttempt(
                    attempt_id=f"att_l_{s_id}_{a_idx}",
                    student_id=s_id,
                    topic_id=topic_id,
                    kp_id=kp_id,
                    submitted_answer="ans",
                    is_correct=True,
                    duration_ms=3000,
                    created_at=datetime(2026, 10, 20, 10, a_idx, tzinfo=timezone.utc),
                )
            )
        # Unprimed review attempts (only 1 out of 3 correct = 33% retention)
        for r_idx in range(3):
            storage.record_attempt(
                InteractionAttempt(
                    attempt_id=f"att_r_{s_id}_{r_idx}",
                    student_id=s_id,
                    topic_id=topic_id,
                    kp_id=kp_id,
                    submitted_answer="wrong" if r_idx > 0 else "ans",
                    is_correct=(r_idx == 0),
                    duration_ms=5000,
                    created_at=datetime(2026, 10, 24, 10, r_idx, tzinfo=timezone.utc),
                )
            )

    report = analytics_service.evaluate_priming_effect(topic_id)
    assert report.lesson_pass_rate == 1.0
    assert report.unprimed_review_quiz_accuracy < 0.60
    assert report.superficial_priming_detected is True
    assert "superficial priming detected" in report.recommendation.lower()


def test_fr8_3_in_task_student_flagging_and_author_queue(analytics_service: AccountabilityAnalyticsService):
    """Verifies student/parent in-task content flagging and author queue aggregation."""
    storage = analytics_service.storage
    student_id = "std_flagger"
    topic_id = "trl_t8_topic_005"
    kp_id = f"{topic_id}#kp_1"

    # 1. Student flags a question during practice
    flag = analytics_service.flag_content_item(
        student_id=student_id,
        topic_id=topic_id,
        kp_id=kp_id,
        problem_stem="Solve for x: 2x + 5 = 11",
        flag_type="AMBIGUOUS_WORDING",
        student_comment="Step 2 in explanation skipped division by 2.",
    )

    assert flag.flag_id.startswith("flag_")
    assert flag.status == "OPEN"

    # 2. Author queue includes the flag
    queue = analytics_service.get_content_author_remediation_queue()
    assert queue["open_flags_count"] >= 1
    found = any(f["flag_id"] == flag.flag_id for f in queue["open_flags"])
    assert found is True

    # 3. Resolve the flag
    analytics_service.resolve_content_flag(flag.flag_id, status="RESOLVED")
    open_flags_after = storage.list_content_flags(status="OPEN")
    assert not any(f.flag_id == flag.flag_id for f in open_flags_after)


def test_fr8_consolidated_parent_portal_summary(analytics_service: AccountabilityAnalyticsService):
    """Verifies that the consolidated parent portal summary correctly bundles all Tier 8 data."""
    storage = analytics_service.storage
    student_id = "std_parent_summary"

    profile = StudentProfile(
        student_id=student_id,
        enrolled_course_id="CRS_TIER8_TEST",
        xp_total=85,
        progress_pct=22.5,
        rolling_pace_xp=32.0,
        independence_stage="DAILY_CHECKIN",
    )
    storage.upsert_student_profile(profile)

    analytics_service.set_study_habit_schedule(
        student_id=student_id,
        scheduled_start_time="17:00",
        scheduled_end_time="18:00",
    )
    analytics_service.create_parent_reward(student_id, "Choice of weekend movie", xp_target=100)
    analytics_service.trigger_adult_resit_advisory(
        student_id,
        reason="FREQUENT_REMEDIATION_HALTS",
        details="Student encountered two Tier-3 remediation halts in quadratic factoring.",
        severity="MEDIUM",
    )

    summary = analytics_service.get_parent_portal_summary(student_id)

    assert summary["student_id"] == student_id
    assert summary["enrolled_course_id"] == "CRS_TIER8_TEST"
    assert summary["xp_total"] == 85
    assert summary["progress_pct"] == 22.5
    assert summary["rolling_pace_xp"] == 32.0
    assert summary["independence"]["current_stage"] == "DAILY_CHECKIN"
    assert summary["habit"]["scheduled_start"] == "17:00"
    assert summary["habit"]["scheduled_end"] == "18:00"
    assert len(summary["rewards"]) == 1
    assert summary["rewards"][0]["title"] == "Choice of weekend movie"
    assert len(summary["active_alerts"]) == 1
    assert summary["active_alerts"][0]["reason"] == "FREQUENT_REMEDIATION_HALTS"


# ---------------------------------------------------------------------------
# Cross-Backend Bit-Identical Parity Test
# ---------------------------------------------------------------------------

def test_cross_backend_bit_identical_parity_tier8():
    """
    Verifies that identical student telemetry, rewards, habit schedules,
    and pass-rate metrics evaluate bit-identically across all 3 backends:
      - MemoryReferenceAdapter (Adapter C)
      - StandaloneRelationalAdapter (Adapter A)
      - StandaloneGraphAdapter (Adapter B)
    """
    backends = ["memory", "relational", "graph"]
    summaries = []
    pass_metrics_lists = []

    for b in backends:
        ad = get_storage_adapter(b)

        # Setup standard course
        course = CourseCatalogItem(
            course_id="CRS_PARITY_8",
            course_code="p8",
            title="Parity Course 8",
            grade_band="8-9",
            benchmark_xp=1500,
        )
        tid = "trl_p8_topic_001"
        kp = KnowledgePoint(
            kp_index=0,
            kp_id=f"{tid}#kp_0",
            scaffolding_tier="KP_1_BASELINE",
            kp_focus="Parity Baseline",
        )
        node = TopicNode(
            id=tid,
            title="Parity Topic",
            course_id="CRS_PARITY_8",
            canonical_domain="06_prealgebra_and_elementary_algebra",
            domain_cluster="CRS_PARITY_8::alg",
            skill_type="PROCEDURAL",
            grade_level_min=8,
            grade_level_max=9,
            knowledge_points=[kp],
        )
        ad.ingest_knowledge_graph(
            {
                "schema_version": "2.0.0-trellis",
                "graph_id": "parity-dag",
                "course_catalog": [course.model_dump()],
                "nodes": [node.model_dump()],
            }
        )

        svc = AccountabilityAnalyticsService(ad)
        s_id = "std_parity_user"

        prof = StudentProfile(
            student_id=s_id,
            enrolled_course_id="CRS_PARITY_8",
            xp_total=75,
            progress_pct=15.0,
            rolling_pace_xp=28.0,
            independence_stage="PER_TASK_CHECKIN",
        )
        ad.upsert_student_profile(prof)

        svc.set_study_habit_schedule(
            student_id=s_id,
            scheduled_start_time="15:30",
            scheduled_end_time="16:30",
        )
        svc.create_parent_reward(s_id, "Parity Reward Item", xp_target=50)  # Already unlocked (75 >= 50)
        svc.trigger_adult_resit_advisory(s_id, "PACING_BELOW_VIABILITY", "Pace test details", severity="LOW")

        # Record standardized attempts
        ad.record_attempt(
            InteractionAttempt(
                attempt_id="att_p_1",
                student_id=s_id,
                topic_id=tid,
                kp_id=f"{tid}#kp_0",
                submitted_answer="42",
                is_correct=True,
                duration_ms=2500,
                created_at=datetime(2026, 10, 24, 15, 45, tzinfo=timezone.utc),
            )
        )

        sum_data = svc.get_parent_portal_summary(s_id)
        pm_data = svc.analyze_pass_rates(topic_id=tid)

        summaries.append(sum_data)
        pass_metrics_lists.append([m.model_dump() for m in pm_data])

        if hasattr(ad, "close"):
            ad.close()

    # Compare backend outputs against reference backend (index 0 = memory)
    ref_summary = summaries[0]
    ref_metrics = pass_metrics_lists[0]

    for i, b_name in enumerate(backends[1:], start=1):
        # Compare key metrics
        assert summaries[i]["xp_total"] == ref_summary["xp_total"], f"XP mismatch in {b_name}"
        assert summaries[i]["progress_pct"] == ref_summary["progress_pct"], f"Progress % mismatch in {b_name}"
        assert summaries[i]["independence"]["current_stage"] == ref_summary["independence"]["current_stage"], f"Stage mismatch in {b_name}"
        assert summaries[i]["habit"]["scheduled_start"] == ref_summary["habit"]["scheduled_start"], f"Habit start mismatch in {b_name}"
        assert len(summaries[i]["rewards"]) == len(ref_summary["rewards"]), f"Rewards count mismatch in {b_name}"
        assert summaries[i]["rewards"][0]["status"] == ref_summary["rewards"][0]["status"], f"Reward status mismatch in {b_name}"
        assert len(summaries[i]["active_alerts"]) == len(ref_summary["active_alerts"]), f"Alerts count mismatch in {b_name}"

        # Compare pass metrics
        assert pass_metrics_lists[i] == ref_metrics, f"Pass rate metrics mismatch in {b_name}"
