from __future__ import annotations

from copy import deepcopy

from repository_inventory.canonical import fingerprint
from repository_inventory.reduction import reduce_inventory_payload, validate_reduced_inventory


def _source() -> dict:
    family = {
        "family_id": "family-a",
        "repository_id": "repo",
        "family_kind": "call",
        "family_label": "send",
        "descriptor": {"argument_count": 3, "nested": {"depth": 2}, "name": "ignored-for-policy"},
        "occurrence_count": 4,
        "file_count": 1,
        "source_tree_scope_count": 1,
        "source_tree_scopes": ["main"],
        "structural_salience_score": 17.5,
        "provenance": {"observed_section": "call"},
    }
    payload = {
        "format": "repository-inventory/v7",
        "identity": {"repository_id": "repo"},
        "inventory_id": "inventory",
        "source_snapshot": {"fingerprint": "snapshot"},
        "structural_families": [family],
        "structural_members": [],
        "family_file_evidence": [{
            "evidence_id": "evidence-a", "family_id": "family-a", "repository_id": "repo",
            "file_id": "file-a", "occurrence_count_in_file": 4,
        }],
        "observation_diagnostics": [],
        "probe_status": [],
    }
    payload["semantic_fingerprint"] = fingerprint(payload)
    return payload


def test_reduced_preserves_minimal_structural_observation_without_moving_policy() -> None:
    reduced = reduce_inventory_payload(_source())
    validate_reduced_inventory(reduced)
    member = reduced["exact_representations"][0]
    assert member["structural_observation"] == {
        "structural_salience_score": 17.5,
        "positive_numeric_metrics": [
            {"path": "$.argument_count", "value": 3.0},
            {"path": "$.nested.depth", "value": 2.0},
        ],
        "outside_analyzer_frontier_extension_family": False,
    }
    assert "complexity_score" not in member["structural_observation"]
    assert "rank" not in member["structural_observation"]
