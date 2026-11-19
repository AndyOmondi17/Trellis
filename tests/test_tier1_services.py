"""
tests/test_tier1_services.py
Comprehensive test suite for Tier 1:
  - FR-1.1: Dual-Edge Graph Traversal Service
  - FR-1.2: Course Tracks, Core Prioritization & Mastery Floors
  - FR-1.3: Decoupled Content Shell Registry & SymPy Problem Blueprint Engine
Parameterized across all standalone database backends: memory, relational, graph.
"""

import time
import pytest
import sympy

from trellis.domain.models import (
    DiagnosticMisconception,
    KPContentShell,
    ProblemBlueprint,
    StudentProfile,
    StudentTopicState,
    TopicNode,
)
from trellis.engine.content_blueprint_solver import (
    BlueprintInstance,
    ConstraintSampler,
    ContentBlueprintSolver,
    SafeSympyParser,
)
from trellis.engine.topology_service import TopologyService
from trellis.ports.storage_port import get_storage_adapter


SAMPLE_TIER1_GRAPH = {
    "schema_version": "2.0.0-trellis",
    "graph_id": "trellis-tier1-dag",
    "course_catalog": [
        {
            "course_id": "CRS_CALC1",
            "course_code": "ca1",
            "title": "Calculus I",
            "grade_band": "13-14",
            "benchmark_xp": 2000,
            "mastery_floor_course_ids": ["CRS_PRECALC"],
        },
        {
            "course_id": "CRS_PRECALC",
            "course_code": "prc",
            "title": "Pre-Calculus",
            "grade_band": "11-12",
            "benchmark_xp": 1800,
            "mastery_floor_course_ids": ["CRS_ALG1"],
        },
        {
            "course_id": "CRS_ALG1",
            "course_code": "alg",
            "title": "Algebra I",
            "grade_band": "8-9",
            "benchmark_xp": 1500,
            "mastery_floor_course_ids": [],
        },
        {
            "course_id": "CRS_GEOM",
            "course_code": "geo",
            "title": "Geometry (Unrelated Supplemental)",
            "grade_band": "9-10",
            "benchmark_xp": 1400,
            "mastery_floor_course_ids": [],
        },
    ],
    "nodes": [
        # CRS_ALG1 nodes
        {
            "id": "trl_alg_lin_001",
            "title": "One-Step Linear Equations",
            "course_id": "CRS_ALG1",
            "canonical_domain": "03_equations_and_inequalities",
            "domain_cluster": "CRS_ALG1::lin",
            "skill_type": "PROCEDURAL",
            "bloom_cognitive_tier": "PROCEDURAL_EXECUTION",
            "grade_level_min": 8,
            "grade_level_max": 9,
            "is_core": True,
            "in_foundations_sequence": True,
            "requires_automaticity": False,
            "base_difficulty": 0.2,
            "estimated_xp": 3,
            "mastery_gate_n": 3,
            "key_prerequisite_id": None,
            "aligned_standards": ["ccss-math:HSA-REI.B.3"],
            "mastery_criteria": ["Solve one-step equations"],
            "knowledge_points": [
                {
                    "kp_index": 0,
                    "kp_id": "trl_alg_lin_001#kp_0",
                    "scaffolding_tier": "KP_1_BASELINE",
                    "kp_focus": "Additive inverse linear step",
                    "novel_chunk_count": 2,
                    "rule_statement": "Add the additive inverse to isolate x.",
                    "problem_blueprint": {
                        "parameter_constraints": "p_1 in {2..9}, p_2 in {-12..12} \\ {0}, gcd(p_1, p_2) = 1",
                        "stem_template": "Solve for x: x + {p_2} = {p_1}",
                        "answer_format": "ALGEBRAIC_EXPRESSION",
                        "verification_solver_expr": "sympy.simplify(target_expr - student_expr) == 0",
                    },
                    "diagnostic_misconceptions": [
                        {
                            "error_tag": "ERR_SIGN_INV",
                            "trap_answer_pattern": "{p_1} + {p_2}",
                            "violated_rule": "Sign inversion across equality boundary.",
                            "tier1_remediation_template": "Subtract p_2 rather than adding it.",
                        }
                    ],
                }
            ],
            "prerequisites": [],
            "encompassed_skills": [],
        },
        # CRS_PRECALC nodes
        {
            "id": "trl_prc_fun_001",
            "title": "Function Inverses",
            "course_id": "CRS_PRECALC",
            "canonical_domain": "04_functions_and_graphs",
            "domain_cluster": "CRS_PRECALC::fun",
            "skill_type": "CONCEPTUAL",
            "bloom_cognitive_tier": "CONCEPTUAL_CLASSIFICATION",
            "grade_level_min": 11,
            "grade_level_max": 12,
            "is_core": True,
            "in_foundations_sequence": True,
            "requires_automaticity": False,
            "base_difficulty": 0.5,
            "estimated_xp": 4,
            "mastery_gate_n": 3,
            "key_prerequisite_id": "trl_alg_lin_001",
            "aligned_standards": ["ccss-math:HSF-BF.B.4"],
            "mastery_criteria": ["Find inverse functions"],
            "knowledge_points": [
                {
                    "kp_index": 0,
                    "kp_id": "trl_prc_fun_001#kp_0",
                    "scaffolding_tier": "KP_1_BASELINE",
                    "kp_focus": "Finding inverse of linear function",
                    "novel_chunk_count": 2,
                    "rule_statement": "Swap x and y, then solve for y.",
                }
            ],
            "prerequisites": [
                {
                    "prerequisite_id": "trl_alg_lin_001",
                    "strength": "hard",
                    "rationale": "Linear equation solving needed to invert linear functions.",
                }
            ],
            "encompassed_skills": [
                {
                    "encompassed_id": "trl_alg_lin_001",
                    "fire_weight": 0.5,
                    "is_non_ancestor": False,
                    "rationale": "Solving for swapped variable exercises linear equation solving.",
                }
            ],
        },
        {
            "id": "trl_prc_rec_001",
            "title": "Recreational Curves & Spirals (Non-Foundations)",
            "course_id": "CRS_PRECALC",
            "canonical_domain": "04_functions_and_graphs",
            "domain_cluster": "CRS_PRECALC::fun",
            "skill_type": "CONCEPTUAL",
            "bloom_cognitive_tier": "CONCEPTUAL_CLASSIFICATION",
            "grade_level_min": 11,
            "grade_level_max": 12,
            "is_core": False,  # Supplemental
            "in_foundations_sequence": False,  # Excluded from accelerated MF track
            "requires_automaticity": False,
            "base_difficulty": 0.6,
            "estimated_xp": 3,
            "mastery_gate_n": 3,
            "key_prerequisite_id": None,
            "aligned_standards": [],
            "mastery_criteria": ["Plot polar spirals"],
            "knowledge_points": [
                {
                    "kp_index": 0,
                    "kp_id": "trl_prc_rec_001#kp_0",
                    "scaffolding_tier": "KP_1_BASELINE",
                    "kp_focus": "Archimedean spirals",
                    "novel_chunk_count": 2,
                }
            ],
            "prerequisites": [],
            "encompassed_skills": [],
        },
        # CRS_CALC1 nodes
        {
            "id": "trl_ca1_der_001",
            "title": "Derivative Definition and Limits",
            "course_id": "CRS_CALC1",
            "canonical_domain": "06_differential_calculus",
            "domain_cluster": "CRS_CALC1::der",
            "skill_type": "PROCEDURAL",
            "bloom_cognitive_tier": "PROCEDURAL_EXECUTION",
            "grade_level_min": 13,
            "grade_level_max": 14,
            "is_core": True,
            "in_foundations_sequence": True,
            "requires_automaticity": False,
            "base_difficulty": 0.7,
            "estimated_xp": 5,
            "mastery_gate_n": 3,
            "key_prerequisite_id": "trl_prc_fun_001",
            "aligned_standards": [],
            "mastery_criteria": ["Evaluate difference quotients"],
            "knowledge_points": [
                {
                    "kp_index": 0,
                    "kp_id": "trl_ca1_der_001#kp_0",
                    "scaffolding_tier": "KP_1_BASELINE",
                    "kp_focus": "Difference quotient limit definition",
                    "novel_chunk_count": 3,
                }
            ],
            "prerequisites": [
                {
                    "prerequisite_id": "trl_prc_fun_001",
                    "strength": "hard",
                    "rationale": "Functions needed for difference quotients.",
                }
            ],
            "encompassed_skills": [
                {
                    "encompassed_id": "trl_prc_fun_001",
                    "fire_weight": 0.4,
                    "is_non_ancestor": False,
                    "rationale": "Evaluating limits of difference quotients exercises function algebra.",
                }
            ],
        },
        # CRS_GEOM node (unrelated high school geometry)
        {
            "id": "trl_geo_tri_001",
            "title": "Congruent Triangles SSS/SAS",
            "course_id": "CRS_GEOM",
            "canonical_domain": "08_geometry_euclidean",
            "domain_cluster": "CRS_GEOM::tri",
            "skill_type": "THEOREM_PROOF",
            "bloom_cognitive_tier": "FORMAL_DERIVATION",
            "grade_level_min": 9,
            "grade_level_max": 10,
            "is_core": True,
            "in_foundations_sequence": False,
            "requires_automaticity": False,
            "base_difficulty": 0.45,
            "estimated_xp": 4,
            "mastery_gate_n": 3,
            "key_prerequisite_id": None,
            "aligned_standards": ["ccss-math:HSG-CO.B.8"],
            "mastery_criteria": ["Prove triangle congruence"],
            "knowledge_points": [
                {
                    "kp_index": 0,
                    "kp_id": "trl_geo_tri_001#kp_0",
                    "scaffolding_tier": "KP_1_BASELINE",
                    "kp_focus": "Triangle congruence axioms",
                    "novel_chunk_count": 2,
                }
            ],
            "prerequisites": [],
            "encompassed_skills": [],
        },
    ],
}


@pytest.fixture(params=["memory", "relational", "graph"])
def tier1_fixture(request):
    """Initializes adapter, ingests Tier 1 sample graph, and constructs services."""
    backend = request.param
    ad = get_storage_adapter(backend)
    ad.ingest_knowledge_graph(SAMPLE_TIER1_GRAPH)
    topo = TopologyService(ad)
    solver = ContentBlueprintSolver(ad)
    yield ad, topo, solver
    if hasattr(ad, "close"):
        ad.close()


# ---------------------------------------------------------------------------
# Test FR-1.1: Dual-Edge Graph Traversals
# ---------------------------------------------------------------------------

def test_fr1_1_dual_edge_traversals(tier1_fixture):
    """Verifies deterministic traversal over G_hard, G_prereq, and G_enc."""
    ad, topo, _ = tier1_fixture

    # 1. Hard parents
    parents = topo.get_hard_parents("trl_ca1_der_001")
    assert len(parents) == 1
    assert parents[0].id == "trl_prc_fun_001"
    assert topo.get_hard_parent_ids("trl_ca1_der_001") == ["trl_prc_fun_001"]

    # 2. Postrequisites
    postreqs = topo.get_postrequisites("trl_alg_lin_001")
    postreq_ids = {p.id for p in postreqs}
    assert "trl_prc_fun_001" in postreq_ids
    assert "trl_ca1_der_001" in postreq_ids

    # 1-hop postrequisites
    postreqs_1hop = topo.get_postrequisites("trl_alg_lin_001", max_hops=1)
    assert [p.id for p in postreqs_1hop] == ["trl_prc_fun_001"]

    # 3. Encompassed ancestors (FIRe trickle-down paths)
    enc_paths = topo.get_encompassed_ancestors("trl_ca1_der_001", max_hops=2)
    path_map = {p.ancestor_id: p.compound_weight for p in enc_paths}
    # Hop 1: trl_prc_fun_001 (w=0.4)
    # Hop 2: trl_alg_lin_001 (w=0.4 * 0.5 = 0.2)
    assert path_map["trl_prc_fun_001"] == pytest.approx(0.4, abs=1e-4)
    assert path_map["trl_alg_lin_001"] == pytest.approx(0.2, abs=1e-4)

    # Node pairs helper
    enc_nodes = topo.get_encompassed_ancestor_nodes("trl_ca1_der_001", max_hops=2)
    assert len(enc_nodes) == 2
    assert {n.id for n, _ in enc_nodes} == {"trl_prc_fun_001", "trl_alg_lin_001"}

    # 4. Encompassing descendants (Summer slide penalty trickle-up paths)
    desc_paths = topo.get_encompassing_descendants("trl_alg_lin_001", max_hops=2)
    desc_map = {p.ancestor_id: p.compound_weight for p in desc_paths}
    assert "trl_prc_fun_001" in desc_map
    assert "trl_ca1_der_001" in desc_map

    # 5. Local Neighborhood
    neigh = topo.get_local_neighborhood("trl_prc_fun_001")
    assert neigh.direct_prerequisites == ["trl_alg_lin_001"]
    assert neigh.key_prerequisite_id == "trl_alg_lin_001"
    assert "trl_alg_lin_001" in neigh.encompassed_ancestors
    assert "trl_ca1_der_001" in neigh.encompassing_descendants
    # cluster sibling
    assert "trl_prc_rec_001" in neigh.cluster_siblings


# ---------------------------------------------------------------------------
# Test FR-1.2: Course Tracks, Core Prioritization & Mastery Floors
# ---------------------------------------------------------------------------

def test_fr1_2_core_closure_and_prioritization(tier1_fixture):
    """Verifies Core Ancestor Closure checking and topic prioritization."""
    _, topo, _ = tier1_fixture

    # The clean sample graph satisfies core ancestor closure
    violations = topo.validate_core_ancestor_closure()
    assert len(violations) == 0

    # Test prioritization: core nodes placed strictly before supplemental
    nodes = [
        topo.storage.get_topic_node("trl_prc_rec_001"),  # is_core == False
        topo.storage.get_topic_node("trl_alg_lin_001"),  # is_core == True
        topo.storage.get_topic_node("trl_ca1_der_001"),  # is_core == True
    ]
    prioritized = topo.prioritize_core_nodes(nodes)
    assert prioritized[0].is_core is True
    assert prioritized[1].is_core is True
    assert prioritized[2].is_core is False
    assert prioritized[2].id == "trl_prc_rec_001"


def test_fr1_2_foundations_track_filtering(tier1_fixture):
    """Verifies accelerated Mathematical Foundations (MF) sequence filtering."""
    _, topo, _ = tier1_fixture

    # Entire graph foundations filter
    fnd_nodes = topo.get_foundations_subgraph()
    fnd_ids = {n.id for n in fnd_nodes}
    assert "trl_alg_lin_001" in fnd_ids
    assert "trl_prc_fun_001" in fnd_ids
    assert "trl_ca1_der_001" in fnd_ids
    # Non-foundations topics pruned
    assert "trl_prc_rec_001" not in fnd_ids
    assert "trl_geo_tri_001" not in fnd_ids

    # Course-specific foundations filter
    prc_fnd = topo.get_foundations_subgraph("CRS_PRECALC")
    assert len(prc_fnd) == 1
    assert prc_fnd[0].id == "trl_prc_fun_001"


def test_fr1_2_mastery_floors_and_remediation_restriction(tier1_fixture):
    """Verifies University Course Mastery Floors and Non-Holistic Targeted Remediation."""
    ad, topo, _ = tier1_fixture
    student_id = "usr_univ_sophomore"

    # Profile for university student enrolled in Calculus I
    ad.upsert_student_profile(
        StudentProfile(
            student_id=student_id,
            enrolled_course_id="CRS_CALC1",
            xp_total=0,
        )
    )

    # 1. Apply mastery floors for CRS_CALC1 (mastery_floor_course_ids = ["CRS_PRECALC"])
    credited = topo.apply_mastery_floors(student_id, "CRS_CALC1")
    assert len(credited) >= 1
    credited_ids = {s.topic_id for s in credited}
    assert "trl_prc_fun_001" in credited_ids

    # Check persisted student topic state
    states = ad.get_student_topic_states(student_id, ["trl_prc_fun_001"])
    assert states["trl_prc_fun_001"].status == "MASTERED_FLOOR"
    assert states["trl_prc_fun_001"].rep_num == 3.0

    # 2. Non-Holistic Targeted Remediation: Ancestor Subgraph
    calc1_ancestors = topo.get_course_ancestor_prerequisite_subgraph("CRS_CALC1")
    assert "trl_ca1_der_001" in calc1_ancestors
    assert "trl_prc_fun_001" in calc1_ancestors
    assert "trl_alg_lin_001" in calc1_ancestors
    # Geometry is NOT an ancestor of Calculus I: strictly excluded!
    assert "trl_geo_tri_001" not in calc1_ancestors
    assert "trl_prc_rec_001" not in calc1_ancestors

    # 3. Restrict candidate remediation list
    candidates = ["trl_alg_lin_001", "trl_geo_tri_001", "trl_prc_fun_001", "trl_prc_rec_001"]
    restricted = topo.restrict_remediation_to_course_ancestors("CRS_CALC1", candidates)
    assert set(restricted) == {"trl_alg_lin_001", "trl_prc_fun_001"}
    assert "trl_geo_tri_001" not in restricted


# ---------------------------------------------------------------------------
# Test FR-1.3: SymPy Problem Blueprints, Grader & Content Shells
# ---------------------------------------------------------------------------

def test_fr1_3_content_shell_registry_and_lifecycle(tier1_fixture):
    """Verifies decoupled content shell retrieval, update, status transitions, and SVG attachment."""
    _, _, solver = tier1_fixture
    kp_id = "trl_alg_lin_001#kp_0"

    # Retrieve initial shell
    shell = solver.get_content_shell(kp_id)
    assert shell.kp_id == kp_id
    assert shell.content_status == "BLUEPRINT_READY"

    # Attach SVG schema
    svg_uri = "assets/schemas/linear_balance_scale.svg"
    updated_shell = solver.attach_svg_asset(kp_id, svg_uri)
    assert updated_shell.svg_asset_uri == svg_uri

    # Valid transitions: BLUEPRINT_READY -> LLM_DRAFTED -> VERIFIED_PRODUCTION
    t1 = solver.transition_shell_status(kp_id, "LLM_DRAFTED")
    assert t1.content_status == "LLM_DRAFTED"

    t2 = solver.transition_shell_status(kp_id, "VERIFIED_PRODUCTION")
    assert t2.content_status == "VERIFIED_PRODUCTION"

    # Invalid transition raises ValueError
    with pytest.raises(ValueError):
        solver.transition_shell_status(kp_id, "LLM_DRAFTED")


def test_fr1_3_constraint_sampler():
    """Verifies deterministic parameter sampling from blueprint constraints."""
    dsl = "p_1 in {2..9}, p_2 in {-12..12} \\ {0}, gcd(p_1, p_2) = 1"

    # Seeded sampling is deterministic
    sample1 = ConstraintSampler.parse_and_sample(dsl, seed=42)
    sample2 = ConstraintSampler.parse_and_sample(dsl, seed=42)
    assert sample1 == sample2
    assert 2 <= sample1["p_1"] <= 9
    assert -12 <= sample1["p_2"] <= 12
    assert sample1["p_2"] != 0
    import math
    assert math.gcd(sample1["p_1"], sample1["p_2"]) == 1

    # Discrete choice and comparison
    dsl_cond = "a in [10, 20, 30], b in {1..5}, a > b"
    s_cond = ConstraintSampler.parse_and_sample(dsl_cond, seed=123)
    assert s_cond["a"] in [10, 20, 30]
    assert s_cond["b"] in [1, 2, 3, 4, 5]
    assert s_cond["a"] > s_cond["b"]


def test_fr1_3_safe_sympy_parser():
    """Verifies AST safety guards (no eval, no dunders, no arbitrary execution) and LaTeX conversion."""
    # 1. LaTeX to SymPy conversion
    expr1 = SafeSympyParser.parse_safely(r"\frac{3x + 1}{2}")
    expr2 = SafeSympyParser.parse_safely("(3*x + 1)/2")
    assert sympy.simplify(expr1 - expr2) == 0

    # 2. Implicit multiplication and exponents
    expr3 = SafeSympyParser.parse_safely("2x^2 + 3x - 5")
    x = sympy.Symbol("x")
    assert expr3.equals(2 * x**2 + 3 * x - 5)

    # 3. Security: Block arbitrary code execution and dunder access
    unsafe_inputs = [
        "__import__('os').system('ls')",
        "().__class__.__bases__[0].__subclasses__()",
        "open('secrets.txt').read()",
        "eval('2+2')",
        "exec('import sys')",
    ]
    for bad in unsafe_inputs:
        with pytest.raises(ValueError):
            SafeSympyParser.parse_safely(bad)


def test_fr1_3_dynamic_variant_generation(tier1_fixture):
    """Verifies variant generation from ProblemBlueprint with rendered LaTeX stem."""
    _, _, solver = tier1_fixture
    kp_id = "trl_alg_lin_001#kp_0"

    variant = solver.generate_variant(kp_id, seed=99)
    assert isinstance(variant, BlueprintInstance)
    assert variant.kp_id == kp_id
    assert "p_1" in variant.parameters
    assert "p_2" in variant.parameters
    p1 = variant.parameters["p_1"]
    p2 = variant.parameters["p_2"]
    assert f"x + {p2} = {p1}" in variant.rendered_stem


def test_fr1_3_mathematical_equivalence_grading(tier1_fixture):
    """Verifies algebraic and numeric equivalence grading via SymPy."""
    _, _, solver = tier1_fixture

    # Simple arithmetic / algebra
    assert solver.grade_student_answer("2x + 4", "2*(x + 2)") is True
    assert solver.grade_student_answer("x / 2", r"\frac{1}{2}x") is True
    assert solver.grade_student_answer(r"\sin^2(x) + \cos^2(x)", "1") is True
    assert solver.grade_student_answer("x^2 - 1", "(x - 1)*(x + 1)") is True

    # Parameter substitution
    params = {"p_1": 5, "p_2": 3}
    # Target: p_1 - p_2 = 2
    assert solver.grade_student_answer("2", "{p_1} - {p_2}", parameters=params) is True
    assert solver.grade_student_answer("5 - 3", "{p_1} - {p_2}", parameters=params) is True
    assert solver.grade_student_answer("10", "{p_1} - {p_2}", parameters=params) is False

    # Invalid syntax does not crash, safely returns False
    assert solver.grade_student_answer("+++", "2") is False


def test_fr1_3_trap_misconception_matching(tier1_fixture):
    """Verifies fast-path (<5 ms) misconception pattern matching."""
    _, _, solver = tier1_fixture

    misconceptions = [
        DiagnosticMisconception(
            error_tag="ERR_SIGN_INV",
            trap_answer_pattern="{p_1} + {p_2}",
            violated_rule="Sign inversion across equality boundary.",
            tier1_remediation_template="Subtract p_2 rather than adding it.",
        ),
        DiagnosticMisconception(
            error_tag="ERR_RECIPROCAL",
            trap_answer_pattern="1 / {p_1}",
            violated_rule="Incorrect reciprocal operation.",
            tier1_remediation_template="Multiply by p_1 instead of dividing.",
        ),
    ]

    params = {"p_1": 7, "p_2": 4}
    # Trap answer: 7 + 4 = 11

    # Measure latency for fast-path check
    t0 = time.perf_counter()
    matched = solver.match_diagnostic_misconception("11", misconceptions, parameters=params)
    duration_ms = (time.perf_counter() - t0) * 1000.0

    assert duration_ms < 20.0  # Well within test tolerances, typically < 3 ms
    assert matched is not None
    assert matched.error_tag == "ERR_SIGN_INV"
    assert "Sign inversion" in matched.violated_rule

    # Non-trap wrong answer
    assert solver.match_diagnostic_misconception("99", misconceptions, parameters=params) is None
