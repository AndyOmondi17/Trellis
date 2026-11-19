# Trellis Pedagogy Prompt Contracts & Interfaces

## 1. Direct Instruction Request Contract (`GenerateInstructionRequest`)
```json
{
  "topic_name": "Evaluating Exponential Expressions with Negative Bases",
  "evidence": [
    "Distinguish between (-a)^n and -a^n",
    "Compute powers with negative integer bases correctly"
  ],
  "kp_index": 1,
  "kp_focus": "Even vs odd integer exponents on negative bases in parentheses",
  "difficulty_level": "novice"
}
```

## 2. Direct Instruction Response Structure (`InstructionCard`)
```json
{
  "rule_statement": "When a negative number inside parentheses is raised to an even power, the result is positive because negative pairs cancel. When raised to an odd power, one negative factor remains, resulting in a negative product.",
  "worked_example": {
    "problem": "Evaluate (-3)^4",
    "steps": [
      "Expand into repeated multiplication: (-3) * (-3) * (-3) * (-3)",
      "Multiply the first pair: (-3) * (-3) = 9",
      "Multiply the second pair: (-3) * (-3) = 9",
      "Multiply the products: 9 * 9 = 81"
    ],
    "final_answer": "81"
  }
}
```

## 3. Tier-1 Remediation Request Contract (`RemediationRequest`)
```json
{
  "topic_name": "Evaluating Exponential Expressions with Negative Bases",
  "evidence_rubric": "Distinguish between (-a)^n and -a^n",
  "problem": "Evaluate (-4)^3",
  "student_answer": "64",
  "correct_answer": "-64"
}
```

## 4. Tier-1 Remediation Response Structure (`RemediationCard`)
```json
{
  "violated_rule": "An odd exponent applied to a negative base leaves one unpaired negative sign, yielding a negative result.",
  "identified_misstep": "You calculated the magnitude 4^3 = 64 correctly, but omitted the negative sign resulting from three negative factors.",
  "corrected_steps": [
    "(-4)^3 = (-4) * (-4) * (-4)",
    "(-4) * (-4) = 16",
    "16 * (-4) = -64"
  ],
  "boxed_answer": "-64"
}
```