# Trellis Engine Cheatsheet & Mathematical Constants

## Algorithmic Constants
- **Consecutive Mastery Threshold (N):** 3 consecutive unassisted correct answers.
- **Session Queue Ratio & Dashboard Split:** 70% Knowledge Frontier learning tasks, 30% Compressed Spaced Reviews (e.g. 4 lessons / 2 reviews for capacity 6).
- **Review-Tsunami Hard Floor:** 25% minimum lesson floor during review waves (e.g. max 4 reviews / min 2 lessons for capacity 6).
- **Look-Ahead Fending Interval:** Upcoming reviews with memory strength in $(0.50, 0.70]$.
- **Composite Importance Weights:** w_knockout = 10.0, w_fending = 3.0, w_enrolled = 2.0, w_urgency = 2.0, w_dependents = 1.0, w_core = 1.0.
- **Candidate Kind Priority:** FRONTIER_LESSON (2) > DUE_REVIEW (1) > MASTERED_REVIEW (0).
- **Default FIRe Weights (w):**
  - `hard` dependency: w = 0.5.
  - `soft` dependency: w = 0.2.
  - Maximum FIRe propagation depth: 2 hops backward along ancestor paths.
- **Memory Stability Trigger:** Cumulative credit delta_C >= 1.0 triggers interval expansion I_{k+1} = I_k * SM.
- **Automaticity Latency Ceiling:** <= 3000 ms (3.0 seconds) per atomic fact problem.
- **Anti-Cheat Idle Timeout:** Session time and XP accumulation freeze if an item interaction interval exceeds 3 * median_latency.
- **Daily Practice Volume Ceiling:** 60 to 90 minutes (60 to 90 XP) maximum recommended deliberate practice per day.

## XP Economy Calibration
- **Equivalence:** 1 XP ~ 1 minute of focused cognitive effort.
- **New Frontier Lesson:** Award 3 to 5 XP upon full node completion.
- **Spaced Review Task:** Award 2 to 4 XP upon clean completion.
- **Diagnostic / Quiz Verification:** 1 XP per retrieved item verified.
- **Careless Guessing Penalty:** Reset consecutive correct counter to 0 and serve fresh randomized variant.

## Node State Machine
```
   [LOCKED]
      |
      | All hard parents == MASTERED
      v
 [AVAILABLE]  <-- Knowledge Frontier (F)
      |
      | Student starts lesson
      v
[IN_PROGRESS]
      |
      +-- N=3 unassisted correct --> [MASTERED]
      |                                   |
      +-- 2 failures at same KP           | Interval decay (HSRS)
              |                           v
              v                     [DUE_FOR_REVIEW]
      [TIER_3_REMEDIATION]
```