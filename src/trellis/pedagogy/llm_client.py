"""
src/trellis/pedagogy/llm_client.py
LLM Client port and implementations (Deterministic Mock & Live Gemini REST).
Ensures zero-network deterministic testing while enabling live Gemini 3.8 Flash execution.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from trellis.domain.models import CanonicalWorkedExample, CanonicalWorkedExampleStep
from trellis.pedagogy.models import (
    GenerateInstructionRequest,
    InstructionCardPayload,
    RemediationCardPayload,
    RemediationRequest,
)
from trellis.pedagogy.prompt_templates import (
    DIRECT_INSTRUCTION_SYSTEM_PROMPT,
    TIER1_REMEDIATION_SYSTEM_PROMPT,
    WOODEN_COACH_SYSTEM_PROMPT,
)


@runtime_checkable
class PedagogyLLMClient(Protocol):
    """Unified Hexagonal Port for pedagogical text and structured payload generation."""

    def generate_instruction_card(
        self, request: GenerateInstructionRequest
    ) -> InstructionCardPayload:
        """Generates an 'I Do' direct instruction card conforming to Catrambone subgoal steps."""
        ...

    def generate_remediation_card(
        self, request: RemediationRequest
    ) -> RemediationCardPayload:
        """Generates a non-Socratic Tier-1 corrective remediation breakdown."""
        ...

    def generate_process_confirmation(self, context: Dict[str, Any]) -> str:
        """Generates a process-grounded athletic coaching confirmation."""
        ...


# ---------------------------------------------------------------------------
# 1. Deterministic Mock LLM Client (Zero-Network Reference for Tests & CI)
# ---------------------------------------------------------------------------

class DeterministicMockLLMClient:
    """
    100% deterministic, offline mock provider for unit tests and parity benchmarks.
    Produces compliant payloads and supports fault-injection to test validators.
    """

    def __init__(
        self,
        inject_socratic_error: bool = False,
        inject_excess_sentences: bool = False,
        inject_excess_chunks: bool = False,
        inject_hollow_praise: bool = False,
        inject_slip_dismissal: bool = False,
    ):
        self.inject_socratic_error = inject_socratic_error
        self.inject_excess_sentences = inject_excess_sentences
        self.inject_excess_chunks = inject_excess_chunks
        self.inject_hollow_praise = inject_hollow_praise
        self.inject_slip_dismissal = inject_slip_dismissal
        self.call_history: List[Dict[str, Any]] = []

    def generate_instruction_card(
        self, request: GenerateInstructionRequest
    ) -> InstructionCardPayload:
        self.call_history.append({"method": "generate_instruction_card", "request": request})

        # Base rule statement (<= 2 sentences)
        if self.inject_socratic_error:
            rule_stmt = "What do you think happens when we add signed numbers? Can you guess the result?"
        elif self.inject_excess_sentences:
            rule_stmt = (
                "First, inspect the terms. Next, combine like terms together. "
                "Then, isolate the variable on one side. Finally, verify the solution."
            )
        else:
            rule_stmt = (
                f"When executing {request.kp_focus.lower()}, apply the standard operational property directly. "
                "Preserve all signs and factor equivalence across each intermediate transformation."
            )

        novel_chunks = 5 if self.inject_excess_chunks else min(request.novel_chunk_count, 4)

        worked_ex = CanonicalWorkedExample(
            problem=request.problem_stem or f"Evaluate standard problem for {request.topic_name}",
            subgoal_steps=[
                CanonicalWorkedExampleStep(
                    subgoal_label="Isolate active operation",
                    operation=f"Apply definition for {request.kp_focus}",
                    justification="Mathematical axiom definition",
                ),
                CanonicalWorkedExampleStep(
                    subgoal_label="Execute procedural transformation",
                    operation="Perform direct algebraic reduction",
                    justification="Standard arithmetic identity",
                ),
            ],
            final_answer="0",
        )

        return InstructionCardPayload(
            topic_id=request.topic_id,
            kp_id=request.kp_id,
            kp_index=request.kp_index,
            scaffolding_tier=request.scaffolding_tier,
            kp_focus=request.kp_focus,
            novel_chunk_count=novel_chunks,
            rule_statement=rule_stmt,
            worked_example=worked_ex,
            svg_asset_uri=None,
        )

    def generate_remediation_card(
        self, request: RemediationRequest
    ) -> RemediationCardPayload:
        self.call_history.append({"method": "generate_remediation_card", "request": request})

        if self.inject_socratic_error:
            violated_rule = "Why did you invert the sign when multiplying negative factors?"
            misstep = "Notice how the signs cancel. What should happen?"
        elif self.inject_slip_dismissal:
            violated_rule = "Standard operational rule for negative signs."
            misstep = "Don't worry, that was just a silly mistake on the sign."
        else:
            violated_rule = (
                f"When solving {request.kp_focus.lower()}, follow the exact procedural specification without sign inversion."
            )
            misstep = (
                f"Submitted answer '{request.student_answer}' differs from verified target '{request.correct_answer}' "
                "due to an incorrect operation applied during reduction."
            )

        steps = [
            f"1. Start with given problem: {request.problem_text}",
            f"2. Apply rule: {violated_rule}",
            f"3. Compute final value: {request.correct_answer}",
        ]

        return RemediationCardPayload(
            topic_id=request.topic_id,
            kp_id=request.kp_id,
            error_tag="ERR_PROCEDURAL_MISSTEP",
            violated_rule=violated_rule,
            identified_misstep=misstep,
            corrected_steps=steps,
            boxed_answer=request.correct_answer,
        )

    def generate_process_confirmation(self, context: Dict[str, Any]) -> str:
        self.call_history.append({"method": "generate_process_confirmation", "context": context})

        if self.inject_hollow_praise:
            return "Good job! You're a math genius!"

        topic_focus = context.get("kp_focus", "the operation")
        return f"You executed the procedural steps for {topic_focus.lower()} cleanly, maintaining sign accuracy."


# ---------------------------------------------------------------------------
# 2. Live Gemini REST Adapter (Production Engine)
# ---------------------------------------------------------------------------

class GeminiPedagogyLLMClient:
    """
    Live LLM provider communicating with Google Gemini API via standard HTTP REST.
    Requires GEMINI_API_KEY or GOOGLE_API_KEY environment variable.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        timeout_seconds: int = 15,
    ):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
        self.model_name = model_name or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.timeout_seconds = timeout_seconds

    def _call_gemini_api(self, system_instruction: str, user_prompt: str) -> str:
        if not self.api_key:
            raise ValueError(
                "Gemini API key is not configured. Set GEMINI_API_KEY or GOOGLE_API_KEY."
            )

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent"
            f"?key={self.api_key}"
        )

        payload = {
            "system_instruction": {
                "parts": [{"text": system_instruction}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_prompt}]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.0,
            },
        }

        req_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=req_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                resp_json = json.loads(resp.read().decode("utf-8"))
                candidates = resp_json.get("candidates", [])
                if not candidates:
                    raise RuntimeError("Gemini API returned no candidates.")
                text_content = candidates[0]["content"]["parts"][0]["text"]
                return text_content
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Gemini API HTTP {e.code} error: {err_body}") from e
        except Exception as e:
            raise RuntimeError(f"Failed to query Gemini API: {str(e)}") from e

    def generate_instruction_card(
        self, request: GenerateInstructionRequest
    ) -> InstructionCardPayload:
        system_prompt = DIRECT_INSTRUCTION_SYSTEM_PROMPT.format(
            topic_name=request.topic_name,
            evidence_list="; ".join(request.evidence_criteria) or "Standard procedural mastery",
            kp_index=request.kp_index,
            kp_focus=request.kp_focus,
            scaffolding_tier=request.scaffolding_tier,
            novel_chunk_count=request.novel_chunk_count,
        )

        user_prompt = json.dumps({
            "task": "GENERATE_INSTRUCTION_CARD",
            "topic_id": request.topic_id,
            "topic_name": request.topic_name,
            "kp_id": request.kp_id,
            "kp_index": request.kp_index,
            "kp_focus": request.kp_focus,
            "problem_stem": request.problem_stem,
            "parameters": request.parameters,
        })

        raw_response = self._call_gemini_api(system_prompt, user_prompt)
        parsed = json.loads(raw_response)

        worked_ex_data = parsed.get("worked_example", {})
        subgoal_steps = []
        for step_data in worked_ex_data.get("subgoal_steps", []):
            subgoal_steps.append(
                CanonicalWorkedExampleStep(
                    subgoal_label=step_data.get("subgoal_label", "Procedural step"),
                    operation=step_data.get("operation", ""),
                    justification=step_data.get("justification", ""),
                )
            )

        worked_ex = CanonicalWorkedExample(
            problem=worked_ex_data.get("problem", request.problem_stem or request.topic_name),
            subgoal_steps=subgoal_steps,
            final_answer=worked_ex_data.get("final_answer", ""),
        )

        return InstructionCardPayload(
            topic_id=request.topic_id,
            kp_id=request.kp_id,
            kp_index=request.kp_index,
            scaffolding_tier=request.scaffolding_tier,
            kp_focus=request.kp_focus,
            novel_chunk_count=request.novel_chunk_count,
            rule_statement=parsed.get("rule_statement", "").strip(),
            worked_example=worked_ex,
            svg_asset_uri=parsed.get("svg_asset_uri"),
        )

    def generate_remediation_card(
        self, request: RemediationRequest
    ) -> RemediationCardPayload:
        system_prompt = TIER1_REMEDIATION_SYSTEM_PROMPT.format(
            topic_name=request.topic_name,
            kp_focus=request.kp_focus,
            evidence_rubric=request.evidence_rubric,
            problem_text=request.problem_text,
            student_answer=request.student_answer,
            correct_answer=request.correct_answer,
            solver_trace="; ".join(request.solver_trace or []),
        )

        user_prompt = json.dumps({
            "task": "GENERATE_TIER1_REMEDIATION",
            "topic_id": request.topic_id,
            "kp_id": request.kp_id,
            "student_answer": request.student_answer,
            "correct_answer": request.correct_answer,
            "problem_text": request.problem_text,
        })

        raw_response = self._call_gemini_api(system_prompt, user_prompt)
        parsed = json.loads(raw_response)

        return RemediationCardPayload(
            topic_id=request.topic_id,
            kp_id=request.kp_id,
            error_tag=parsed.get("error_tag", "ERR_PROCEDURAL_MISSTEP"),
            violated_rule=parsed.get("violated_rule", "").strip(),
            identified_misstep=parsed.get("identified_misstep", "").strip(),
            corrected_steps=parsed.get("corrected_steps", []),
            boxed_answer=parsed.get("boxed_answer", request.correct_answer).strip(),
        )

    def generate_process_confirmation(self, context: Dict[str, Any]) -> str:
        system_prompt = WOODEN_COACH_SYSTEM_PROMPT
        user_prompt = json.dumps({
            "task": "GENERATE_PROCESS_CONFIRMATION",
            "context": context,
        })

        raw_response = self._call_gemini_api(system_prompt, user_prompt)
        try:
            parsed = json.loads(raw_response)
            return parsed.get("confirmation_message", raw_response).strip()
        except Exception:
            return raw_response.strip()
