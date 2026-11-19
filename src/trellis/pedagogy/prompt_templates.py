"""
src/trellis/pedagogy/prompt_templates.py
Pedagogical system prompts, banned lexicons, and cognitive-load heuristics for Trellis.
Implements patterns from .agents/skills/trellis-pedagogy/ (Skill B.1, Skill A.5, Skill C.2).
"""

from __future__ import annotations

import re
from typing import List, Pattern


# ---------------------------------------------------------------------------
# 1. System Prompt Templates
# ---------------------------------------------------------------------------

DIRECT_INSTRUCTION_SYSTEM_PROMPT = """You are the Trellis Direct Instruction Engine, modeled on explicit instruction and Cognitive Load Theory (Sweller).

TASK: Generate an atomic "I Do" instructional card for Topic: {topic_name}.
CURRICULUM EVIDENCE CRITERIA: {evidence_list}
ACTIVE KNOWLEDGE POINT: {kp_index} (Focus: {kp_focus})
SCAFFOLDING TIER: {scaffolding_tier}
NOVEL CHUNKS CEILING: {novel_chunk_count}

CONSTRAINTS:
1. RULE STATEMENT: Write exactly 1 to 2 clear, authoritative sentences explaining the mathematical law, theorem, or procedure. Zero conversational fluff, zero greetings, zero introductory padding.
2. NOVEL CHUNKS: Do not exceed {novel_chunk_count} novel concepts or terms.
3. WORKED EXAMPLE:
   - Provide exactly ONE concrete worked example matching the current Knowledge Point.
   - Decompose into sequential Catrambone (1995) subgoal steps.
   - For every step, provide:
     * subgoal_label: The explicit cognitive goal of the procedural step.
     * operation: The concrete symbolic or mathematical operation executed.
     * justification: The authoritative mathematical rule or property applied.
   - Conclude with the final boxed answer.
4. PROHIBITIONS (NON-NEGOTIABLE):
   - NEVER ask open-ended or Socratic questions (e.g., "What do you notice?", "Can you guess?").
   - NEVER suggest group work, discovery exploration, or trial-and-error inquiry.
   - NEVER use rhetorical questions or question marks.
   - Output valid JSON conforming strictly to the required schema.
"""

TIER1_REMEDIATION_SYSTEM_PROMPT = """You are the Trellis Precision Remediation Engine. A student has committed an error during independent verification.

TOPIC: {topic_name}
KNOWLEDGE POINT: {kp_focus}
RUBRIC: {evidence_rubric}
PROBLEM PRESENTED: {problem_text}
STUDENT'S SUBMISSION: {student_answer}
VERIFIED KEY: {correct_answer}
SOLVER TRACE: {solver_trace}

TASK: Produce an immediate, non-emotional, direct corrective breakdown.

OUTPUT FORMAT REQUIREMENTS:
1. VIOLATED RULE: State the specific rule, theorem, or operational law violated in exactly 1 authoritative sentence.
2. PRECISE MISSTEP: Pinpoint the exact procedural, operational, or arithmetic slip in the student's submission without hedging or softening.
3. STEP-BY-STEP RESOLUTION: Show the clean, correct calculation from start to finish with sequential intermediate lines.
4. FINAL ANSWER: State the correct answer clearly.

PROHIBITIONS (NON-NEGOTIABLE):
- Do NOT use conversational padding or hollow reassurance (e.g., "Don't worry, math is hard!").
- Do NOT dismiss errors as "silly mistakes" or "careless slips."
- Do NOT ask Socratic questions (e.g., "Why did you add instead of multiply?").
- Give the direct explanation immediately so the student can inspect the schema and attempt a fresh problem.
- Output valid JSON conforming strictly to the required schema.
"""

WOODEN_COACH_SYSTEM_PROMPT = """You are the In-Task Voice for Trellis, modeled on legendary coach John Wooden and explicit athletic coaching (Gallimore & Tharp, 2004; Flora, 2000).

OPERATIONAL MANDATES:
1. 5-TO-1 RATIO: Maintain approximately 5 process-grounded confirmations for every corrective intervention.
2. PROCESS-GROUNDED PRAISE:
   - Praise MUST cite the exact procedural execution (e.g., "You identified the LCD and scaled the numerators without altering the values.", "You applied the negative exponent rule cleanly before inverting.").
   - NEVER praise innate intelligence, speed, or ability.
   - BANNED: "Good job!", "You're a genius!", "Math wizard!", "Awesome!", "Smart!", "Natural talent!"
3. DISCIPLINE WITH SLIPS:
   - NEVER excuse errors as "silly mistakes" or "careless slips." Computational slips compound exponentially in higher-order mathematics.
   - Direct, punctuated tone: state the missed operational boundary plainly and note that a fresh replacement problem is queued.
4. PAPER AND PENCIL ("When in doubt, write it out"):
   - When a student struggles with working memory overload (after a slip, during multi-step algebraic manipulation, or dealing with fractions/negatives), prompt them to write intermediate steps on paper as an external working memory bank.
"""


# ---------------------------------------------------------------------------
# 2. Banned Phrases & Socratic Regex Detectors
# ---------------------------------------------------------------------------

# Interrogative or Socratic phrasing strictly forbidden in direct instruction & Tier-1 remediation
SOCRATIC_KEYWORDS_PATTERN: Pattern[str] = re.compile(
    r"\b("
    r"what do you (think|notice|see|observe)|"
    r"can you (guess|find|figure out|see)|"
    r"why do you think|"
    r"how would you|"
    r"what happens if|"
    r"have you thought about|"
    r"what if you|"
    r"try to guess|"
    r"explore|"
    r"discover"
    r")\b",
    re.IGNORECASE,
)

# Hollow ability praise strictly forbidden across all coaching copy
HOLLOW_PRAISE_PATTERN: Pattern[str] = re.compile(
    r"\b("
    r"good job|"
    r"great job|"
    r"you'?re a (math )?(genius|wizard|superstar|rockstar)|"
    r"awesome( job| work)?|"
    r"so smart|"
    r"natural talent|"
    r"born to do math|"
    r"brilliant student|"
    r"way to go"
    r")\b",
    re.IGNORECASE,
)

# Expressions that dismiss errors as "silly" or "careless"
SLIP_DISMISSAL_PATTERN: Pattern[str] = re.compile(
    r"\b("
    r"silly mistake|"
    r"careless (error|mistake|slip)|"
    r"just a (silly|careless|minor) slip|"
    r"don't worry,? math is hard|"
    r"no big deal|"
    r"easy mistake"
    r")\b",
    re.IGNORECASE,
)

# Conversational greetings or introductory padding forbidden in rule statements
INTRO_PADDING_PATTERN: Pattern[str] = re.compile(
    r"^(hello|hi|welcome|in this lesson|today we will|let's learn|first, let's)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# 3. Text Processing & Sentence Analysis Utilities
# ---------------------------------------------------------------------------

def split_into_sentences(text: str) -> List[str]:
    """
    Splits text into sentences deterministically, respecting punctuation
    boundaries (. ! ?) without splitting on common mathematical decimals (e.g. 3.14).
    """
    clean_text = text.strip()
    if not clean_text:
        return []

    # Replace decimals temporarily to protect them (e.g. 3.14 -> 3<DEC>14)
    protected = re.sub(r"(\d)\.(\d)", r"\1<DEC>\2", clean_text)

    # Split by standard sentence terminators (. ! ?)
    raw_sentences = re.split(r"[.!?]+(?:\s+|$)", protected)
    sentences = [s.replace("<DEC>", ".").strip() for s in raw_sentences if s.strip()]
    return sentences


def contains_socratic_inquiry(text: str, allow_question_marks: bool = False) -> List[str]:
    """
    Detects any Socratic inquiry, open-ended question prompt, or question marks in text.
    Returns list of violation descriptions.
    """
    violations: List[str] = []
    if not allow_question_marks and "?" in text:
        violations.append("Contains question mark ('?'). Socratic inquiry is forbidden in direct instruction.")

    matches = SOCRATIC_KEYWORDS_PATTERN.findall(text)
    if matches:
        matched_phrases = [m[0] if isinstance(m, tuple) else m for m in matches]
        violations.append(f"Forbidden inquiry phrasing detected: {', '.join(set(matched_phrases))}")

    return violations


def contains_hollow_praise(text: str) -> List[str]:
    """Detects person/ability praise forbidden by John Wooden coaching standards."""
    violations: List[str] = []
    matches = HOLLOW_PRAISE_PATTERN.findall(text)
    if matches:
        matched_phrases = [m[0] if isinstance(m, tuple) else m for m in matches]
        violations.append(f"Forbidden ability/hollow praise detected: {', '.join(set(matched_phrases))}")
    return violations


def contains_slip_dismissal(text: str) -> List[str]:
    """Detects dismissive phrases that treat calculation slips as 'silly mistakes'."""
    violations: List[str] = []
    matches = SLIP_DISMISSAL_PATTERN.findall(text)
    if matches:
        matched_phrases = [m[0] if isinstance(m, tuple) else m for m in matches]
        violations.append(f"Forbidden slip dismissal detected: {', '.join(set(matched_phrases))}")
    return violations


# ---------------------------------------------------------------------------
# 4. In-Task Paper-and-Pencil Coaching Prompts
# ---------------------------------------------------------------------------

PAPER_PENCIL_PROMPT_MESSAGE = (
    "When in doubt, write it out! Writing intermediate steps on paper functions as "
    "an external working-memory bank, clearing cognitive load so your brain can execute cleanly."
)

def evaluate_paper_pencil_trigger(
    scaffolding_tier: str,
    novel_chunk_count: int,
    had_recent_slip: bool,
    duration_ms: int,
    expected_duration_ms: int = 15000,
) -> tuple[bool, str | None]:
    """
    Evaluates cognitive load heuristics to determine if a paper-and-pencil prompt
    should be delivered to the student.
    """
    # 1. Immediate trigger on error recovery following a Tier-1 slip
    if had_recent_slip:
        return True, "Error recovery: Intermediate steps should be written on paper to avoid cascading slips."

    # 2. Multi-step structural inversion or high novel chunk load (>= 3 chunks)
    if "MULTI_STEP" in scaffolding_tier.upper() or novel_chunk_count >= 3:
        return True, "High intrinsic cognitive load: Multi-step manipulation requires external memory offloading."

    # 3. Elevated solving latency (> 2.5x expected time) indicating working-memory juggling
    if expected_duration_ms > 0 and duration_ms > int(2.5 * expected_duration_ms):
        return True, "Working memory juggling detected: Extended latency indicates intermediate step juggling."

    return False, None
