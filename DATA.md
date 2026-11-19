# Trellis Data & Persistence Architecture (`DATA.md`)

## 1. Mutually Exclusive Dual-Backend Mandate

Trellis enforces a **Mutually Exclusive Dual-Backend Architecture** to enable a rigorous, apples-to-apples evaluation between a **100% Standalone Relational Database (`Adapter A`)** and a **100% Standalone Graph Database (`Adapter B`)**.

- **Zero Cross-Database Coordination:** No query, transaction, or `UnitOfWork` spans across two database engines.
- **Single Runtime Switch:** The application boots with a single environment variable:
  ```bash
  DB_BACKEND="relational"  # Adapter A: 100% Standalone Relational (PostgreSQL / SQLite)
  DB_BACKEND="graph"       # Adapter B: 100% Standalone Graph (Neo4j / Memgraph)
  DB_BACKEND="memory"      # Adapter C: In-Memory Reference (SQLite + NetworkX for unit tests)
  ```
- **Complete State Ownership:** Whichever backend is selected independently stores **100% of the platform state**:
  1. **Curriculum DAG Topology:** `Courses`, `TopicNodes`, `KnowledgePoints` (`#kp_0`..`#kp_3`), `PREREQUISITE_OF` edges (`hard`/`soft`), `ENCOMPASSES` edges (`fire_weight` $w \in (0, 1]$), `KEY_PREREQUISITE` pointers (topic-level and KP-level), and `MASTERY_FLOOR_OF` course edges.
  2. **Content Shells & Problem Blueprints:** `KPContentShell` records (`BLUEPRINT_READY`, `LLM_DRAFTED`, `VERIFIED_PRODUCTION`), `SymPy` parameter/solver specs, `diagnostic_misconceptions`, and dual-coded SVG references.
  3. **Transactional Student State & Telemetry:** `StudentProfile`, `StudentTopicState` (`status`, `rep_num`, `stability_interval_days`, `memory`, `student_ability`, `speed`, `allow_implicit_fire_credit`, `diagnostic_plus_minus_balance`, `is_conditionally_completed`), `DashboardTaskMenu` (sticky "Math Buffet" items), `InteractionAttempt` sensor logs, `XPLedgerEntry` records, and `WeeklyLeague` memberships.

---

## 2. Clean-Room Knowledge Graph Source of Truth

All curriculum data is ingested from `data/clean_spec/trellis_knowledge_graph.json` (`schema_version: "2.0.0-trellis"`, validated by `schemas/knowledge_graph_schema.json` and `scripts/validate_math_dag.py`):

- **Curricular Tracks:**
  - **K–12 Sequence & Foundations (`grade_level_min/max: 0..12`):** Covers early arithmetic (`CRS_FND_K2`) through high school Pre-Calculus (`CRS_PRECALC`), with the adult accelerated Foundations path tagged via `in_foundations_sequence = true`.
  - **Undergraduate University Courses (`is_university_level = true`, levels 13–16):** Calculus I/II, Multivariable Calculus, Linear Algebra, Differential Equations, Discrete Math, Statistics, and Abstract Algebra with non-holistic ancestral remediation boundaries.
- **Deterministic Node IDs:** `trl_<course_code>_<domain_code>_<seq_num>` (e.g., `trl_alg1_lin_004`).
- **Deterministic Knowledge Point IDs:** `trl_<course_code>_<domain_code>_<seq_num>#kp_<0..3>`.
- **Dual-Edge Separation:**
  - `prerequisites` ($u \to v$): Forward pedagogical learning dependency (`strength: "hard" | "soft"`). Transitively reduced on `hard` edges.
  - `encompassed_skills` ($v \to u$): Backward Fractional Implicit Repetition (FIRe) containment (`fire_weight` $w \in (0, 1]$, `is_non_ancestor: bool`).

---

## 3. Unified `TrellisStoragePort` Contract (`FR-0.1`)

All domain services (`Tier 1`–`Tier 8`) depend exclusively on `src/trellis/ports/storage_port.py`:

```python
from typing import Literal, Protocol

class TrellisStoragePort(Protocol):
    # --- A. Curriculum & Full-State DAG Ingestion ---
    def ingest_knowledge_graph(self, graph_payload: dict) -> GraphIngestionReport: ...
    def get_topic_node(self, topic_id: str) -> TopicNode: ...
    def get_course_subgraph(
        self, course_id: str, foundations_only: bool = False, core_only: bool = False
    ) -> list[TopicNode]: ...

    # --- B. Multi-Hop Graph Traversals (Executed 100% inside Active Backend) ---
    def get_hard_parents(self, topic_id: str) -> list[str]: ...
    def get_postrequisites(self, topic_id: str, max_hops: int | None = None) -> list[str]: ...
    def get_encompassed_ancestors(
        self, topic_id: str, max_hops: int = 3
    ) -> list[WeightedEncompassingPath]: ...
    def get_encompassing_descendants(
        self, topic_id: str, max_hops: int = 3
    ) -> list[WeightedEncompassingPath]: ...
    def get_local_neighborhood(self, topic_id: str) -> LocalNeighborhood: ...
    def compute_knowledge_frontier(self, student_id: str, course_id: str) -> list[TopicNode]: ...
    def compute_compressed_review_cover(self, student_id: str) -> ReviewCompressionPlan: ...
    def get_compressed_diagnostic_cover(self, course_id: str) -> list[str]: ...

    # --- C. Transactional Student State & Telemetry (Executed 100% inside Active Backend) ---
    def get_student_profile(self, student_id: str) -> StudentProfile: ...
    def upsert_student_profile(self, profile: StudentProfile) -> None: ...
    def get_student_topic_states(
        self, student_id: str, topic_ids: list[str] | None = None
    ) -> dict[str, StudentTopicState]: ...
    def apply_submission_and_propagate_fire(
        self,
        attempt: InteractionAttempt,
        updated_states: list[StudentTopicState],
        xp_entry: XPLedgerEntry | None = None,
    ) -> None: ...
    def get_dashboard_menu(self, student_id: str) -> list[DashboardTaskItem]: ...
    def save_dashboard_menu(self, student_id: str, items: list[DashboardTaskItem]) -> None: ...
    def get_kp_content_shell(self, kp_id: str) -> KPContentShell: ...
    def upsert_kp_content_shell(self, shell: KPContentShell) -> None: ...
```

---

## 4. Adapter A: 100% Standalone Relational Schema (`PostgreSQL / SQLite`)

In `StandaloneRelationalAdapter`, both the curriculum DAG and mutable student state live in normalized relational tables accelerated by composite B-tree indexes, `WITH RECURSIVE` CTEs, and a precomputed `topic_closure_paths` table for multi-hop traversals:

| Table Name | Primary Key | Key Columns & Indexes | Purpose |
| :--- | :--- | :--- | :--- |
| `courses` | `course_id` | `title`, `grade_band`, `sequence_order`, `is_university_level`, `benchmark_xp` | Course catalog metadata |
| `course_mastery_floors` | `(course_id, floor_course_id)` | Indexed on both columns | Auto-credits lower-level courses (`FR-1.2`, `FR-2.3`) |
| `topic_nodes` | `topic_id` (`trl_*`) | `course_id`, `domain_cluster`, `skill_type`, `is_core`, `in_foundations_sequence`, `base_difficulty`, `key_prerequisite_id`, `requires_automaticity`, `automaticity_latency_ms` | Complete topic node specification |
| `kp_nodes` | `kp_id` (`trl_*#kp_j`) | `topic_id`, `kp_index`, `title`, `key_prerequisite_id`, `mastery_gate_n`, `intrinsic_load_chunks` | Scaffolded Knowledge Point staircase (`FR-5.1`, `FR-5.2`) |
| `kp_content_shells` | `kp_id` | `content_status`, `rule_statement`, `worked_example_json`, `problem_blueprint_json`, `misconceptions_json`, `svg_asset_uri` | Decoupled instructional content & `SymPy` blueprints (`FR-1.3`, `FR-6.1`) |
| `prerequisite_edges` | `(source_topic_id, target_topic_id)` | `strength` (`hard` \| `soft`), `rationale`; indexed on `(target_topic_id, strength)` and `(source_topic_id)` | Forward prerequisite gating ($G_{\text{prereq}}$, $G_{\text{hard}}$) |
| `encompassing_edges` | `(advanced_topic_id, simpler_topic_id)` | `fire_weight` ($w \in (0,1]$), `is_non_ancestor`; indexed both ways | Direct FIRe containment ($G_{\text{enc}}$) |
| `topic_closure_paths` | `(origin_id, target_id, edge_kind)` | `hops`, `compound_weight` ($\prod w_i$), `is_non_ancestor`; indexed on `(origin_id, edge_kind, hops)` and `(target_id, edge_kind, hops)` | Materialized multi-hop closure for $O(1)$-join FIRe trickle-down, penalty trickle-up, and diagnostic propagation |
| `student_profiles` | `student_id` | `enrolled_course_id`, `xp_total`, `progress_pct`, `daily_xp_target`, `rolling_pace_xp`, `independence_stage`, `process_praise_counter` | Learner profile, pace, and coaching counters |
| `student_topic_states` | `(student_id, topic_id)` | `status`, `rep_num`, `stability_interval_days`, `last_reviewed_at`, `due_review_at`, `cumulative_fire_credit`, `student_ability`, `speed`, `allow_implicit_fire_credit`, `diagnostic_plus_minus_balance`, `is_conditionally_completed`, `current_kp_index`, `consecutive_correct`, `kp_failure_counts_json`, `cooldown_until` | Complete per-student topic mastery & HSRS/FIRe state |
| `dashboard_task_menus` | `(student_id, topic_id)` | `task_type`, `rank_score`, `is_sticky`, `assigned_at` | Sticky "Math Buffet" task menu (`FR-4.3`) |
| `interaction_attempts` | `attempt_id` | `student_id`, `topic_id`, `kp_id`, `submitted_answer`, `is_correct`, `duration_ms`, `reference_peeked`, `created_at` | Append-only telemetry & automaticity log (`FR-5.3`, `FR-5.4`) |
| `xp_ledger_entries` | `entry_id` | `student_id`, `topic_id`, `task_type`, `quality_tier`, `delta_xp`, `reason`, `created_at` | Auditable XP & penalty ledger (`FR-7.1`, `FR-7.2`) |

---

## 5. Adapter B: 100% Standalone Property Graph Schema (`Neo4j / Memgraph`)

In `StandaloneGraphAdapter`, both the curriculum DAG and all student state live inside the property graph engine and are queried/mutated via single-engine Cypher transactions:

- **Curriculum & Content Node Labels:**
  - `(:Course {course_id, title, grade_band, is_university_level, benchmark_xp})`
  - `(:TopicNode {topic_id, course_id, domain_cluster, skill_type, is_core, in_foundations_sequence, base_difficulty, requires_automaticity, automaticity_latency_ms})`
  - `(:KnowledgePoint {kp_id, topic_id, kp_index, mastery_gate_n, intrinsic_load_chunks})`
  - `(:ContentShell {kp_id, content_status, rule_statement, worked_example_json, problem_blueprint_json, misconceptions_json})`
- **Curriculum Relationship Types:**
  - `(:Course)-[:CONTAINS_TOPIC]->(:TopicNode)`
  - `(:Course)-[:MASTERY_FLOOR_OF]->(:Course)`
  - `(:TopicNode)-[:PREREQUISITE_OF {strength: "hard"|"soft"}]->(:TopicNode)`
  - `(:TopicNode)-[:ENCOMPASSES {fire_weight: float, is_non_ancestor: bool}]->(:TopicNode)`
  - `(:TopicNode)-[:KEY_PREREQUISITE]->(:TopicNode)`
  - `(:TopicNode)-[:HAS_KP {kp_index: int}]->(:KnowledgePoint)`
  - `(:KnowledgePoint)-[:KP_KEY_PREREQUISITE]->(:TopicNode)`
  - `(:KnowledgePoint)-[:HAS_CONTENT_SHELL]->(:ContentShell)`
- **Student State & Telemetry Nodes/Relationships (Stored in Same Graph DB):**
  - `(:Student {student_id, enrolled_course_id, xp_total, progress_pct, rolling_pace_xp, independence_stage})`
  - `(:Student)-[:HAS_STATE {status, rep_num, stability_interval_days, last_reviewed_at, due_review_at, cumulative_fire_credit, student_ability, speed, allow_implicit_fire_credit, diagnostic_plus_minus_balance, is_conditionally_completed, current_kp_index, consecutive_correct, cooldown_until}]->(:TopicNode)`
  - `(:Student)-[:ON_DASHBOARD_MENU {task_type, rank_score, is_sticky, assigned_at}]->(:TopicNode)`
  - `(:Student)-[:SUBMITTED]->(:Attempt {attempt_id, duration_ms, is_correct, reference_peeked, created_at})-[:ON_KP]->(:KnowledgePoint)`
  - `(:Student)-[:EARNED_XP]->(:XPLedgerEntry {entry_id, delta_xp, quality_tier, created_at})`

---

## 6. Head-to-Head Benchmarking Protocol (`FR-0.4` — `scripts/benchmark_databases.py`)

To make a data-driven production database decision, `scripts/benchmark_databases.py` executes 5 canonical workloads side-by-side against **Adapter A (Standalone Relational)** and **Adapter B (Standalone Graph)**:

1. **Workload 1 — Knowledge Frontier Scan (`compute_knowledge_frontier`):** Evaluates all `LOCKED` topics in a student's course where 100% of incoming `hard` prerequisites have `status IN ("MASTERED", "MASTERED_FLOOR")`.
2. **Workload 2 — Multi-Hop FIRe Propagation (`apply_submission_and_propagate_fire`):** Performs up to 3-hop downward credit trickle-down on pass (or upward penalty trickle-up with overdue `decay` on fail) and commits updated `rep_num`, `stability_interval_days`, and `cumulative_fire_credit` atomically.
3. **Workload 3 — Set-Cover Review Compression (`compute_compressed_review_cover`):** Gathers all `DUE_FOR_REVIEW` topics for a student and scores candidate frontier/mastered nodes by multi-hop FIRe coverage to select the minimal review cover.
4. **Workload 4 — Diagnostic Plus-Minus Propagation:** Updates `diagnostic_plus_minus_balance` across multi-hop ancestor prerequisites (on pass) or post-requisites (on fail) during placement diagnostics.
5. **Workload 5 — Concurrent Student Submission Writes:** Simulates concurrent students submitting answers, logging `:Attempt` / `interaction_attempts`, mutating KP progress, and appending `:XPLedgerEntry` / `xp_ledger_entries`.
