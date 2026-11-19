# Trellis Agent & Subagent Operational Directives (`AGENTS.md`)

## 1. Clean-Room Air-Gap Rule (CRITICAL)

- **NEVER read, list, search, or import from `algebrica/` or `os-taxonomy/`.** Those directories are quarantined legacy references.
- Always use the clean-room specification in `data/clean_spec/trellis_knowledge_graph.json`, `schemas/knowledge_graph_schema.json`, `scripts/validate_math_dag.py`, and `The Math Academy Way.md`.

---

## 2. Mutually Exclusive Dual-Backend Persistence Rule (`Tier 0`)

- **Single Unified Port (`TrellisStoragePort`):** Every domain service in `src/trellis/engine/`, `src/trellis/pedagogy/`, and `src/trellis/services/` must interact with persistence exclusively through `TrellisStoragePort` (`src/trellis/ports/storage_port.py`).
- **Zero Cross-Database Coordination:** Do not split queries across a Relational DB and a Graph DB simultaneously. At runtime, the system runs on **one standalone database backend** selected via `DB_BACKEND="relational" | "graph" | "memory"`.
  - **Adapter A (`StandaloneRelationalAdapter`):** Stores 100% of curriculum DAG + student state in a Relational DB.
  - **Adapter B (`StandaloneGraphAdapter`):** Stores 100% of curriculum DAG + student state in a Graph DB.
  - **Adapter C (`MemoryReferenceAdapter`):** Stores 100% of curriculum DAG + student state in memory for zero-setup unit tests.
- **Zero Raw Query Leakage:** Raw SQL and raw Cypher strings are strictly forbidden outside `src/trellis/adapters/db/`.

---

## 3. Domain Skill Activation Boundaries

When working on Trellis modules, activate and follow the repository's specialized skills in `.agents/skills/`:

1. **`trellis-engine` (`.agents/skills/trellis-engine/SKILL.md`):**
   - Activate whenever implementing or modifying `TrellisStoragePort` adapters, graph traversals, placement diagnostics, Knowledge Frontier gating, HSRS/FIRe propagation, Set-Cover review compression, 3-Tier remediation routing, automaticity latency sensors ($\le 3000\text{ ms}$), or XP calculations.
   - **Invariant:** All state transitions and calculations are 100% deterministic.
2. **`trellis-pedagogy` (`.agents/skills/trellis-pedagogy/SKILL.md`):**
   - Activate whenever implementing or modifying "I Do" `InstructionCard` generation, Catrambone subgoal labeling, dual-coding SVG integration, Tier-1 `RemediationCard` generation, or John Wooden 5-to-1 process coaching.
   - **Invariant:** Strictly direct instruction ("I Do, We Do, You Do"). Zero Socratic questioning for novices, zero unguided discovery, and zero person/ability praise.

---

## 4. Verification & Precedence Discipline

- Follow the topological tier ordering (`Tier 0` $\to$ `Tier 8`) defined in `PRD.md` and `ARCHITECTURE.md`. Never implement a higher-tier feature before its upstream `Depends On` requirements pass automated tests.
- Run `python scripts/validate_math_dag.py` whenever modifying graph schemas or ingestion scripts.
- Run `pytest tests/ -v` across parameterized `DB_BACKEND` fixtures (`relational`, `graph`, `memory`) to verify bit-identical mathematical parity across standalone database adapters.

---

## 5. GitHub Publishing & Clean-Room Exclusion Rule

- **Do NOT delete local files:** Never delete local directories or files (`os-taxonomy/`, `algebrica/`, or existing extraction scripts from the filesystem).
- **Git Exclusions (`.gitignore`):** Git must completely ignore external datasets, intermediate extraction scripts, local databases, and benchmark artifacts.
- **Zero Reference Leakage:** Zero references, strings, or identifiers related to legacy reference datasets may exist in any tracked source code or schema files pushed to GitHub.
