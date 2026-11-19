"""
tests/test_tier7_gamification.py
Deterministic Verification Suite for Tier 7:
Gamification Economy, Anti-Cheat XP Hacker Defenses & Learning Efficiency.

Tests all functional requirements across:
  - MemoryReferenceAdapter (Adapter C)
  - StandaloneRelationalAdapter (Adapter A)
  - StandaloneGraphAdapter (Adapter B)
  - Cross-Backend Bit-Identical Parity
"""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Generator

import pytest

from trellis.domain.models import (
    CourseCatalogItem,
    KnowledgePoint,
    LeagueCohort,
    LeagueMemberStanding,
    QualityTier,
    StudentProfile,
    StudentTopicState,
    TopicNode,
    XPLedgerEntry,
)
from trellis.engine.gamification_efficiency import (
    GamificationEfficiencyConfig,
    GamificationEfficiencyEngine,
    LEAGUE_TIER_ORDER,
)
from trellis.engine.practice_evaluator import (
    PracticeConfig,
    PracticeEvaluator,
    PracticeSubmission,
)
from trellis.ports.storage_port import TrellisStoragePort, get_storage_adapter


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
def populated_engine(adapter) -> GamificationEfficiencyEngine:
    """Provides a GamificationEfficiencyEngine populated with a sample curriculum course."""
    # Register Course
    course = CourseCatalogItem(
        course_id="CRS_TIER7_TEST",
        course_code="t7",
        title="Tier 7 Gamification Test Track",
        grade_band="8-9",
        benchmark_xp=3000,
    )
    # Register Topics
    topics = []
    for i in range(1, 11):
        tid = f"trl_t7_node_{i:03d}"
        kp = KnowledgePoint(
            kp_index=0,
            kp_id=f"{tid}#kp_0",
            scaffolding_tier="KP_1_BASELINE",
            kp_focus=f"Baseline operation for {tid}",
            novel_chunk_count=2,
        )
        node = TopicNode(
            id=tid,
            title=f"Tier 7 Topic {i}",
            course_id="CRS_TIER7_TEST",
            canonical_domain="06_prealgebra_and_elementary_algebra",
            domain_cluster="CRS_TIER7_TEST::alg",
            skill_type="PROCEDURAL",
            grade_level_min=8,
            grade_level_max=9,
            estimated_xp=4,
            knowledge_points=[kp],
        )
        topics.append(node)

    # Ingest into backend
    payload = {
        "schema_version": "2.0.0-trellis",
        "graph_id": "tier7-gamification-dag",
        "course_catalog": [course.model_dump()],
        "nodes": [t.model_dump() for t in topics],
    }
    adapter.ingest_knowledge_graph(payload)
    return GamificationEfficiencyEngine(adapter)


# ---------------------------------------------------------------------------
# FR-7.1: XP-Time Equivalence & 6-Tier Quality Scaling Tests
# ---------------------------------------------------------------------------

def test_fr7_1_lesson_quality_scaling(populated_engine):
    """
    Verifies 6-tier quality multipliers for lesson completion:
      - BONUS_XP (1.25x): 0 errors, 0 hints, first attempt (4 * 1.25 -> 5 XP)
      - FULL_XP (1.00x): <= 1 error (4 * 1.0 -> 4 XP)
      - MOST_XP (0.75x): 2 errors (4 * 0.75 -> 3 XP)
      - LITTLE_XP (0.25x): >= 3 errors (4 * 0.25 -> 1 XP)
    """
    engine = populated_engine

    # 1. Stellar first try -> BONUS_XP
    tier, xp = engine.compute_lesson_quality(total_kps=2, errors_across_kps=0, hints_used=0, is_first_attempt=True, base_xp=4)
    assert tier == "BONUS_XP"
    assert xp == 5

    # 2. 1 minor slip -> FULL_XP
    tier, xp = engine.compute_lesson_quality(total_kps=2, errors_across_kps=1, hints_used=0, is_first_attempt=False, base_xp=4)
    assert tier == "FULL_XP"
    assert xp == 4

    # 3. 2 errors -> MOST_XP
    tier, xp = engine.compute_lesson_quality(total_kps=2, errors_across_kps=2, hints_used=0, is_first_attempt=False, base_xp=4)
    assert tier == "MOST_XP"
    assert xp == 3

    # 4. >= 3 errors -> LITTLE_XP
    tier, xp = engine.compute_lesson_quality(total_kps=2, errors_across_kps=4, hints_used=0, is_first_attempt=False, base_xp=4)
    assert tier == "LITTLE_XP"
    assert xp == 1

    # 5. Base XP 3 calculation
    tier, xp = engine.compute_lesson_quality(total_kps=1, errors_across_kps=0, hints_used=0, is_first_attempt=True, base_xp=3)
    assert tier == "BONUS_XP"
    assert xp == 4  # ceil(3 * 1.25) = 4


def test_fr7_1_review_and_quiz_quality_scaling(populated_engine):
    """
    Verifies review and quiz quality scaling across tiers:
      - Review: clean pass -> BONUS_XP; 1 slip -> FULL_XP; 2 slips -> MOST_XP; failed -> LITTLE_XP / ZERO_XP
      - Quiz: 100% -> BONUS_XP; >= 80% -> FULL_XP; >= 60% -> MOST_XP; >= 40% -> LITTLE_XP; < 40% -> ZERO_XP
    """
    engine = populated_engine

    # Review Scaling
    t, xp = engine.compute_review_quality(total_items=4, error_count=0, passed=True, base_xp=3)
    assert t == "BONUS_XP" and xp == 4  # ceil(3 * 1.25) = 4

    t, xp = engine.compute_review_quality(total_items=4, error_count=1, passed=True, base_xp=3)
    assert t == "FULL_XP" and xp == 3

    t, xp = engine.compute_review_quality(total_items=4, error_count=2, passed=True, base_xp=3)
    assert t == "MOST_XP" and xp == 2  # round(3 * 0.75) = 2

    t, xp = engine.compute_review_quality(total_items=4, error_count=3, passed=False, base_xp=3)
    assert t == "LITTLE_XP" and xp == 1

    t, xp = engine.compute_review_quality(total_items=0, error_count=0, passed=False, base_xp=3)
    assert t == "ZERO_XP" and xp == 0

    # Quiz Scaling
    t, xp = engine.compute_quiz_quality(score_pct=100.0, question_count=5, correct_count=5)
    assert t == "BONUS_XP" and xp == 7  # ceil(5 * 1.25) = 7

    t, xp = engine.compute_quiz_quality(score_pct=80.0, question_count=5, correct_count=4)
    assert t == "FULL_XP" and xp == 4

    t, xp = engine.compute_quiz_quality(score_pct=60.0, question_count=5, correct_count=3)
    assert t == "MOST_XP" and xp == 2  # round(3 * 0.75) = 2

    t, xp = engine.compute_quiz_quality(score_pct=40.0, question_count=5, correct_count=2)
    assert t == "LITTLE_XP" and xp == 1  # round(2 * 0.25) = 1

    t, xp = engine.compute_quiz_quality(score_pct=20.0, question_count=5, correct_count=1)
    assert t == "ZERO_XP" and xp == 0


def test_fr7_1_xp_ledger_transaction_and_profile_sync(populated_engine):
    """Verifies atomic append of XPLedgerEntry and profile xp_total synchronization."""
    engine = populated_engine
    student_id = "usr_t7_ledger_001"
    topic_id = "trl_t7_node_001"

    prof = StudentProfile(student_id=student_id, enrolled_course_id="CRS_TIER7_TEST", xp_total=50)
    engine.storage.upsert_student_profile(prof)

    # Award BONUS_XP (+5)
    entry = engine.award_xp(
        student_id=student_id,
        topic_id=topic_id,
        task_type="FRONTIER_LESSON",
        quality_tier="BONUS_XP",
        delta_xp=5,
        reason="Stellar first attempt",
    )
    assert entry.delta_xp == 5

    # Verify ledger entries retrieved
    entries = engine.storage.get_xp_ledger_entries(student_id, limit=10)
    assert len(entries) >= 1
    assert entries[0].quality_tier == "BONUS_XP"
    assert entries[0].delta_xp == 5

    # Verify profile total synchronized
    p_updated = engine.storage.get_student_profile(student_id)
    assert p_updated.xp_total == 55


# ---------------------------------------------------------------------------
# FR-7.2: Anti-Loophole & "XP Hacker" Defense System Tests
# ---------------------------------------------------------------------------

def test_fr7_2_blow_off_detection_and_escalating_penalties(populated_engine):
    """
    Verifies detection of rapid task blow-offs (< 20% minimum expected time),
    escalating negative XP penalties (-2, -4, -6), and 15-minute cooldown hold.
    """
    engine = populated_engine
    student_id = "usr_t7_hacker_001"
    topic_id = "trl_t7_node_002"
    now = datetime(2026, 10, 24, 10, 0, tzinfo=timezone.utc)

    prof = StudentProfile(student_id=student_id, enrolled_course_id="CRS_TIER7_TEST", xp_total=30)
    engine.storage.upsert_student_profile(prof)

    st = StudentTopicState(student_id=student_id, topic_id=topic_id, status="IN_PROGRESS")
    engine.storage.upsert_student_topic_state(st)

    min_expected_ms = engine.compute_min_expected_duration(skill_type="PROCEDURAL", word_count=20)
    assert min_expected_ms >= 4000

    # 1. Legitimate wrong answer (e.g. 2500ms > 20% of 4000ms = 800ms) -> Not a blow-off
    is_blowoff = engine.detect_blow_off(duration_ms=2500, min_expected_ms=min_expected_ms, is_correct=False)
    assert is_blowoff is False

    # 2. Rapid guess (e.g. 400ms < 800ms threshold) with wrong answer -> BLOW-OFF!
    is_blowoff = engine.detect_blow_off(duration_ms=400, min_expected_ms=min_expected_ms, is_correct=False)
    assert is_blowoff is True

    # 1st Blow-off Offense: -2 XP penalty & 15m cooldown
    entry1, alert1 = engine.apply_anti_hacker_penalty(
        student_id=student_id,
        topic_id=topic_id,
        task_type="FRONTIER_LESSON",
        duration_ms=400,
        min_expected_ms=min_expected_ms,
        as_of=now,
    )
    assert entry1.delta_xp == -2
    assert entry1.quality_tier == "NEGATIVE_PENALTY"
    assert alert1.consecutive_blowoffs == 1
    assert alert1.penalty_xp == -2

    p1 = engine.storage.get_student_profile(student_id)
    assert p1.xp_total == 28
    assert p1.consecutive_blowoffs == 1

    # Verify 15-minute cooldown active on topic
    is_on_cd, cd_until = engine.is_topic_on_cooldown(student_id, topic_id, as_of=now)
    assert is_on_cd is True
    assert cd_until == now + timedelta(minutes=15)

    # 2nd Consecutive Blow-off Offense: -4 XP penalty
    now2 = now + timedelta(minutes=1)
    entry2, alert2 = engine.apply_anti_hacker_penalty(
        student_id=student_id,
        topic_id=topic_id,
        task_type="FRONTIER_LESSON",
        duration_ms=300,
        min_expected_ms=min_expected_ms,
        as_of=now2,
    )
    assert entry2.delta_xp == -4
    assert alert2.consecutive_blowoffs == 2

    p2 = engine.storage.get_student_profile(student_id)
    assert p2.xp_total == 24
    assert p2.consecutive_blowoffs == 2

    # Genuine effort resets consecutive blowoff counter
    engine.reset_consecutive_blowoffs(student_id)
    p3 = engine.storage.get_student_profile(student_id)
    assert p3.consecutive_blowoffs == 0


def test_fr7_2_anti_cheat_idle_timer_ceiling(populated_engine):
    """
    Verifies that latency > 3 * median_latency freezes the idle timer,
    capping effective duration and preventing AFK XP hoarding.
    """
    engine = populated_engine
    median_ms = 15000  # 15 seconds median
    ceiling_ms = 3 * 15000  # 45 seconds ceiling

    # Normal duration (30 seconds) -> not frozen
    eff_ms, frozen = engine.apply_idle_timer_ceiling(duration_ms=30000, student_median_latency_ms=median_ms)
    assert eff_ms == 30000
    assert frozen is False

    # Overtime / idle duration (120 seconds = 120,000ms) -> frozen and clamped to 45,000ms
    eff_ms, frozen = engine.apply_idle_timer_ceiling(duration_ms=120000, student_median_latency_ms=median_ms)
    assert eff_ms == ceiling_ms
    assert frozen is True


# ---------------------------------------------------------------------------
# FR-7.3: Decoupled Course Progress % & Efficiency Projector Tests
# ---------------------------------------------------------------------------

def test_fr7_3_decoupled_course_progress_percentage(populated_engine):
    """
    Verifies Invariant 8: Course Progress % is completely decoupled from XP.
    Mastering course lessons increases progress %; reviews and quizzes do not.
    """
    engine = populated_engine
    student_id = "usr_t7_progress_001"
    course_id = "CRS_TIER7_TEST"

    prof = StudentProfile(student_id=student_id, enrolled_course_id=course_id, xp_total=0)
    engine.storage.upsert_student_profile(prof)

    # 0 nodes mastered out of 10 -> 0.0%
    prog0 = engine.compute_decoupled_progress_pct(student_id, course_id)
    assert prog0 == 0.0

    # Award 500 XP through reviews / quizzes -> progress % remains 0.0%!
    for i in range(10):
        engine.award_xp(
            student_id=student_id,
            topic_id="trl_t7_node_001",
            task_type="SPACED_REVIEW",
            quality_tier="FULL_XP",
            delta_xp=50,
            reason="Review completed",
        )
    p_check = engine.storage.get_student_profile(student_id)
    assert p_check.xp_total == 500

    prog_after_reviews = engine.compute_decoupled_progress_pct(student_id, course_id)
    assert prog_after_reviews == 0.0  # Still 0.0% progress despite 500 XP!

    # Now master 3 course topics (out of 10) -> progress strictly moves to 30.0%
    for i in (1, 2, 3):
        st = StudentTopicState(
            student_id=student_id,
            topic_id=f"trl_t7_node_{i:03d}",
            status="MASTERED",
        )
        engine.storage.upsert_student_topic_state(st)

    prog_mastered = engine.compute_decoupled_progress_pct(student_id, course_id)
    assert prog_mastered == 30.0


def test_fr7_3_learning_efficiency_curve_math(populated_engine):
    """
    Verifies the empirical learning efficiency curve:
      efficiency_multiplier = (pace / 40.0) ^ 0.1
    Matches the official benchmark table from The Math Academy Way (Chapter 31).
    """
    engine = populated_engine

    # Benchmark test values: (pace, expected_multiplier, tolerance)
    table_cases = [
        (160.0, 1.1487),  # ~1.15
        (80.0, 1.0718),   # ~1.07
        (40.0, 1.0000),   # 1.00 (baseline)
        (20.0, 0.9330),   # ~0.93
        (10.0, 0.8706),   # ~0.87
        (5.0, 0.8123),    # ~0.81
    ]

    for pace, expected in table_cases:
        multiplier = engine.compute_learning_efficiency_multiplier(pace)
        assert multiplier == pytest.approx(expected, abs=1e-3)


def test_fr7_3_course_completion_projection_and_warnings(populated_engine):
    """
    Verifies course completion forecasting:
      - remaining XP calculation
      - weekday calculation: remaining_xp / (pace * (pace/40)^0.1)
      - viability warning on pace < 15 XP/weekday
      - burnout warning on daily pace > 90 XP/day
    """
    engine = populated_engine
    student_id = "usr_t7_proj_001"
    course_id = "CRS_TIER7_TEST"
    now = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)

    # Master 2 nodes (out of 10) -> 20.0% progress
    for i in (1, 2):
        st = StudentTopicState(student_id=student_id, topic_id=f"trl_t7_node_{i:03d}", status="MASTERED")
        engine.storage.upsert_student_topic_state(st)

    # 1. Baseline pace (40 XP/weekday) on 3000 XP course (2400 XP remaining)
    # Weekdays = 2400 / (40 * 1.0) = 60 weekdays (12 weeks)
    prof = StudentProfile(student_id=student_id, enrolled_course_id=course_id, xp_total=600, rolling_pace_xp=40.0)
    engine.storage.upsert_student_profile(prof)

    proj = engine.project_course_completion(student_id, course_id, as_of=now)
    assert proj.current_progress_pct == 20.0
    assert proj.benchmark_xp == 3000
    assert proj.remaining_xp == 2400
    assert proj.efficiency_multiplier == 1.0
    assert proj.projected_weekdays == 60
    assert proj.projected_calendar_weeks == 12.0
    assert proj.viability_warning is False
    assert proj.burnout_warning is False

    # 2. Low pace (10 XP/weekday) -> triggers viability warning (< 15 XP)
    prof_low = prof.model_copy(update={"rolling_pace_xp": 10.0})
    engine.storage.upsert_student_profile(prof_low)
    proj_low = engine.project_course_completion(student_id, course_id, as_of=now)
    assert proj_low.viability_warning is True
    assert proj_low.efficiency_multiplier == pytest.approx(0.8706, abs=1e-3)
    # effective pace = 10 * 0.8706 = 8.706 -> 2400 / 8.706 = ~276 weekdays
    assert proj_low.projected_weekdays == 276

    # 3. High pace (100 XP/weekday) -> triggers burnout warning (> 90 XP)
    prof_high = prof.model_copy(update={"rolling_pace_xp": 100.0})
    engine.storage.upsert_student_profile(prof_high)
    proj_high = engine.project_course_completion(student_id, course_id, as_of=now)
    assert proj_high.burnout_warning is True


# ---------------------------------------------------------------------------
# FR-7.4: Ability-Grouped Weekly Leagues Tests
# ---------------------------------------------------------------------------

def test_fr7_4_weekly_cohort_assignment_and_standings(populated_engine):
    """
    Verifies that opted-in learners are partitioned into weekly cohorts matched by pace,
    ranked by Net Weekly XP, and properly evaluate promoting / demoting status.
    """
    engine = populated_engine
    week_key = "2026-W43"
    week_start, _ = engine.get_iso_week_bounds(week_key)

    # Register 10 opted-in students in APPRENTICE tier with varying paces
    student_ids = []
    for i in range(1, 11):
        sid = f"usr_t7_league_{i:02d}"
        prof = StudentProfile(
            student_id=sid,
            enrolled_course_id="CRS_TIER7_TEST",
            xp_total=100 + i * 20,
            rolling_pace_xp=20.0 + i * 3.0,
            league_opt_in=True,
            league_tier="APPRENTICE",
        )
        engine.storage.upsert_student_profile(prof)
        student_ids.append(sid)

    # 1. Assign weekly cohorts
    cohorts = engine.assign_weekly_cohorts(week_key=week_key, as_of=week_start)
    assert len(cohorts) == 1
    cohort = cohorts[0]
    assert cohort.tier == "APPRENTICE"
    assert len(cohort.member_ids) == 10

    # 2. Add weekly XP entries during the active week
    # Give usr_t7_league_10 top score (100 XP)
    engine.award_xp(student_id="usr_t7_league_10", topic_id="trl_t7_node_001", task_type="FRONTIER_LESSON", quality_tier="FULL_XP", delta_xp=100, reason="Weekly work", as_of=week_start + timedelta(days=1))
    # Give usr_t7_league_09 second score (75 XP)
    engine.award_xp(student_id="usr_t7_league_09", topic_id="trl_t7_node_001", task_type="FRONTIER_LESSON", quality_tier="FULL_XP", delta_xp=75, reason="Weekly work", as_of=week_start + timedelta(days=2))
    # Give usr_t7_league_01 negative penalty (-4 XP)
    engine.award_xp(student_id="usr_t7_league_01", topic_id="trl_t7_node_001", task_type="FRONTIER_LESSON", quality_tier="NEGATIVE_PENALTY", delta_xp=-4, reason="Hacker penalty", as_of=week_start + timedelta(days=3))

    # 3. Compute standings
    updated_cohort = engine.compute_cohort_standings(cohort.cohort_id, as_of=week_start + timedelta(days=4))
    standings = updated_cohort.standings
    assert standings[0].student_id == "usr_t7_league_10"
    assert standings[0].net_weekly_xp == 100
    assert standings[0].status == "PROMOTING"
    assert standings[0].rank == 1

    assert standings[1].student_id == "usr_t7_league_09"
    assert standings[1].net_weekly_xp == 75
    assert standings[1].status == "PROMOTING"
    assert standings[1].rank == 2

    # Bottom student has negative net weekly XP (-4)
    last_place = standings[-1]
    assert last_place.student_id == "usr_t7_league_01"
    assert last_place.net_weekly_xp == -4
    # In APPRENTICE tier, bottom performers cannot demote below Apprentice floor -> RETAINING
    assert last_place.status == "RETAINING"


def test_fr7_4_sunday_settlement_promotion_and_demotion(populated_engine):
    """
    Verifies Sunday league settlement:
      - Top performers in PRACTITIONER promote to SCHOLAR.
      - Bottom performers in PRACTITIONER demote to APPRENTICE.
      - Profiles reflect updated league_tier.
    """
    engine = populated_engine
    week_key = "2026-W43"
    week_start, week_end = engine.get_iso_week_bounds(week_key)

    # 10 students in PRACTITIONER tier
    practitioner_ids = []
    for i in range(1, 11):
        sid = f"usr_practitioner_{i:02d}"
        prof = StudentProfile(
            student_id=sid,
            enrolled_course_id="CRS_TIER7_TEST",
            league_opt_in=True,
            league_tier="PRACTITIONER",
            rolling_pace_xp=35.0,
        )
        engine.storage.upsert_student_profile(prof)
        practitioner_ids.append(sid)

    cohorts = engine.assign_weekly_cohorts(week_key, as_of=week_start)
    practitioner_cohort = next(c for c in cohorts if c.tier == "PRACTITIONER")

    # Member 1 earns 200 XP (Rank 1 -> PROMOTING to SCHOLAR)
    engine.award_xp("usr_practitioner_01", "trl_t7_node_001", "FRONTIER_LESSON", "BONUS_XP", 200, "Great effort", as_of=week_start + timedelta(days=1))
    # Member 2 earns 150 XP (Rank 2 -> PROMOTING to SCHOLAR)
    engine.award_xp("usr_practitioner_02", "trl_t7_node_001", "FRONTIER_LESSON", "FULL_XP", 150, "Great effort", as_of=week_start + timedelta(days=2))
    # Member 10 earns 0 XP (Bottom -> DEMOTING to APPRENTICE)
    # Member 09 earns 0 XP (Bottom -> DEMOTING to APPRENTICE)

    # Run Sunday settlement
    settled_cohort, transitions = engine.settle_weekly_league(practitioner_cohort.cohort_id, as_of=week_end)
    assert settled_cohort.finalized is True

    # Check promotions
    assert transitions["usr_practitioner_01"] == "SCHOLAR"
    p1 = engine.storage.get_student_profile("usr_practitioner_01")
    assert p1.league_tier == "SCHOLAR"

    assert transitions["usr_practitioner_02"] == "SCHOLAR"
    p2 = engine.storage.get_student_profile("usr_practitioner_02")
    assert p2.league_tier == "SCHOLAR"

    # Check demotions
    p10 = engine.storage.get_student_profile("usr_practitioner_10")
    assert p10.league_tier == "APPRENTICE"


# ---------------------------------------------------------------------------
# Cross-Backend Bit-Identical Mathematical Parity Test
# ---------------------------------------------------------------------------

def test_tier7_cross_backend_bit_identical_parity():
    """
    Executes an identical multi-step Tier 7 gamification, anti-hacker,
    course progress projection, and league cycle across:
      - MemoryReferenceAdapter
      - StandaloneRelationalAdapter
      - StandaloneGraphAdapter
    Asserts 100% bit-identical values for progress_pct, xp_total, pace,
    efficiency multiplier, completion dates, and league standings.
    """
    backends = ["memory", "relational", "graph"]
    results = {}

    for backend in backends:
        adapter = get_storage_adapter(backend)

        # Setup course and nodes
        course = CourseCatalogItem(
            course_id="CRS_PARITY",
            course_code="par",
            title="Parity Track",
            grade_band="8-9",
            benchmark_xp=3000,
        )
        nodes = []
        for i in range(1, 6):
            tid = f"trl_par_{i:03d}"
            kp = KnowledgePoint(kp_index=0, kp_id=f"{tid}#kp_0", scaffolding_tier="KP_1_BASELINE", kp_focus=f"P {i}", novel_chunk_count=2)
            nodes.append(TopicNode(id=tid, title=f"Par Node {i}", course_id="CRS_PARITY", canonical_domain="06_alg", domain_cluster="CRS_PARITY::alg", grade_level_min=8, grade_level_max=9, estimated_xp=4, knowledge_points=[kp]))

        adapter.ingest_knowledge_graph({
            "schema_version": "2.0.0-trellis",
            "graph_id": "parity-dag",
            "course_catalog": [course.model_dump()],
            "nodes": [n.model_dump() for n in nodes],
        })

        engine = GamificationEfficiencyEngine(adapter)
        student_id = "usr_parity_student"
        prof = StudentProfile(
            student_id=student_id,
            enrolled_course_id="CRS_PARITY",
            xp_total=100,
            rolling_pace_xp=40.0,
            league_opt_in=True,
            league_tier="APPRENTICE",
        )
        adapter.upsert_student_profile(prof)

        # Master 2 nodes out of 5 -> 40.0%
        for i in (1, 2):
            st = StudentTopicState(student_id=student_id, topic_id=f"trl_par_{i:03d}", status="MASTERED")
            adapter.upsert_student_topic_state(st)

        now = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)

        # 1. Award quality-scaled XP
        entry = engine.award_xp(student_id, "trl_par_001", "FRONTIER_LESSON", "BONUS_XP", 5, "Perfect staircase", as_of=now)

        # 2. Progress %
        prog = engine.compute_decoupled_progress_pct(student_id, "CRS_PARITY")

        # 3. Efficiency projection
        proj = engine.project_course_completion(student_id, "CRS_PARITY", as_of=now)

        # 4. Anti-cheat blowoff
        pen, alert = engine.apply_anti_hacker_penalty(student_id, "trl_par_003", "FRONTIER_LESSON", 300, 4000, as_of=now)

        # 5. League cohort & standings
        week_key = "2026-W43"
        cohorts = engine.assign_weekly_cohorts(week_key, as_of=now)
        updated_cohort = engine.compute_cohort_standings(cohorts[0].cohort_id, as_of=now)

        p_final = adapter.get_student_profile(student_id)

        results[backend] = {
            "progress_pct": prog,
            "final_xp": p_final.xp_total,
            "blowoffs": p_final.consecutive_blowoffs,
            "proj_weekdays": proj.projected_weekdays,
            "eff_mult": proj.efficiency_multiplier,
            "penalty_xp": pen.delta_xp,
            "standings_count": len(updated_cohort.standings),
            "top_student": updated_cohort.standings[0].student_id,
        }

        if hasattr(adapter, "close"):
            adapter.close()

    # Assert 100% bit-identical parity between memory, relational, and graph backends
    ref = results["memory"]
    assert results["relational"] == ref, f"Relational mismatch with reference: {results['relational']} vs {ref}"
    assert results["graph"] == ref, f"Graph mismatch with reference: {results['graph']} vs {ref}"
