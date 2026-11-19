"""Initial Trellis relational schema (17 normalized tables & indexes).

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-19
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Courses
    op.create_table(
        "courses",
        sa.Column("course_id", sa.Text(), primary_key=True),
        sa.Column("course_code", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("grade_band", sa.Text(), nullable=False),
        sa.Column("benchmark_xp", sa.Integer(), nullable=False),
        sa.Column("mastery_floor_course_ids_json", sa.Text(), nullable=False),
    )

    # 2. Topic Nodes
    op.create_table(
        "topic_nodes",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("course_id", sa.Text(), sa.ForeignKey("courses.course_id"), nullable=False),
        sa.Column("canonical_domain", sa.Text(), nullable=False),
        sa.Column("domain_cluster", sa.Text(), nullable=False),
        sa.Column("skill_type", sa.Text(), nullable=False),
        sa.Column("bloom_cognitive_tier", sa.Text(), nullable=False),
        sa.Column("grade_level_min", sa.Integer(), nullable=False),
        sa.Column("grade_level_max", sa.Integer(), nullable=False),
        sa.Column("is_core", sa.Integer(), nullable=False),
        sa.Column("in_foundations_sequence", sa.Integer(), nullable=False),
        sa.Column("requires_automaticity", sa.Integer(), nullable=False),
        sa.Column("automaticity_Defining_latency_ms", sa.Integer(), nullable=True),
        sa.Column("base_difficulty", sa.Float(), nullable=False),
        sa.Column("estimated_xp", sa.Integer(), nullable=False),
        sa.Column("mastery_gate_n", sa.Integer(), nullable=False),
        sa.Column("key_prerequisite_id", sa.Text(), nullable=True),
        sa.Column("aligned_standards_json", sa.Text(), nullable=False),
        sa.Column("mastery_criteria_json", sa.Text(), nullable=False),
    )
    op.create_index("idx_topics_course", "topic_nodes", ["course_id"])
    op.create_index("idx_topics_domain", "topic_nodes", ["domain_cluster"])

    # 3. Knowledge Points
    op.create_table(
        "kp_nodes",
        sa.Column("kp_id", sa.Text(), primary_key=True),
        sa.Column("topic_id", sa.Text(), sa.ForeignKey("topic_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kp_index", sa.Integer(), nullable=False),
        sa.Column("scaffolding_tier", sa.Text(), nullable=False),
        sa.Column("kp_focus", sa.Text(), nullable=False),
        sa.Column("novel_chunk_count", sa.Integer(), nullable=False),
        sa.Column("key_prerequisite_id", sa.Text(), nullable=True),
        sa.Column("rule_statement", sa.Text(), nullable=False),
        sa.Column("canonical_worked_example_json", sa.Text(), nullable=True),
        sa.Column("problem_blueprint_json", sa.Text(), nullable=True),
        sa.Column("diagnostic_misconceptions_json", sa.Text(), nullable=True),
    )
    op.create_index("idx_kp_topic", "kp_nodes", ["topic_id"])

    # 4. Content Shells
    op.create_table(
        "kp_content_shells",
        sa.Column("kp_id", sa.Text(), primary_key=True),
        sa.Column("content_status", sa.Text(), nullable=False),
        sa.Column("rule_statement", sa.Text(), nullable=False),
        sa.Column("worked_example_json", sa.Text(), nullable=True),
        sa.Column("problem_blueprint_json", sa.Text(), nullable=True),
        sa.Column("diagnostic_misconceptions_json", sa.Text(), nullable=True),
        sa.Column("svg_asset_uri", sa.Text(), nullable=True),
    )

    # 5. Prerequisite Edges (u -> v)
    op.create_table(
        "prerequisite_edges",
        sa.Column("source_topic_id", sa.Text(), sa.ForeignKey("topic_nodes.id"), nullable=False),
        sa.Column("target_topic_id", sa.Text(), sa.ForeignKey("topic_nodes.id"), nullable=False),
        sa.Column("strength", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("source_topic_id", "target_topic_id"),
    )
    op.create_index("idx_prereqs_target", "prerequisite_edges", ["target_topic_id", "strength"])
    op.create_index("idx_prereqs_source", "prerequisite_edges", ["source_topic_id"])

    # 6. Encompassing Edges (v -> u)
    op.create_table(
        "encompassing_edges",
        sa.Column("advanced_topic_id", sa.Text(), sa.ForeignKey("topic_nodes.id"), nullable=False),
        sa.Column("simpler_topic_id", sa.Text(), sa.ForeignKey("topic_nodes.id"), nullable=False),
        sa.Column("fire_weight", sa.Float(), nullable=False),
        sa.Column("is_non_ancestor", sa.Integer(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("advanced_topic_id", "simpler_topic_id"),
    )
    op.create_index("idx_enc_adv", "encompassing_edges", ["advanced_topic_id"])
    op.create_index("idx_enc_simp", "encompassing_edges", ["simpler_topic_id"])

    # 7. Materialized Topic Closure Paths
    op.create_table(
        "topic_closure_paths",
        sa.Column("origin_id", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("edge_kind", sa.Text(), nullable=False),
        sa.Column("hops", sa.Integer(), nullable=False),
        sa.Column("compound_weight", sa.Float(), nullable=False),
        sa.Column("is_non_ancestor", sa.Integer(), nullable=False),
        sa.Column("path_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("origin_id", "target_id", "edge_kind", "hops", "path_json"),
    )
    op.create_index("idx_closure_origin", "topic_closure_paths", ["origin_id", "edge_kind", "hops"])
    op.create_index("idx_closure_target", "topic_closure_paths", ["target_id", "edge_kind", "hops"])

    # 8. Student Profiles
    op.create_table(
        "student_profiles",
        sa.Column("student_id", sa.Text(), primary_key=True),
        sa.Column("enrolled_course_id", sa.Text(), nullable=False),
        sa.Column("xp_total", sa.Integer(), nullable=False),
        sa.Column("progress_pct", sa.Float(), nullable=False),
        sa.Column("daily_xp_target", sa.Integer(), nullable=False),
        sa.Column("rolling_pace_xp", sa.Float(), nullable=False),
        sa.Column("independence_stage", sa.Text(), nullable=False),
        sa.Column("process_praise_counter", sa.Integer(), nullable=False),
        sa.Column("rolling_latency_ms_json", sa.Text(), nullable=False),
        sa.Column("league_opt_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("league_tier", sa.Text(), nullable=False, server_default="APPRENTICE"),
        sa.Column("consecutive_blowoffs", sa.Integer(), nullable=False, server_default="0"),
    )

    # 9. Student Topic States
    op.create_table(
        "student_topic_states",
        sa.Column("student_id", sa.Text(), nullable=False),
        sa.Column("topic_id", sa.Text(), sa.ForeignKey("topic_nodes.id"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("rep_num", sa.Float(), nullable=False),
        sa.Column("stability_interval_days", sa.Float(), nullable=False),
        sa.Column("memory_strength", sa.Float(), nullable=False),
        sa.Column("last_reviewed_at", sa.Text(), nullable=True),
        sa.Column("due_review_at", sa.Text(), nullable=True),
        sa.Column("cumulative_fire_credit", sa.Float(), nullable=False),
        sa.Column("student_ability", sa.Float(), nullable=False),
        sa.Column("topic_difficulty", sa.Float(), nullable=False),
        sa.Column("speed", sa.Float(), nullable=False),
        sa.Column("allow_implicit_fire_credit", sa.Integer(), nullable=False),
        sa.Column("diagnostic_plus_minus_balance", sa.Float(), nullable=False),
        sa.Column("is_conditionally_completed", sa.Integer(), nullable=False),
        sa.Column("current_kp_index", sa.Integer(), nullable=False),
        sa.Column("consecutive_correct", sa.Integer(), nullable=False),
        sa.Column("kp_failure_counts_json", sa.Text(), nullable=False),
        sa.Column("cooldown_until", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("student_id", "topic_id"),
    )
    op.create_index("idx_student_topic_status", "student_topic_states", ["student_id", "status"])

    # 10. Dashboard Task Menus
    op.create_table(
        "dashboard_task_menus",
        sa.Column("student_id", sa.Text(), nullable=False),
        sa.Column("topic_id", sa.Text(), nullable=False),
        sa.Column("task_type", sa.Text(), nullable=False),
        sa.Column("rank_score", sa.Float(), nullable=False),
        sa.Column("is_sticky", sa.Integer(), nullable=False),
        sa.Column("assigned_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("student_id", "topic_id"),
    )

    # 11. Interaction Attempts Telemetry
    op.create_table(
        "interaction_attempts",
        sa.Column("attempt_id", sa.Text(), primary_key=True),
        sa.Column("student_id", sa.Text(), nullable=False),
        sa.Column("topic_id", sa.Text(), nullable=False),
        sa.Column("kp_id", sa.Text(), nullable=False),
        sa.Column("submitted_answer", sa.Text(), nullable=False),
        sa.Column("is_correct", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("reference_peeked", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_attempts_student", "interaction_attempts", ["student_id", "topic_id"])

    # 12. XP Ledger
    op.create_table(
        "xp_ledger_entries",
        sa.Column("entry_id", sa.Text(), primary_key=True),
        sa.Column("student_id", sa.Text(), nullable=False),
        sa.Column("topic_id", sa.Text(), nullable=False),
        sa.Column("task_type", sa.Text(), nullable=False),
        sa.Column("quality_tier", sa.Text(), nullable=False),
        sa.Column("delta_xp", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_ledger_student", "xp_ledger_entries", ["student_id", "created_at"])

    # 13. Weekly League Cohorts (Tier 7)
    op.create_table(
        "league_cohorts",
        sa.Column("cohort_id", sa.Text(), primary_key=True),
        sa.Column("week_key", sa.Text(), nullable=False),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
    )
    op.create_index("idx_league_cohorts_week", "league_cohorts", ["week_key"])

    # 14. Custom Parent Rewards (Tier 8)
    op.create_table(
        "parent_rewards",
        sa.Column("reward_id", sa.Text(), primary_key=True),
        sa.Column("student_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("xp_target", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("unlocked_at", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.Text(), nullable=True),
    )
    op.create_index("idx_parent_rewards_student", "parent_rewards", ["student_id"])

    # 15. Study Habit Schedules (Tier 8)
    op.create_table(
        "study_habit_schedules",
        sa.Column("student_id", sa.Text(), primary_key=True),
        sa.Column("target_daily_xp", sa.Integer(), nullable=False),
        sa.Column("scheduled_start_time", sa.Text(), nullable=False),
        sa.Column("scheduled_end_time", sa.Text(), nullable=False),
        sa.Column("active_days_json", sa.Text(), nullable=False),
        sa.Column("current_streak_days", sa.Integer(), nullable=False),
        sa.Column("longest_streak_days", sa.Integer(), nullable=False),
        sa.Column("last_active_date", sa.Text(), nullable=True),
    )

    # 16. Adult Re-Sit Alerts (Tier 8)
    op.create_table(
        "adult_resit_alerts",
        sa.Column("alert_id", sa.Text(), primary_key=True),
        sa.Column("student_id", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("acknowledged", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("acknowledged_at", sa.Text(), nullable=True),
    )
    op.create_index("idx_adult_resit_student", "adult_resit_alerts", ["student_id", "acknowledged"])

    # 17. Content Remediation Flags (Tier 8)
    op.create_table(
        "content_remediation_flags",
        sa.Column("flag_id", sa.Text(), primary_key=True),
        sa.Column("student_id", sa.Text(), nullable=False),
        sa.Column("topic_id", sa.Text(), nullable=False),
        sa.Column("kp_id", sa.Text(), nullable=True),
        sa.Column("problem_stem", sa.Text(), nullable=True),
        sa.Column("flag_type", sa.Text(), nullable=False),
        sa.Column("student_comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
    )
    op.create_index("idx_content_flags_topic", "content_remediation_flags", ["topic_id", "status"])


def downgrade() -> None:
    op.drop_table("content_remediation_flags")
    op.drop_table("adult_resit_alerts")
    op.drop_table("study_habit_schedules")
    op.drop_table("parent_rewards")
    op.drop_table("league_cohorts")
    op.drop_table("xp_ledger_entries")
    op.drop_table("interaction_attempts")
    op.drop_table("dashboard_task_menus")
    op.drop_table("student_topic_states")
    op.drop_table("student_profiles")
    op.drop_table("topic_closure_paths")
    op.drop_table("encompassing_edges")
    op.drop_table("prerequisite_edges")
    op.drop_table("kp_content_shells")
    op.drop_table("kp_nodes")
    op.drop_table("topic_nodes")
    op.drop_table("courses")
