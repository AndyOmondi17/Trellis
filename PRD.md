# Trellis Product Requirements Document (PRD)

## 1. Product Vision & Pedagogical Foundation

**Trellis** is an adaptive, mastery-based mathematics learning platform spanning **K–12 School Foundations and Undergraduate University Mathematics** engineered around **Cognitive Load Theory (Sweller)** and **Hierarchical Spaced Repetition / Fractional Implicit Repetition (Skycak, *The Math Academy Way*)**.

Trellis eliminates unguided discovery frustration and passive lecture decay across two distinct curricular tracks:

### Curricular Track Partitioning & Pedagogical Regimes
1. **Track 1: K–12 School Mathematics & Accelerated Foundations:**
   - **Scope:** Early arithmetic through high school Pre-Calculus (`CRS_FND_K2` through `CRS_PRECALC`, grades K–12).
   - **Cumulative Mastery & Foundations Sequence:** While standard school tracks progress incrementally, Trellis provides an accelerated **Mathematical Foundations (MF)** track (`in_foundations_sequence = true`) that prunes ~30% of decorative/recreational topics, allowing adult learners, career switchers, and incoming college freshmen to achieve rigorous university-STEM readiness in record time.
2. **Track 2: Undergraduate / University Modular Mathematics:**
   - **Scope:** Higher-education, degree-specific courses (`CRS_CALC1` through `CRS_ABSALG`, university levels 13–16), including Calculus I & II, Multivariable Calculus, Linear Algebra, Differential Equations, Discrete Mathematics, Probability & Statistics for Engineers, and Abstract Algebra.
   - **Non-Holistic Targeted Remediation:** University students are not high schoolers. A college sophomore in Linear Algebra struggling with vector projection must **not** be forced to remediate unrelated high school geometry or trigonometry. Remediation and diagnostic sweeps are strictly confined to the **ancestor prerequisite subgraph of the enrolled course** ($\text{Ancestors}(G_{\text{hard}}, \text{enrolled\_course\_topics})$).
   - **Course-Level Mastery Floors:** University courses enforce hard mastery floors (`mastery_floor_course_ids`), automatically assuming and crediting baseline prerequisite courses to prevent diagnostic fatigue.

### Core Architectural Pillars
1. **A Validated Clean-Room Knowledge Graph** (`data/clean_spec/trellis_knowledge_graph.json`, schema `2.0.0-trellis`) featuring separated `prerequisites` (`hard`/`soft`) and `encompassed_skills` (`fire_weight` $w \in (0, 1]$), atomic `KnowledgePoint` staircases (`#kp_0`..`#kp_3`), KP-level `key_prerequisite_id` fallbacks, and deterministic `SymPy` problem blueprints.
2. **A Deterministic Algorithmic Core (`trellis-engine`)** governing placement diagnostics, Knowledge Frontier gating, Hierarchical Spaced Repetition (HSRS) with downward FIRe credit trickle-down and upward penalty trickle-up, Set-Cover review compression, 3-Tier remediation, automaticity verification ($\le 3000\text{ ms}$), and an anti-loophole XP economy.
3. **A Constrained Direct-Instruction Layer (`trellis-pedagogy`)** enforcing the **"I Do, We Do, You Do"** cycle with Catrambone subgoal labels, dual-coded visual schemas, non-Socratic Tier-1 error remediation, and John Wooden's 5-to-1 process coaching ratio.
4. **A Mutually Exclusive Dual-Backend Persistence Layer (`Tier 0`)** that abstracts 100% of system state behind a single `TrellisStoragePort` interface so **Standalone Relational** and **Standalone Graph** database engines can be benchmarked head-to-head and switched via a single runtime flag (`DB_BACKEND="relational" | "graph" | "memory"`).

---

## 2. Priority & Precedence Enumeration (`Tier 0` $\to$ `Tier 8`)

Every functional requirement (`FR-0.1` through `FR-8.3`) is ordered by topological dependency tier (`Tier 0` $\to$ `Tier 8`) and priority (`P0` $\to$ `P3`) so zero features fail due to missing upstream dependencies.

### Tier 0: Mutually Exclusive Dual-Backend Persistence Layer (`P0 — Foundation`)

| ID | Priority | Requirement Name | Depends On | Acceptance Criteria |
| :--- | :---: | :--- | :---: | :--- |
| **`FR-0.1`** | **P0** | **Unified `TrellisStoragePort` Contract & Single-Backend Runtime Factory** | *None (Root)* | Single repository interface covering 100% of curriculum ingestion, multi-hop graph traversals, content shells, and transactional student state reads/writes. Zero cross-database coordination. Runtime selects backend via `DB_BACKEND="relational" \| "graph" \| "memory"`. Zero raw SQL or Cypher outside `src/trellis/adapters/db/`. |
| **`FR-0.2`** | **P0** | **Independent Full-State Adapters (`Adapter A: Relational`, `Adapter B: Graph`, `Adapter C: Memory`)** | `FR-0.1` | **Adapter A (Standalone Relational — PostgreSQL/SQLite):** Stores 100% of student state, content shells, and curriculum DAG (using adjacency tables, `WITH RECURSIVE` CTEs, and materialized `topic_closure_paths`). <br/>**Adapter B (Standalone Graph — Neo4j/Memgraph):** Stores 100% of curriculum DAG and student state (`:Student` nodes, stateful `[:HAS_STATE]` edges, `:Attempt` nodes, `:XPLedgerEntry` nodes). <br/>**Adapter C (In-Memory Reference — SQLite/NetworkX):** Zero-setup reference backend for unit tests. |
| **`FR-0.3`** | **P0** | **Clean-Room Knowledge Graph Ingestion & Version Delta Tracker** | `FR-0.1`, `FR-0.2` | Validates `data/clean_spec/trellis_knowledge_graph.json` via `scripts/validate_math_dag.py`, ingests all courses, topics, KPs, 4 edge types, and `KPContentShell` records (`BLUEPRINT_READY`) into the active backend, and computes structural diffs (`added_nodes`, `rewired_edges`) for Supplemental Diagnostics. |
| **`FR-0.4`** | **P0** | **Head-to-Head Database Benchmarking Harness (`scripts/benchmark_databases.py`)** | `FR-0.1`, `FR-0.2`, `FR-0.3` | Executes identical simulated student cohorts against **Standalone Relational (`Adapter A`)** vs. **Standalone Graph (`Adapter B`)** side-by-side. Reports p50/p95/p99 latency, memory footprint, and write throughput across 5 canonical workloads: (1) Knowledge Frontier Scan, (2) Multi-Hop FIRe Propagation, (3) Set-Cover Review Compression, (4) Diagnostic Plus-Minus Propagation, and (5) Concurrent Student Submission Writes. |

---

### Tier 1: Curriculum Graph Topology & Decoupled Content Shell Services (`P0 — Core Domain`)

| ID | Priority | Requirement Name | Depends On | Acceptance Criteria |
| :--- | :---: | :--- | :---: | :--- |
| **`FR-1.1`** | **P0** | **Dual-Edge Graph Traversal Service (`Prerequisites` vs. `Encompassings`)** | `FR-0.3` | Exposes deterministic traversal methods over $G_{\text{hard}}$, $G_{\text{prereq}}$, and $G_{\text{enc}}$: `get_hard_parents`, `get_postrequisites`, `get_encompassed_ancestors` (weighted FIRe trickle-down paths), `get_encompassing_descendants` (FIRe penalty trickle-up paths), and `get_local_neighborhood` (direct prereqs, key prereqs, encompassings, and same-module `domain_cluster` siblings). |
| **`FR-1.2`** | **P0** | **Course Tracks, Core Prioritization & Mastery Floor Service** | `FR-1.1` | 1. Enforces `is_core` ancestor closure and prioritizes `is_core = true` nodes before supplemental nodes. <br/>2. Filters K–12 subgraph to `in_foundations_sequence = true` for the accelerated **Mathematical Foundations (MF)** adult track. <br/>3. Marks topics in `mastery_floor_course_ids` as `MASTERED_FLOOR` and restricts university foundational remediation strictly to ancestor prerequisites of the enrolled course. |
| **`FR-1.3`** | **P0** | **Decoupled Content Shell Registry & SymPy Problem Blueprint Engine** | `FR-0.3` | Stores `KPContentShell` (`BLUEPRINT_READY` $\to$ `LLM_DRAFTED` $\to$ `VERIFIED_PRODUCTION`), dynamically samples parameters from `problem_blueprint`, renders LaTeX stems, grades student answer equivalence via `SymPy`, matches wrong answers against `diagnostic_misconceptions.trap_answer_pattern`, and supports dual-coded SVG assets. |

---

### Tier 2: Student Knowledge Profile & Adaptive Placement Diagnostic Engine (`P0 — Onboarding & Placement`)

| ID | Priority | Requirement Name | Depends On | Acceptance Criteria |
| :--- | :---: | :--- | :---: | :--- |
| **`FR-2.1`** | **P0** | **Pre-Diagnostic Graph Compression & Max-Entropy Question Selector** | `FR-1.1`, `FR-1.2`, `FR-1.3` | Compresses the target course + foundations subgraph into the minimal cover where every topic has an ancestor and progeny within $\le 3$ prerequisite edges. Dynamically selects the next `diagnostic_blueprint` question maximizing expected information gain (~50% accuracy boundary) across 20–60 questions. |
| **`FR-2.2`** | **P0** | **Plus-Minus Confidence Balance & Conflict Resolution Engine** | `FR-2.1` | Tracks signed `diagnostic_plus_minus_balance` per topic: <br/>1. **Accuracy-Time Conflict Discounting:** Decays positive weight ($+1.0 \to +0.15$) when `duration_ms` exceeds `expected_time_seconds * 1000`. <br/>2. **"I Don't Know" Action:** Explicit `"I_DONT_KNOW"` flag records $-1.0$ weight immediately without guessing distortion. <br/>3. **Causal & Correlational Propagation:** Positive weight flows to ancestors and module leaf siblings; negative weight flows to post-requisites. <br/>4. **Prerequisite-Postrequisite Conflict Resolution:** Places student at the **bottom of their lowest knowledge hole**. |
| **`FR-2.3`** | **P0** | **Conditional Completion, Supplemental Diagnostics & "I Already Know This" Fast-Track** | `FR-2.2` | 1. Topics with `balance >= 1.0` become `MASTERED` (`rep_num = floor(balance)`). <br/>2. Topics with $0.0 < \text{balance} < 1.0$ become `MASTERED` with `is_conditionally_completed = true`; failing a dependent task immediately revokes credit and peels back the knowledge profile. <br/>3. Serves 2–5 question **Supplemental Diagnostics** on newly added/rewired nodes (`balance == 0`). <br/>4. Supports **"I Already Know This"** lesson bypass via 2 unassisted highest-KP questions answered within expected time. |

---

### Tier 3: Deterministic Mastery State Machine & Hierarchical Spaced Repetition (HSRS / FIRe) (`P0 — Algorithmic Core`)

| ID | Priority | Requirement Name | Depends On | Acceptance Criteria |
| :--- | :---: | :--- | :---: | :--- |
| **`FR-3.1`** | **P0** | **Knowledge Frontier Calculation & Foundational Momentum Sequencer (`Skill A.1`)** | `FR-1.1`, `FR-1.2`, `FR-2.3` | Transitions `LOCKED` $\to$ `AVAILABLE` strictly when all incoming `hard` prerequisites are `MASTERED` or `MASTERED_FLOOR`. Prioritizes `AVAILABLE` topics *within* the student's enrolled course before dropping down to missing lower-grade foundational remediation nodes so the student builds early momentum. |
| **`FR-3.2`** | **P0** | **Student-Topic Learning Speed Calibration & Preventative Remediation Gate** | `FR-1.1`, `FR-3.1` | Computes per-topic `student_ability` (initialized from Local Neighborhood mean, updated via EWMA and propagated along `ENCOMPASSES` edges), `topic_difficulty`, and `speed = student_ability / topic_difficulty` ($\in [0.25, 2.50]$). When `speed < 1.0`, sets `allow_implicit_fire_credit = false`, blocking implicit FIRe credit and forcing explicit spaced reviews. |
| **`FR-3.3`** | **P0** | **HSRS Memory Decay & Bidirectional FIRe Propagation Engine (`Skill A.2`)** | `FR-1.1`, `FR-3.2` | 1. Tracks `rep_num`, `stability_interval_days`, `memory = (0.5)^(days_elapsed / stability_interval_days)`, and `cumulative_fire_credit`. Transitions `MASTERED` $\to$ `DUE_FOR_REVIEW` when `memory <= 0.50`. <br/>2. Discounts positive `rawDelta` on early repetitions (`days_elapsed < stability_interval_days`). <br/>3. **Downward Credit Trickle-Down:** Passing advanced node $v$ propagates discounted credit $\Delta C = \text{rawDelta} \times \prod w_i$ down `ENCOMPASSES` edges to `MASTERED` ancestors (`allow_implicit_fire_credit == true`), expanding interval $I_{k+1} = I_k \times (1.0 + 0.5 \cdot \text{speed})$ when `cumulative_fire_credit >= 1.0`. <br/>4. **Upward Penalty Trickle-Up:** Failing simpler node $u$ ($\text{rawDelta} < 0$) applies overdue `decay >= 1.0` ("summer slide") and propagates penalty upward to `MASTERED` encompassing descendants. |

---

### Tier 4: Spaced Repetition Compression, Non-Interference & Task Dashboard (`P1 — Scheduling & Optimization`)

| ID | Priority | Requirement Name | Depends On | Acceptance Criteria |
| :--- | :---: | :--- | :---: | :--- |
| **`FR-4.1`** | **P1** | **Spaced Repetition Compression Set-Cover Optimizer (`Skill A.3`)** | `FR-3.1`, `FR-3.3` | Scores candidate frontier lessons $F$ and newer `MASTERED` topics $M$ by the FIRe credit they flow into due reviews $R_{\text{due}}$. Greedily selects tasks that knock out the largest subset of $R_{\text{due}}$, and performs look-ahead fending for upcoming reviews (`memory` in $[0.50, 0.70]$). |
| **`FR-4.2`** | **P1** | **Macro-Interleaving, Non-Interference & Review-Tsunami Throttle (`Skill B.5`)** | `FR-4.1` | Targets a 70% Frontier Lesson / 30% Compressed Review split at baseline pace, enforcing a hard **25% minimum lesson floor** (at least 1 lesson per 3 reviews) during review waves. Forbids consecutive or simultaneous tasks from the same `domain_cluster` unless bottlenecked in a single prerequisite cluster. |
| **`FR-4.3`** | **P1** | **Sticky "Math Buffet" Task Dashboard Manager** | `FR-4.1`, `FR-4.2` | Ranks dashboard tasks by composite Importance. Enforces **Dashboard Stickiness**: completing a task never evicts other uncompleted lessons from the student's dashboard menu; pending reviews are only removed if implicitly knocked out by a newly completed encompassing task. |

---

### Tier 5: Active Learning Execution Loops — Lessons, Reviews & Timed Quizzes (`P1 — Student Practice Experience`)

| ID | Priority | Requirement Name | Depends On | Acceptance Criteria |
| :--- | :---: | :--- | :---: | :--- |
| **`FR-5.1`** | **P1** | **Hyper-Scaffolded Lesson Loop & $N=3$ Mastery Gate (`Skill A.5`)** | `FR-1.3`, `FR-4.3` | Steps through 2–4 sequential `KnowledgePoint`s (`#kp_0`..`#kp_3`). Presents `InstructionCard`, supports Expertise Reversal bypass ($>85\%$ diagnostic accuracy), and requires **$N=3$ consecutive unassisted correct answers** per KP before advancing. |
| **`FR-5.2`** | **P1** | **3-Tier Targeted Remediation Engine** | `FR-1.1`, `FR-5.1` | **Tier 1 (1st error at $\text{KP}_j$):** Resets `consecutive_correct = 0`, increments `kp_failure_counts[kp]`, shows `RemediationCard`, serves fresh variant. <br/>**Tier 2 (Attempt 1 Halt & Consolidation Cooldown):** Halts lesson on attempt budget exhaustion, sets `cooldown_until` timestamp for LTM consolidation while student works on other dashboard tasks. <br/>**Tier 3 (2nd Failure at Identical $\text{KP}_j$):** Halts lesson (`TIER_3_REMEDIATION`) and injects a mandatory **Targeted Remedial Review** on that KP's `key_prerequisite_id` (peeling back to the full prerequisite lesson if the remedial review is failed). |
| **`FR-5.3`** | **P1** | **Micro-Interleaved Spaced Review Loop, Sequence Grading & "Spotter" Reference Mode** | `FR-4.3`, `FR-5.1` | Serves 3–5 micro-interleaved review questions with worked examples hidden. Evaluates exact answer sequence (`✖✔✖✔✔` passes; `✔✔✖✔✖` fails). Allows read-only "Spotter" Reference Mode only when the problem input is locked/closed before answering, logging `reference_peeked = true` and discounting `rawDelta`. |
| **`FR-5.4`** | **P1** | **Automaticity Latency Sensor ($\le 3.0\text{s}$) & Math Facts Fluency Intervention (`Skill A.6`)** | `FR-5.1`, `FR-5.3` | Verifies `duration_ms <= 3000` on `FLUENCY_FACT` nodes (`requires_automaticity == true`). Injects rapid-fire retrieval drills when high-accuracy responses plateau above the $3000\text{ ms}$ recalculation ceiling. |
| **`FR-5.5`** | **P1** | **Timed Interleaved Quizzes (80–85% Calibration), Post-Quiz Remediation & Retakes** | `FR-5.2`, `FR-5.3` | Schedules timed quizzes every ~100–150 XP across the entire mastered profile calibrated to an 80–85% expected score. Queues mandatory **Post-Quiz Remedial Reviews** on missed topics (with upward penalty flow) and unlocks a **Quiz Retake** once remedial reviews are completed. |
| **`FR-5.6`** | **P2** | **Multi-Part Synthesis Problems & Exam Test-Prep Mode** | `FR-5.5` | Supports multi-part applied synthesis problems and a toggleable **Test-Prep Mode** (pauses course promotion, brings 100% of course topics to peak freshness, serves timed practice exams every 500 XP, and places missed exam problems on a 1d $\to$ 3d $\to$ 7d spaced re-attempt schedule). |

---

### Tier 6: Constrained LLM Direct-Instruction & Process Coaching Layer (`trellis-pedagogy`) (`P1/P2 — Instructional Layer`)

| ID | Priority | Requirement Name | Depends On | Acceptance Criteria |
| :--- | :---: | :--- | :---: | :--- |
| **`FR-6.1`** | **P1** | **"I Do" Direct Instruction Generator & Validator (`Skill B.1`)** | `FR-1.3`, `FR-5.1` | Generates and deterministically validates `InstructionCard` payloads (`rule_statement` $\le 2$ sentences, $\le 4$ novel chunks, 1 subgoal-labeled `worked_example`, zero Socratic questions), caching verified cards in `kp_content_shells`. |
| **`FR-6.2`** | **P1** | **Non-Socratic Tier-1 Precision Remediation Engine (`Skill A.5 Tier 1`)** | `FR-5.2`, `FR-6.1` | Matches known `trap_answer_pattern` misconceptions in $<5\text{ ms}$ or invokes the constrained LLM with a verified `SymPy` trace to produce `{ violated_rule, identified_misstep, corrected_steps, boxed_answer }`. Strictly bans Socratic questions and hollow praise. |
| **`FR-6.3`** | **P2** | **John Wooden 5-to-1 In-Task Process Coach & Paper-and-Pencil Prompts (`Skill C.2`)** | `FR-5.1`, `FR-5.2` | Enforces 5 specific process-attributed confirmations per 1 corrective intervention, bans ability/person praise, never excuses arithmetic/sign errors as "silly mistakes," and prompts students to write intermediate steps on paper (*"When in doubt, write it out"*). |

---

### Tier 7: Gamification Economy, Anti-Cheat XP Hacker Defenses & Learning Efficiency (`P2 — Incentives & Integrity`)

| ID | Priority | Requirement Name | Depends On | Acceptance Criteria |
| :--- | :---: | :--- | :---: | :--- |
| **`FR-7.1`** | **P2** | **XP-Time Equivalence ($1\text{ XP} \approx 1\text{ Min}$) & 6-Tier Quality Scaling** | `FR-5.1`, `FR-5.3`, `FR-5.5` | Scales base XP (Lesson: 3–5 XP, Review: 2–4 XP, Diagnostic item: 1 XP) across 6 quality tiers: Bonus ($1.25\times$), Full ($1.0\times$), Most ($0.75\times$), Little ($0.25\times$), Zero ($0$), and Negative Penalty ($<0$). |
| **`FR-7.2`** | **P2** | **Anti-Loophole & "XP Hacker" Defense System** | `FR-7.1` | Deducts negative XP ($-2\text{ XP}$ escalating) for rapid random guessing / task blow-offs ($<20\%$ minimum expected reading/solving time), freezes timers/XP when latency $> 3 \times \text{median\_latency}$, and enforces re-attempt cooldown delays with freshly parameterized variants. |
| **`FR-7.3`** | **P2** | **Decoupled Course Progress % vs. XP & Learning Efficiency Projector** | `FR-7.1`, `FR-7.2` | Computes `progress_pct` strictly from mastered course nodes vs. total course nodes. Tracks `pace` (minimum $15\text{ XP/weekday}$, baseline $40\text{ XP/weekday}$, cap $60\text{–}90\text{ XP/day}$) and projects course completion using the $(\text{pace}/40)^{0.1}$ efficiency curve. |
| **`FR-7.4`** | **P3** | **Ability-Grouped Weekly Leagues & Promotion/Demotion** | `FR-7.3` | Groups opted-in students into ~20–30 learner weekly cohorts matched by historical pace/tier, promoting top performers and demoting bottom performers by net weekly XP. |

---

### Tier 8: Accountability Portal, Habit Formation & Content Remediation Analytics (`P3 — Coaching & Continuous Improvement`)

| ID | Priority | Requirement Name | Depends On | Acceptance Criteria |
| :--- | :---: | :--- | :---: | :--- |
| **`FR-8.1`** | **P3** | **Parent / Coach Accountability Portal & 5-Stage Scaffolded Independence Tracker** | `FR-7.2`, `FR-7.3` | Tracks student independence across 5 stages (`CO_SITTING` $\to$ `PER_TASK_CHECKIN` $\to$ `DAILY_CHECKIN` $\to$ `EVERY_FEW_DAYS_CHECKIN` $\to$ `WEEKLY_CHECKIN`), triggers immediate Adult Re-Sit Alerts on XP Hacker penalties or stalls, and binds custom parent rewards to XP targets. |
| **`FR-8.2`** | **P3** | **Contextual Habit Formation & Minimum Pace Monitor** | `FR-8.1` | Anchors daily study windows and warns learners when rolling pace drops below the $15\text{ XP/weekday}$ viability floor or exceeds the $90\text{ XP/day}$ burnout ceiling. |
| **`FR-8.3`** | **P2** | **Granular Content Remediation Analytics (`95% / 99%` Pass Bar) & Student Flagging** | `FR-5.2`, `FR-5.5` | Flags any Topic, KP (`#kp_j`), or Problem Blueprint where 1st-attempt pass rate $<95\%$ or 2-attempt pass rate $<99\%$, cross-checks lesson pass rates against subsequent unprimed Review/Quiz accuracy, and provides an in-task student question/explanation flag button. |
