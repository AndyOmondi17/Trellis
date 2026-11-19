"""
tests/test_tier0_ingestion.py
Unit tests for KnowledgeGraphIngestionService (FR-0.3)
Verifies validation, ingestion, and structural delta calculation.
"""

import pytest
from trellis.ports.storage_port import get_storage_adapter
from trellis.services.ingestion_service import KnowledgeGraphIngestionService


def test_ingestion_service_validation():
    adapter = get_storage_adapter("memory")
    service = KnowledgeGraphIngestionService(adapter)

    # Empty payload
    with pytest.raises(ValueError, match="schema_version"):
        service.validate_graph_payload({"schema_version": "1.0.0"})

    with pytest.raises(ValueError, match="zero nodes"):
        service.validate_graph_payload({"schema_version": "2.0.0-trellis", "nodes": []})

    # Missing prereq
    invalid_graph = {
        "schema_version": "2.0.0-trellis",
        "nodes": [
            {
                "id": "trl_node_a",
                "prerequisites": [{"prerequisite_id": "trl_missing"}],
                "encompassed_skills": [],
            }
        ],
    }
    with pytest.raises(ValueError, match="missing prerequisite"):
        service.validate_graph_payload(invalid_graph)


def test_structural_delta_calculation():
    adapter = get_storage_adapter("memory")
    service = KnowledgeGraphIngestionService(adapter)

    base = {
        "schema_version": "2.0.0-trellis",
        "nodes": [
            {
                "id": "trl_node_1",
                "prerequisites": [],
                "knowledge_points": [{"kp_id": "trl_node_1#kp_0"}],
            },
            {
                "id": "trl_node_2",
                "prerequisites": [{"prerequisite_id": "trl_node_1", "strength": "hard"}],
                "knowledge_points": [{"kp_id": "trl_node_2#kp_0"}],
            },
        ],
    }

    # Node 3 added, Node 2 prereq changed, Node 1 KP added
    new = {
        "schema_version": "2.0.0-trellis",
        "nodes": [
            {
                "id": "trl_node_1",
                "prerequisites": [],
                "knowledge_points": [{"kp_id": "trl_node_1#kp_0"}, {"kp_id": "trl_node_1#kp_1"}],
            },
            {
                "id": "trl_node_2",
                "prerequisites": [{"prerequisite_id": "trl_node_1", "strength": "soft"}],
                "knowledge_points": [{"kp_id": "trl_node_2#kp_0"}],
            },
            {
                "id": "trl_node_3",
                "prerequisites": [{"prerequisite_id": "trl_node_1", "strength": "hard"}],
                "knowledge_points": [{"kp_id": "trl_node_3#kp_0"}],
            },
        ],
    }

    delta = service.compute_structural_delta(base, new)
    assert delta["added_nodes"] == ["trl_node_3"]
    assert delta["removed_nodes"] == []
    assert len(delta["rewired_edges"]) == 1
    assert delta["rewired_edges"][0]["topic_id"] == "trl_node_2"
    assert delta["updated_kps"] == ["trl_node_1"]
