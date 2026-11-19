#!/usr/bin/env python3
"""
scripts/validate_math_dag.py
Production Mathematical & HSRS/FIRe Graph Validator for Trellis (`networkx`).
Exits with code 0 only if all DAG, connectivity, transitive reduction,
Core-closure, and FIRe weight invariants pass.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import networkx as nx

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
GRAPH_PATH = WORKSPACE_ROOT / "data" / "clean_spec" / "trellis_knowledge_graph.json"


def validate_trellis_dag(graph_data: Dict[str, Any]) -> Tuple[List[str], List[str], Dict[str, Any]]:
    errors: List[str] = []
    warnings: List[str] = []

    nodes = graph_data.get("nodes", [])
    node_map: Dict[str, Dict[str, Any]] = {}

    G_prereq = nx.DiGraph()      # u (prereq) -> v (dependent)
    G_hard = nx.DiGraph()        # u (hard prereq) -> v (dependent)
    G_enc = nx.DiGraph()         # v (advanced) -> u (encompassed simpler skill)
    G_hierarchy = nx.DiGraph()   # unified simpler -> advanced orientation

    # 1. Node registration & uniqueness check
    for n in nodes:
        nid = n["id"]
        if nid in node_map:
            errors.append(f"Duplicate node ID: {nid}")
        node_map[nid] = n
        G_prereq.add_node(nid)
        G_hard.add_node(nid)
        G_enc.add_node(nid)
        G_hierarchy.add_node(nid)

    # 2. Edge construction & referential integrity
    for n in nodes:
        nid = n["id"]
        is_root_grade = n.get("grade_level_min", 0) <= 2

        # Validate topic-level key_prerequisite_id existence
        kpid = n.get("key_prerequisite_id")
        if not is_root_grade and not kpid:
            errors.append(f"Node {nid} (grade_min={n.get('grade_level_min')}) missing key_prerequisite_id")
        if kpid and kpid not in node_map:
            errors.append(f"Node {nid} references unknown key_prerequisite_id {kpid}")
        if kpid == nid:
            errors.append(f"Node {nid} cannot reference itself as key_prerequisite_id")

        # Validate KP-level scaffolding and KP key_prerequisite_id existence
        kps = n.get("knowledge_points", [])
        if not (2 <= len(kps) <= 4):
            errors.append(f"Node {nid} has {len(kps)} KPs (must be 2..4)")
        for idx, kp in enumerate(kps):
            if kp.get("kp_index") != idx:
                errors.append(f"Node {nid} KP index mismatch at position {idx}")
            if kp.get("novel_chunk_count", 5) > 4:
                errors.append(f"Node {nid} KP {idx} exceeds CLT limit (novel_chunk_count > 4)")
            kp_prereq = kp.get("key_prerequisite_id")
            if not is_root_grade and not kp_prereq:
                errors.append(f"Node {nid} KP {idx} missing KP-level key_prerequisite_id")
            if kp_prereq and kp_prereq not in node_map:
                errors.append(f"Node {nid} KP {idx} references unknown key_prerequisite_id {kp_prereq}")
            if kp_prereq == nid:
                errors.append(f"Node {nid} KP {idx} cannot reference its own parent node as key_prerequisite_id")

        # Prerequisite edges
        for p in n.get("prerequisites", []):
            pid = p["prerequisite_id"]
            strength = p["strength"]
            if pid not in node_map:
                errors.append(f"Node {nid} references unknown prerequisite_id {pid}")
                continue
            if pid == nid:
                errors.append(f"Self-prerequisite cycle on {nid}")
                continue
            G_prereq.add_edge(pid, nid, strength=strength)
            G_hierarchy.add_edge(pid, nid)
            if strength == "hard":
                G_hard.add_edge(pid, nid)
                # Core closure check (Math Academy Ch. 32)
                if n.get("is_core") and not node_map[pid].get("is_core"):
                    errors.append(
                        f"Core Closure Violation: Core node {nid} depends on non-core hard prereq {pid}"
                    )

        # Encompassing (FIRe) edges
        for e in n.get("encompassed_skills", []):
            eid = e["encompassed_id"]
            w = e.get("fire_weight", 0.0)
            is_non_anc = bool(e.get("is_non_ancestor", False))
            if eid not in node_map:
                errors.append(f"Node {nid} references unknown encompassed_id {eid}")
                continue
            if eid == nid:
                errors.append(f"Self-encompassing loop on {nid}")
                continue
            if not (0.0 < w <= 1.0):
                errors.append(f"Invalid fire_weight {w} on encompassing edge {nid} -> {eid}")
            G_enc.add_edge(nid, eid, fire_weight=w, is_non_ancestor=is_non_anc)
            # In hierarchy terms, encompassed skill `eid` is simpler than `nid`
            G_hierarchy.add_edge(eid, nid)

    # 3. Strict Acyclicity Checks
    prereq_is_dag = nx.is_directed_acyclic_graph(G_prereq)
    hard_is_dag = nx.is_directed_acyclic_graph(G_hard)

    if not prereq_is_dag:
        cycle = nx.find_cycle(G_prereq)
        errors.append(f"Prerequisite Graph contains a directed cycle: {cycle}")

    if not nx.is_directed_acyclic_graph(G_enc):
        cycle = nx.find_cycle(G_enc)
        errors.append(f"Encompassing (FIRe) Graph contains a directed cycle: {cycle}")

    if not nx.is_directed_acyclic_graph(G_hierarchy):
        cycle = nx.find_cycle(G_hierarchy)
        errors.append(
            f"Combined Prereq/Encompassing Hierarchy Inversion (A requires B, but B is encompassed by A): {cycle}"
        )

    # 4. Topological Edge Case 1: Multi-Hop `key_prerequisite_id` Ancestor Verification
    #    & Strict Chord-Free Transitive Reduction on G_hard
    if hard_is_dag and G_hard.number_of_nodes() > 0:
        for n in nodes:
            nid = n["id"]
            kpid = n.get("key_prerequisite_id")
            if kpid and kpid in node_map and kpid != nid:
                if not nx.has_path(G_hard, kpid, nid):
                    errors.append(
                        f"Tier-3 Remediation Routing Error: Node {nid} key_prerequisite_id {kpid} "
                        f"is not a reachable ancestor in G_hard"
                    )
            for idx, kp in enumerate(n.get("knowledge_points", [])):
                kp_prereq = kp.get("key_prerequisite_id")
                if kp_prereq and kp_prereq in node_map and kp_prereq != nid:
                    if not nx.has_path(G_hard, kp_prereq, nid):
                        errors.append(
                            f"Tier-3 KP Remediation Routing Error: Node {nid} KP {idx} "
                            f"key_prerequisite_id {kp_prereq} is not a reachable ancestor in G_hard"
                        )

        TR_hard = nx.transitive_reduction(G_hard)
        redundant_edges = set(G_hard.edges()) - set(TR_hard.edges())
        for u, v in sorted(redundant_edges):
            errors.append(
                f"Transitive Reduction Violation: Redundant hard prerequisite chord {u} -> {v} "
                f"(keep multi-hop references in key_prerequisite_id, not in direct prerequisites)"
            )

    # 5. Topological Edge Case 2: Ancestor vs. Non-Ancestor Encompassing Verification
    if prereq_is_dag:
        for u_adv, v_simp, edata in G_enc.edges(data=True):
            is_non_anc = edata.get("is_non_ancestor", False)
            has_prereq_path = nx.has_path(G_prereq, v_simp, u_adv)
            if not is_non_anc and not has_prereq_path:
                errors.append(
                    f"Unmarked Non-Ancestor Encompassing: {u_adv} ->_enc {v_simp} has is_non_ancestor=False "
                    f"but {v_simp} is not a prerequisite ancestor of {u_adv}"
                )
            elif is_non_anc and nx.has_path(G_prereq, u_adv, v_simp):
                errors.append(
                    f"Inverted Non-Ancestor Encompassing: {u_adv} ->_enc {v_simp} attempts to encompass "
                    f"a downstream post-requisite {v_simp}"
                )

    # 6. Full K-16 Continuum Connectivity & Orphan Audit
    roots = [n for n, deg in G_hard.in_degree() if deg == 0]
    terminals = [n for n, deg in G_hard.out_degree() if deg == 0]
    k2_roots = [r for r in roots if node_map[r].get("grade_level_min", 0) <= 2]

    for r in roots:
        gmin = node_map[r].get("grade_level_min", 0)
        if gmin > 2:
            errors.append(
                f"Orphaned Post-K2 Root Node: {r} ('{node_map[r].get('title')}', grade_min={gmin}) has in_degree=0"
            )

    if G_prereq.number_of_nodes() > 0:
        wcc_count = nx.number_weakly_connected_components(G_prereq)
        if wcc_count > 1:
            errors.append(f"Graph is fragmented into {wcc_count} weakly connected components (must be 1)")

        # Verify EVERY non-root node across Grades 3-16 is reachable from at least one K-2 root
        if hard_is_dag and k2_roots:
            reachable_from_k2: Set[str] = set()
            for kr in k2_roots:
                reachable_from_k2.update(nx.descendants(G_hard, kr))
                reachable_from_k2.add(kr)
            for nid, n in node_map.items():
                if n.get("grade_level_min", 0) > 2 and nid not in reachable_from_k2:
                    errors.append(
                        f"Disconnected K-16 Node {nid} ('{n.get('title')}', grade_min={n.get('grade_level_min')}) "
                        f"is unreachable from K-2 foundation roots"
                    )

    stats = {
        "total_nodes": len(nodes),
        "prereq_edges": G_prereq.number_of_edges(),
        "hard_prereq_edges": G_hard.number_of_edges(),
        "encompassing_edges": G_enc.number_of_edges(),
        "k2_roots": len(k2_roots),
        "terminal_nodes": len(terminals),
        "dag_depth_longest_path": (
            nx.dag_longest_path_length(G_hard)
            if hard_is_dag and G_hard.number_of_nodes() > 0
            else 0
        ),
    }
    return errors, warnings, stats


def main() -> None:
    if not GRAPH_PATH.exists():
        print(f"[ERROR] Graph file not found at {GRAPH_PATH}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    errors, warnings, stats = validate_trellis_dag(data)

    print("=== TRELLIS MATHEMATICAL DAG VALIDATION REPORT ===")
    print(json.dumps(stats, indent=2))
    for w in warnings[:20]:
        print(f"  [WARN] {w}")
    if errors:
        print(f"\n[FAIL] {len(errors)} validation error(s) detected:")
        for e in errors:
            print(f"  [ERR]  {e}")
        sys.exit(1)

    print("\n[PASS] All DAG, acyclicity, connectivity, Core-closure, and FIRe invariants verified.")


if __name__ == "__main__":
    main()
