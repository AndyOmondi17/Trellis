"""
src/trellis/adapters/db/standalone_relational_adapter.py
Adapter A: 100% Standalone Relational Persistence Backend (SQLite / PostgreSQL).
Stores 100% of curriculum DAG, content shells, student states, and telemetry
using normalized tables, B-tree indexes, recursive CTEs, and materialized closure paths.
Zero cross-database coordination.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

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


class StandaloneRelationalAdapter:
    """
    Adapter A: Standalone Relational implementation conforming to TrellisStoragePort.
    Defaults to SQLite (:memory: or path) with full relational parity.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        # Connect to SQLite with WAL mode & foreign keys enabled
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        """Initializes all 12 normalized relational tables and performance indexes."""
        with self.conn:
            self.conn.executescript("""
                PRAGMA foreign_keys = ON;

                -- 1. Courses
                CREATE TABLE IF NOT EXISTS courses (
                    course_id TEXT PRIMARY KEY,
                    course_code TEXT NOT NULL,
                    title TEXT NOT NULL,
                    grade_band TEXT NOT NULL,
                    benchmark_xp INTEGER NOT NULL,
                    mastery_floor_course_ids_json TEXT NOT NULL
                );

                -- 2. Topic Nodes
                CREATE TABLE IF NOT EXISTS topic_nodes (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    course_id TEXT NOT NULL,
                    canonical_domain TEXT NOT NULL,
                    domain_cluster TEXT NOT NULL,
                    skill_type TEXT NOT NULL,
                    bloom_cognitive_tier TEXT NOT NULL,
                    grade_level_min INTEGER NOT NULL,
                    grade_level_max INTEGER NOT NULL,
                    is_core INTEGER NOT NULL,
                    in_foundations_sequence INTEGER NOT NULL,
                    requires_automaticity INTEGER NOT NULL,
                    automaticity_Defining_latency_ms INTEGER,
                    base_difficulty REAL NOT NULL,
                    estimated_xp INTEGER NOT NULL,
                    mastery_gate_n INTEGER NOT NULL,
                    key_prerequisite_id TEXT,
                    aligned_standards_json TEXT NOT NULL,
                    mastery_criteria_json TEXT NOT NULL,
                    FOREIGN KEY(course_id) REFERENCES courses(course_id)
                );
                CREATE INDEX IF NOT EXISTS idx_topics_course ON topic_nodes(course_id);
                CREATE INDEX IF NOT EXISTS idx_topics_domain ON topic_nodes(domain_cluster);

                -- 3. Knowledge Points
                CREATE TABLE IF NOT EXISTS kp_nodes (
                    kp_id TEXT PRIMARY KEY,
                    topic_id TEXT NOT NULL,
                    kp_index INTEGER NOT NULL,
                    scaffolding_tier TEXT NOT NULL,
                    kp_focus TEXT NOT NULL,
                    novel_chunk_count INTEGER NOT NULL,
                    key_prerequisite_id TEXT,
                    rule_statement TEXT NOT NULL,
                    canonical_worked_example_json TEXT,
                    problem_blueprint_json TEXT,
                    diagnostic_misconceptions_json TEXT,
                    FOREIGN KEY(topic_id) REFERENCES topic_nodes(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_kp_topic ON kp_nodes(topic_id);

                -- 4. Content Shells
                CREATE TABLE IF NOT EXISTS kp_content_shells (
                    kp_id TEXT PRIMARY KEY,
                    content_status TEXT NOT NULL,
                    rule_statement TEXT NOT NULL,
                    worked_example_json TEXT,
                    problem_blueprint_json TEXT,
                    diagnostic_misconceptions_json TEXT,
                    svg_asset_uri TEXT
                );

                -- 5. Prerequisite Edges (u -> v)
                CREATE TABLE IF NOT EXISTS prerequisite_edges (
                    source_topic_id TEXT NOT NULL,
                    target_topic_id TEXT NOT NULL,
                    strength TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    PRIMARY KEY(source_topic_id, target_topic_id),
                    FOREIGN KEY(source_topic_id) REFERENCES topic_nodes(id),
                    FOREIGN KEY(target_topic_id) REFERENCES topic_nodes(id)
                );
                CREATE INDEX IF NOT EXISTS idx_prereqs_target ON prerequisite_edges(target_topic_id, strength);
                CREATE INDEX IF NOT EXISTS idx_prereqs_source ON prerequisite_edges(source_topic_id);

                -- 6. Encompassing Edges (v -> u)
                CREATE TABLE IF NOT EXISTS encompassing_edges (
                    advanced_topic_id TEXT NOT NULL,
                    simpler_topic_id TEXT NOT NULL,
                    fire_weight REAL NOT NULL,
                    is_non_ancestor INTEGER NOT NULL,
                    rationale TEXT NOT NULL,
                    PRIMARY KEY(advanced_topic_id, simpler_topic_id),
                    FOREIGN KEY(advanced_topic_id) REFERENCES topic_nodes(id),
                    FOREIGN KEY(simpler_topic_id) REFERENCES topic_nodes(id)
                );
                CREATE INDEX IF NOT EXISTS idx_enc_adv ON encompassing_edges(advanced_topic_id);
                CREATE INDEX IF NOT EXISTS idx_enc_simp ON encompassing_edges(simpler_topic_id);

                -- 7. Materialized Topic Closure Paths
                CREATE TABLE IF NOT EXISTS topic_closure_paths (
                    origin_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    edge_kind TEXT NOT NULL,
                    hops INTEGER NOT NULL,
                    compound_weight REAL NOT NULL,
                    is_non_ancestor INTEGER NOT NULL,
                    path_json TEXT NOT NULL,
                    PRIMARY KEY(origin_id, target_id, edge_kind, hops, path_json)
                );
                CREATE INDEX IF NOT EXISTS idx_closure_origin ON topic_closure_paths(origin_id, edge_kind, hops);
                CREATE INDEX IF NOT EXISTS idx_closure_target ON topic_closure_paths(target_id, edge_kind, hops);

                -- 8. Student Profiles
                CREATE TABLE IF NOT EXISTS student_profiles (
                    student_id TEXT PRIMARY KEY,
                    enrolled_course_id TEXT NOT NULL,
                    xp_total INTEGER NOT NULL,
                    progress_pct REAL NOT NULL,
                    daily_xp_target INTEGER NOT NULL,
                    rolling_pace_xp REAL NOT NULL,
                    independence_stage TEXT NOT NULL,
                    process_praise_counter INTEGER NOT NULL,
                    rolling_latency_ms_json TEXT NOT NULL,
                    league_opt_in INTEGER NOT NULL DEFAULT 0,
                    league_tier TEXT NOT NULL DEFAULT 'APPRENTICE',
                    consecutive_blowoffs INTEGER NOT NULL DEFAULT 0
                );

                -- 9. Student Topic States
                CREATE TABLE IF NOT EXISTS student_topic_states (
                    student_id TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    rep_num REAL NOT NULL,
                    stability_interval_days REAL NOT NULL,
                    memory_strength REAL NOT NULL,
                    last_reviewed_at TEXT,
                    due_review_at TEXT,
                    cumulative_fire_credit REAL NOT NULL,
                    student_ability REAL NOT NULL,
                    topic_difficulty REAL NOT NULL,
                    speed REAL NOT NULL,
                    allow_implicit_fire_credit INTEGER NOT NULL,
                    diagnostic_plus_minus_balance REAL NOT NULL,
                    is_conditionally_completed INTEGER NOT NULL,
                    current_kp_index INTEGER NOT NULL,
                    consecutive_correct INTEGER NOT NULL,
                    kp_failure_counts_json TEXT NOT NULL,
                    cooldown_until TEXT,
                    PRIMARY KEY(student_id, topic_id),
                    FOREIGN KEY(topic_id) REFERENCES topic_nodes(id)
                );
                CREATE INDEX IF NOT EXISTS idx_student_topic_status ON student_topic_states(student_id, status);

                -- 10. Dashboard Task Menus
                CREATE TABLE IF NOT EXISTS dashboard_task_menus (
                    student_id TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    rank_score REAL NOT NULL,
                    is_sticky INTEGER NOT NULL,
                    assigned_at TEXT NOT NULL,
                    PRIMARY KEY(student_id, topic_id)
                );

                -- 11. Interaction Attempts Telemetry
                CREATE TABLE IF NOT EXISTS interaction_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    student_id TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    kp_id TEXT NOT NULL,
                    submitted_answer TEXT NOT NULL,
                    is_correct INTEGER NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    reference_peeked INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_attempts_student ON interaction_attempts(student_id, topic_id);

                -- 12. XP Ledger
                CREATE TABLE IF NOT EXISTS xp_ledger_entries (
                    entry_id TEXT PRIMARY KEY,
                    student_id TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    quality_tier TEXT NOT NULL,
                    delta_xp INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ledger_student ON xp_ledger_entries(student_id, created_at);

                -- 13. Tier 7 Weekly League Cohorts
                CREATE TABLE IF NOT EXISTS league_cohorts (
                    cohort_id TEXT PRIMARY KEY,
                    week_key TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_league_cohorts_week ON league_cohorts (week_key);

                -- 14. Tier 8 Custom Parent Rewards
                CREATE TABLE IF NOT EXISTS parent_rewards (
                    reward_id TEXT PRIMARY KEY,
                    student_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    xp_target INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    unlocked_at TEXT,
                    claimed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_parent_rewards_student ON parent_rewards (student_id);

                -- 15. Tier 8 Study Habit Schedules
                CREATE TABLE IF NOT EXISTS study_habit_schedules (
                    student_id TEXT PRIMARY KEY,
                    target_daily_xp INTEGER NOT NULL,
                    scheduled_start_time TEXT NOT NULL,
                    scheduled_end_time TEXT NOT NULL,
                    active_days_json TEXT NOT NULL,
                    current_streak_days INTEGER NOT NULL,
                    longest_streak_days INTEGER NOT NULL,
                    last_active_date TEXT
                );

                -- 16. Tier 8 Adult Re-Sit Alerts
                CREATE TABLE IF NOT EXISTS adult_resit_alerts (
                    alert_id TEXT PRIMARY KEY,
                    student_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    details TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    acknowledged INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    acknowledged_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_adult_resit_student ON adult_resit_alerts (student_id, acknowledged);

                -- 17. Tier 8 Content Remediation Flags
                CREATE TABLE IF NOT EXISTS content_remediation_flags (
                    flag_id TEXT PRIMARY KEY,
                    student_id TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    kp_id TEXT,
                    problem_stem TEXT,
                    flag_type TEXT NOT NULL,
                    student_comment TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_content_flags_topic ON content_remediation_flags (topic_id, status);
            """)

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

        with self.conn:
            # 1. Ingest Courses
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO courses (
                    course_id, course_code, title, grade_band, benchmark_xp, mastery_floor_course_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        c["course_id"],
                        c["course_code"],
                        c["title"],
                        c["grade_band"],
                        c.get("benchmark_xp", 1500),
                        json.dumps(c.get("mastery_floor_course_ids", [])),
                    )
                    for c in courses_data
                ],
            )

            # 2. Ingest Topic Nodes
            topic_rows = []
            kp_rows = []
            shell_rows = []
            prereq_rows = []
            enc_rows = []

            for n in nodes_data:
                topic_rows.append((
                    n["id"],
                    n["title"],
                    n["course_id"],
                    n["canonical_domain"],
                    n["domain_cluster"],
                    n.get("skill_type", "PROCEDURAL"),
                    n.get("bloom_cognitive_tier", "PROCEDURAL_EXECUTION"),
                    n["grade_level_min"],
                    n["grade_level_max"],
                    1 if n.get("is_core", True) else 0,
                    1 if n.get("in_foundations_sequence", False) else 0,
                    1 if n.get("requires_automaticity", False) else 0,
                    n.get("automaticity_Defining_latency_ms"),
                    n.get("base_difficulty", 0.5),
                    n.get("estimated_xp", 3),
                    n.get("mastery_gate_n", 3),
                    n.get("key_prerequisite_id"),
                    json.dumps(n.get("aligned_standards", [])),
                    json.dumps(n.get("mastery_criteria", [])),
                ))

                for kp in n.get("knowledge_points", []):
                    kp_count += 1
                    cwe = kp.get("canonical_worked_example")
                    pb = kp.get("problem_blueprint")
                    dm = kp.get("diagnostic_misconceptions", [])

                    cwe_json = json.dumps(cwe) if cwe else None
                    pb_json = json.dumps(pb) if pb else None
                    dm_json = json.dumps(dm) if dm else None

                    kp_rows.append((
                        kp["kp_id"],
                        n["id"],
                        kp["kp_index"],
                        kp.get("scaffolding_tier", "KP_1_BASELINE"),
                        kp.get("kp_focus", ""),
                        kp.get("novel_chunk_count", 2),
                        kp.get("key_prerequisite_id"),
                        kp.get("rule_statement", ""),
                        cwe_json,
                        pb_json,
                        dm_json,
                    ))

                    shell_rows.append((
                        kp["kp_id"],
                        "BLUEPRINT_READY",
                        kp.get("rule_statement", ""),
                        cwe_json,
                        pb_json,
                        dm_json,
                        None,
                    ))
                    shell_count += 1

                for p in n.get("prerequisites", []):
                    prereq_count += 1
                    prereq_rows.append((
                        p["prerequisite_id"],
                        n["id"],
                        p.get("strength", "hard"),
                        p.get("rationale", ""),
                    ))

                for e in n.get("encompassed_skills", []):
                    enc_count += 1
                    enc_rows.append((
                        n["id"],
                        e["encompassed_id"],
                        e.get("fire_weight", 0.5),
                        1 if e.get("is_non_ancestor", False) else 0,
                        e.get("rationale", ""),
                    ))

            self.conn.executemany(
                """
                INSERT OR REPLACE INTO topic_nodes VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                topic_rows,
            )

            self.conn.executemany(
                """
                INSERT OR REPLACE INTO kp_nodes VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                kp_rows,
            )

            self.conn.executemany(
                """
                INSERT OR REPLACE INTO kp_content_shells VALUES (
                    ?, ?, ?, ?, ?, ?, ?
                )
                """,
                shell_rows,
            )

            self.conn.executemany(
                """
                INSERT OR REPLACE INTO prerequisite_edges VALUES (?, ?, ?, ?)
                """,
                prereq_rows,
            )

            self.conn.executemany(
                """
                INSERT OR REPLACE INTO encompassing_edges VALUES (?, ?, ?, ?, ?)
                """,
                enc_rows,
            )

            # 3. Materialize Multi-Hop Closure Paths for FIRe and Traversals
            closure_paths_count = self._materialize_closure_paths()

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return GraphIngestionReport(
            courses_ingested=course_count,
            nodes_ingested=node_count,
            knowledge_points_ingested=kp_count,
            prereq_edges_ingested=prereq_count,
            encompassing_edges_ingested=enc_count,
            content_shells_ingested=shell_count,
            closure_paths_computed=closure_paths_count,
            duration_ms=round(duration_ms, 2),
        )

    def _materialize_closure_paths(self) -> int:
        """Precomputes up to 3-hop encompassing paths and prerequisite closure paths."""
        closure_rows = []

        # Encompassing closure (1 to 3 hops)
        # Hop 1
        cursor = self.conn.execute(
            """
            SELECT advanced_topic_id, simpler_topic_id, fire_weight, is_non_ancestor
            FROM encompassing_edges
            """
        )
        hop1 = cursor.fetchall()
        for row in hop1:
            adv, simp, fw, non_anc = row[0], row[1], row[2], row[3]
            closure_rows.append((
                adv, simp, "ENCOMPASSES", 1, fw, non_anc, json.dumps([adv, simp])
            ))

        # Hop 2
        cursor = self.conn.execute(
            """
            SELECT e1.advanced_topic_id, e2.simpler_topic_id, (e1.fire_weight * e2.fire_weight),
                   MAX(e1.is_non_ancestor, e2.is_non_ancestor), e1.simpler_topic_id
            FROM encompassing_edges e1
            JOIN encompassing_edges e2 ON e1.simpler_topic_id = e2.advanced_topic_id
            WHERE e1.advanced_topic_id != e2.simpler_topic_id
            """
        )
        for row in cursor.fetchall():
            adv, simp, fw, non_anc, mid = row[0], row[1], row[2], row[3], row[4]
            closure_rows.append((
                adv, simp, "ENCOMPASSES", 2, round(fw, 6), non_anc, json.dumps([adv, mid, simp])
            ))

        # Hop 3
        cursor = self.conn.execute(
            """
            SELECT e1.advanced_topic_id, e3.simpler_topic_id, (e1.fire_weight * e2.fire_weight * e3.fire_weight),
                   MAX(e1.is_non_ancestor, e2.is_non_ancestor, e3.is_non_ancestor), e1.simpler_topic_id, e2.simpler_topic_id
            FROM encompassing_edges e1
            JOIN encompassing_edges e2 ON e1.simpler_topic_id = e2.advanced_topic_id
            JOIN encompassing_edges e3 ON e2.simpler_topic_id = e3.advanced_topic_id
            WHERE e1.advanced_topic_id != e3.simpler_topic_id
            """
        )
        for row in cursor.fetchall():
            adv, simp, fw, non_anc, m1, m2 = row[0], row[1], row[2], row[3], row[4], row[5]
            closure_rows.append((
                adv, simp, "ENCOMPASSES", 3, round(fw, 6), non_anc, json.dumps([adv, m1, m2, simp])
            ))

        self.conn.executemany(
            """
            INSERT OR REPLACE INTO topic_closure_paths VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            closure_rows,
        )
        return len(closure_rows)

    def get_course_catalog(self) -> List[CourseCatalogItem]:
        cursor = self.conn.execute("SELECT * FROM courses ORDER BY course_id")
        items = []
        for row in cursor.fetchall():
            items.append(
                CourseCatalogItem(
                    course_id=row["course_id"],
                    course_code=row["course_code"],
                    title=row["title"],
                    grade_band=row["grade_band"],
                    benchmark_xp=row["benchmark_xp"],
                    mastery_floor_course_ids=json.loads(row["mastery_floor_course_ids_json"]),
                )
            )
        return items

    def _row_to_topic_node(self, row: sqlite3.Row) -> TopicNode:
        tid = row["id"]
        # Fetch knowledge points
        kp_cursor = self.conn.execute("SELECT * FROM kp_nodes WHERE topic_id = ? ORDER BY kp_index", (tid,))
        kps = []
        for kr in kp_cursor.fetchall():
            cwe = json.loads(kr["canonical_worked_example_json"]) if kr["canonical_worked_example_json"] else None
            pb = json.loads(kr["problem_blueprint_json"]) if kr["problem_blueprint_json"] else None
            dm = json.loads(kr["diagnostic_misconceptions_json"]) if kr["diagnostic_misconceptions_json"] else []
            kps.append(
                KnowledgePoint(
                    kp_index=kr["kp_index"],
                    kp_id=kr["kp_id"],
                    scaffolding_tier=kr["scaffolding_tier"],
                    kp_focus=kr["kp_focus"],
                    novel_chunk_count=kr["novel_chunk_count"],
                    key_prerequisite_id=kr["key_prerequisite_id"],
                    rule_statement=kr["rule_statement"],
                    canonical_worked_example=cwe,
                    problem_blueprint=pb,
                    diagnostic_misconceptions=dm,
                )
            )

        # Fetch prereqs
        prereq_cursor = self.conn.execute("SELECT * FROM prerequisite_edges WHERE target_topic_id = ?", (tid,))
        prereqs = [
            PrerequisiteEdge(
                prerequisite_id=pr["source_topic_id"],
                strength=pr["strength"],
                rationale=pr["rationale"],
            )
            for pr in prereq_cursor.fetchall()
        ]

        # Fetch encompassings
        enc_cursor = self.conn.execute("SELECT * FROM encompassing_edges WHERE advanced_topic_id = ?", (tid,))
        encs = [
            EncompassingEdge(
                encompassed_id=er["simpler_topic_id"],
                fire_weight=er["fire_weight"],
                is_non_ancestor=bool(er["is_non_ancestor"]),
                rationale=er["rationale"],
            )
            for er in enc_cursor.fetchall()
        ]

        return TopicNode(
            id=row["id"],
            title=row["title"],
            course_id=row["course_id"],
            canonical_domain=row["canonical_domain"],
            domain_cluster=row["domain_cluster"],
            skill_type=row["skill_type"],
            bloom_cognitive_tier=row["bloom_cognitive_tier"],
            grade_level_min=row["grade_level_min"],
            grade_level_max=row["grade_level_max"],
            is_core=bool(row["is_core"]),
            in_foundations_sequence=bool(row["in_foundations_sequence"]),
            requires_automaticity=bool(row["requires_automaticity"]),
            automaticity_Defining_latency_ms=row["automaticity_Defining_latency_ms"],
            base_difficulty=row["base_difficulty"],
            estimated_xp=row["estimated_xp"],
            mastery_gate_n=row["mastery_gate_n"],
            key_prerequisite_id=row["key_prerequisite_id"],
            aligned_standards=json.loads(row["aligned_standards_json"]),
            mastery_criteria=json.loads(row["mastery_criteria_json"]),
            knowledge_points=kps,
            prerequisites=prereqs,
            encompassed_skills=encs,
        )

    def get_topic_node(self, topic_id: str) -> TopicNode:
        cursor = self.conn.execute("SELECT * FROM topic_nodes WHERE id = ?", (topic_id,))
        row = cursor.fetchone()
        if not row:
            raise KeyError(f"TopicNode with id '{topic_id}' not found.")
        return self._row_to_topic_node(row)

    def get_course_subgraph(
        self,
        course_id: str,
        foundations_only: bool = False,
        core_only: bool = False,
    ) -> List[TopicNode]:
        query = "SELECT * FROM topic_nodes WHERE course_id = ?"
        params: List[Any] = [course_id]

        if foundations_only:
            query += " AND in_foundations_sequence = 1"
        if core_only:
            query += " AND is_core = 1"

        cursor = self.conn.execute(query, params)
        return [self._row_to_topic_node(r) for r in cursor.fetchall()]

    # -----------------------------------------------------------------------
    # B. Multi-Hop Graph Traversals
    # -----------------------------------------------------------------------

    def get_hard_parents(self, topic_id: str) -> List[str]:
        cursor = self.conn.execute(
            """
            SELECT source_topic_id FROM prerequisite_edges
            WHERE target_topic_id = ? AND strength = 'hard'
            ORDER BY source_topic_id
            """,
            (topic_id,),
        )
        return [r[0] for r in cursor.fetchall()]

    def get_postrequisites(self, topic_id: str, max_hops: Optional[int] = None) -> List[str]:
        if max_hops is not None:
            # Recursive CTE bounded by max_hops
            query = """
                WITH RECURSIVE postreqs(tid, depth) AS (
                    SELECT target_topic_id, 1
                    FROM prerequisite_edges
                    WHERE source_topic_id = ?
                    UNION
                    SELECT p.target_topic_id, pr.depth + 1
                    FROM prerequisite_edges p
                    JOIN postreqs pr ON p.source_topic_id = pr.tid
                    WHERE pr.depth < ?
                )
                SELECT DISTINCT tid FROM postreqs ORDER BY tid;
            """
            cursor = self.conn.execute(query, (topic_id, max_hops))
        else:
            # Unbounded recursive CTE
            query = """
                WITH RECURSIVE postreqs(tid) AS (
                    SELECT target_topic_id
                    FROM prerequisite_edges
                    WHERE source_topic_id = ?
                    UNION
                    SELECT p.target_topic_id
                    FROM prerequisite_edges p
                    JOIN postreqs pr ON p.source_topic_id = pr.tid
                )
                SELECT DISTINCT tid FROM postreqs ORDER BY tid;
            """
            cursor = self.conn.execute(query, (topic_id,))
        return [r[0] for r in cursor.fetchall()]

    def get_encompassed_ancestors(
        self, topic_id: str, max_hops: int = 3
    ) -> List[WeightedEncompassingPath]:
        cursor = self.conn.execute(
            """
            SELECT target_id, hops, compound_weight, path_json, is_non_ancestor
            FROM topic_closure_paths
            WHERE origin_id = ? AND edge_kind = 'ENCOMPASSES' AND hops <= ?
            ORDER BY hops, target_id
            """,
            (topic_id, max_hops),
        )
        paths = []
        for r in cursor.fetchall():
            paths.append(
                WeightedEncompassingPath(
                    ancestor_id=r["target_id"],
                    hops=r["hops"],
                    compound_weight=r["compound_weight"],
                    path=json.loads(r["path_json"]),
                    is_non_ancestor=bool(r["is_non_ancestor"]),
                )
            )
        paths.sort(key=lambda p: (p.hops, p.ancestor_id, -p.compound_weight, p.path))
        return paths

    def get_encompassing_descendants(
        self, topic_id: str, max_hops: int = 3
    ) -> List[WeightedEncompassingPath]:
        cursor = self.conn.execute(
            """
            SELECT origin_id, hops, compound_weight, path_json, is_non_ancestor
            FROM topic_closure_paths
            WHERE target_id = ? AND edge_kind = 'ENCOMPASSES' AND hops <= ?
            ORDER BY hops, origin_id
            """,
            (topic_id, max_hops),
        )
        paths = []
        for r in cursor.fetchall():
            paths.append(
                WeightedEncompassingPath(
                    ancestor_id=r["origin_id"],
                    hops=r["hops"],
                    compound_weight=r["compound_weight"],
                    path=json.loads(r["path_json"]),
                    is_non_ancestor=bool(r["is_non_ancestor"]),
                )
            )
        paths.sort(key=lambda p: (p.hops, p.ancestor_id, -p.compound_weight, p.path))
        return paths

    def get_local_neighborhood(self, topic_id: str) -> LocalNeighborhood:
        node = self.get_topic_node(topic_id)
        direct_prereqs = self.get_hard_parents(topic_id)
        enc_anc = [p.ancestor_id for p in self.get_encompassed_ancestors(topic_id, max_hops=1)]
        enc_desc = [p.ancestor_id for p in self.get_encompassing_descendants(topic_id, max_hops=1)]

        cursor = self.conn.execute(
            "SELECT id FROM topic_nodes WHERE domain_cluster = ? AND id != ? ORDER BY id",
            (node.domain_cluster, topic_id),
        )
        siblings = [r[0] for r in cursor.fetchall()]

        return LocalNeighborhood(
            topic_id=topic_id,
            direct_prerequisites=direct_prereqs,
            key_prerequisite_id=node.key_prerequisite_id,
            encompassed_ancestors=enc_anc,
            encompassing_descendants=enc_desc,
            cluster_siblings=siblings,
        )

    def compute_knowledge_frontier(self, student_id: str, course_id: str) -> List[TopicNode]:
        """
        Relational SQL Knowledge Frontier query.
        Finds topics in course_id where:
          1. Current status is NOT IN ('MASTERED', 'MASTERED_FLOOR')
          2. Zero unmastered incoming hard parents exist
        """
        query = """
            SELECT t.* FROM topic_nodes t
            LEFT JOIN student_topic_states s
                ON t.id = s.topic_id AND s.student_id = ?
            WHERE t.course_id = ?
              AND (s.status IS NULL OR s.status NOT IN ('MASTERED', 'MASTERED_FLOOR'))
              AND NOT EXISTS (
                  SELECT 1 FROM prerequisite_edges pe
                  LEFT JOIN student_topic_states ps
                      ON pe.source_topic_id = ps.topic_id AND ps.student_id = ?
                  WHERE pe.target_topic_id = t.id
                    AND pe.strength = 'hard'
                    AND (ps.status IS NULL OR ps.status NOT IN ('MASTERED', 'MASTERED_FLOOR'))
              )
            ORDER BY t.id;
        """
        cursor = self.conn.execute(query, (student_id, course_id, student_id))
        return [self._row_to_topic_node(r) for r in cursor.fetchall()]

    def compute_compressed_review_cover(self, student_id: str) -> ReviewCompressionPlan:
        # Fetch all due reviews
        cursor = self.conn.execute(
            "SELECT topic_id FROM student_topic_states WHERE student_id = ? AND status = 'DUE_FOR_REVIEW'",
            (student_id,),
        )
        due_reviews = {r[0] for r in cursor.fetchall()}
        if not due_reviews:
            return ReviewCompressionPlan(
                selected_task_ids=[],
                covered_review_ids=[],
                fended_review_ids=[],
                efficiency_ratio=1.0,
            )

        # Greedily pick candidate topics covering due reviews
        uncovered = set(due_reviews)
        selected_tasks: List[str] = []

        # Find all candidate topics that cover any due review
        query = """
            SELECT origin_id, target_id
            FROM topic_closure_paths
            WHERE edge_kind = 'ENCOMPASSES' AND hops <= 2
            ORDER BY origin_id, target_id
        """
        cursor = self.conn.execute(query)
        coverage_map: Dict[str, Set[str]] = {}
        for r in cursor.fetchall():
            origin, target = r[0], r[1]
            if target in due_reviews:
                coverage_map.setdefault(origin, set()).add(target)
        for dr in due_reviews:
            coverage_map.setdefault(dr, set()).add(dr)

        while uncovered and coverage_map:
            best_cand = min(coverage_map.keys(), key=lambda c: (-len(coverage_map[c] & uncovered), c))
            newly_covered = coverage_map[best_cand] & uncovered
            if not newly_covered:
                break
            selected_tasks.append(best_cand)
            uncovered -= newly_covered
            del coverage_map[best_cand]

        covered_list = sorted(list(due_reviews - uncovered))
        efficiency = (len(due_reviews) / max(1, len(selected_tasks))) if selected_tasks else 1.0

        return ReviewCompressionPlan(
            selected_task_ids=selected_tasks,
            covered_review_ids=covered_list,
            fended_review_ids=[],
            efficiency_ratio=round(efficiency, 2),
        )

    def get_compressed_diagnostic_cover(self, course_id: str) -> List[str]:
        cursor = self.conn.execute(
            "SELECT id FROM topic_nodes WHERE course_id = ? ORDER BY id",
            (course_id,),
        )
        all_ids = [r[0] for r in cursor.fetchall()]
        if not all_ids:
            return []

        # Sample every 3rd node or roots
        cover = []
        for i, tid in enumerate(all_ids):
            if i % 3 == 0 or len(self.get_hard_parents(tid)) == 0:
                cover.append(tid)
        return cover

    # -----------------------------------------------------------------------
    # C. Content Shell Registry
    # -----------------------------------------------------------------------

    def get_kp_content_shell(self, kp_id: str) -> KPContentShell:
        cursor = self.conn.execute("SELECT * FROM kp_content_shells WHERE kp_id = ?", (kp_id,))
        row = cursor.fetchone()
        if not row:
            raise KeyError(f"KPContentShell '{kp_id}' not found.")

        cwe = json.loads(row["worked_example_json"]) if row["worked_example_json"] else None
        pb = json.loads(row["problem_blueprint_json"]) if row["problem_blueprint_json"] else None
        dm = json.loads(row["diagnostic_misconceptions_json"]) if row["diagnostic_misconceptions_json"] else []

        return KPContentShell(
            kp_id=row["kp_id"],
            content_status=row["content_status"],
            rule_statement=row["rule_statement"],
            worked_example=cwe,
            problem_blueprint=pb,
            diagnostic_misconceptions=dm,
            svg_asset_uri=row["svg_asset_uri"],
        )

    def upsert_kp_content_shell(self, shell: KPContentShell) -> None:
        cwe_json = json.dumps(shell.worked_example.model_dump()) if shell.worked_example else None
        pb_json = json.dumps(shell.problem_blueprint.model_dump()) if shell.problem_blueprint else None
        dm_json = json.dumps([m.model_dump() for m in shell.diagnostic_misconceptions]) if shell.diagnostic_misconceptions else None

        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO kp_content_shells VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    shell.kp_id,
                    shell.content_status,
                    shell.rule_statement,
                    cwe_json,
                    pb_json,
                    dm_json,
                    shell.svg_asset_uri,
                ),
            )

    # -----------------------------------------------------------------------
    # D. Transactional Student State & Telemetry
    # -----------------------------------------------------------------------

    def get_student_profile(self, student_id: str) -> StudentProfile:
        cursor = self.conn.execute("SELECT * FROM student_profiles WHERE student_id = ?", (student_id,))
        row = cursor.fetchone()
        if not row:
            raise KeyError(f"StudentProfile for '{student_id}' not found.")
        keys = row.keys() if hasattr(row, "keys") else []
        return StudentProfile(
            student_id=row["student_id"],
            enrolled_course_id=row["enrolled_course_id"],
            xp_total=row["xp_total"],
            progress_pct=row["progress_pct"],
            daily_xp_target=row["daily_xp_target"],
            rolling_pace_xp=row["rolling_pace_xp"],
            independence_stage=row["independence_stage"],
            process_praise_counter=row["process_praise_counter"],
            rolling_latency_ms=json.loads(row["rolling_latency_ms_json"]),
            league_opt_in=bool(row["league_opt_in"]) if "league_opt_in" in keys else False,
            league_tier=row["league_tier"] if "league_tier" in keys else "APPRENTICE",
            consecutive_blowoffs=row["consecutive_blowoffs"] if "consecutive_blowoffs" in keys else 0,
        )

    def list_student_profiles(self, course_id: Optional[str] = None) -> List[StudentProfile]:
        if course_id:
            cursor = self.conn.execute(
                "SELECT * FROM student_profiles WHERE enrolled_course_id = ?",
                (course_id,),
            )
        else:
            cursor = self.conn.execute("SELECT * FROM student_profiles")
        rows = cursor.fetchall()
        profiles = []
        for row in rows:
            keys = row.keys() if hasattr(row, "keys") else []
            profiles.append(
                StudentProfile(
                    student_id=row["student_id"],
                    enrolled_course_id=row["enrolled_course_id"],
                    xp_total=row["xp_total"],
                    progress_pct=row["progress_pct"],
                    daily_xp_target=row["daily_xp_target"],
                    rolling_pace_xp=row["rolling_pace_xp"],
                    independence_stage=row["independence_stage"],
                    process_praise_counter=row["process_praise_counter"],
                    rolling_latency_ms=json.loads(row["rolling_latency_ms_json"]),
                    league_opt_in=bool(row["league_opt_in"]) if "league_opt_in" in keys else False,
                    league_tier=row["league_tier"] if "league_tier" in keys else "APPRENTICE",
                    consecutive_blowoffs=row["consecutive_blowoffs"] if "consecutive_blowoffs" in keys else 0,
                )
            )
        return profiles

    def upsert_student_profile(self, profile: StudentProfile) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO student_profiles (
                    student_id, enrolled_course_id, xp_total, progress_pct,
                    daily_xp_target, rolling_pace_xp, independence_stage,
                    process_praise_counter, rolling_latency_ms_json,
                    league_opt_in, league_tier, consecutive_blowoffs
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.student_id,
                    profile.enrolled_course_id,
                    profile.xp_total,
                    profile.progress_pct,
                    profile.daily_xp_target,
                    profile.rolling_pace_xp,
                    profile.independence_stage,
                    profile.process_praise_counter,
                    json.dumps(profile.rolling_latency_ms),
                    1 if profile.league_opt_in else 0,
                    profile.league_tier,
                    profile.consecutive_blowoffs,
                ),
            )

    def get_student_topic_states(
        self, student_id: str, topic_ids: Optional[List[str]] = None
    ) -> Dict[str, StudentTopicState]:
        if topic_ids is not None:
            placeholders = ",".join("?" for _ in topic_ids)
            query = f"SELECT * FROM student_topic_states WHERE student_id = ? AND topic_id IN ({placeholders})"
            cursor = self.conn.execute(query, [student_id] + topic_ids)
        else:
            cursor = self.conn.execute("SELECT * FROM student_topic_states WHERE student_id = ?", (student_id,))

        states = {}
        for r in cursor.fetchall():
            lr = datetime.fromisoformat(r["last_reviewed_at"]) if r["last_reviewed_at"] else None
            dr = datetime.fromisoformat(r["due_review_at"]) if r["due_review_at"] else None
            cd = datetime.fromisoformat(r["cooldown_until"]) if r["cooldown_until"] else None

            st = StudentTopicState(
                student_id=r["student_id"],
                topic_id=r["topic_id"],
                status=r["status"],
                rep_num=r["rep_num"],
                stability_interval_days=r["stability_interval_days"],
                memory_strength=r["memory_strength"],
                last_reviewed_at=lr,
                due_review_at=dr,
                cumulative_fire_credit=r["cumulative_fire_credit"],
                student_ability=r["student_ability"],
                topic_difficulty=r["topic_difficulty"],
                speed=r["speed"],
                allow_implicit_fire_credit=bool(r["allow_implicit_fire_credit"]),
                diagnostic_plus_minus_balance=r["diagnostic_plus_minus_balance"],
                is_conditionally_completed=bool(r["is_conditionally_completed"]),
                current_kp_index=r["current_kp_index"],
                consecutive_correct=r["consecutive_correct"],
                kp_failure_counts={int(k): v for k, v in json.loads(r["kp_failure_counts_json"]).items()},
                cooldown_until=cd,
            )
            states[st.topic_id] = st
        return states

    def upsert_student_topic_state(self, state: StudentTopicState) -> None:
        lr = state.last_reviewed_at.isoformat() if state.last_reviewed_at else None
        dr = state.due_review_at.isoformat() if state.due_review_at else None
        cd = state.cooldown_until.isoformat() if state.cooldown_until else None

        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO student_topic_states VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    state.student_id,
                    state.topic_id,
                    state.status,
                    state.rep_num,
                    state.stability_interval_days,
                    state.memory_strength,
                    lr,
                    dr,
                    state.cumulative_fire_credit,
                    state.student_ability,
                    state.topic_difficulty,
                    state.speed,
                    1 if state.allow_implicit_fire_credit else 0,
                    state.diagnostic_plus_minus_balance,
                    1 if state.is_conditionally_completed else 0,
                    state.current_kp_index,
                    state.consecutive_correct,
                    json.dumps(state.kp_failure_counts),
                    cd,
                ),
            )

    def apply_submission_and_propagate_fire(
        self,
        attempt: InteractionAttempt,
        updated_states: List[StudentTopicState],
        xp_entry: Optional[XPLedgerEntry] = None,
    ) -> None:
        """Atomic transaction committing attempt, states, and XP ledger."""
        with self.conn:
            # 1. Record attempt
            self.conn.execute(
                """
                INSERT OR REPLACE INTO interaction_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.attempt_id,
                    attempt.student_id,
                    attempt.topic_id,
                    attempt.kp_id,
                    attempt.submitted_answer,
                    1 if attempt.is_correct else 0,
                    attempt.duration_ms,
                    1 if attempt.reference_peeked else 0,
                    attempt.created_at.isoformat(),
                ),
            )

            # 2. Update states
            for s in updated_states:
                lr = s.last_reviewed_at.isoformat() if s.last_reviewed_at else None
                dr = s.due_review_at.isoformat() if s.due_review_at else None
                cd = s.cooldown_until.isoformat() if s.cooldown_until else None
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO student_topic_states VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        s.student_id,
                        s.topic_id,
                        s.status,
                        s.rep_num,
                        s.stability_interval_days,
                        s.memory_strength,
                        lr,
                        dr,
                        s.cumulative_fire_credit,
                        s.student_ability,
                        s.topic_difficulty,
                        s.speed,
                        1 if s.allow_implicit_fire_credit else 0,
                        s.diagnostic_plus_minus_balance,
                        1 if s.is_conditionally_completed else 0,
                        s.current_kp_index,
                        s.consecutive_correct,
                        json.dumps(s.kp_failure_counts),
                        cd,
                    ),
                )

            # 3. Append XP ledger
            if xp_entry is not None:
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO xp_ledger_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        xp_entry.entry_id,
                        xp_entry.student_id,
                        xp_entry.topic_id,
                        xp_entry.task_type,
                        xp_entry.quality_tier,
                        xp_entry.delta_xp,
                        xp_entry.reason,
                        xp_entry.created_at.isoformat(),
                    ),
                )
                self.conn.execute(
                    """
                    UPDATE student_profiles
                    SET xp_total = MAX(0, xp_total + ?)
                    WHERE student_id = ?
                    """,
                    (xp_entry.delta_xp, xp_entry.student_id),
                )

    def get_dashboard_menu(self, student_id: str) -> List[DashboardTaskItem]:
        cursor = self.conn.execute(
            """
            SELECT * FROM dashboard_task_menus
            WHERE student_id = ?
            ORDER BY rank_score DESC, assigned_at ASC, topic_id ASC
            """,
            (student_id,),
        )
        items = []
        for r in cursor.fetchall():
            items.append(
                DashboardTaskItem(
                    student_id=r["student_id"],
                    topic_id=r["topic_id"],
                    task_type=r["task_type"],
                    rank_score=r["rank_score"],
                    is_sticky=bool(r["is_sticky"]),
                    assigned_at=datetime.fromisoformat(r["assigned_at"]),
                )
            )
        return items

    def save_dashboard_menu(self, student_id: str, items: List[DashboardTaskItem]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM dashboard_task_menus WHERE student_id = ?", (student_id,))
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO dashboard_task_menus VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        it.student_id,
                        it.topic_id,
                        it.task_type,
                        it.rank_score,
                        1 if it.is_sticky else 0,
                        it.assigned_at.isoformat(),
                    )
                    for it in items
                ],
            )

    def record_attempt(self, attempt: InteractionAttempt) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO interaction_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.attempt_id,
                    attempt.student_id,
                    attempt.topic_id,
                    attempt.kp_id,
                    attempt.submitted_answer,
                    1 if attempt.is_correct else 0,
                    attempt.duration_ms,
                    1 if attempt.reference_peeked else 0,
                    attempt.created_at.isoformat(),
                ),
            )

    def append_xp_ledger(self, entry: XPLedgerEntry) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO xp_ledger_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.entry_id,
                    entry.student_id,
                    entry.topic_id,
                    entry.task_type,
                    entry.quality_tier,
                    entry.delta_xp,
                    entry.reason,
                    entry.created_at.isoformat(),
                ),
            )
            # Update student profile xp_total atomically
            self.conn.execute(
                """
                UPDATE student_profiles
                SET xp_total = MAX(0, xp_total + ?)
                WHERE student_id = ?
                """,
                (entry.delta_xp, entry.student_id),
            )

    def get_xp_ledger_entries(
        self,
        student_id: str,
        limit: int = 50,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> List[XPLedgerEntry]:
        query = "SELECT * FROM xp_ledger_entries WHERE student_id = ?"
        params: List[Any] = [student_id]
        if since is not None:
            query += " AND created_at >= ?"
            params.append(since.isoformat())
        if until is not None:
            query += " AND created_at <= ?"
            params.append(until.isoformat())
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = self.conn.execute(query, tuple(params))
        entries = []
        for r in cursor.fetchall():
            entries.append(
                XPLedgerEntry(
                    entry_id=r["entry_id"],
                    student_id=r["student_id"],
                    topic_id=r["topic_id"],
                    task_type=r["task_type"],
                    quality_tier=r["quality_tier"],
                    delta_xp=r["delta_xp"],
                    reason=r["reason"],
                    created_at=datetime.fromisoformat(r["created_at"]),
                )
            )
        return entries

    # --- E. Tier 7 Weekly Leagues Persistence ---
    def save_league_cohort(self, cohort: LeagueCohort) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO league_cohorts VALUES (?, ?, ?, ?)
                """,
                (
                    cohort.cohort_id,
                    cohort.week_key,
                    cohort.tier,
                    cohort.model_dump_json(),
                ),
            )

    def get_league_cohort(self, cohort_id: str) -> Optional[LeagueCohort]:
        cursor = self.conn.execute(
            "SELECT payload_json FROM league_cohorts WHERE cohort_id = ?",
            (cohort_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return LeagueCohort.model_validate_json(row["payload_json"])

    def get_student_league_cohort(self, student_id: str, week_key: str) -> Optional[LeagueCohort]:
        cursor = self.conn.execute(
            "SELECT payload_json FROM league_cohorts WHERE week_key = ?",
            (week_key,),
        )
        for row in cursor.fetchall():
            cohort = LeagueCohort.model_validate_json(row["payload_json"])
            if student_id in cohort.member_ids:
                return cohort
        return None

    def list_league_cohorts(self, week_key: Optional[str] = None) -> List[LeagueCohort]:
        if week_key:
            cursor = self.conn.execute(
                "SELECT payload_json FROM league_cohorts WHERE week_key = ?",
                (week_key,),
            )
        else:
            cursor = self.conn.execute("SELECT payload_json FROM league_cohorts")
        return [LeagueCohort.model_validate_json(r["payload_json"]) for r in cursor.fetchall()]

    # --- F. Tier 8 Accountability, Habit & Content Remediation Persistence ---
    def save_parent_reward(self, reward: ParentCustomReward) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO parent_rewards VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reward.reward_id,
                    reward.student_id,
                    reward.title,
                    reward.xp_target,
                    reward.status,
                    reward.created_at.isoformat(),
                    reward.unlocked_at.isoformat() if reward.unlocked_at else None,
                    reward.claimed_at.isoformat() if reward.claimed_at else None,
                ),
            )

    def get_parent_rewards(self, student_id: str) -> List[ParentCustomReward]:
        cursor = self.conn.execute(
            """
            SELECT reward_id, student_id, title, xp_target, status, created_at, unlocked_at, claimed_at
            FROM parent_rewards
            WHERE student_id = ?
            ORDER BY xp_target ASC, created_at ASC
            """,
            (student_id,),
        )
        rewards = []
        for row in cursor.fetchall():
            rewards.append(
                ParentCustomReward(
                    reward_id=row["reward_id"],
                    student_id=row["student_id"],
                    title=row["title"],
                    xp_target=row["xp_target"],
                    status=row["status"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    unlocked_at=datetime.fromisoformat(row["unlocked_at"]) if row["unlocked_at"] else None,
                    claimed_at=datetime.fromisoformat(row["claimed_at"]) if row["claimed_at"] else None,
                )
            )
        return rewards

    def update_parent_reward_status(
        self, reward_id: str, status: str, claimed_at: Optional[datetime] = None
    ) -> None:
        with self.conn:
            if status == "UNLOCKED":
                now_str = datetime.now(timezone.utc).isoformat()
                self.conn.execute(
                    """
                    UPDATE parent_rewards
                    SET status = ?, unlocked_at = COALESCE(unlocked_at, ?)
                    WHERE reward_id = ?
                    """,
                    (status, now_str, reward_id),
                )
            elif status == "CLAIMED":
                c_str = (claimed_at or datetime.now(timezone.utc)).isoformat()
                self.conn.execute(
                    """
                    UPDATE parent_rewards
                    SET status = ?, claimed_at = ?
                    WHERE reward_id = ?
                    """,
                    (status, c_str, reward_id),
                )
            else:
                self.conn.execute(
                    "UPDATE parent_rewards SET status = ? WHERE reward_id = ?",
                    (status, reward_id),
                )

    def save_study_habit_schedule(self, schedule: StudyHabitSchedule) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO study_habit_schedules VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    schedule.student_id,
                    schedule.target_daily_xp,
                    schedule.scheduled_start_time,
                    schedule.scheduled_end_time,
                    json.dumps(schedule.active_days),
                    schedule.current_streak_days,
                    schedule.longest_streak_days,
                    schedule.last_active_date,
                ),
            )

    def get_study_habit_schedule(self, student_id: str) -> Optional[StudyHabitSchedule]:
        cursor = self.conn.execute(
            """
            SELECT student_id, target_daily_xp, scheduled_start_time, scheduled_end_time,
                   active_days_json, current_streak_days, longest_streak_days, last_active_date
            FROM study_habit_schedules
            WHERE student_id = ?
            """,
            (student_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return StudyHabitSchedule(
            student_id=row["student_id"],
            target_daily_xp=row["target_daily_xp"],
            scheduled_start_time=row["scheduled_start_time"],
            scheduled_end_time=row["scheduled_end_time"],
            active_days=json.loads(row["active_days_json"]),
            current_streak_days=row["current_streak_days"],
            longest_streak_days=row["longest_streak_days"],
            last_active_date=row["last_active_date"],
        )

    def record_adult_resit_alert(self, alert: AdultReSitAlert) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO adult_resit_alerts VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert.alert_id,
                    alert.student_id,
                    alert.reason,
                    alert.details,
                    alert.severity,
                    1 if alert.acknowledged else 0,
                    alert.created_at.isoformat(),
                    alert.acknowledged_at.isoformat() if alert.acknowledged_at else None,
                ),
            )

    def list_adult_resit_alerts(
        self, student_id: str, unacknowledged_only: bool = False
    ) -> List[AdultReSitAlert]:
        query = """
            SELECT alert_id, student_id, reason, details, severity, acknowledged, created_at, acknowledged_at
            FROM adult_resit_alerts
            WHERE student_id = ?
        """
        params: List[Any] = [student_id]
        if unacknowledged_only:
            query += " AND acknowledged = 0"
        query += " ORDER BY created_at DESC"

        cursor = self.conn.execute(query, params)
        alerts = []
        for row in cursor.fetchall():
            alerts.append(
                AdultReSitAlert(
                    alert_id=row["alert_id"],
                    student_id=row["student_id"],
                    reason=row["reason"],
                    details=row["details"],
                    severity=row["severity"],
                    acknowledged=bool(row["acknowledged"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                    acknowledged_at=datetime.fromisoformat(row["acknowledged_at"]) if row["acknowledged_at"] else None,
                )
            )
        return alerts

    def acknowledge_adult_resit_alert(
        self, alert_id: str, acknowledged_at: Optional[datetime] = None
    ) -> None:
        ack_str = (acknowledged_at or datetime.now(timezone.utc)).isoformat()
        with self.conn:
            self.conn.execute(
                """
                UPDATE adult_resit_alerts
                SET acknowledged = 1, acknowledged_at = ?
                WHERE alert_id = ?
                """,
                (ack_str, alert_id),
            )

    def record_content_flag(self, flag: ContentRemediationFlag) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO content_remediation_flags VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    flag.flag_id,
                    flag.student_id,
                    flag.topic_id,
                    flag.kp_id,
                    flag.problem_stem,
                    flag.flag_type,
                    flag.student_comment,
                    flag.created_at.isoformat(),
                    flag.status,
                ),
            )

    def list_content_flags(
        self, topic_id: Optional[str] = None, status: Optional[str] = None
    ) -> List[ContentRemediationFlag]:
        query = "SELECT flag_id, student_id, topic_id, kp_id, problem_stem, flag_type, student_comment, created_at, status FROM content_remediation_flags WHERE 1=1"
        params: List[Any] = []
        if topic_id:
            query += " AND topic_id = ?"
            params.append(topic_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"

        cursor = self.conn.execute(query, params)
        flags = []
        for row in cursor.fetchall():
            flags.append(
                ContentRemediationFlag(
                    flag_id=row["flag_id"],
                    student_id=row["student_id"],
                    topic_id=row["topic_id"],
                    kp_id=row["kp_id"],
                    problem_stem=row["problem_stem"],
                    flag_type=row["flag_type"],
                    student_comment=row["student_comment"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    status=row["status"],
                )
            )
        return flags

    def update_content_flag_status(self, flag_id: str, status: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE content_remediation_flags SET status = ? WHERE flag_id = ?",
                (status, flag_id),
            )

    def get_interaction_attempts(
        self,
        student_id: Optional[str] = None,
        topic_id: Optional[str] = None,
        kp_id: Optional[str] = None,
        limit: int = 1000,
    ) -> List[InteractionAttempt]:
        query = """
            SELECT attempt_id, student_id, topic_id, kp_id, submitted_answer, is_correct,
                   duration_ms, reference_peeked, created_at
            FROM interaction_attempts
            WHERE 1=1
        """
        params: List[Any] = []
        if student_id:
            query += " AND student_id = ?"
            params.append(student_id)
        if topic_id:
            query += " AND topic_id = ?"
            params.append(topic_id)
        if kp_id:
            query += " AND kp_id = ?"
            params.append(kp_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = self.conn.execute(query, params)
        attempts = []
        for row in cursor.fetchall():
            attempts.append(
                InteractionAttempt(
                    attempt_id=row["attempt_id"],
                    student_id=row["student_id"],
                    topic_id=row["topic_id"],
                    kp_id=row["kp_id"],
                    submitted_answer=row["submitted_answer"],
                    is_correct=bool(row["is_correct"]),
                    duration_ms=row["duration_ms"],
                    reference_peeked=bool(row["reference_peeked"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
            )
        return attempts

