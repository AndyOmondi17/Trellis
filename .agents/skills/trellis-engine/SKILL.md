---
name: trellis-engine
description: >-
  Deterministic backend logic and mathematical state machine for Trellis.
  Use this skill whenever generating or modifying TrellisStoragePort database schemas/adapters,
  graph traversal algorithms, placement diagnostics, Knowledge Frontier gating, HSRS/FIRe
  spaced repetition propagation, Set-Cover review compression, 3-Tier remediation routing,
  automaticity latency sensors (<= 3.0s), or XP scoring services.
---

# Skill: Trellis Engine (Deterministic Core)

## Metadata
- **Name:** `trellis-engine`
- **Type:** Deterministic Backend Logic & State Machine
- **Architecture Role:** Graph traversal, student state transitions, spaced repetition scheduling, automaticity validation, diagnostic evaluation, and effort accounting.
- **Trigger Conditions:** Use whenever generating or modifying database schemas, graph algorithms, session queue generators, mastery validators, diagnostic search routines, or scoring services.

## Core Operational Mandate
All learning state transitions, review intervals, queue sequences, and credit awards are deterministic mathematical algorithms. Probabilistic Large Language Models (LLMs) must never be permitted to determine mastery, adjust student status, sequence learning queues, or award points.

## Architectural Invariants
1. **Prerequisite Gating:** A topic node transitions from `LOCKED` to `AVAILABLE` strictly when all `hard` prerequisite parents evaluate to `MASTERED`.
2. **Mastery Gate:** A topic or Knowledge Point flips to `MASTERED` strictly upon achieving N = 3 consecutive unassisted correct answers.
3. **FIRe Credit Propagation:** Passing an advanced node issues Fractional Implicit Repetition (FIRe) credit backward to direct ancestors (default w = 0.5 for `hard` edges).
4. **Spaced Repetition Compression:** Due review tasks must be compressed into the minimal set of encompassing topics, constructing a daily session queue targeting a 70% frontier / 30% review ratio.
5. **Tier-3 Foundational Fallback:** If a student fails twice at the identical Knowledge Point (KP_j), the lesson is halted and a remedial review is injected for that point's `key_prerequisite_id`.
6. **Automaticity Evaluation:** Retrieval latency on atomic fact items must be verified against an upper ceiling of <= 3.0 seconds.
7. **Interleaving Scheduling:** Active practice items must alternate across dissimilar mathematical domain clusters to prevent blocked practice.
8. **Dual-Metric Economy:** Curriculum Progress % (mastered nodes / total nodes) is completely decoupled from Monotonic XP (1 XP ~ 1 minute of focused work).

## Reference Specifications
- [Data Contracts & Schemas](./contracts.md)
- [Algorithmic Patterns](./patterns.md)
- [Quantitative Thresholds Cheatsheet](./cheatsheet.md)
