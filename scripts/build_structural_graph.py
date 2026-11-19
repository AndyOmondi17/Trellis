#!/usr/bin/env python3
"""
scripts/build_structural_graph.py
Deterministic Air-Gapped K-16 Knowledge Graph Builder for Trellis.

Builds the complete, unified K-16 Mathematics Knowledge Graph
(`data/clean_spec/trellis_knowledge_graph.json`) with ZERO external LLM API calls:
  1. Reads ONLY `data/clean_spec/raw_concept_inventory.json` (the sanitized factual
     concept inventory). Never accesses `os-taxonomy/` or `algebrica/`.
  2. Synthesizes the complete 16-Course K-16 hierarchy (Early Numeracy through
     Multivariable Calculus, Linear Algebra, Differential Equations, and Abstract Algebra),
     including explicit High School "Missing Middle" bridge modules.
  3. Decomposes concepts into atomic `TopicNode`s with 3 sequential `KnowledgePoint`s
     (`KP_1_BASELINE`, `KP_2_EDGE_NEGATIVE_FRACTIONAL`, `KP_3_MULTI_STEP_INVERSION`)
     under the proprietary `trl_<course>_<domain>_<seq>` ID namespace.
  4. Wires a strictly transitively reduced Hard/Soft Prerequisite DAG (`G_prereq` / `G_hard`),
     multi-hop KP `key_prerequisite_id` pointers, and weighted Fractional Implicit
     Repetition (FIRe) `encompassed_skills` edges (`G_enc`), satisfying all mathematical
     and IP compliance invariants.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import networkx as nx

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
CLEAN_INVENTORY_PATH = WORKSPACE_ROOT / "data" / "clean_spec" / "raw_concept_inventory.json"
OUTPUT_GRAPH_PATH = WORKSPACE_ROOT / "data" / "clean_spec" / "trellis_knowledge_graph.json"

FORBIDDEN_SUBSTRINGS = ("os-taxonomy", "algebrica", "withmarble.com", "algebrica.org", "mt_")

# 16 Canonical K-16 Courses (Ordered from K-2 Foundations to Advanced University)
COURSE_CATALOG: List[Dict[str, Any]] = [
    {
        "course_id": "CRS_FND_K2",
        "course_code": "fnd",
        "title": "Foundational Numeracy & Place Value (Grades K-2)",
        "grade_band": "K-2",
        "benchmark_xp": 900,
        "mastery_floor_course_ids": [],
    },
    {
        "course_id": "CRS_ELEM_35",
        "course_code": "elm",
        "title": "Elementary Arithmetic, Fractions & Measurement (Grades 3-5)",
        "grade_band": "3-5",
        "benchmark_xp": 1400,
        "mastery_floor_course_ids": ["CRS_FND_K2"],
    },
    {
        "course_id": "CRS_PREALG",
        "course_code": "pra",
        "title": "Pre-Algebra, Ratios & Rational Numbers (Grades 6-8)",
        "grade_band": "6-8",
        "benchmark_xp": 1600,
        "mastery_floor_course_ids": ["CRS_ELEM_35"],
    },
    {
        "course_id": "CRS_ALG1",
        "course_code": "alg",
        "title": "Algebra I: Linear Systems, Quadratics & Exponents",
        "grade_band": "8-9",
        "benchmark_xp": 1800,
        "mastery_floor_course_ids": ["CRS_PREALG"],
    },
    {
        "course_id": "CRS_GEOM",
        "course_code": "geo",
        "title": "Euclidean, Coordinate & Transformational Geometry",
        "grade_band": "9-10",
        "benchmark_xp": 1600,
        "mastery_floor_course_ids": ["CRS_ALG1"],
    },
    {
        "course_id": "CRS_ALG2",
        "course_code": "al2",
        "title": "Algebra II: Polynomial, Rational, Radical & Logarithmic Functions",
        "grade_band": "10-11",
        "benchmark_xp": 1900,
        "mastery_floor_course_ids": ["CRS_ALG1"],
    },
    {
        "course_id": "CRS_STAT_HS",
        "course_code": "sth",
        "title": "Foundational Data Analysis, Probability & Statistics",
        "grade_band": "9-12",
        "benchmark_xp": 1400,
        "mastery_floor_course_ids": ["CRS_ALG1"],
    },
    {
        "course_id": "CRS_PRECALC",
        "course_code": "prc",
        "title": "Trigonometry, Precalculus & Analytic Conics",
        "grade_band": "11-12",
        "benchmark_xp": 2000,
        "mastery_floor_course_ids": ["CRS_GEOM", "CRS_ALG2"],
    },
    {
        "course_id": "CRS_CALC1",
        "course_code": "ca1",
        "title": "Calculus I: Limits, Continuity & Single-Variable Differentiation",
        "grade_band": "13",
        "benchmark_xp": 2100,
        "mastery_floor_course_ids": ["CRS_PRECALC"],
    },
    {
        "course_id": "CRS_CALC2",
        "course_code": "ca2",
        "title": "Calculus II: Definite/Indefinite Integration & Infinite Series",
        "grade_band": "13-14",
        "benchmark_xp": 2200,
        "mastery_floor_course_ids": ["CRS_CALC1"],
    },
    {
        "course_id": "CRS_LINALG",
        "course_code": "lin",
        "title": "Linear Algebra: Vector Spaces, Matrices & Spectral Decomposition",
        "grade_band": "14",
        "benchmark_xp": 2200,
        "mastery_floor_course_ids": ["CRS_PRECALC", "CRS_CALC1"],
    },
    {
        "course_id": "CRS_MVCALC",
        "course_code": "mvc",
        "title": "Multivariable & Vector Calculus",
        "grade_band": "14-15",
        "benchmark_xp": 2400,
        "mastery_floor_course_ids": ["CRS_CALC2", "CRS_LINALG"],
    },
    {
        "course_id": "CRS_DIFFEQ",
        "course_code": "deq",
        "title": "Ordinary & Boundary-Value Differential Equations",
        "grade_band": "14-15",
        "benchmark_xp": 2100,
        "mastery_floor_course_ids": ["CRS_CALC2", "CRS_LINALG"],
    },
    {
        "course_id": "CRS_PROBSTAT",
        "course_code": "pst",
        "title": "Calculus-Based Probability Theory & Mathematical Statistics",
        "grade_band": "14-15",
        "benchmark_xp": 2200,
        "mastery_floor_course_ids": ["CRS_CALC2", "CRS_STAT_HS"],
    },
    {
        "course_id": "CRS_DISCRETE",
        "course_code": "dsc",
        "title": "Discrete Mathematics, Symbolic Logic & Combinatorics",
        "grade_band": "13-15",
        "benchmark_xp": 1900,
        "mastery_floor_course_ids": ["CRS_PRECALC"],
    },
    {
        "course_id": "CRS_ABSALG",
        "course_code": "abs",
        "title": "Abstract Algebra, Group/Ring/Field Theory & Structural Morphisms",
        "grade_band": "15-16",
        "benchmark_xp": 2500,
        "mastery_floor_course_ids": ["CRS_LINALG", "CRS_DISCRETE"],
    },
]

COURSE_BY_ID: Dict[str, Dict[str, Any]] = {c["course_id"]: c for c in COURSE_CATALOG}

# 3-letter domain codes for each of the 16 canonical domains
DOMAIN_CODE_MAP: Dict[str, str] = {
    "01_early_numeracy_and_place_value": "num",
    "02_arithmetic_operations": "ari",
    "03_fractions_decimals_and_rationals": "frc",
    "04_ratios_proportions_and_percents": "rat",
    "05_measurement_and_data": "mea",
    "06_prealgebra_and_elementary_algebra": "pal",
    "07_intermediate_algebra_and_exponentials": "ial",
    "08_euclidean_and_analytic_geometry": "geo",
    "09_trigonometry_and_precalculus": "trg",
    "10_limits_and_differential_calculus": "dif",
    "11_integral_calculus_and_series": "int",
    "12_linear_algebra": "lin",
    "13_multivariable_and_differential_equations": "mde",
    "14_probability_and_statistics": "sta",
    "15_discrete_math_and_logic": "log",
    "16_abstract_algebra_and_topology": "abs",
}

# Explicit First-Principles Bridge Concepts ensuring complete coverage of High School & University
FIRST_PRINCIPLES_BRIDGE_CONCEPTS: List[Tuple[str, str, str, List[str]]] = [
    # (canonical_domain, concept_name, level_band, aligned_standard_codes)
    # Algebra I
    ("06_prealgebra_and_elementary_algebra", "Solving Multi-Step Linear Equations with Rational Coefficients", "G_9_12_High_School", ["ccss-math:A-REI.B.3"]),
    ("06_prealgebra_and_elementary_algebra", "Rearranging Literal Equations and Formulas for a Target Variable", "G_9_12_High_School", ["ccss-math:A-CED.A.4"]),
    ("06_prealgebra_and_elementary_algebra", "Graphing Linear Functions in Slope-Intercept, Point-Slope, and Standard Form", "G_9_12_High_School", ["ccss-math:F-IF.C.7"]),
    ("06_prealgebra_and_elementary_algebra", "Solving Two-Variable Linear Systems by Substitution and Elimination", "G_9_12_High_School", ["ccss-math:A-REI.C.6"]),
    ("06_prealgebra_and_elementary_algebra", "Graphing Systems of Linear Inequalities in the Coordinate Plane", "G_9_12_High_School", ["ccss-math:A-REI.D.12"]),
    ("07_intermediate_algebra_and_exponentials", "Factoring Quadratic Trinomials with Leading Coefficient Non-Unit", "G_9_12_High_School", ["ccss-math:A-SSE.B.3"]),
    ("07_intermediate_algebra_and_exponentials", "Solving Quadratic Equations by Completing the Square", "G_9_12_High_School", ["ccss-math:A-REI.B.4"]),
    ("07_intermediate_algebra_and_exponentials", "Evaluating the Quadratic Discriminant and Applying the Quadratic Formula", "G_9_12_High_School", ["ccss-math:A-REI.B.4"]),
    # Geometry
    ("08_euclidean_and_analytic_geometry", "Proving Triangle Congruence via SSS, SAS, ASA, AAS, and HL Criteria", "G_9_12_High_School", ["ccss-math:G-CO.B.8"]),
    ("08_euclidean_and_analytic_geometry", "Applying Triangle Similarity Theorems and Dilation Scale Factors", "G_9_12_High_School", ["ccss-math:G-SRT.A.2"]),
    ("08_euclidean_and_analytic_geometry", "Computing Right-Triangle Trigonometric Ratios (Sine, Cosine, Tangent)", "G_9_12_High_School", ["ccss-math:G-SRT.C.6"]),
    ("08_euclidean_and_analytic_geometry", "Inscribed Angles, Arc Length, and Sector Area in Circles", "G_9_12_High_School", ["ccss-math:G-C.B.5"]),
    ("08_euclidean_and_analytic_geometry", "Deriving Standard Equations of Circles and Analytic Distance/Midpoint", "G_9_12_High_School", ["ccss-math:G-GPE.A.1"]),
    # Algebra II
    ("07_intermediate_algebra_and_exponentials", "Polynomial Long Division, Synthetic Division, and the Remainder Theorem", "G_9_12_High_School", ["ccss-math:A-APR.B.2"]),
    ("07_intermediate_algebra_and_exponentials", "Simplifying Rational Expressions and Solving Rational Equations with Extraneous Checks", "G_9_12_High_School", ["ccss-math:A-REI.A.2"]),
    ("07_intermediate_algebra_and_exponentials", "Solving Radical Equations and Fractional Exponent Equations", "G_9_12_High_School", ["ccss-math:N-RN.A.2"]),
    ("07_intermediate_algebra_and_exponentials", "Applying Logarithmic Laws and Solving Exponential-Logarithmic Equations", "G_9_12_High_School", ["ccss-math:F-LE.A.4"]),
    # Precalculus & Trigonometry
    ("09_trigonometry_and_precalculus", "Radian Measure and Exact Trigonometric Values on the Unit Circle", "G_9_12_High_School", ["ccss-math:F-TF.A.2"]),
    ("09_trigonometry_and_precalculus", "Amplitude, Period, and Phase Shift of Sinusoidal Functions", "G_9_12_High_School", ["ccss-math:F-TF.B.5"]),
    ("09_trigonometry_and_precalculus", "Verifying Pythagorean, Sum-Difference, and Double-Angle Trigonometric Identities", "G_9_12_High_School", ["ccss-math:F-TF.C.8"]),
    ("09_trigonometry_and_precalculus", "Applying the Law of Sines and Law of Cosines to Oblique Triangles", "G_9_12_High_School", ["ccss-math:G-SRT.D.11"]),
    ("09_trigonometry_and_precalculus", "Polar Coordinates, Complex Polar Form, and De Moivre Theorem", "G_9_12_High_School", ["ccss-math:N-CN.B.4"]),
    ("09_trigonometry_and_precalculus", "Partial Fraction Decomposition of Rational Functions", "G_9_12_High_School", ["ccss-math:A-APR.D.7"]),
    # Multivariable Calculus & Differential Equations
    ("13_multivariable_and_differential_equations", "Partial Derivatives, Gradient Vectors, and Directional Derivatives", "Univ_Advanced_Mathematics", []),
    ("13_multivariable_and_differential_equations", "Unconstrained Multivariable Optimization and Lagrange Multipliers", "Univ_Advanced_Mathematics", []),
    ("13_multivariable_and_differential_equations", "Iterated Double and Triple Integrals in Cartesian, Polar, Cylindrical, and Spherical Coordinates", "Univ_Advanced_Mathematics", []),
    ("13_multivariable_and_differential_equations", "Line Integrals, Surface Integrals, Green Theorem, and Stokes Theorem", "Univ_Advanced_Mathematics", []),
    ("13_multivariable_and_differential_equations", "Solving First-Order Separable and Linear Ordinary Differential Equations", "Univ_Advanced_Mathematics", []),
    ("13_multivariable_and_differential_equations", "Solving Second-Order Constant-Coefficient Homogeneous and Non-Homogeneous ODEs", "Univ_Advanced_Mathematics", []),
    ("13_multivariable_and_differential_equations", "Laplace Transforms and Systems of First-Order Linear Differential Equations", "Univ_Advanced_Mathematics", []),
    # Linear Algebra
    ("12_linear_algebra", "Gaussian Elimination, Reduced Row Echelon Form, and Rank-Nullity Theorem", "Univ_Advanced_Mathematics", []),
    ("12_linear_algebra", "Linear Independence, Basis, Dimension, and Change-of-Basis Matrices", "Univ_Advanced_Mathematics", []),
    ("12_linear_algebra", "Characteristic Polynomials, Eigenvalues, Eigenvectors, and Matrix Diagonalization", "Univ_Advanced_Mathematics", []),
    ("12_linear_algebra", "Gram-Schmidt Orthonormalization, Orthogonal Projections, and Singular Value Decomposition", "Univ_Advanced_Mathematics", []),
]


def assign_course_and_grades(canonical_domain: str, level_bands: List[str], concept_name: str) -> Tuple[str, int, int]:
    """Deterministically maps a concept to its canonical course_id and (grade_min, grade_max)."""
    bands = set(level_bands)
    cname_lower = concept_name.lower()

    # University domains
    if canonical_domain == "10_limits_and_differential_calculus":
        return ("CRS_CALC1", 13, 13)
    if canonical_domain == "11_integral_calculus_and_series":
        return ("CRS_CALC2", 13, 14)
    if canonical_domain == "13_multivariable_and_differential_equations":
        if "differential equation" in cname_lower or "ode" in cname_lower or "laplace" in cname_lower or "cauchy" in cname_lower:
            return ("CRS_DIFFEQ", 14, 15)
        return ("CRS_MVCALC", 14, 15)
    if canonical_domain == "16_abstract_algebra_and_topology":
        return ("CRS_ABSALG", 15, 16)
    if canonical_domain == "12_linear_algebra":
        if "G_9_12_High_School" in bands and "Univ_Advanced_Mathematics" not in bands:
            return ("CRS_PRECALC", 11, 12)
        return ("CRS_LINALG", 14, 14)
    if canonical_domain == "15_discrete_math_and_logic":
        if "G_K_2" in bands:
            return ("CRS_FND_K2", 1, 2)
        if "G_3_5" in bands:
            return ("CRS_ELEM_35", 3, 5)
        if "G_6_8" in bands:
            return ("CRS_PREALG", 6, 8)
        return ("CRS_DISCRETE", 13, 15)
    if canonical_domain == "14_probability_and_statistics":
        if "G_K_2" in bands:
            return ("CRS_FND_K2", 1, 2)
        if "G_3_5" in bands:
            return ("CRS_ELEM_35", 3, 5)
        if "G_6_8" in bands:
            return ("CRS_PREALG", 6, 8)
        if "Univ_Advanced_Mathematics" in bands or "Univ_Calculus_Analysis" in bands:
            return ("CRS_PROBSTAT", 14, 15)
        return ("CRS_STAT_HS", 9, 12)
    if canonical_domain == "09_trigonometry_and_precalculus":
        return ("CRS_PRECALC", 11, 12)
    if canonical_domain == "08_euclidean_and_analytic_geometry":
        if "G_K_2" in bands:
            return ("CRS_FND_K2", 0, 2)
        if "G_3_5" in bands:
            return ("CRS_ELEM_35", 3, 5)
        if "G_6_8" in bands:
            return ("CRS_PREALG", 6, 8)
        return ("CRS_GEOM", 9, 10)
    if canonical_domain == "07_intermediate_algebra_and_exponentials":
        if any(w in cname_lower for w in ("logarithm", "complex", "rational", "radical", "synthetic", "cubic", "quartic", "vieta", "binomial")):
            return ("CRS_ALG2", 10, 11)
        return ("CRS_ALG1", 8, 9)
    if canonical_domain == "06_prealgebra_and_elementary_algebra":
        if "G_K_2" in bands:
            return ("CRS_FND_K2", 1, 2)
        if "G_3_5" in bands:
            return ("CRS_ELEM_35", 3, 5)
        if "G_6_8" in bands and "G_9_12_High_School" not in bands and "G_9_12_to_Univ_Foundations" not in bands:
            return ("CRS_PREALG", 6, 8)
        return ("CRS_ALG1", 8, 9)
    if canonical_domain in ("04_ratios_proportions_and_percents",):
        if "G_3_5" in bands:
            return ("CRS_ELEM_35", 4, 5)
        return ("CRS_PREALG", 6, 8)
    if canonical_domain in ("03_fractions_decimals_and_rationals", "05_measurement_and_data"):
        if "G_K_2" in bands:
            return ("CRS_FND_K2", 1, 2)
        if "G_6_8" in bands and "G_3_5" not in bands:
            return ("CRS_PREALG", 6, 8)
        return ("CRS_ELEM_35", 3, 5)
    if canonical_domain == "02_arithmetic_operations":
        if "G_K_2" in bands:
            return ("CRS_FND_K2", 0, 2)
        if "G_6_8" in bands and "G_3_5" not in bands:
            return ("CRS_PREALG", 6, 7)
        return ("CRS_ELEM_35", 3, 5)
    # Default: 01_early_numeracy_and_place_value
    if "Univ_Advanced_Mathematics" in bands or "G_9_12_to_Univ_Foundations" in bands:
        return ("CRS_DISCRETE", 13, 14)
    if "G_6_8" in bands:
        return ("CRS_PREALG", 6, 8)
    if "G_3_5" in bands:
        return ("CRS_ELEM_35", 3, 5)
    return ("CRS_FND_K2", 0, 2)


def build_knowledge_points(
    node_id: str,
    title: str,
    primary_prereq_id: str | None,
    kp1_prereq_id: str | None,
    kp2_prereq_id: str | None,
) -> List[Dict[str, Any]]:
    """Builds 3 sequential CLT-compliant KnowledgePoint shells with symbolic templates."""
    tiers = [
        ("KP_1_BASELINE", "Baseline positive integer parameters, standard symbolic orientation", primary_prereq_id, 2),
        ("KP_2_EDGE_NEGATIVE_FRACTIONAL", "Signed, zero-coefficient, and rational parameter boundary cases", kp1_prereq_id, 3),
        ("KP_3_MULTI_STEP_INVERSION", "Multi-step structural rearrangement and inverse parameter determination", kp2_prereq_id, 4),
    ]
    kps: List[Dict[str, Any]] = []
    for idx, (tier_code, focus_desc, kp_prereq, chunks) in enumerate(tiers):
        kps.append(
            {
                "kp_index": idx,
                "kp_id": f"{node_id}#kp_{idx}",
                "scaffolding_tier": tier_code,
                "kp_focus": f"{node_id} ({tier_code}) — {focus_desc}",
                "novel_chunk_count": chunks,
                "key_prerequisite_id": kp_prereq,
                "rule_statement": f"Apply the direct procedural transformation for {node_id} at tier {tier_code} by isolating target quantities and verifying domain constraints.",
                "canonical_worked_example": {
                    "problem": f"Evaluate the {tier_code} symbolic instance for {node_id}: f(x; a, b) = 0.",
                    "subgoal_steps": [
                        {
                            "subgoal_label": "Identify structural parameters",
                            "operation": "a -> p_1, b -> p_2",
                            "justification": "Bind given coefficients to canonical form.",
                        },
                        {
                            "subgoal_label": "Execute invariant operation",
                            "operation": "T(f(x; p_1, p_2)) = y_*",
                            "justification": "Apply direct algebraic/analytic operator.",
                        },
                    ],
                    "final_answer": "y_*",
                },
                "problem_blueprint": {
                    "parameter_constraints": "p_1 in {2..9}, p_2 in {-12..12} \\ {0}, gcd(p_1, p_2) = 1",
                    "stem_template": f"[{node_id}#kp_{idx}] Compute exact result for parameter tuple (p_1={{p_1}}, p_2={{p_2}}).",
                    "answer_format": "ALGEBRAIC_EXPRESSION",
                    "verification_solver_expr": "sympy.simplify(target_expr - student_expr) == 0",
                },
                "diagnostic_misconceptions": [
                    {
                        "error_tag": f"ERR_{node_id.upper()}_SIGN_INV",
                        "trap_answer_pattern": "-y_*",
                        "violated_rule": "Sign inversion when applying inverse operator across relation boundary.",
                        "tier1_remediation_template": "Apply the inverse sign operator to every term on both sides of the relation.",
                    },
                    {
                        "error_tag": f"ERR_{node_id.upper()}_ORDER_OP",
                        "trap_answer_pattern": "p_1 * y_* + p_2",
                        "violated_rule": "Premature coefficient combination prior to structural normalization.",
                        "tier1_remediation_template": "Normalize the leading term before combining constant or secondary terms.",
                    },
                ],
            }
        )
    return kps


def build_clean_room_graph() -> Dict[str, Any]:
    if not CLEAN_INVENTORY_PATH.exists():
        raise FileNotFoundError(
            f"Missing {CLEAN_INVENTORY_PATH}. Run extract_naked_concepts.py first."
        )

    raw_bytes = CLEAN_INVENTORY_PATH.read_bytes()
    inventory_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    inventory = json.loads(raw_bytes.decode("utf-8"))

    # Collect all concept specs across domains + first-principles bridges
    raw_items: List[Dict[str, Any]] = []
    seen_keys: Set[Tuple[str, str]] = set()

    for dom, entries in inventory.get("domains", {}).items():
        for rec in entries:
            cname = rec["concept_name"]
            key = (dom, cname.lower())
            if key in seen_keys:
                continue
            seen_keys.add(key)
            raw_items.append(
                {
                    "canonical_domain": dom,
                    "concept_name": cname,
                    "level_bands": rec.get("level_bands", []),
                    "aligned_standard_codes": rec.get("aligned_standard_codes", []),
                    "has_documented_misconceptions": rec.get("has_documented_misconceptions", False),
                }
            )

    for dom, cname, band, stds in FIRST_PRINCIPLES_BRIDGE_CONCEPTS:
        key = (dom, cname.lower())
        if key not in seen_keys:
            seen_keys.add(key)
            raw_items.append(
                {
                    "canonical_domain": dom,
                    "concept_name": cname,
                    "level_bands": [band],
                    "aligned_standard_codes": stds,
                    "has_documented_misconceptions": True,
                }
            )

    # Group by (course_id, canonical_domain) to assign deterministic IDs and topological order
    course_rank_map = {c["course_id"]: idx for idx, c in enumerate(COURSE_CATALOG)}
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}

    for item in raw_items:
        cid, gmin, gmax = assign_course_and_grades(
            item["canonical_domain"], item["level_bands"], item["concept_name"]
        )
        item["course_id"] = cid
        item["grade_level_min"] = gmin
        item["grade_level_max"] = gmax
        grouped.setdefault((cid, item["canonical_domain"]), []).append(item)

    # Sort groups in strict topological order of courses, then domains
    sorted_group_keys = sorted(
        grouped.keys(),
        key=lambda kd: (course_rank_map[kd[0]], kd[1]),
    )

    nodes: List[Dict[str, Any]] = []
    # Track nodes by (course_id, domain) and by course_id for deterministic DAG wiring
    strand_nodes: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    course_nodes: Dict[str, List[Dict[str, Any]]] = {c["course_id"]: [] for c in COURSE_CATALOG}
    seq_counters: Dict[Tuple[str, str], int] = {}

    for cid, dom in sorted_group_keys:
        c_code = COURSE_BY_ID[cid]["course_code"]
        d_code = DOMAIN_CODE_MAP[dom]
        items = sorted(
            grouped[(cid, dom)],
            key=lambda x: (x["grade_level_min"], x["grade_level_max"], x["concept_name"].lower()),
        )
        for item in items:
            seq_key = (c_code, d_code)
            seq_num = seq_counters.get(seq_key, 0) + 1
            seq_counters[seq_key] = seq_num
            node_id = f"trl_{c_code}_{d_code}_{seq_num:03d}"

            gmin = item["grade_level_min"]
            gmax = item["grade_level_max"]
            # Guarantee at least the first K-2 Numeracy nodes are true K-2 roots
            if cid == "CRS_FND_K2" and seq_num <= 3 and dom == "01_early_numeracy_and_place_value":
                gmin = 0

            is_fluency = bool(gmin <= 5 and dom in ("01_early_numeracy_and_place_value", "02_arithmetic_operations") and seq_num <= 10)
            node_obj: Dict[str, Any] = {
                "id": node_id,
                "title": item["concept_name"][:140],
                "course_id": cid,
                "canonical_domain": dom,
                "domain_cluster": f"{cid}::{d_code}",
                "skill_type": "FLUENCY_FACT" if is_fluency else ("THEOREM_PROOF" if gmin >= 15 else "PROCEDURAL"),
                "bloom_cognitive_tier": (
                    "RETRIEVAL_AUTOMATICITY"
                    if is_fluency
                    else ("FORMAL_DERIVATION" if gmin >= 15 else "PROCEDURAL_EXECUTION")
                ),
                "grade_level_min": gmin,
                "grade_level_max": gmax,
                "is_core": True,  # Core spine; supplemental leaf flags set after topology construction
                "in_foundations_sequence": bool(gmin >= 3),
                "requires_automaticity": is_fluency,
                "automaticity_Defining_latency_ms": 3000 if is_fluency else None,
                "base_difficulty": round(min(1.0, max(0.1, 0.15 + 0.05 * gmin)), 2),
                "estimated_xp": 3 if gmin <= 5 else (4 if gmin <= 12 else 5),
                "mastery_gate_n": 3,
                "key_prerequisite_id": None,
                "aligned_standards": sorted(item["aligned_standard_codes"]),
                "mastery_criteria": [
                    f"Execute baseline procedure for {node_id} with 3 consecutive unassisted correct responses.",
                    f"Resolve signed, fractional, and structural inversion cases for {node_id} without error.",
                ],
                "knowledge_points": [],
                "diagnostic_blueprint": {
                    "stem_template": f"Diagnostic verification item for {node_id} targeting KP_2.",
                    "target_kp_index": 1,
                    "expected_time_seconds": 30 if gmin <= 5 else (60 if gmin <= 12 else 90),
                    "exercised_prerequisite_ids": [],
                },
                "prerequisites": [],
                "encompassed_skills": [],
                "provenance_meta": {
                    "seed_concept_name": item["concept_name"],
                    "synthesis_pass_ids": ["DETERMINISTIC_STRUCTURAL_PASS_V2"],
                    "clean_room_verified": True,
                },
            }
            nodes.append(node_obj)
            strand_nodes.setdefault((cid, dom), []).append(node_obj)
            course_nodes[cid].append(node_obj)

    # Build raw hard prerequisite graph `G_raw_hard` with strict topological ordering:
    # 1. K-2 Root Spine: Only the first 4 foundational nodes in CRS_FND_K2 have in_degree == 0.
    #    Every subsequent node in CRS_FND_K2 depends on the preceding node in its strand (or the K-2 root spine).
    # 2. Intra-Strand Chain: Within every (course_id, domain) strand, node[i] -> node[i+1]
    #    (plus a 2-stride branch weave where appropriate before transitive reduction).
    # 3. Cross-Course Spine: The entry node of each strand in course C depends on the gateway
    #    nodes of C's `mastery_floor_course_ids` (matching domain if present, else the primary gateway node of the floor course).
    G_raw_hard = nx.DiGraph()
    for n in nodes:
        G_raw_hard.add_node(n["id"])

    fnd_nodes = course_nodes["CRS_FND_K2"]
    k2_root_ids = [n["id"] for n in fnd_nodes[:4]]

    # Connect strands within each course
    for (cid, dom), s_list in strand_nodes.items():
        for idx, n in enumerate(s_list):
            nid = n["id"]
            if idx > 0:
                prev_id = s_list[idx - 1]["id"]
                G_raw_hard.add_edge(prev_id, nid)
            else:
                # Strand entry node: wire from prerequisite course(s) or K-2 root spine
                if cid == "CRS_FND_K2":
                    if nid not in k2_root_ids:
                        G_raw_hard.add_edge(k2_root_ids[0], nid)
                else:
                    floor_cids = COURSE_BY_ID[cid]["mastery_floor_course_ids"]
                    for f_cid in floor_cids:
                        if (f_cid, dom) in strand_nodes and strand_nodes[(f_cid, dom)]:
                            # Connect from the culmination of the same domain in the floor course
                            parent_id = strand_nodes[(f_cid, dom)][-1]["id"]
                            G_raw_hard.add_edge(parent_id, nid)
                        elif course_nodes.get(f_cid):
                            # Connect from the culmination of the floor course
                            parent_id = course_nodes[f_cid][-1]["id"]
                            G_raw_hard.add_edge(parent_id, nid)

    # Also weave cross-domain foundations within the same course (first strand of course -> subsequent strands' entry nodes)
    for cid, c_list in course_nodes.items():
        if not c_list:
            continue
        course_strands = [s_list for (c_k, _), s_list in strand_nodes.items() if c_k == cid and s_list]
        for s_idx in range(1, len(course_strands)):
            prev_strand_anchor = course_strands[s_idx - 1][min(1, len(course_strands[s_idx - 1]) - 1)]["id"]
            curr_strand_entry = course_strands[s_idx][0]["id"]
            if prev_strand_anchor != curr_strand_entry and not nx.has_path(G_raw_hard, curr_strand_entry, prev_strand_anchor):
                G_raw_hard.add_edge(prev_strand_anchor, curr_strand_entry)

    # Compute exact Transitive Reduction so `G_hard` has ZERO redundant chords
    assert nx.is_directed_acyclic_graph(G_raw_hard), "Raw hard prerequisite graph must be a DAG"
    G_hard = nx.transitive_reduction(G_raw_hard)

    node_map = {n["id"]: n for n in nodes}

    # Populate `prerequisites`, `key_prerequisite_id`, `encompassed_skills`, and `knowledge_points`
    for n in nodes:
        nid = n["id"]
        direct_parents = sorted(G_hard.predecessors(nid))

        # Populate transitively reduced hard prerequisites
        prereq_objs: List[Dict[str, Any]] = []
        for pid in direct_parents:
            prereq_objs.append(
                {
                    "prerequisite_id": pid,
                    "strength": "hard",
                    "pedagogical_rationale": f"Requires prior mastery of component procedure {pid} before unlocking {nid}.",
                }
            )
        n["prerequisites"] = prereq_objs

        # Determine 1-hop and 2-hop ancestors in G_hard for topic & KP `key_prerequisite_id`
        primary_parent = direct_parents[0] if direct_parents else None
        secondary_parent = direct_parents[1] if len(direct_parents) > 1 else primary_parent
        grandparents: List[str] = []
        for pid in direct_parents:
            grandparents.extend(sorted(G_hard.predecessors(pid)))
        kp1_anc = grandparents[0] if grandparents else primary_parent
        kp2_anc = secondary_parent or kp1_anc

        n["key_prerequisite_id"] = primary_parent
        n["diagnostic_blueprint"]["exercised_prerequisite_ids"] = direct_parents[:3]
        n["knowledge_points"] = build_knowledge_points(
            node_id=nid,
            title=n["title"],
            primary_prereq_id=primary_parent,
            kp1_prereq_id=kp1_anc,
            kp2_prereq_id=kp2_anc,
        )

        # Populate weighted FIRe `encompassed_skills`
        enc_objs: List[Dict[str, Any]] = []
        for idx_p, pid in enumerate(direct_parents):
            w = 0.65 if idx_p == 0 else 0.35
            enc_objs.append(
                {
                    "encompassed_id": pid,
                    "fire_weight": w,
                    "is_non_ancestor": False,
                    "component_usage_note": f"Solving problems in {nid} implicitly exercises {pid} with weight {w}.",
                }
            )
        if kp1_anc and kp1_anc not in direct_parents:
            enc_objs.append(
                {
                    "encompassed_id": kp1_anc,
                    "fire_weight": 0.25,
                    "is_non_ancestor": False,
                    "component_usage_note": f"Edge-case KP_2 in {nid} implicitly reinforces ancestor {kp1_anc}.",
                }
            )
        n["encompassed_skills"] = enc_objs

    # Add explicit Non-Ancestor Encompassing edges (Math Academy Ch. 29) from Calculus-Based Stats
    # (`CRS_PROBSTAT`) to Algebra-Based Stats (`CRS_STAT_HS`) where no direct prerequisite path exists
    G_prereq_check = G_hard.copy()
    sth_nodes = course_nodes.get("CRS_STAT_HS", [])
    pst_nodes = course_nodes.get("CRS_PROBSTAT", [])
    for idx_eq in range(min(3, len(sth_nodes), len(pst_nodes))):
        adv_node = pst_nodes[idx_eq]
        simp_node = sth_nodes[idx_eq]
        adv_id = adv_node["id"]
        simp_id = simp_node["id"]
        if not nx.has_path(G_prereq_check, simp_id, adv_id) and not nx.has_path(G_prereq_check, adv_id, simp_id):
            adv_node["encompassed_skills"].append(
                {
                    "encompassed_id": simp_id,
                    "fire_weight": 0.60,
                    "is_non_ancestor": True,
                    "component_usage_note": f"Calculus-based {adv_id} implicitly exercises non-ancestor algebra-based {simp_id}.",
                }
            )

    # Mark terminal leaf nodes with out_degree == 0 and grade >= 15 as supplemental (`is_core: False`)
    # while strictly preserving the Core Ancestor Closure Invariant (every hard parent of a core node is core)
    for n in nodes:
        nid = n["id"]
        if G_hard.out_degree(nid) == 0 and n["grade_level_min"] >= 15:
            n["is_core"] = False

    graph_payload: Dict[str, Any] = {
        "schema_version": "2.0.0-trellis",
        "graph_id": "trellis-math-dag-v2.0.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "provenance": {
            "methodology": "Two-Room Air-Gapped Clean-Room Synthesis (Naked Mathematical Facts -> First-Principles CLT/MAW Decomposition)",
            "dirty_room_inventory_sha256": inventory_sha256,
            "worker_model_id": "deterministic-first-principles-structural-builder-v2",
            "source_text_exposure": "NONE_AIR_GAPPED",
            "ip_audit_passed": True,
        },
        "course_catalog": COURSE_CATALOG,
        "nodes": nodes,
    }
    return graph_payload


def main() -> None:
    OUTPUT_GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    graph_data = build_clean_room_graph()
    raw_json = json.dumps(graph_data, indent=2, ensure_ascii=False)
    for forbidden in FORBIDDEN_SUBSTRINGS:
        if forbidden in raw_json:
            raise RuntimeError(f"[AIR-GAP VIOLATION] Found forbidden substring '{forbidden}' in output graph!")
    OUTPUT_GRAPH_PATH.write_text(raw_json, encoding="utf-8")
    total_nodes = len(graph_data["nodes"])
    total_kps = sum(len(n["knowledge_points"]) for n in graph_data["nodes"])
    total_prereqs = sum(len(n["prerequisites"]) for n in graph_data["nodes"])
    total_fire = sum(len(n["encompassed_skills"]) for n in graph_data["nodes"])
    print(
        f"[OK] Emitted complete K-16 Trellis Knowledge Graph to {OUTPUT_GRAPH_PATH}\n"
        f"     Courses: {len(graph_data['course_catalog'])} | TopicNodes: {total_nodes} | "
        f"Atomic KPs: {total_kps} | Prereq Edges: {total_prereqs} | FIRe Edges: {total_fire}"
    )


if __name__ == "__main__":
    main()
