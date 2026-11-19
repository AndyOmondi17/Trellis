"""
src/trellis/engine/content_blueprint_solver.py
Decoupled Content Shell Registry & SymPy Problem Blueprint Engine (FR-1.3).
Provides safe AST-based SymPy parsing, deterministic parameter constraint sampling,
LaTeX stem rendering, mathematical equivalence grading, and fast-path trap misconception matching.
"""

from __future__ import annotations

import ast
import itertools
import math
import random
import re
from typing import Any, Dict, List, Optional, Set, Tuple

import sympy
from sympy.parsing.sympy_parser import (
    convert_xor,
    eval_expr,
    function_exponentiation,
    implicit_multiplication_application,
    standard_transformations,
    stringify_expr,
)

from trellis.domain.models import (
    ContentStatus,
    DiagnosticMisconception,
    KPContentShell,
    ProblemBlueprint,
)
from trellis.ports.storage_port import TrellisStoragePort
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Whitelist of safe mathematical functions and constants for SymPy AST
# ---------------------------------------------------------------------------

FORBIDDEN_IDENTIFIERS: Set[str] = {
    "eval", "exec", "open", "__import__", "compile", "exit", "quit",
    "system", "popen", "spawn", "getattr", "setattr", "delattr",
    "globals", "locals", "vars", "dir", "breakpoint", "help",
}


class BlueprintInstance(BaseModel):
    """Concrete problem instance generated from a ProblemBlueprint."""
    kp_id: str
    parameters: Dict[str, Any]
    rendered_stem: str
    target_answer: str
    answer_format: str
    verification_solver_expr: str
    svg_asset_uri: Optional[str] = None
    diagnostic_misconceptions: List[DiagnosticMisconception] = Field(default_factory=list)


class SafeSympyParser:
    """
    AST-guarded SymPy Parser.
    Guarantees zero arbitrary code execution (no eval, exec, dunder access, or arbitrary calls).
    Converts LaTeX and standard mathematical notation into safe SymPy expressions.
    """

    @classmethod
    def sanitize_math_string(cls, expr_str: str) -> str:
        """Cleans and translates LaTeX math notation into standard mathematical syntax."""
        cleaned = expr_str.strip()
        # Remove surrounding LaTeX math delimiters
        cleaned = re.sub(r"^\$+", "", cleaned)
        cleaned = re.sub(r"\$+$", "", cleaned)
        cleaned = re.sub(r"^\\\(", "", cleaned)
        cleaned = re.sub(r"\\\)$", "", cleaned)
        cleaned = re.sub(r"^\\\[", "", cleaned)
        cleaned = re.sub(r"\\\]$", "", cleaned)
        cleaned = cleaned.strip()

        # LaTeX fraction replacements: \frac{a}{b} -> ((a)/(b))
        while r"\frac" in cleaned:
            match = re.search(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", cleaned)
            if not match:
                break
            num, den = match.groups()
            cleaned = cleaned[:match.start()] + f"(({num})/({den}))" + cleaned[match.end():]

        # \sqrt{x} -> sqrt(x)
        cleaned = re.sub(r"\\sqrt\{([^{}]+)\}", r"sqrt(\1)", cleaned)
        # \cdot, \times -> *
        cleaned = re.sub(r"\\cdot|\\times", "*", cleaned)
        # \left, \right -> strip
        cleaned = re.sub(r"\\left|\\right", "", cleaned)
        # Trig / log functions with backslash
        cleaned = re.sub(r"\\(sin|cos|tan|cot|sec|csc|log|ln|exp)", r"\1", cleaned)
        cleaned = re.sub(r"\bln\b", "log", cleaned)
        # \pi -> pi
        cleaned = re.sub(r"\\pi\b", "pi", cleaned)
        # Power notation ^ -> **
        cleaned = cleaned.replace("^", "**")

        return cleaned

    @classmethod
    def validate_ast_safety(cls, code_str: str) -> None:
        """
        Walks Python AST of stringified code to ensure no forbidden nodes or dunder access exist.
        Raises ValueError if unsafe syntax is encountered.
        """
        try:
            tree = ast.parse(code_str, mode="eval")
        except SyntaxError as e:
            raise ValueError(f"Invalid mathematical syntax: {code_str}") from e

        for node in ast.walk(tree):
            # Forbid attribute access completely (prevents .__class__, .system, .read, etc.)
            if isinstance(node, ast.Attribute):
                raise ValueError(f"Attribute access is strictly forbidden in mathematical input: {code_str}")

            # Forbid imports
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                raise ValueError(f"Import statements are strictly forbidden: {code_str}")

            # Forbid dangerous names
            if isinstance(node, ast.Name):
                if node.id in FORBIDDEN_IDENTIFIERS or node.id.startswith("_"):
                    raise ValueError(f"Forbidden identifier '{node.id}' in expression")

            # Forbid dunder strings
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "__" in node.value or "import" in node.value:
                    raise ValueError(f"Forbidden string literal '{node.value}' in expression")

    @classmethod
    def parse_safely(
        cls,
        expr_str: str,
        custom_symbols: Optional[Dict[str, sympy.Symbol]] = None,
    ) -> sympy.Expr:
        """
        Safely parses a mathematical expression string into a SymPy Expr.
        Validates the transformed AST before evaluation.
        """
        sanitized = cls.sanitize_math_string(expr_str)

        transformations = standard_transformations + (
            implicit_multiplication_application,
            convert_xor,
            function_exponentiation,
        )

        global_dict: Dict[str, Any] = {}
        exec("from sympy import *", global_dict)

        local_dict: Dict[str, Any] = {
            "pi": sympy.pi,
            "E": sympy.E,
            "I": sympy.I,
            "oo": sympy.oo,
            "zoo": sympy.zoo,
            "sqrt": sympy.sqrt,
            "sin": sympy.sin,
            "cos": sympy.cos,
            "tan": sympy.tan,
            "cot": sympy.cot,
            "sec": sympy.sec,
            "csc": sympy.csc,
            "log": sympy.log,
            "exp": sympy.exp,
            "Abs": sympy.Abs,
            "gcd": sympy.gcd,
            "lcm": sympy.lcm,
        }
        if custom_symbols:
            local_dict.update(custom_symbols)

        try:
            code = stringify_expr(sanitized, local_dict, global_dict, transformations)
        except Exception as e:
            raise ValueError(f"Invalid mathematical syntax for '{expr_str}': {e}") from e

        # Validate AST security of the transformed code
        cls.validate_ast_safety(code)

        try:
            return eval_expr(code, local_dict, global_dict)
        except Exception as e:
            raise ValueError(f"SymPy evaluation error for '{expr_str}': {e}") from e


class ConstraintSampler:
    """
    Deterministic DSL Parser and Sampler for Problem Blueprint Parameter Constraints.
    Supports range syntax (p_1 in {2..9}), set difference (\\ {0}),
    discrete choice sets (p in [1, 2, 5]), and relational conditions (gcd(p_1, p_2) = 1, p_1 != p_2).
    """

    @classmethod
    def split_dsl_clauses(cls, text: str) -> List[str]:
        """Splits DSL string by commas that are outside parentheses, brackets, or braces."""
        clauses: List[str] = []
        current: List[str] = []
        paren_depth = 0
        brace_depth = 0
        bracket_depth = 0

        for char in text:
            if char == "(":
                paren_depth += 1
            elif char == ")":
                paren_depth = max(0, paren_depth - 1)
            elif char == "{":
                brace_depth += 1
            elif char == "}":
                brace_depth = max(0, brace_depth - 1)
            elif char == "[":
                bracket_depth += 1
            elif char == "]":
                bracket_depth = max(0, bracket_depth - 1)

            if char == "," and paren_depth == 0 and brace_depth == 0 and bracket_depth == 0:
                clause = "".join(current).strip()
                if clause:
                    clauses.append(clause)
                current = []
            else:
                current.append(char)

        if current:
            clause = "".join(current).strip()
            if clause:
                clauses.append(clause)

        return clauses

    @classmethod
    def parse_and_sample(
        cls,
        constraints_dsl: str,
        seed: Optional[int] = None,
        max_attempts: int = 1000,
    ) -> Dict[str, Any]:
        """
        Samples a valid parameter assignment dictionary satisfying the constraint DSL string.
        Uses seed for deterministic repeatability.
        """
        rng = random.Random(seed) if seed is not None else random.Random()
        clauses = cls.split_dsl_clauses(constraints_dsl)

        domain_candidates: Dict[str, List[int]] = {}
        conditions: List[str] = []

        for clause in clauses:
            # Pattern 1: var in {min..max} [ \ {exclusions} ] (single or double backslash)
            range_match = re.match(
                r"^([a-zA-Z0-9_]+)\s+in\s+\{([-\d]+)\.\.([-\d]+)\}(?:\s*\\{1,2}\s*\{([-\d,\s]+)\})?$",
                clause,
            )
            if range_match:
                var, min_str, max_str, excl_str = range_match.groups()
                min_val = int(min_str)
                max_val = int(max_str)
                step = 1 if max_val >= min_val else -1
                values = set(range(min_val, max_val + step, step))
                if excl_str:
                    excl_vals = {int(x.strip()) for x in excl_str.split(",") if x.strip()}
                    values = values - excl_vals
                domain_candidates[var] = sorted(list(values))
                continue

            # Pattern 2: var in [val1, val2, ...] or {val1, val2, ...}
            set_match = re.match(r"^([a-zA-Z0-9_]+)\s+in\s+[\[\{]([-\d,\s]+)[\]\}]$", clause)
            if set_match:
                var, val_str = set_match.groups()
                vals = [int(x.strip()) for x in val_str.split(",") if x.strip()]
                domain_candidates[var] = sorted(list(set(vals)))
                continue

            # Otherwise, treat as condition clause (e.g. gcd(p_1, p_2) = 1, p_1 != p_2)
            conditions.append(clause)

        if not domain_candidates:
            return {}

        var_names = sorted(list(domain_candidates.keys()))

        # Attempt random sampling up to max_attempts
        for _ in range(max_attempts):
            assignment = {var: rng.choice(domain_candidates[var]) for var in var_names}
            if cls._verify_conditions(assignment, conditions):
                return assignment

        # Fallback: systematic cartesian search for determinism
        candidate_lists = [domain_candidates[v] for v in var_names]
        for combo in itertools.product(*candidate_lists):
            assignment = dict(zip(var_names, combo))
            if cls._verify_conditions(assignment, conditions):
                return assignment

        raise ValueError(
            f"Unable to find parameter assignment satisfying constraints: '{constraints_dsl}'"
        )

    @classmethod
    def _verify_conditions(cls, params: Dict[str, Any], conditions: List[str]) -> bool:
        """Evaluates whether the candidate parameter assignment satisfies all condition clauses."""
        for cond in conditions:
            # Condition: gcd(var1, var2) = val
            gcd_match = re.match(r"^gcd\(([a-zA-Z0-9_]+)\s*,\s*([a-zA-Z0-9_]+)\)\s*={1,2}\s*(\d+)$", cond)
            if gcd_match:
                v1, v2, target_gcd = gcd_match.groups()
                if math.gcd(int(params[v1]), int(params[v2])) != int(target_gcd):
                    return False
                continue

            # Condition: var1 != var2
            neq_match = re.match(r"^([a-zA-Z0-9_]+)\s*!=\s*([a-zA-Z0-9_]+)$", cond)
            if neq_match:
                v1, v2 = neq_match.groups()
                if params[v1] == params[v2]:
                    return False
                continue

            # Condition: var1 > var2
            gt_match = re.match(r"^([a-zA-Z0-9_]+)\s*>\s*([a-zA-Z0-9_]+)$", cond)
            if gt_match:
                v1, v2 = gt_match.groups()
                if not (params[v1] > params[v2]):
                    return False
                continue

            # Condition: var1 < var2
            lt_match = re.match(r"^([a-zA-Z0-9_]+)\s*<\s*([a-zA-Z0-9_]+)$", cond)
            if lt_match:
                v1, v2 = lt_match.groups()
                if not (params[v1] < params[v2]):
                    return False
                continue

        return True


class ContentBlueprintSolver:
    """
    Decoupled Content Shell Registry & SymPy Problem Blueprint Engine.
    Handles shell life-cycle, blueprint instantiation, safe mathematical equivalence grading,
    and fast-path (<5 ms) misconception matching.
    """

    def __init__(self, storage: TrellisStoragePort) -> None:
        self.storage = storage

    # -----------------------------------------------------------------------
    # Content Shell Registry Operations
    # -----------------------------------------------------------------------

    def get_content_shell(self, kp_id: str) -> KPContentShell:
        """Retrieves the decoupled content shell for a Knowledge Point."""
        return self.storage.get_kp_content_shell(kp_id)

    def upsert_content_shell(self, shell: KPContentShell) -> None:
        """Upserts a content shell into the active backend."""
        self.storage.upsert_kp_content_shell(shell)

    def transition_shell_status(
        self, kp_id: str, new_status: ContentStatus
    ) -> KPContentShell:
        """
        Transitions a content shell through its lifecycle:
          BLUEPRINT_READY -> LLM_DRAFTED -> VERIFIED_PRODUCTION
        """
        shell = self.storage.get_kp_content_shell(kp_id)
        valid_transitions = {
            "BLUEPRINT_READY": {"BLUEPRINT_READY", "LLM_DRAFTED", "VERIFIED_PRODUCTION"},
            "LLM_DRAFTED": {"LLM_DRAFTED", "VERIFIED_PRODUCTION", "BLUEPRINT_READY"},
            "VERIFIED_PRODUCTION": {"VERIFIED_PRODUCTION", "BLUEPRINT_READY"},
        }

        if new_status not in valid_transitions.get(shell.content_status, set()):
            raise ValueError(
                f"Invalid content status transition from '{shell.content_status}' to '{new_status}'."
            )

        updated_shell = shell.model_copy(update={"content_status": new_status})
        self.storage.upsert_kp_content_shell(updated_shell)
        return updated_shell

    def attach_svg_asset(self, kp_id: str, svg_asset_uri: str) -> KPContentShell:
        """Attaches a dual-coded SVG visual schema reference to a content shell."""
        shell = self.storage.get_kp_content_shell(kp_id)
        updated_shell = shell.model_copy(update={"svg_asset_uri": svg_asset_uri})
        self.storage.upsert_kp_content_shell(updated_shell)
        return updated_shell

    # -----------------------------------------------------------------------
    # Dynamic Blueprint Instantiation & Rendering
    # -----------------------------------------------------------------------

    def generate_variant(
        self,
        kp_id: str,
        seed: Optional[int] = None,
    ) -> BlueprintInstance:
        """
        Samples a concrete parameterized problem instance from the KP's ProblemBlueprint.
        Renders LaTeX stems and resolves target answer expressions.
        """
        shell = self.storage.get_kp_content_shell(kp_id)
        if not shell.problem_blueprint:
            raise ValueError(f"Content shell for '{kp_id}' has no problem_blueprint defined.")

        blueprint = shell.problem_blueprint
        params = ConstraintSampler.parse_and_sample(
            blueprint.parameter_constraints, seed=seed
        )

        rendered_stem = blueprint.stem_template
        for k, v in params.items():
            rendered_stem = rendered_stem.replace(f"{{{k}}}", str(v))

        # Determine target answer: from worked example or parameter interpolation
        target_answer = "0"
        if shell.worked_example:
            target_answer = shell.worked_example.final_answer
            for k, v in params.items():
                target_answer = target_answer.replace(f"{{{k}}}", str(v))

        return BlueprintInstance(
            kp_id=kp_id,
            parameters=params,
            rendered_stem=rendered_stem,
            target_answer=target_answer,
            answer_format=blueprint.answer_format,
            verification_solver_expr=blueprint.verification_solver_expr,
            svg_asset_uri=shell.svg_asset_uri,
            diagnostic_misconceptions=shell.diagnostic_misconceptions,
        )

    # -----------------------------------------------------------------------
    # SymPy Mathematical Equivalence Grading & Misconception Matching
    # -----------------------------------------------------------------------

    def grade_student_answer(
        self,
        student_input: str,
        target_answer: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Grades whether a student's answer is mathematically equivalent to the target answer.
        Safe against arbitrary code execution (AST-guarded).
        Handles algebraic identities, fraction simplification, and numerical approximations.
        """
        clean_student = student_input.strip()
        clean_target = target_answer.strip()

        if not clean_student:
            return False

        # Fast direct string equivalence
        if clean_student == clean_target:
            return True

        # Interpolate known parameters if present in target
        if parameters:
            for k, v in parameters.items():
                clean_target = clean_target.replace(f"{{{k}}}", str(v))
                clean_student = clean_student.replace(f"{{{k}}}", str(v))

        try:
            student_expr = SafeSympyParser.parse_safely(clean_student)
            target_expr = SafeSympyParser.parse_safely(clean_target)
        except Exception:
            # Syntax error in student expression: not mathematically equivalent
            return False

        # Substitute parameter symbols if present as symbols
        if parameters:
            param_subs = {sympy.Symbol(k): v for k, v in parameters.items()}
            student_expr = student_expr.subs(param_subs)
            target_expr = target_expr.subs(param_subs)

        # Method 1: Direct difference simplification
        try:
            diff = sympy.simplify(student_expr - target_expr)
            if diff == 0:
                return True
        except Exception:
            pass

        # Method 2: SymPy .equals() method (comprehensive identity check)
        try:
            if student_expr.equals(target_expr):
                return True
        except Exception:
            pass

        # Method 3: Numerical evaluation at randomized test points
        try:
            free_syms = list(student_expr.free_symbols.union(target_expr.free_symbols))
            if not free_syms:
                # Pure numbers
                v1 = complex(student_expr.evalf())
                v2 = complex(target_expr.evalf())
                if abs(v1 - v2) < 1e-7:
                    return True
            else:
                # Sample 3 test points for free variables
                match_all = True
                test_rng = random.Random(42)
                for _ in range(3):
                    subs_dict = {s: test_rng.uniform(1.5, 5.5) for s in free_syms}
                    val1 = complex(student_expr.evalf(subs=subs_dict))
                    val2 = complex(target_expr.evalf(subs=subs_dict))
                    if abs(val1 - val2) > 1e-6:
                        match_all = False
                        break
                if match_all:
                    return True
        except Exception:
            pass

        return False

    def match_diagnostic_misconception(
        self,
        student_input: str,
        misconceptions: List[DiagnosticMisconception],
        parameters: Optional[Dict[str, Any]] = None,
    ) -> Optional[DiagnosticMisconception]:
        """
        Fast-path (<5 ms) misconception matcher.
        Matches a wrong student answer against documented empirical misconception patterns.
        Returns the DiagnosticMisconception if matched, or None.
        """
        clean_student = student_input.strip()
        if not clean_student:
            return None

        for misc in misconceptions:
            pattern = misc.trap_answer_pattern
            if parameters:
                for k, v in parameters.items():
                    pattern = pattern.replace(f"{{{k}}}", str(v))

            # Direct string match
            if clean_student == pattern.strip():
                return misc

            # Mathematical equivalence with trap pattern
            if self.grade_student_answer(clean_student, pattern, parameters=parameters):
                return misc

        return None
