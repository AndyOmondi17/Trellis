"""
src/trellis/pedagogy/instruction_coach_service.py
Tier 6: Constrained LLM Direct-Instruction & Process Coaching Layer (trellis-pedagogy).
Implements FR-6.1 ("I Do" Generator & Validator), FR-6.2 (Non-Socratic Tier-1 Precision Remediation),
and FR-6.3 (John Wooden 5-to-1 Process Coach & Paper-and-Pencil Prompts).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from trellis.domain.models import (
    CanonicalWorkedExample,
    CanonicalWorkedExampleStep,
    KPContentShell,
    KnowledgePoint,
    TopicNode,
)
from trellis.engine.content_blueprint_solver import ContentBlueprintSolver
from trellis.pedagogy.llm_client import (
    DeterministicMockLLMClient,
    PedagogyLLMClient,
)
from trellis.pedagogy.models import (
    CoachStudentState,
    CoachingFeedback,
    CoachingFeedbackType,
    GenerateInstructionRequest,
    InstructionCardPayload,
    InstructionCardValidationResult,
    RemediationCardPayload,
    RemediationCardValidationResult,
    RemediationRequest,
)
from trellis.pedagogy.prompt_templates import (
    PAPER_PENCIL_PROMPT_MESSAGE,
    contains_hollow_praise,
    contains_slip_dismissal,
    contains_socratic_inquiry,
    evaluate_paper_pencil_trigger,
    split_into_sentences,
)
from trellis.ports.storage_port import TrellisStoragePort


class InstructionCoachService:
    """
    Pedagogical orchestration service for Trellis.
    Enforces Cognitive Load Theory constraints, Direct Instruction schemas,
    immediate Tier-1 misstep resolution, and John Wooden 5-to-1 process praise.
    """

    def __init__(
        self,
        storage: TrellisStoragePort,
        solver: ContentBlueprintSolver,
        llm_client: Optional[PedagogyLLMClient] = None,
    ):
        self.storage = storage
        self.solver = solver
        self.llm_client: PedagogyLLMClient = llm_client or DeterministicMockLLMClient()
        self._student_coaching_states: Dict[str, CoachStudentState] = {}

    def get_or_create_coach_state(self, student_id: str) -> CoachStudentState:
        """Retrieves or initializes the student's in-task coaching state."""
        if student_id not in self._student_coaching_states:
            try:
                prof = self.storage.get_student_profile(student_id)
                init_praise = prof.process_praise_counter
            except Exception:
                init_praise = 0
            self._student_coaching_states[student_id] = CoachStudentState(
                student_id=student_id,
                confirmations_count=init_praise,
                corrections_count=0,
            )
        return self._student_coaching_states[student_id]

    # =======================================================================
    # FR-6.1: "I Do" Direct Instruction Generator & Validator (Skill B.1)
    # =======================================================================

    def generate_instruction_card(
        self,
        kp_id: str,
        force_refresh: bool = False,
        svg_asset_uri: Optional[str] = None,
    ) -> InstructionCardPayload:
        """
        Generates and deterministically validates an 'I Do' Direct Instruction card.
        Caches validated cards in KPContentShell within TrellisStoragePort, advancing
        status BLUEPRINT_READY -> LLM_DRAFTED.
        """
        shell = self.storage.get_kp_content_shell(kp_id)
        topic_id = kp_id.split("#")[0]
        node = self.storage.get_topic_node(topic_id)

        # Locate active KnowledgePoint
        kp: Optional[KnowledgePoint] = None
        for pt in node.knowledge_points:
            if pt.kp_id == kp_id:
                kp = pt
                break
        if kp is None:
            raise KeyError(f"KnowledgePoint '{kp_id}' not found on TopicNode '{topic_id}'.")

        # Check existing cached card
        if not force_refresh and shell.rule_statement and shell.worked_example:
            candidate_card = InstructionCardPayload(
                topic_id=topic_id,
                kp_id=kp_id,
                kp_index=kp.kp_index,
                scaffolding_tier=kp.scaffolding_tier,
                kp_focus=kp.kp_focus,
                novel_chunk_count=kp.novel_chunk_count,
                rule_statement=shell.rule_statement,
                worked_example=shell.worked_example,
                svg_asset_uri=svg_asset_uri or shell.svg_asset_uri,
            )
            val_res = self.validate_instruction_card(candidate_card)
            if val_res.is_valid:
                return candidate_card

        # Construct generation request
        req = GenerateInstructionRequest(
            topic_id=topic_id,
            topic_name=node.title,
            evidence_criteria=node.mastery_criteria,
            kp_index=kp.kp_index,
            kp_id=kp_id,
            kp_focus=kp.kp_focus,
            scaffolding_tier=kp.scaffolding_tier,
            novel_chunk_count=kp.novel_chunk_count,
            problem_stem=shell.problem_blueprint.stem_template if shell.problem_blueprint else None,
        )

        card = self.llm_client.generate_instruction_card(req)
        if svg_asset_uri or shell.svg_asset_uri:
            card = card.model_copy(update={"svg_asset_uri": svg_asset_uri or shell.svg_asset_uri})

        # Deterministic Validation
        val_res = self.validate_instruction_card(card)
        if not val_res.is_valid:
            raise ValueError(
                f"Generated InstructionCard for '{kp_id}' failed deterministic validation: "
                f"{'; '.join(val_res.errors)}"
            )

        # Cache verified card in storage port and advance lifecycle
        updated_status = "LLM_DRAFTED" if shell.content_status == "BLUEPRINT_READY" else shell.content_status
        updated_shell = shell.model_copy(
            update={
                "rule_statement": card.rule_statement,
                "worked_example": card.worked_example,
                "svg_asset_uri": card.svg_asset_uri,
                "content_status": updated_status,
            }
        )
        self.storage.upsert_kp_content_shell(updated_shell)

        return card

    def validate_instruction_card(
        self, card: InstructionCardPayload
    ) -> InstructionCardValidationResult:
        """
        Deterministically verifies all Cognitive Load Theory and Direct Instruction invariants:
          1. Rule statement sentence count <= 2 and >= 1.
          2. Novel cognitive chunk ceiling <= 4.
          3. Exactly 1 worked example with >= 1 Catrambone subgoal step and non-empty final answer.
          4. Zero Socratic inquiries or open-ended questions.
          5. Zero hollow praise or dismissive language.
        """
        errors: List[str] = []
        socratic_violations: List[str] = []
        banned_violations: List[str] = []

        # 1. Rule statement sentence count
        sentences = split_into_sentences(card.rule_statement)
        rule_sentence_count = len(sentences)
        if rule_sentence_count < 1:
            errors.append("Rule statement is empty; must contain at least 1 authoritative sentence.")
        elif rule_sentence_count > 2:
            errors.append(
                f"Rule statement exceeds 2-sentence ceiling ({rule_sentence_count} sentences detected). "
                "Working memory capacity is violated."
            )

        # 2. Novel chunks budget
        if card.novel_chunk_count > 4:
            errors.append(
                f"Novel chunk count ({card.novel_chunk_count}) exceeds cognitive load limit of 4 chunks."
            )

        # 3. Socratic inquiry scan
        rule_soc = contains_socratic_inquiry(card.rule_statement, allow_question_marks=False)
        socratic_violations.extend(rule_soc)

        # 4. Worked example completeness & Catrambone subgoal steps
        has_worked_ex = card.worked_example is not None
        subgoal_step_count = 0
        if not has_worked_ex:
            errors.append("InstructionCard is missing required CanonicalWorkedExample ('I Do').")
        else:
            ex = card.worked_example
            assert ex is not None
            if not ex.problem or not ex.problem.strip():
                errors.append("Worked example problem stem is empty.")
            if not ex.final_answer or not ex.final_answer.strip():
                errors.append("Worked example final boxed answer is empty.")

            subgoal_step_count = len(ex.subgoal_steps)
            if subgoal_step_count == 0:
                errors.append("Worked example must contain at least 1 Catrambone subgoal step.")

            for i, step in enumerate(ex.subgoal_steps):
                if not step.subgoal_label or not step.subgoal_label.strip():
                    errors.append(f"Subgoal step {i+1} is missing a subgoal_label.")
                if not step.operation or not step.operation.strip():
                    errors.append(f"Subgoal step {i+1} is missing a concrete operation.")
                if not step.justification or not step.justification.strip():
                    errors.append(f"Subgoal step {i+1} is missing a mathematical justification.")

                step_soc = contains_socratic_inquiry(
                    f"{step.subgoal_label} {step.operation} {step.justification}"
                )
                socratic_violations.extend(step_soc)

        # 5. Banned hollow praise & dismissals
        banned_praise = contains_hollow_praise(card.rule_statement)
        banned_dismiss = contains_slip_dismissal(card.rule_statement)
        banned_violations.extend(banned_praise + banned_dismiss)

        errors.extend(socratic_violations)
        errors.extend(banned_violations)

        return InstructionCardValidationResult(
            is_valid=len(errors) == 0,
            rule_sentence_count=rule_sentence_count,
            novel_chunk_count=card.novel_chunk_count,
            has_canonical_worked_example=has_worked_ex,
            subgoal_step_count=subgoal_step_count,
            socratic_violations=socratic_violations,
            banned_phrase_violations=banned_violations,
            errors=errors,
        )

    # =======================================================================
    # FR-6.2: Non-Socratic Tier-1 Precision Remediation Engine (Skill A.5)
    # =======================================================================

    def generate_tier1_remediation(
        self,
        topic_id: str,
        kp_id: str,
        student_answer: str,
        target_answer: str,
        problem_stem: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> RemediationCardPayload:
        """
        Produces an immediate, non-emotional, direct corrective breakdown for a 1st error.
        Fast-path (<5 ms): Matches pre-cataloged diagnostic misconception from blueprint.
        Fallback path: Computes verified SymPy step trace and queries constrained LLM.
        """
        shell = self.storage.get_kp_content_shell(kp_id)
        node = self.storage.get_topic_node(topic_id)

        # Locate KnowledgePoint
        kp: Optional[KnowledgePoint] = None
        for pt in node.knowledge_points:
            if pt.kp_id == kp_id:
                kp = pt
                break
        if kp is None:
            raise KeyError(f"KnowledgePoint '{kp_id}' not found.")

        # --- Fast Path: Blueprint Diagnostic Misconception (<5 ms) ---
        all_misconceptions = (shell.diagnostic_misconceptions or []) + (kp.diagnostic_misconceptions or [])
        matched = self.solver.match_diagnostic_misconception(
            student_input=student_answer,
            misconceptions=all_misconceptions,
            parameters=parameters,
        )

        if matched:
            card = RemediationCardPayload(
                topic_id=topic_id,
                kp_id=kp_id,
                error_tag=matched.error_tag,
                violated_rule=matched.violated_rule,
                identified_misstep=matched.tier1_remediation_template,
                corrected_steps=[
                    f"1. Target operational rule: {matched.violated_rule}",
                    f"2. Precise procedural correction: {matched.tier1_remediation_template}",
                    f"3. Verified boxed answer: {target_answer}",
                ],
                boxed_answer=target_answer,
            )
            val_res = self.validate_remediation_card(card)
            if val_res.is_valid:
                return card

        # --- Fallback Path: Verified Step Trace + Constrained Formatter ---
        solver_trace = [
            f"Given expression: {problem_stem}",
            f"Operational focus: {kp.kp_focus}",
            f"Verified target calculation: {target_answer}",
        ]

        req = RemediationRequest(
            topic_id=topic_id,
            topic_name=node.title,
            kp_id=kp_id,
            kp_focus=kp.kp_focus,
            evidence_rubric="; ".join(node.mastery_criteria) or "Standard procedural accuracy",
            problem_text=problem_stem,
            student_answer=student_answer,
            correct_answer=target_answer,
            solver_trace=solver_trace,
            parameters=parameters,
        )

        card = self.llm_client.generate_remediation_card(req)

        val_res = self.validate_remediation_card(card)
        if not val_res.is_valid:
            # Deterministic fallback guaranteed to pass validation
            card = RemediationCardPayload(
                topic_id=topic_id,
                kp_id=kp_id,
                error_tag="ERR_PROCEDURAL_MISSTEP",
                violated_rule=shell.rule_statement or kp.rule_statement or f"Follow standard procedure for {kp.kp_focus}.",
                identified_misstep=f"Submitted answer '{student_answer}' differs from verified target '{target_answer}'.",
                corrected_steps=[
                    f"1. Inspect problem: {problem_stem}",
                    f"2. Apply rule: {shell.rule_statement or kp.rule_statement or kp.kp_focus}",
                    f"3. Final boxed answer: {target_answer}",
                ],
                boxed_answer=target_answer,
            )

        return card

    def validate_remediation_card(
        self, card: RemediationCardPayload
    ) -> RemediationCardValidationResult:
        """
        Deterministically verifies that a Tier-1 RemediationCard contains zero Socratic
        questions, zero hollow praise, zero dismissals of slips, and complete resolution steps.
        """
        errors: List[str] = []
        socratic_violations: List[str] = []
        hollow_violations: List[str] = []
        dismissal_violations: List[str] = []

        # 1. Required fields
        has_rule = bool(card.violated_rule and card.violated_rule.strip())
        has_misstep = bool(card.identified_misstep and card.identified_misstep.strip())
        has_steps = bool(card.corrected_steps and len(card.corrected_steps) >= 1)
        has_boxed = bool(card.boxed_answer and card.boxed_answer.strip())

        if not has_rule:
            errors.append("RemediationCard is missing violated_rule.")
        if not has_misstep:
            errors.append("RemediationCard is missing identified_misstep.")
        if not has_steps:
            errors.append("RemediationCard is missing corrected_steps.")
        if not has_boxed:
            errors.append("RemediationCard is missing boxed_answer.")

        # 2. Socratic inquiry check (Zero Socratic questions allowed)
        combined_text = f"{card.violated_rule} {card.identified_misstep} {' '.join(card.corrected_steps)}"
        soc = contains_socratic_inquiry(combined_text, allow_question_marks=False)
        socratic_violations.extend(soc)

        # 3. Hollow praise check
        hollow = contains_hollow_praise(combined_text)
        hollow_violations.extend(hollow)

        # 4. Slip dismissal check
        dismiss = contains_slip_dismissal(combined_text)
        dismissal_violations.extend(dismiss)

        errors.extend(socratic_violations)
        errors.extend(hollow_violations)
        errors.extend(dismissal_violations)

        return RemediationCardValidationResult(
            is_valid=len(errors) == 0,
            has_violated_rule=has_rule,
            has_identified_misstep=has_misstep,
            has_step_by_step_resolution=has_steps,
            has_boxed_answer=has_boxed,
            socratic_violations=socratic_violations,
            hollow_praise_violations=hollow_violations,
            slip_dismissal_violations=dismissal_violations,
            errors=errors,
        )

    # =======================================================================
    # FR-6.3: John Wooden 5-to-1 Process Coach & Paper-and-Pencil Prompts (Skill C.2)
    # =======================================================================

    def evaluate_coaching_feedback(
        self,
        student_id: str,
        topic_id: str,
        is_correct: bool,
        kp_index: int,
        duration_ms: int,
        step_description: str = "",
        student_answer: str = "",
        target_answer: str = "",
        expected_duration_ms: int = 15000,
    ) -> CoachingFeedback:
        """
        Emits John Wooden athletic coaching feedback adhering to:
          1. 5-to-1 ratio: ~5 process confirmations per 1 corrective intervention.
          2. Strictly process-attributed confirmations (zero person/ability praise).
          3. Zero dismissal of errors as "silly mistakes."
          4. Paper-and-pencil intervention prompts ("When in doubt, write it out").
          5. Persistent telemetry update in StudentProfile.
        """
        state = self.get_or_create_coach_state(student_id)
        node = self.storage.get_topic_node(topic_id)
        total_kps = len(node.knowledge_points)
        kp_idx = min(kp_index, total_kps - 1)
        kp = node.knowledge_points[kp_idx]

        prompt_paper = False
        paper_rationale: Optional[str] = None

        if not is_correct:
            # Corrective Intervention
            state.corrections_count += 1
            state.consecutive_correct_steps = 0
            state.recent_slip_on_kp = kp.kp_id
            state.last_feedback_type = "CORRECTIVE_INTERVENTION"

            # Check paper and pencil trigger (immediate after a slip)
            prompt_paper, paper_rationale = evaluate_paper_pencil_trigger(
                scaffolding_tier=kp.scaffolding_tier,
                novel_chunk_count=kp.novel_chunk_count,
                had_recent_slip=True,
                duration_ms=duration_ms,
                expected_duration_ms=expected_duration_ms,
            )

            current_ratio = state.confirmations_count / max(1, state.corrections_count)
            msg = (
                f"Operational boundary missed on {kp.kp_focus.lower()}. "
                "Inspect the corrected calculation above. A replacement problem is queued."
            )

            return CoachingFeedback(
                feedback_type="CORRECTIVE_INTERVENTION",
                message=msg,
                process_confirmations_count=state.confirmations_count,
                corrective_interventions_count=state.corrections_count,
                current_praise_ratio=round(current_ratio, 2),
                prompt_paper_and_pencil=prompt_paper,
                paper_and_pencil_rationale=paper_rationale,
            )

        else:
            # Disciplined Execution (Process Confirmation)
            state.confirmations_count += 1
            state.consecutive_correct_steps += 1
            had_prior_slip = bool(state.recent_slip_on_kp == kp.kp_id)
            if had_prior_slip:
                state.recent_slip_on_kp = None

            state.last_feedback_type = "PROCESS_CONFIRMATION"

            # Check cognitive load paper and pencil trigger
            prompt_paper, paper_rationale = evaluate_paper_pencil_trigger(
                scaffolding_tier=kp.scaffolding_tier,
                novel_chunk_count=kp.novel_chunk_count,
                had_recent_slip=False,
                duration_ms=duration_ms,
                expected_duration_ms=expected_duration_ms,
            )

            # Generate process-attributed praise
            context = {
                "topic_name": node.title,
                "kp_focus": kp.kp_focus,
                "scaffolding_tier": kp.scaffolding_tier,
                "step_description": step_description,
            }
            raw_praise = self.llm_client.generate_process_confirmation(context)

            # Ensure zero hollow praise or slip dismissal
            if contains_hollow_praise(raw_praise) or contains_slip_dismissal(raw_praise):
                praise_msg = (
                    f"Disciplined execution: You executed {kp.kp_focus.lower()} cleanly, "
                    "maintaining sign integrity and operational precedence."
                )
            else:
                praise_msg = raw_praise.strip()

            current_ratio = state.confirmations_count / max(1, state.corrections_count)

            # Persist update to StudentProfile
            try:
                prof = self.storage.get_student_profile(student_id)
                self.storage.upsert_student_profile(
                    prof.model_copy(update={"process_praise_counter": state.confirmations_count})
                )
            except KeyError:
                pass

            return CoachingFeedback(
                feedback_type="PROCESS_CONFIRMATION",
                message=praise_msg,
                process_confirmations_count=state.confirmations_count,
                corrective_interventions_count=state.corrections_count,
                current_praise_ratio=round(current_ratio, 2),
                prompt_paper_and_pencil=prompt_paper,
                paper_and_pencil_rationale=paper_rationale,
            )
