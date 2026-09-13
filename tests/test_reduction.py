from __future__ import annotations

import json
from pathlib import Path

import pytest

from repository_inventory.canonical import fingerprint, pretty_json_bytes
from repository_inventory.cli import main
from repository_inventory.reduction import (
    ReducedInventoryError,
    build_reduced_inventory,
    reduce_inventory_payload,
    validate_reduced_inventory,
)


def _source_inventory() -> dict:
    families = [
        {
            "family_id": "family-a",
            "repository_id": "repo-1",
            "family_kind": "api_call_observation",
            "family_label": "send",
            "descriptor": {
                "argument_count": 1,
                "call_kind": "qualified_method_invocation",
                "callable_name": "send",
                "language": "java",
                "receiver_identity": "clientA",
            },
            "occurrence_count": 1,
            "file_count": 1,
            "source_tree_scope_count": 1,
            "source_tree_scopes": ["main"],
            "provenance": {"observed_section": "api_call_observation"},
            "structural_salience_score": 12.5,
        },
        {
            "family_id": "family-b",
            "repository_id": "repo-1",
            "family_kind": "api_call_observation",
            "family_label": "send",
            "descriptor": {
                "argument_count": 1,
                "call_kind": "qualified_method_invocation",
                "callable_name": "send",
                "language": "java",
                "receiver_identity": "clientB",
            },
            "occurrence_count": 1,
            "file_count": 1,
            "source_tree_scope_count": 1,
            "source_tree_scopes": ["test"],
            "provenance": {"observed_section": "api_call_observation"},
        },
        {
            "family_id": "family-c",
            "repository_id": "repo-1",
            "family_kind": "api_call_observation",
            "family_label": "send",
            "descriptor": {
                "argument_count": 2,
                "call_kind": "qualified_method_invocation",
                "callable_name": "send",
                "language": "java",
                "receiver_identity": "clientC",
            },
            "occurrence_count": 1,
            "file_count": 1,
            "source_tree_scope_count": 1,
            "source_tree_scopes": ["main"],
            "provenance": {"observed_section": "api_call_observation"},
        },
    ]
    evidence = [
        {
            "evidence_id": "evidence-a",
            "family_id": "family-a",
            "repository_id": "repo-1",
            "file_id": "file-a",
            "repository_relative_path": "src/A.java",
            "source_tree_scope": "main",
            "occurrence_count_in_file": 1,
        },
        {
            "evidence_id": "evidence-b",
            "family_id": "family-b",
            "repository_id": "repo-1",
            "file_id": "file-b",
            "repository_relative_path": "test/B.java",
            "source_tree_scope": "test",
            "occurrence_count_in_file": 1,
        },
        {
            "evidence_id": "evidence-c",
            "family_id": "family-c",
            "repository_id": "repo-1",
            "file_id": "file-c",
            "repository_relative_path": "src/C.java",
            "source_tree_scope": "main",
            "occurrence_count_in_file": 1,
        },
    ]
    diagnostic = {
        "diagnostic_id": "diag-maven-1",
        "code": "dependency_warning",
        "message": "same observed warning",
        "source_ref": {"repository_relative_path": "pom.xml"},
    }
    payload = {
        "format": "repository-inventory/v7",
        "run_provenance": {"component": "repository-inventory", "implementation_version": "test"},
        "identity": {"repository_id": "repo-1", "repository_name": "repo-1"},
        "source_snapshot": {"fingerprint": "snapshot-1", "file_count": 3},
        "files": [],
        "structural_families": families,
        "structural_members": [],
        "family_file_evidence": evidence,
        "file_observations": [],
        "observation_diagnostics": [diagnostic, dict(diagnostic)],
        "probe_status": [{"probe_id": "java", "status": "complete"}],
        "source_content_store": {"capture_status": "not_evaluated"},
        "semantic_policy": {"observed_source_state_only": True},
        "inventory_id": "inventory-1",
    }
    payload["semantic_fingerprint"] = fingerprint(payload)
    return payload


def test_reduction_discards_duplicate_membership_and_keeps_one_representative_per_exact_state() -> None:
    source = _source_inventory()
    reduced = reduce_inventory_payload(source)

    assert reduced["format"] == "repository-inventory-reduced/v3"
    assert reduced["source_inventory"]["semantic_fingerprint"] == source["semantic_fingerprint"]
    assert reduced["counts"]["source_structural_families"] == 3
    assert reduced["counts"]["exact_representations"] == 2
    assert reduced["counts"]["collapsed_source_families"] == 1
    assert reduced["counts"]["structural_similarity_groups"] == 1
    assert reduced["counts"]["observed_identities"] == 3
    assert "family_membership" not in reduced

    exact_rows = reduced["exact_representations"]
    ab = next(row for row in exact_rows if row["source_family_count"] == 2)
    c = next(row for row in exact_rows if row["source_family_count"] == 1)
    assert ab["exact_representation_id"] != c["exact_representation_id"]
    assert ab["occurrence_count"] == 2
    assert ab["file_count"] == 2
    assert ab["family_file_evidence_count"] == 2
    assert ab["representative_evidence_id"] == "evidence-a"
    assert ab["representative_source_family_id"] == "family-a"
    observation = ab["structural_observation"]
    assert observation["structural_salience_score"] == 12.5
    assert observation["positive_numeric_metrics"] == [{"path": "$.argument_count", "value": 1.0}]
    assert observation["outside_analyzer_frontier_extension_family"] is False
    identities = reduced["observed_identities"]
    assert {row["identity"]["family_label"] for row in identities} == {"send"}
    assert sum(row["source_family_count"] for row in identities) == 3
    assert {
        facet["value"]
        for row in identities
        for facet in row["identity"]["string_facets"]
    } == {"clientA", "clientB", "clientC"}


def test_similarity_representatives_cover_each_exact_variant_and_diagnostics_are_not_duplicated() -> None:
    reduced = reduce_inventory_payload(_source_inventory())
    group = reduced["structural_similarity_groups"][0]

    assert group["exact_representation_count"] == 2
    assert len(reduced["exact_representations"]) == 2
    assert group["variation_dimensions"]
    assert reduced["counts"]["source_diagnostic_rows"] == 2
    assert reduced["counts"]["unique_diagnostics"] == 1
    assert reduced["diagnostics"][0]["source_row_count"] == 2
    assert reduced["diagnostics"][0]["distinct_variant_count"] == 1
    assert reduced["diagnostics"][0]["identity_conflict"] is False
    validate_reduced_inventory(reduced)


def test_concrete_names_do_not_create_structural_shapes_but_remain_observed_identities() -> None:
    source = _source_inventory()
    source["structural_families"][0]["family_label"] = "sendA"
    source["structural_families"][0]["descriptor"]["callable_name"] = "sendA"
    source["structural_families"][1]["family_label"] = "sendB"
    source["structural_families"][1]["descriptor"]["callable_name"] = "sendB"
    source.pop("semantic_fingerprint", None)
    source["semantic_fingerprint"] = fingerprint(source)

    reduced = reduce_inventory_payload(source)

    # sendA/clientA and sendB/clientB have the same call shape and are one exact shape.
    one_arg = [row for row in reduced["exact_representations"] if row["source_family_count"] == 2]
    assert len(one_arg) == 1
    assert "family_label" not in one_arg[0]["exact_representation_basis"]
    labels = {row["identity"]["family_label"] for row in reduced["observed_identities"]}
    assert labels == {"sendA", "sendB", "send"}
    assert reduced["reducer"]["concrete_names_participate_in_structural_shape_identity"] is False
    assert reduced["reducer"]["concrete_names_preserved_as_observed_identities"] is True


def test_reduction_is_byte_stable_and_needs_only_inventory_json(tmp_path: Path) -> None:
    source = _source_inventory()
    inventory_path = tmp_path / "repository_inventory.json"
    inventory_path.write_bytes(pretty_json_bytes(source))
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    build_reduced_inventory(inventory=inventory_path, output=first)
    build_reduced_inventory(inventory=inventory_path, output=second)

    assert first.read_bytes() == second.read_bytes()
    assert json.loads(first.read_text())["source_inventory"]["inventory_id"] == "inventory-1"


def test_reduction_rejects_modified_source_with_stale_semantic_fingerprint() -> None:
    source = _source_inventory()
    source["structural_families"][0]["occurrence_count"] = 99
    with pytest.raises(ReducedInventoryError, match="semantic fingerprint mismatch"):
        reduce_inventory_payload(source)


def test_cli_reduce_and_verify_reduced(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    inventory_path = tmp_path / "repository_inventory.json"
    inventory_path.write_bytes(pretty_json_bytes(_source_inventory()))
    reduced_path = tmp_path / "repository_inventory_reduced.json"

    assert main(["reduce", str(inventory_path), "--output", str(reduced_path)]) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["status"] == "reduced"
    assert built["format"] == "repository-inventory-reduced/v3"

    assert main(["verify-reduced", str(reduced_path)]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["status"] == "verified"
    assert verified["semantic_fingerprint"] == built["semantic_fingerprint"]
