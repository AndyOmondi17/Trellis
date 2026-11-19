# Trellis Security, Clean-Room Air-Gap & Integrity Policy (`SECURITY.md`)

## 1. Strict Clean-Room Intellectual Property Air-Gap

Trellis enforces a strict clean-room air-gap to ensure all curriculum graphs, schemas, problem blueprints, and engine implementations remain 100% original and unencumbered:

1. **Quarantined Legacy Reference Folders:**
   - The directories `algebrica/` and `os-taxonomy/` are **strictly off-limits** for reading, copying, importing, or runtime access by any application code, test suite, or AI agent.
   - All curriculum topology and problem specifications must originate exclusively from `data/clean_spec/trellis_knowledge_graph.json` (`schema_version: "2.0.0-trellis"`), validated by `schemas/knowledge_graph_schema.json` and `scripts/validate_math_dag.py`.
2. **Proprietary Identifier Namespace:**
   - All topic IDs must follow `^trl_[a-z0-9]{2,6}_[a-z0-9]{2,6}_[0-9]{3}$` and Knowledge Point IDs must follow `^trl_[a-z0-9]{2,6}_[a-z0-9]{2,6}_[0-9]{3}#kp_[0-3]$`. Legacy `mt_*` identifiers are banned from production code and schemas.

---

## 2. Deterministic Authority Boundary (Anti-Hallucination Guardrails)

Probabilistic Large Language Models (`trellis-pedagogy`) are strictly isolated from stateful authority (`trellis-engine`):

1. **Zero LLM State Mutation:** LLMs have zero write access to `TrellisStoragePort` state tables (`student_topic_states`, `xp_ledger_entries`, `dashboard_task_menus`).
2. **Symbolic Mathematical Verification (`SymPy`):** Every dynamically generated problem variant and every LLM-drafted worked example or Tier-1 remediation card must have its mathematical steps and final answer verified by `SymPy` (`content_blueprint_solver.py`) before being served to a student or cached in `kp_content_shells`.
3. **Deterministic Post-Processing Filter:** Any LLM instructional output containing question marks (`?` — Socratic probing), unverified equations, or person-level praise (*"You're so smart!"*) is rejected by the deterministic validator before reaching the client.

---

## 3. Anti-Cheat & "XP Hacker" Defenses (`FR-7.2`)

To preserve the integrity of Mastery Learning, XP Leaderboards, and Parent/Coach Accountability reporting, the backend enforces server-side telemetry validation:

1. **Rapid-Guessing / Task Blow-Off Penalty:**
   - Submissions arriving faster than $20\%$ of the KP's minimum expected reading/solving latency or repeated immediate incorrect submissions without reading Tier-1 remediation cards are classified as **XP Hacker Blow-Offs**.
   - Blow-offs incur **Negative XP Penalties** (starting at $-2\text{ XP}$ and escalating on consecutive offenses) and trigger an immediate **Adult Re-Sit Alert** in the Parent/Coach portal (`FR-8.1`).
2. **Idle & Multitasking Timeout Freeze:**
   - Whenever item latency exceeds $3 \times \text{median\_latency}$, task timers and XP accumulation freeze automatically.
3. **Server-Side Answer & Reference Mode Enforcement:**
   - Answer keys and `verification_solver_expr` strings are never sent to the client browser during active problem-solving ("You Do"), Reviews, Diagnostics, or Timed Quizzes.
   - Opening a lesson in "Spotter" Reference Mode during a review logs a server-side event (`reference_peeked = true`), requires closing the reference before unlocking the problem input, and discounts the resulting HSRS `rawDelta` (`FR-5.3`).
4. **Parameterized Variant Rotation & Cooldown:**
   - Failed lessons enforce a server-side `cooldown_until` consolidation timestamp (`FR-5.2` Tier 2) and re-parameterize all problem stems on subsequent attempts so memorized answers cannot be replayed.

---

## 4. Credential & Database Connection Safety

- Database credentials (`POSTGRES_DSN`, `NEO4J_URI`, `NEO4J_AUTH`, `LLM_API_KEY`) must be loaded strictly from environment variables (`.env` excluded from version control), never hardcoded in source files or committed to the repository.
- Parameterized queries (`$1` / `:param` in SQL and `$param` in Cypher) are mandatory across all `TrellisStoragePort` adapters to prevent SQL and Cypher injection.
