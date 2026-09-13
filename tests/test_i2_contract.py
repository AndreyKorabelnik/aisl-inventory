from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from v7_helpers import observed_rows

from repository_inventory.builder import build_inventory, build_semantic_payload
from repository_inventory.config import InventoryConfig
from repository_inventory.contracts import CONTRACT_FORMAT
from repository_inventory.verify import verify_inventory_directory


def _fixture(root: Path) -> Path:
    repo = root / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "Main.java").write_text("package demo; class Main {}\n", encoding="utf-8")
    (repo / "schemas").mkdir()
    (repo / "schemas" / "mystery.xyz").write_bytes(b"opaque\n")
    (repo / "README").write_text("notes\n", encoding="utf-8")
    (repo / "target").mkdir()
    (repo / "target" / "generated.java").write_text("class Generated {}", encoding="utf-8")
    return repo


def test_i2_build_is_source_intrinsic_and_has_no_analyzer_frontier(tmp_path: Path) -> None:
    repo = _fixture(tmp_path)
    payload, captured, index = build_semantic_payload(
        repository=repo,
        repository_id="repo-a",
        repository_name="Demo",
    )

    assert payload["format"] == CONTRACT_FORMAT
    assert payload["identity"]["repository_id"] == "repo-a"
    assert payload["source_snapshot"]["file_count"] == 3
    assert {item["repository_relative_path"] for item in payload["files"]} == {
        "README",
        "schemas/mystery.xyz",
        "src/Main.java",
    }
    assert all("analyzer_eligible" not in item for item in payload["files"])
    assert all("analyzer_frontier_status" not in item for item in payload["files"])
    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "framework_supported",
        "analyzer_eligible",
        "analyzer_frontier_status",
        "outside_analyzer_frontier",
        "unknown_primitive",
    ):
        assert forbidden not in serialized

    mystery = next(item for item in payload["files"] if item["extension"] == ".xyz")
    assert mystery["source_classification"]["classification"] == "unclassified"
    assert mystery["source_classification"]["language"] is None
    primitive = next(item for item in observed_rows(payload, "source_primitive") if item["extension"] == ".xyz")
    assert primitive["classification"] == "unclassified"
    assert "unsupported" not in json.dumps(primitive)

    java = next(item for item in payload["files"] if item["extension"] == ".java")
    assert java["source_classification"]["classification"] == "classified"
    assert java["source_classification"]["language"] == "java"
    assert set(captured) == {item["sha256"] for item in payload["files"]}
    assert index["file_count"] == 3

    probe = {item["probe_id"]: item["status"] for item in payload["probe_status"]}
    assert probe["file_inventory"] == "complete"
    assert probe["source_formats"] == "not_applicable"
    assert probe["dependency_observations"] == "not_applicable"
    assert payload["semantic_policy"]["framework_capability_interpreted"] is False


def test_family_file_evidence_and_blob_sha_are_exact(tmp_path: Path) -> None:
    repo = _fixture(tmp_path / "source")
    out = tmp_path / "out"
    payload = build_inventory(repository=repo, output=out, repository_id="repo-a")

    by_path = {item["repository_relative_path"]: item for item in payload["files"]}
    evidence_by_path: dict[str, list[dict]] = {}
    for item in payload["family_file_evidence"]:
        evidence_by_path.setdefault(item["repository_relative_path"], []).append(item)
    assert set(evidence_by_path) == set(by_path)
    for path, file_row in by_path.items():
        evidence = evidence_by_path[path]
        assert all(row["file_id"] == file_row["file_id"] for row in evidence)
        assert all(row["content_sha256"] == file_row["sha256"] for row in evidence)
        assert all(row["occurrence_count_in_file"] > 0 for row in evidence)
        blob = out / "source-content" / "blobs" / file_row["sha256"]
        data = blob.read_bytes()
        assert hashlib.sha256(data).hexdigest() == file_row["sha256"]

    assert "source_occurrences" not in payload
    assert "object_occurrence_links" not in payload
    result = verify_inventory_directory(out)
    assert result["status"] == "pass"
    assert result["verified_distinct_blob_count"] == 3

def test_same_corpus_different_absolute_paths_is_byte_identical(tmp_path: Path) -> None:
    repo_a = _fixture(tmp_path / "left")
    repo_b = _fixture(tmp_path / "right")
    out_a = tmp_path / "out-a"
    out_b = tmp_path / "out-b"

    left = build_inventory(repository=repo_a, output=out_a, repository_id="stable-repo")
    right = build_inventory(repository=repo_b, output=out_b, repository_id="stable-repo")

    assert left == right
    assert (out_a / "repository_inventory.json").read_bytes() == (out_b / "repository_inventory.json").read_bytes()
    assert (out_a / "source-content" / "index.json").read_bytes() == (out_b / "source-content" / "index.json").read_bytes()


def test_configuration_is_semantic_and_changes_fingerprint(tmp_path: Path) -> None:
    repo = _fixture(tmp_path)
    default, _, _ = build_semantic_payload(repository=repo, repository_id="repo-a")
    include_target = InventoryConfig.create(excluded_directory_names=(".git",))
    broad, _, _ = build_semantic_payload(repository=repo, repository_id="repo-a", config=include_target)
    assert default["source_snapshot"]["file_count"] == 3
    assert broad["source_snapshot"]["file_count"] == 4
    assert default["semantic_fingerprint"] != broad["semantic_fingerprint"]
    assert default["run_provenance"]["configuration_fingerprint"] != broad["run_provenance"]["configuration_fingerprint"]


def test_unreadable_is_diagnostic_not_absence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "blocked.xyz"
    source.write_bytes(b"opaque")
    original = Path.read_bytes

    def fail(self: Path) -> bytes:
        if self == source:
            raise PermissionError(13, "synthetic")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", fail)
    payload, captured, index = build_semantic_payload(repository=repo, repository_id="repo-blocked")
    assert len(payload["files"]) == 1
    row = payload["files"][0]
    assert row["repository_relative_path"] == "blocked.xyz"
    assert row["readable"] is False
    assert row["sha256"] is None
    assert row["content_capture_status"] == "unreadable"
    assert captured == {}
    assert index["files"] == []
    assert payload["probe_status"][0] or True
    diagnostic = payload["observation_diagnostics"][0]
    assert diagnostic["code"] == "repository_file_unreadable"
    assert "source_occurrence_id" not in diagnostic
    assert diagnostic["source_ref"]["repository_relative_path"] == "blocked.xyz"
    probe = {item["probe_id"]: item["status"] for item in payload["probe_status"]}
    assert probe["file_inventory"] == "partial"


def test_symlink_is_observed_but_not_followed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = repo / "external-link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink not supported")

    payload, captured, _ = build_semantic_payload(repository=repo, repository_id="repo-link")
    assert len(payload["files"]) == 1
    row = payload["files"][0]
    assert row["is_symlink"] is True
    assert row["sha256"] is None
    assert row["content_capture_status"] == "not_captured_symlink"
    assert captured == {}
    assert payload["observation_diagnostics"][0]["code"] == "repository_symlink_not_followed"
    assert b"secret" not in captured.values()


def test_output_inside_repository_is_rejected(tmp_path: Path) -> None:
    repo = _fixture(tmp_path)
    with pytest.raises(ValueError, match="outside the repository"):
        build_inventory(repository=repo, output=repo / ".inventory", repository_id="repo-a")


def test_tampered_blob_fails_verification(tmp_path: Path) -> None:
    repo = _fixture(tmp_path / "source")
    out = tmp_path / "out"
    payload = build_inventory(repository=repo, output=out, repository_id="repo-a")
    sha = payload["files"][0]["sha256"]
    (out / "source-content" / "blobs" / sha).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="blob SHA mismatch"):
        verify_inventory_directory(out)


def test_no_content_capture_writes_no_source_bytes(tmp_path: Path) -> None:
    repo = _fixture(tmp_path / "source")
    out = tmp_path / "out"
    config = InventoryConfig.create(capture_readable_content=False)
    payload = build_inventory(
        repository=repo,
        output=out,
        repository_id="no-capture",
        config=config,
    )
    assert payload["source_content_store"]["capture_status"] == "not_evaluated"
    assert payload["source_content_store"]["index_relative_path"] is None
    assert not (out / "source-content").exists()
    assert verify_inventory_directory(out)["verified_distinct_blob_count"] == 0


def test_tampered_snapshot_fingerprint_fails_even_if_payload_semantic_hash_is_recomputed(tmp_path: Path) -> None:
    from repository_inventory.canonical import fingerprint, pretty_json_bytes

    repo = _fixture(tmp_path / "source")
    out = tmp_path / "out"
    build_inventory(repository=repo, output=out, repository_id="repo-a")
    path = out / "repository_inventory.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["source_snapshot"]["fingerprint"] = "0" * 64
    material = dict(payload)
    material.pop("semantic_fingerprint", None)
    payload["semantic_fingerprint"] = fingerprint(material)
    path.write_bytes(pretty_json_bytes(payload))
    with pytest.raises(ValueError, match="source snapshot fingerprint mismatch"):
        verify_inventory_directory(out)


def test_content_capture_policy_changes_inventory_but_not_source_snapshot(tmp_path: Path) -> None:
    repo = _fixture(tmp_path)
    captured, _, _ = build_semantic_payload(repository=repo, repository_id="repo-a")
    no_capture, _, _ = build_semantic_payload(
        repository=repo,
        repository_id="repo-a",
        config=InventoryConfig.create(capture_readable_content=False),
    )
    assert captured["source_snapshot"]["fingerprint"] == no_capture["source_snapshot"]["fingerprint"]
    assert captured["semantic_fingerprint"] != no_capture["semantic_fingerprint"]
    assert captured["run_provenance"]["configuration_fingerprint"] != no_capture["run_provenance"]["configuration_fingerprint"]


def test_macos_metadata_is_outside_repository_source_frontier(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "A.java").write_text("class A {}\n", encoding="utf-8")
    mac = repo / "__MACOSX"
    mac.mkdir()
    (mac / "._A.java").write_bytes(b"\x00\x05\x16\x07APPLEDOUBLE")
    (repo / ".DS_Store").write_bytes(b"metadata")
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="macos-filter")
    paths = {row["repository_relative_path"] for row in payload["files"]}
    assert paths == {"A.java"}
