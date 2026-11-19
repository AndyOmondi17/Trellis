# Trellis Engine Data Contracts & Schemas (`2.0.0-trellis`)

## 1. Input Curriculum Contracts (from `data/clean_spec/trellis_knowledge_graph.json`)

### `TopicNode` & `KnowledgePointSpec`
```json
{
  "id": "trl_el3_frc_012",
  "title": "Adding Fractions with Unlike Denominators",
  "course_id": "CRS_ELEM_35",
  "canonical_domain": "02_fractions_decimals_and_ratios",
  "domain_cluster": "CRS_ELEM_35::frc",
  "skill_type": "PROCEDURAL",
  "bloom_cognitive_tier": "PROCEDURAL_EXECUTION",
  "grade_level_min": 4,
  "grade_level_max": 5,
  "is_core": true,
  "in_foundations_sequence": true,
  "requires_automaticity": false,
  "automaticity_Defining_latency_ms": null,
  "base_difficulty": 0.38,
  "estimated_xp": 4,
  "mastery_gate_n": 3,
  "key_prerequisite_id": "trl_el3_frc_008",
  "knowledge_points": [
    {
      "kp_index": 0,
      "kp_id": "trl_el3_frc_012#kp_0",
      "scaffolding_tier": "KP_1_BASELINE",
      "kp_focus": "Adding proper fractions where one denominator is a multiple of the other",
      "novel_chunk_count": 2,
      "key_prerequisite_id": "trl_el3_frc_008"
    },
    {
      "kp_index": 1,
      "kp_id": "trl_el3_frc_012#kp_1",
      "scaffolding_tier": "KP_2_SIGNED_RATIONAL",
      "kp_focus": "Adding fractions with coprime denominators requiring least common multiple",
      "novel_chunk_count": 3,
      "key_prerequisite_id": "trl_el3_frc_008"
    }
  ],
  "prerequisites": [
    {
      "topic_id": "trl_el3_frc_008",
      "strength": "hard",
      "rationale": "Finding equivalent fractions with a common denominator is required before summing numerators."
    }
  ],
  "encompassed_skills": [
    {
      "topic_id": "trl_el3_frc_008",
      "fire_weight": 0.5,
      "is_non_ancestor": false,
      "rationale": "Every unlike-denominator fraction addition exercises equivalent fraction conversion."
    }
  ]
}
```

---

## 2. Student State Records (Persisted via Unified `TrellisStoragePort`)

### `StudentTopicState`
```json
{
  "student_id": "usr_991823",
  "topic_id": "trl_el3_frc_012",
  "status": "IN_PROGRESS",
  "rep_num": 1.0,
  "stability_interval_days": 1.5,
  "memory_strength": 0.72,
  "last_reviewed_at": "2026-10-19T08:00:00Z",
  "due_review_at": "2026-10-20T20:00:00Z",
  "cumulative_fire_credit": 0.5,
  "student_ability": 0.65,
  "topic_difficulty": 0.38,
  "speed": 1.71,
  "allow_implicit_fire_credit": true,
  "diagnostic_plus_minus_balance": 1.25,
  "is_conditionally_completed": false,
  "current_kp_index": 1,
  "consecutive_correct": 2,
  "kp_failure_counts": {
    "0": 0,
    "1": 1
  },
  "cooldown_until": null
}
```

### `StudentProfile`
```json
{
  "student_id": "usr_991823",
  "enrolled_course_id": "CRS_PREALG",
  "xp_total": 420,
  "progress_pct": 14.8,
  "daily_xp_target": 40,
  "rolling_pace_xp": 38.5,
  "independence_stage": "DAILY_CHECKIN",
  "process_praise_counter": 3,
  "rolling_latency_ms": {
    "math_facts": 1820
  }
}
```

---

## 3. Sensor & API Payloads

### Submission Sensor Payload (`POST /api/session/submit`)
```json
{
  "student_id": "usr_991823",
  "topic_id": "trl_el3_frc_012",
  "kp_id": "trl_el3_frc_012#kp_1",
  "kp_index": 1,
  "problem_id": "prob_trl_el3_frc_012_kp1_042",
  "student_answer": "11/15",
  "duration_ms": 2480,
  "reference_peeked": false,
  "submission_flag": "ANSWER_SUBMITTED"
}
```

### Evaluation Response Payload
```json
{
  "correct": true,
  "consecutive_correct": 3,
  "kp_completed": true,
  "node_mastered": true,
  "xp_awarded": 4,
  "quality_tier": "FULL_XP",
  "tier_remediation": null,
  "remedial_target_topic_id": null,
  "next_action": "SERVE_NEXT_TASK"
}
```
