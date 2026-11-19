"""
src/trellis/services/ingestion_service.py
Clean-Room Knowledge Graph Ingestion & Version Delta Tracker (FR-0.3).
Validates, ingests, and tracks topological deltas between curriculum graph versions.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from trellis.domain.models import GraphIngestionReport, TopicNode
from trellis.ports.storage_port import TrellisStoragePort

logger = logging.getLogger(__name__)


class KnowledgeGraphIngestionService:
    """
    Manages the lifecycle, topological validation, and delta tracking
    of Trellis knowledge graphs into the active persistence backend.
    """

    def __init__(self, storage_port: TrellisStoragePort) -> None:
        self.storage = storage_port

    def ingest_from_file(
        self,
        graph_file_path: Path | str,
        validate_first: bool = True,
    ) -> GraphIngestionReport:
        """Loads a clean-room JSON knowledge graph from disk and ingests it."""
        path = Path(graph_file_path)
        if not path.exists():
            raise FileNotFoundError(f"Knowledge graph file not found at: {path}")

        payload = json.loads(path.read_text(encoding="utf-8"))
        return self.ingest_payload(payload, validate_first=validate_first)

    def ingest_payload(
        self,
        graph_payload: Dict[str, Any],
        validate_first: bool = True,
    ) -> GraphIngestionReport:
        """Ingests a graph dictionary payload into the storage port."""
        if validate_first:
            self.validate_graph_payload(graph_payload)

        return self.storage.ingest_knowledge_graph(graph_payload)

    def validate_graph_payload(self, graph_payload: Dict[str, Any]) -> None:
        """Runs fast in-memory DAG integrity checks prior to persistence."""
        schema_version = graph_payload.get("schema_version")
        if schema_version != "2.0.0-trellis":
            raise ValueError(f"Invalid schema_version '{schema_version}'. Expected '2.0.0-trellis'.")

        nodes = graph_payload.get("nodes", [])
        if not nodes:
            raise ValueError("Knowledge graph payload contains zero nodes.")

        # Check unique IDs
        node_ids: Set[str] = set()
        for n in nodes:
            nid = n.get("id")
            if not nid:
                raise ValueError("Encountered node without 'id' field.")
            if nid in node_ids:
                raise ValueError(f"Duplicate node ID found: {nid}")
            node_ids.add(nid)

        # Referential checks on prerequisites and encompassings
        for n in nodes:
            nid = n["id"]
            for p in n.get("prerequisites", []):
                pid = p["prerequisite_id"]
                if pid not in node_ids:
                    raise ValueError(f"Node {nid} has missing prerequisite {pid}")
            for e in n.get("encompassed_skills", []):
                eid = e["encompassed_id"]
                if eid not in node_ids:
                    raise ValueError(f"Node {nid} has missing encompassed skill {eid}")

    def compute_structural_delta(
        self,
        base_payload: Dict[str, Any],
        new_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Computes the structural delta between two graph snapshots.
        Used for Supplemental Diagnostics on newly added or rewired nodes (FR-2.3).
        """
        base_nodes = {n["id"]: n for n in base_payload.get("nodes", [])}
        new_nodes = {n["id"]: n for n in new_payload.get("nodes", [])}

        base_ids = set(base_nodes.keys())
        new_ids = set(new_nodes.keys())

        added_nodes = sorted(list(new_ids - base_ids))
        removed_nodes = sorted(list(base_ids - new_ids))
        retained_ids = base_ids & new_ids

        rewired_edges: List[Dict[str, Any]] = []
        updated_kps: List[str] = []

        for nid in sorted(retained_ids):
            b_node = base_nodes[nid]
            n_node = new_nodes[nid]

            # Compare prerequisite sets
            b_prereqs = {(p["prerequisite_id"], p.get("strength", "hard")) for p in b_node.get("prerequisites", [])}
            n_prereqs = {(p["prerequisite_id"], p.get("strength", "hard")) for p in n_node.get("prerequisites", [])}

            if b_prereqs != n_prereqs:
                rewired_edges.append({
                    "topic_id": nid,
                    "added_prereqs": sorted(list(n_prereqs - b_prereqs)),
                    "removed_prereqs": sorted(list(b_prereqs - n_prereqs)),
                })

            # Compare KP count or IDs
            b_kp_ids = [kp["kp_id"] for kp in b_node.get("knowledge_points", [])]
            n_kp_ids = [kp["kp_id"] for kp in n_node.get("knowledge_points", [])]
            if b_kp_ids != n_kp_ids:
                updated_kps.append(nid)

        return {
            "added_node_count": len(added_nodes),
            "added_nodes": added_nodes,
            "removed_node_count": len(removed_nodes),
            "removed_nodes": removed_nodes,
            "rewired_edge_count": len(rewired_edges),
            "rewired_edges": rewired_edges,
            "updated_kps_count": len(updated_kps),
            "updated_kps": updated_kps,
        }
