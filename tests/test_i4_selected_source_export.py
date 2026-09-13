from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

from repository_inventory.builder import build_inventory
from repository_inventory.cli import main
from repository_inventory.config import InventoryConfig
from repository_inventory.contracts import (
    SELECTED_SOURCE_EXPORT_FORMAT,
    SELECTED_SOURCE_EXPORT_REQUEST_FORMAT,
)
from repository_inventory.selected_source_export import (
    _fragment_request_from_evidence,
    build_selected_source_export,
    verify_selected_source_export,
)


def _build_fixture(tmp_path: Path) -> tuple[Path, Path, dict]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "one.txt").write_bytes(b"same bytes\n")
    (repo / "two.txt").write_bytes(b"same bytes\n")
    (repo / "other.txt").write_bytes(b"other bytes\n")
    out = tmp_path / "inventory"
    payload = build_inventory(repository=repo, output=out, repository_id="i4-repo")
    return repo, out, payload


def _file_evidence(payload: dict, path: str) -> dict:
    return next(row for row in payload["family_file_evidence"] if row["repository_relative_path"] == path)


def test_selected_export_is_fragment_transport_and_deduplicates_fragment_blobs(tmp_path: Path) -> None:
    repo, inventory, payload = _build_fixture(tmp_path)
    one = _file_evidence(payload, "one.txt")
    two = _file_evidence(payload, "two.txt")
    export = tmp_path / "export"

    shutil.rmtree(repo)  # prove there is no repository fallback/read during export
    result = build_selected_source_export(
        inventory=inventory,
        output=export,
        evidence_ids=[two["evidence_id"], one["evidence_id"], one["evidence_id"]],
    )

    assert result["format"] == SELECTED_SOURCE_EXPORT_FORMAT
    assert result["status"] == "complete"
    assert result["counts"] == {
        "requested_evidence": 2,
        "resolved_evidence": 2,
        "unresolved_evidence": 0,
        "source_files_touched": 2,
        "fragments": 2,
        "distinct_fragment_blobs": 1,
        "fragment_bytes": len(b"same bytes\n"),
    }
    assert {row["repository_relative_path"] for row in result["source_files"]} == {"one.txt", "two.txt"}
    assert len(result["fragments"]) == 2
    assert all(row["fragment_scope"] == "file_scope" for row in result["fragments"])
    assert result["transport_policy"]["selection_semantics_interpreted"] is False
    assert result["transport_policy"]["repository_source_read"] is False
    assert result["transport_policy"]["workspace_fallback_allowed"] is False
    assert result["transport_policy"]["full_source_file_blobs_copied"] is False
    assert result["transport_policy"]["redaction_performed"] is False
    assert not (export / "source-content").exists()

    fragment_path = export / result["fragments"][0]["fragment_blob_path"]
    assert fragment_path.read_bytes() == b"same bytes\n"
    assert hashlib.sha256(fragment_path.read_bytes()).hexdigest() == result["fragments"][0]["fragment_sha256"]
    assert verify_selected_source_export(export)["status"] == "pass"


def test_exact_span_fragment_uses_only_observed_span_plus_context() -> None:
    source = b"one\ntwo\nthree\nfour\nfive\n"
    evidence = {
        "evidence_id": "e1",
        "repository_relative_path": "A.java",
        "content_sha256": hashlib.sha256(source).hexdigest(),
        "exemplar": {
            "localization_kind": "exact_span",
            "line_start": 3,
            "line_end": 3,
            "column_start": 1,
            "column_end": 5,
        },
    }
    request, diagnostic = _fragment_request_from_evidence(
        source_bytes=source,
        evidence=evidence,
        context_lines=1,
        file_scope_max_bytes=64 * 1024,
    )
    assert diagnostic is None
    assert request is not None
    assert request["scope"] == "localized_span_with_context"
    assert request["requested_line_start"] == 2
    assert request["requested_line_end"] == 4


def test_file_scope_evidence_over_limit_is_partial_without_guessed_fragment(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "large.txt").write_bytes(b"x" * 100)
    inventory = tmp_path / "inventory"
    payload = build_inventory(repository=repo, output=inventory, repository_id="large-file")
    evidence = _file_evidence(payload, "large.txt")

    result = build_selected_source_export(
        inventory=inventory,
        output=tmp_path / "export",
        evidence_ids=[evidence["evidence_id"]],
        file_scope_max_bytes=32,
    )
    assert result["status"] == "partial"
    assert result["counts"]["resolved_evidence"] == 0
    assert result["counts"]["unresolved_evidence"] == 1
    assert result["fragments"] == []
    assert [row["diagnostic_kind"] for row in result["diagnostics"]] == [
        "file_scope_evidence_exceeds_fragment_limit"
    ]


def test_selected_export_missing_blob_is_partial_without_fallback(tmp_path: Path) -> None:
    _, inventory, payload = _build_fixture(tmp_path)
    one = _file_evidence(payload, "one.txt")
    (inventory / "source-content" / "blobs" / one["content_sha256"]).unlink()

    result = build_selected_source_export(
        inventory=inventory,
        output=tmp_path / "export",
        evidence_ids=[one["evidence_id"]],
    )
    assert result["status"] == "partial"
    assert result["counts"]["resolved_evidence"] == 0
    assert result["counts"]["unresolved_evidence"] == 1
    assert [row["diagnostic_kind"] for row in result["diagnostics"]] == ["source_content_blob_missing"]
    assert verify_selected_source_export(tmp_path / "export")["export_status"] == "partial"


def test_selected_export_sha_mismatch_is_partial_and_does_not_export_tampered_content(tmp_path: Path) -> None:
    _, inventory, payload = _build_fixture(tmp_path)
    one = _file_evidence(payload, "one.txt")
    source_blob = inventory / "source-content" / "blobs" / one["content_sha256"]
    source_blob.write_bytes(b"tampered")

    result = build_selected_source_export(
        inventory=inventory,
        output=tmp_path / "export",
        evidence_ids=[one["evidence_id"]],
    )
    assert result["status"] == "partial"
    assert result["fragments"] == []
    assert [row["diagnostic_kind"] for row in result["diagnostics"]] == ["source_content_blob_sha_mismatch"]
    assert not any((tmp_path / "export" / "fragments" / "blobs").iterdir())


def test_selected_export_unknown_evidence_is_explicit_partial(tmp_path: Path) -> None:
    _, inventory, _ = _build_fixture(tmp_path)
    result = build_selected_source_export(
        inventory=inventory,
        output=tmp_path / "export",
        evidence_ids=["family_file_evidence_does_not_exist"],
    )
    assert result["status"] == "partial"
    assert [row["diagnostic_kind"] for row in result["diagnostics"]] == ["family_file_evidence_not_found"]


def test_selected_export_requires_captured_inventory_content(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("a", encoding="utf-8")
    inventory = tmp_path / "inventory"
    payload = build_inventory(
        repository=repo,
        output=inventory,
        repository_id="no-capture",
        config=InventoryConfig.create(capture_readable_content=False),
    )
    occurrence = _file_evidence(payload, "a.txt")
    result = build_selected_source_export(
        inventory=inventory,
        output=tmp_path / "export",
        evidence_ids=[occurrence["evidence_id"]],
    )
    assert result["status"] == "partial"
    kinds = [row["diagnostic_kind"] for row in result["diagnostics"]]
    assert kinds == ["source_content_index_entry_missing", "source_content_store_unavailable"]


def test_cli_export_selected_accepts_generic_request_contract(tmp_path: Path, capsys) -> None:
    _, inventory, payload = _build_fixture(tmp_path)
    one = _file_evidence(payload, "one.txt")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({
        "format": SELECTED_SOURCE_EXPORT_REQUEST_FORMAT,
        "evidence_ids": [one["evidence_id"]],
    }), encoding="utf-8")
    export = tmp_path / "export"

    assert main([
        "export-selected", str(inventory), "--output", str(export), "--request", str(request)
    ]) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["status"] == "complete"
    assert built["format"] == SELECTED_SOURCE_EXPORT_FORMAT
    assert built["fragments"] == 1

    assert main(["verify-selected-export", str(export)]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["status"] == "pass"
    assert verified["export_status"] == "complete"


def test_export_selected_can_take_all_reduced_references_without_external_request_script(tmp_path: Path) -> None:
    from repository_inventory.canonical import pretty_json_bytes
    from repository_inventory.reduction import reduce_inventory_payload

    repo, inventory, payload = _build_fixture(tmp_path)
    reduced = reduce_inventory_payload(payload)
    reduced_path = tmp_path / "reduced.json"
    reduced_path.write_bytes(pretty_json_bytes(reduced))
    shutil.rmtree(repo)  # source repository is not used by transport

    result = build_selected_source_export(
        inventory=inventory,
        output=tmp_path / "export",
        evidence_ids=[],
        reduced=reduced_path,
    )
    referenced = {
        str(row.get("representative_evidence_id") or "")
        for section in ("exact_representations", "observed_identities")
        for row in reduced[section]
        if str(row.get("representative_evidence_id") or "")
    }
    assert result["status"] == "complete"
    assert set(result["request"]["requested_evidence_ids"]) == referenced
    assert result["counts"]["resolved_evidence"] == len(referenced)
    assert result["transport_policy"]["reduced_references_consumed"] is True


def test_export_selected_rejects_reduced_from_different_inventory(tmp_path: Path) -> None:
    from repository_inventory.canonical import pretty_json_bytes, fingerprint
    from repository_inventory.reduction import reduce_inventory_payload

    _, inventory, payload = _build_fixture(tmp_path)
    reduced = reduce_inventory_payload(payload)
    reduced["source_inventory"]["repository_id"] = "other-repository"
    reduced.pop("semantic_fingerprint", None)
    reduced["semantic_fingerprint"] = fingerprint(reduced)
    reduced_path = tmp_path / "wrong-reduced.json"
    reduced_path.write_bytes(pretty_json_bytes(reduced))

    import pytest
    with pytest.raises(ValueError, match="source identity does not match"):
        build_selected_source_export(
            inventory=inventory,
            output=tmp_path / "export",
            evidence_ids=[],
            reduced=reduced_path,
        )


def test_cli_export_selected_reduced_mode(tmp_path: Path, capsys) -> None:
    from repository_inventory.canonical import pretty_json_bytes
    from repository_inventory.reduction import reduce_inventory_payload

    _, inventory, payload = _build_fixture(tmp_path)
    reduced = reduce_inventory_payload(payload)
    reduced_path = tmp_path / "reduced.json"
    reduced_path.write_bytes(pretty_json_bytes(reduced))
    export = tmp_path / "export"

    assert main([
        "export-selected", str(inventory), "--output", str(export), "--reduced", str(reduced_path)
    ]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["status"] == "complete"
    assert status["resolved_evidence"] > 0
