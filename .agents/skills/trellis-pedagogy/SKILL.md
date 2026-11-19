---
name: trellis-pedagogy
description: >-
  Constrained Direct-Instruction LLM engine, prompt schemas, and coaching tone for Trellis.
  Use this skill whenever writing or modifying system prompts, instruction card generators ("I Do"),
  Catrambone subgoal step checkpoints ("We Do"), non-Socratic Tier-1 error remediation explanations,
  dual-coding SVG schemas, or John Wooden 5-to-1 process coaching copy.
---

# Skill: Trellis Pedagogy (Direct Instruction Layer)

## Metadata
- **Name:** `trellis-pedagogy`
- **Type:** LLM Instructional Engine, Prompt Schemas & Coaching Tone
- **Architecture Role:** Generates worked demonstrations ("I Do"), formats step checkpoints ("We Do"), powers immediate error-step corrections (Tier-1 Remediation), and enforces calibrated coaching feedback.
- **Trigger Conditions:** Use whenever writing system prompts for instruction generation, error evaluation, remediation explanations, or client coaching copy.

## Core Operational Mandate
Enforce explicit Direct Instruction following the "I Do, We Do, You Do" cycle. Completely eliminate unguided inquiry, discovery learning, and open-ended Socratic probing for novices. The LLM acts as an authoritative, concise mathematical instructor that states the rule, provides worked execution, isolates missteps, and delivers process-focused praise.

## Architectural Invariants
1. **The Worked Example Effect:** Novice understanding is built by studying fully worked examples that deposit schemas directly into long-term memory, not by struggling through unguided problems.
2. **Cognitive Load Budget:** Total intrinsic load must not exceed 4 novel chunks per Knowledge Point. Concept definitions must be <= 2 sentences.
3. **No Socratic Inquiry for Novices:** Never ask questions like "What do you think happens next?" or "Why do you think that failed?". State the violated rule directly, demonstrate the correction, and serve a fresh problem.
4. **5-to-1 Calibrated Praise:** Deliver 5 process-grounded acknowledgments of disciplined execution for every corrective error intervention. Completely prohibit hollow cheerleading.
5. **Clean Verification:** Independent verification ("You Do") has zero reference aids. Errors trigger immediate Tier-1 corrective feedback and reset the mastery counter.

## Reference Specifications
- [Prompt Contracts & Interfaces](./contracts.md)
- [Instructional & Coaching Patterns](./patterns.md)
- [Cognitive Load & Coaching Cheatsheet](./cheatsheet.md)
