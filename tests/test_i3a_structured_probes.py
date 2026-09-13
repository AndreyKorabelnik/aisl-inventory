from __future__ import annotations

import json
from pathlib import Path

from v7_helpers import evidence_for_family, observed_rows, structural_members

from repository_inventory.builder import build_semantic_payload
from repository_inventory.config import InventoryConfig


def _write(repo: Path, name: str, text: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _clean_fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "api/openapi.yaml",
        """openapi: 3.0.3
info:
  title: Demo
  version: 1.0.0
paths:
  /pets:
    get:
      responses:
        '200':
          description: ok
""",
    )
    _write(
        repo,
        "schema/person.schema.json",
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "urn:person",
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            }
        ),
    )
    _write(
        repo,
        "schema/inferred.json",
        json.dumps({"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}),
    )
    _write(repo, "ordinary/openapi.json", json.dumps({"name": "not openapi", "type": "widget"}))
    _write(
        repo,
        "proto/service.proto",
        'syntax = "proto3";\npackage demo;\nmessage Request { string id = 1; }\nservice Demo { }\n',
    )
    _write(
        repo,
        "xml/schema.xsd",
        """<?xml version="1.0"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:demo">
  <xs:element name="person" type="xs:string"/>
</xs:schema>
""",
    )
    _write(
        repo,
        "xml/sample.xml",
        """<root xmlns="urn:r" xmlns:x="urn:x" id="1"><x:item code="A"/><item/></root>""",
    )
    _write(
        repo,
        "config/app.yaml",
        """service:
  name: demo
  endpoints:
    - path: /a
      method: GET
    - path: /b
      method: POST
""",
    )
    return repo


def test_i3a_exact_standard_formats_and_negative_controls(tmp_path: Path) -> None:
    repo = _clean_fixture(tmp_path)
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="i3a-clean")

    by_format: dict[str, list[dict]] = {}
    for row in observed_rows(payload, "source_format_observation"):
        by_format.setdefault(row["source_format"], []).append(row)

    assert {row["repository_relative_path"] for row in by_format["openapi"]} == {"api/openapi.yaml"}
    openapi = by_format["openapi"][0]
    assert openapi["format_version"] == "3.0.3"
    assert openapi["claim"]["classification"] == "observed_fact"
    assert openapi["claim"]["confidence"] == "high"

    schemas = {row["repository_relative_path"]: row for row in by_format["json_schema"]}
    assert set(schemas) == {"schema/person.schema.json", "schema/inferred.json"}
    assert schemas["schema/person.schema.json"]["claim"]["classification"] == "observed_fact"
    assert schemas["schema/inferred.json"]["claim"]["classification"] == "strongly_supported_inference"
    assert "ordinary/openapi.json" not in schemas

    protobuf = by_format["protobuf"][0]
    assert protobuf["repository_relative_path"] == "proto/service.proto"
    assert protobuf["format_version"] == "proto3"
    assert protobuf["claim"]["basis"]["syntax"] == "proto3"

    xsd = by_format["xsd"][0]
    assert xsd["repository_relative_path"] == "xml/schema.xsd"
    assert xsd["claim"]["basis"]["namespace_uri"] == "http://www.w3.org/2001/XMLSchema"

    statuses = {row["probe_id"]: row["status"] for row in payload["probe_status"]}
    assert statuses["source_formats"] == "complete"
    assert statuses["structured_families"] == "complete"
    assert statuses["xml_observations"] == "complete"


def test_i3a_structured_key_paths_and_xml_shapes_are_source_observations(tmp_path: Path) -> None:
    repo = _clean_fixture(tmp_path)
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="i3a-shapes")

    members = structural_members(payload)
    assert {"service", "name", "endpoints", "path", "method"}.issubset({row.get("key") for row in members})
    method_member = next(row for row in members if row.get("key") == "method")
    assert method_member["single_path"] == ["service", "endpoints", "[]", "method"]
    method_family = next(
        row for row in observed_rows(payload, "structured_family")
        if row["family_kind"] == "structured_key_family" and row.get("key") == "method"
    )
    method_evidence = evidence_for_family(payload, method_family["family_id"])
    assert len(method_evidence) == 1
    assert method_evidence[0]["repository_relative_path"] == "config/app.yaml"
    assert method_evidence[0]["occurrence_count_in_file"] == 2

    name_member = next(row for row in members if row.get("key") == "name" and row.get("single_path") == ["properties", "name"])
    assert name_member["single_path"] == ["properties", "name"]
    type_members = [row for row in members if row.get("key") == "type"]
    assert any(row.get("single_path") is None for row in type_members)
    assert any(row["family_kind"] == "structured_document_shape" for row in observed_rows(payload, "structured_family"))
    assert any(row["family_kind"] == "structured_key_family" and row.get("key") == "type" for row in observed_rows(payload, "structured_family"))

    xml = [row for row in observed_rows(payload, "xml_observation") if row["repository_relative_path"] == "xml/sample.xml"]
    root = next(row for row in xml if row["observation_kind"] == "document_root")
    assert root["local_name"] == "root"
    assert root["namespace_uri"] == "urn:r"
    namespaces = {(row["prefix"], row["namespace_uri"]) for row in xml if row["observation_kind"] == "namespace"}
    assert ("", "urn:r") in namespaces
    assert ("x", "urn:x") in namespaces
    assert any(row["observation_kind"] == "element" and row["local_name"] == "item" for row in xml)
    assert any(row["observation_kind"] == "attribute" and row["local_name"] == "code" for row in xml)
    assert all(row["claim"]["classification"] == "observed_fact" for row in xml)


def test_i3a_parse_gaps_and_ambiguity_are_diagnostics_not_absence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "broken.json", "{not-json")
    _write(repo, "templated.yaml", "openapi: 3.0.0\n{{- if .Values.enabled }}\npaths: {}\n{{- end }}\n")
    _write(repo, "weak.json", json.dumps({"properties": {"x": 1}}))
    _write(repo, "empty.proto", "// intentionally no proto declarations\n")
    _write(repo, "wrong.xsd", '<root xmlns="urn:not-xsd"/>')
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="i3a-gaps")

    assert observed_rows(payload, "source_format_observation") == []
    codes = {row["code"] for row in payload["observation_diagnostics"]}
    assert "structured_json_parse_failed" in codes
    assert "structured_yaml_scan_partial" in codes
    assert "json_schema_markers_ambiguous" in codes
    assert "proto_format_not_confirmed" in codes
    assert "xsd_format_not_confirmed" in codes
    statuses = {row["probe_id"]: row["status"] for row in payload["probe_status"]}
    assert statuses["source_formats"] == "partial"
    assert statuses["structured_families"] == "partial"
    assert statuses["xml_observations"] == "complete"


def test_i3a_observations_do_not_depend_on_content_capture(tmp_path: Path) -> None:
    repo = _clean_fixture(tmp_path)
    captured, _, _ = build_semantic_payload(repository=repo, repository_id="capture-independent")
    not_captured, captured_bytes, _ = build_semantic_payload(
        repository=repo,
        repository_id="capture-independent",
        config=InventoryConfig.create(capture_readable_content=False),
    )
    assert captured_bytes == {}
    assert observed_rows(captured, "source_format_observation") == observed_rows(not_captured, "source_format_observation")
    assert observed_rows(captured, "structured_family") == observed_rows(not_captured, "structured_family")
    assert structural_members(captured) == structural_members(not_captured)
    assert observed_rows(captured, "xml_observation") == observed_rows(not_captured, "xml_observation")
    assert captured["source_snapshot"]["fingerprint"] == not_captured["source_snapshot"]["fingerprint"]


def test_i3a_probe_size_limit_is_explicit_partial_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "large.json", json.dumps({"openapi": "3.0.0", "padding": "x" * 200}))
    payload, _, _ = build_semantic_payload(
        repository=repo,
        repository_id="size-limited",
        config=InventoryConfig.create(structured_probe_max_bytes=64),
    )
    assert observed_rows(payload, "source_format_observation") == []
    assert any(row["code"] == "inventory_probe_file_size_limit" for row in payload["observation_diagnostics"])
    statuses = {row["probe_id"]: row["status"] for row in payload["probe_status"]}
    assert statuses["source_formats"] == "partial"
    assert statuses["structured_families"] == "partial"


def test_i3a_compact_families_have_bounded_file_evidence(tmp_path: Path) -> None:
    repo = _clean_fixture(tmp_path)
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="i3a-links")
    family_ids = {row["family_id"] for row in payload["structural_families"]}
    file_ids = {row["file_id"] for row in payload["files"]}
    assert payload["family_file_evidence"]
    assert all(row["family_id"] in family_ids for row in payload["family_file_evidence"])
    assert all(row["file_id"] in file_ids for row in payload["family_file_evidence"])
    assert all(row["family_id"] in family_ids for row in payload["structural_members"])
    assert "source_occurrences" not in payload
    assert "object_occurrence_links" not in payload

def test_i3a_avsc_shape_is_not_misclassified_as_json_schema(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "event.avsc", json.dumps({"type": "record", "name": "E", "fields": [{"name": "id", "type": "string"}]}))
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="avsc-negative")
    assert observed_rows(payload, "source_format_observation") == []
    assert any(row["repository_relative_path"] == "event.avsc" for row in payload["family_file_evidence"])
    assert any(row.get("syntax") == "json" for row in payload["structural_members"])
    statuses = {row["probe_id"]: row["status"] for row in payload["probe_status"]}
    assert statuses["source_formats"] == "not_applicable"
    assert statuses["structured_families"] == "complete"


def test_i3a_yaml_json_schema_explicit_marker_is_confirmed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "schema.yaml",
        "$schema: https://json-schema.org/draft/2020-12/schema\ntype: object\nproperties:\n  id:\n    type: string\n",
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="yaml-schema")
    assert len(observed_rows(payload, "source_format_observation")) == 1
    row = observed_rows(payload, "source_format_observation")[0]
    assert row["source_format"] == "json_schema"
    assert row["claim"]["classification"] == "observed_fact"
    assert row["format_version"] == "https://json-schema.org/draft/2020-12/schema"


def test_i3a_repeated_json_array_keys_have_unique_member_ids(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "items.json", json.dumps({"items": [{"id": 1}, {"id": 2}, {"id": 3}]}))
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="array-members")
    rows = [row for row in structural_members(payload) if row["key"] == "id"]
    assert len(rows) == 1
    assert rows[0]["single_path"] == ["items", "[]", "id"]
    family = next(row for row in observed_rows(payload, "structured_family") if row.get("key") == "id")
    evidence = evidence_for_family(payload, family["family_id"])
    assert family["occurrence_count"] == 3
    assert evidence[0]["occurrence_count_in_file"] == 3
    assert evidence[0]["exemplar"]["localization_kind"] == "file"


def test_i3a_proto_markers_inside_comments_do_not_confirm_format(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "comment-only.proto", '// syntax = "proto3";\n/* message Fake { string id = 1; } */\n')
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="proto-comment")
    assert observed_rows(payload, "source_format_observation") == []
    assert any(row["code"] == "proto_format_not_confirmed" for row in payload["observation_diagnostics"])


def test_i3a_yaml_multiline_flow_and_quoted_scalars_do_not_create_false_parse_gaps(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "api.yaml",
        '''openapi: 3.0.3
info:
  title: Demo
  description: "Long description
    continued on another line"
components:
  schemas:
    State:
      type: string
      enum: [
        NEW,
        ACTIVE
      ]
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="yaml-multiline")
    assert [row["source_format"] for row in observed_rows(payload, "source_format_observation")] == ["openapi"]
    statuses = {row["probe_id"]: row["status"] for row in payload["probe_status"]}
    assert statuses["source_formats"] == "complete"
    assert statuses["structured_families"] == "complete"
    assert not any(row["code"] == "structured_yaml_scan_partial" for row in payload["observation_diagnostics"])


def test_i3a_yaml_empty_key_followed_by_multiline_plain_scalar_is_observed_without_gap(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "api.yml",
        '''openapi: 3.0.3
info:
  title: Demo
components:
  schemas:
    Example:
      description:
        First line of description
        second line of description
      exampleCode:
        427638CDAE351911
        427638CDAE351900
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="yaml-nested-plain")
    assert [row["source_format"] for row in observed_rows(payload, "source_format_observation")] == ["openapi"]
    assert not any(row["code"] == "structured_yaml_scan_partial" for row in payload["observation_diagnostics"])
    members = {row["key"]: row for row in structural_members(payload)}
    assert members["description"]["value_kinds"] == ["string"]
    assert members["exampleCode"]["value_kinds"] == ["string"]


def test_i3a_repeated_xml_shapes_are_aggregated_with_unique_ids(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "pom.xml",
        "<project><properties><item code=\"A\"/><item code=\"B\"/><item/></properties></project>",
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="xml-repeat")
    rows = observed_rows(payload, "xml_observation")
    ids = [row["xml_observation_id"] for row in rows]
    assert len(ids) == len(set(ids))
    item = next(
        row for row in rows
        if row["observation_kind"] == "element" and row["local_name"] == "item"
    )
    code = next(
        row for row in rows
        if row["observation_kind"] == "attribute" and row["local_name"] == "code"
    )
    assert item["occurrence_count"] == 3
    assert code["occurrence_count"] == 2
    evidence = evidence_for_family(payload, item["family_id"])
    assert evidence[0]["exemplar"]["localization_kind"] == "file"
    assert item["claim"]["basis"] == "xml_parser_structure_file_local_aggregation"


def test_i3a_identical_document_shapes_are_one_repository_family(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "a.json", json.dumps({"service": {"name": "a", "enabled": True}}))
    _write(repo, "b.json", json.dumps({"service": {"name": "b", "enabled": False}}))
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="shape-repeat")
    shapes = [row for row in observed_rows(payload, "structured_family") if row["family_kind"] == "structured_document_shape"]
    assert len(shapes) == 1
    shape = shapes[0]
    assert shape["occurrence_count"] == 2
    assert shape["file_count"] == 2
    assert len(shape["file_ids"]) == 2
    assert len(evidence_for_family(payload, shape["family_id"])) == 2
    ids = [row["family_id"] for row in observed_rows(payload, "structured_family")]
    assert len(ids) == len(set(ids))
