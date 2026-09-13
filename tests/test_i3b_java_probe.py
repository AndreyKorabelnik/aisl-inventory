from __future__ import annotations

from pathlib import Path

from v7_helpers import observed_rows

from repository_inventory.builder import build_semantic_payload
from repository_inventory.config import InventoryConfig


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_java_package_import_annotation_and_arguments_are_observed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "module/src/main/java/demo/Example.java",
        '''package demo.model;
import java.util.List;
import static java.util.Collections.*;
@MetaRootEntity(id = "id", version = "version", collocationId = Key.class)
public class Example {
  @jakarta.validation.constraints.Size(max = 255)
  private String value;
}
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="java-struct")
    rows = observed_rows(payload, "import_namespace_observation")
    assert sorted((r["observation_kind"], r["namespace"], r["is_static"], r["is_wildcard"]) for r in rows) == sorted([
        ("package_declaration", "demo.model", False, False),
        ("import_declaration", "java.util.Collections.*", True, True),
        ("import_declaration", "java.util.List", False, False),
    ])
    anns = {r["simple_name"]: r for r in observed_rows(payload, "annotation_observation")}
    root = anns["MetaRootEntity"]
    assert root["argument_count"] == 3
    assert root["arguments"][0] == {"position": 1, "name": "id", "value_kind": "literal", "observed_value": {"literal_kind": "string", "lexeme": '"id"'}}
    assert root["arguments"][2]["value_kind"] == "class_literal"
    assert root["arguments"][2]["observed_symbol"] == "Key"
    size = anns["Size"]
    assert size["annotation_name"] == "jakarta.validation.constraints.Size"
    assert size["arguments"][0]["name"] == "max"
    assert size["arguments"][0]["observed_value"]["lexeme"] == "255"
    statuses = {r["probe_id"]: r["status"] for r in payload["probe_status"]}
    assert statuses["import_namespace_observations"] == "complete"
    assert statuses["annotation_observations"] == "complete"


def test_java_markers_inside_comments_and_strings_are_not_observed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "A.java",
        '''package real.pkg;
// import fake.Type;
/* @Fake(key = "x") */
class A {
  String text = "@Ghost(x=1) import no.Type; fake.call()";
}
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="java-negative")
    assert [r["namespace"] for r in observed_rows(payload, "import_namespace_observation")] == ["real.pkg"]
    assert observed_rows(payload, "annotation_observation") == []
    assert not any(r["callable_name"] == "call" for r in observed_rows(payload, "api_call_observation"))


def test_java_qualified_calls_and_constructors_form_source_intrinsic_families(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = '''package demo;
class A {
 void run() {
   DSL.select(TABLE.ID).from(TABLE).fetch();
   DSL.select(TABLE.NAME).from(TABLE).fetch();
   service.client().send(request);
   new KafkaProducer<String, String>(props);
 }
}
'''
    _write(repo, "a/src/main/java/demo/A.java", source)
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="calls")
    calls = observed_rows(payload, "api_call_observation")
    select = next(r for r in calls if r["callable_name"] == "select")
    assert select["call_kind"] == "qualified_method_invocation"
    assert select["receiver_identity"] == "DSL"
    assert select["occurrence_count"] == 2
    assert select["file_count"] == 1
    assert select["source_tree_scopes"] == ["a"]
    chained_send = next(r for r in calls if r["callable_name"] == "send")
    assert chained_send["receiver_identity"] is None
    assert chained_send["receiver_basis"] == "expression"
    ctor = next(r for r in calls if r["call_kind"] == "constructor_invocation")
    assert ctor["callable_name"] == "KafkaProducer"
    assert ctor["argument_count"] == 1
    # `run()` is a declaration, and unqualified calls are intentionally not guessed as API calls.
    assert not any(r["callable_name"] == "run" for r in calls)


def test_java_annotations_keep_complex_values_as_fingerprinted_evidence_not_semantic_guesses(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "A.java", '@Rule(values = {A.X, B.Y}, nested = @Inner(flag = true))\nclass A {}\n')
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="ann-complex")
    row = next(r for r in observed_rows(payload, "annotation_observation") if r["simple_name"] == "Rule")
    values = row["arguments"]
    assert values[0]["value_kind"] == "array_initializer_expression"
    assert "expression_fingerprint" in values[0]
    assert values[1]["value_kind"] == "nested_annotation_expression"
    assert "expression_fingerprint" in values[1]
    assert "business" not in str(row).lower()


def test_java_probe_is_independent_of_content_capture(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "A.java", 'package p; import q.R; @Flag("x") class A { void x(){ Util.call(1); } }')
    on, _, _ = build_semantic_payload(repository=repo, repository_id="capture-java", config=InventoryConfig.create(capture_readable_content=True))
    off, _, _ = build_semantic_payload(repository=repo, repository_id="capture-java", config=InventoryConfig.create(capture_readable_content=False))
    for section in ("import_namespace_observation", "annotation_observation", "api_call_observation"):
        assert observed_rows(on, section) == observed_rows(off, section)


def test_java_probe_size_limit_is_explicit_partial_not_silent_absence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "A.java", "package p;\n" + " " * 200 + "class A {}\n")
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="java-limit", config=InventoryConfig.create(java_probe_max_bytes=32))
    statuses = {r["probe_id"]: r["status"] for r in payload["probe_status"]}
    assert statuses["import_namespace_observations"] == "partial"
    assert statuses["annotation_observations"] == "partial"
    assert statuses["api_call_observations"] == "partial"
    assert any(r["code"] == "java_probe_file_too_large" for r in payload["observation_diagnostics"])


def test_java_probe_uses_shared_point_sequence_api_only() -> None:
    """Regression guard for the industrial Tree-sitter Point crash.

    Inventory no longer owns Point normalization. The shared Java syntax owner must use
    tuple/sequence coordinates and Inventory must not touch Point directly.
    """
    import inspect
    import source_syntax_primitives.java as shared_java

    project_root = Path(__file__).resolve().parents[1]
    inventory_source = (project_root / "repository_inventory" / "java_probe.py").read_text(encoding="utf-8")
    shared_source = inspect.getsource(shared_java)
    forbidden = ("start_point.row", "start_point.column", "end_point.row", "end_point.column")
    assert not any(token in shared_source for token in forbidden)
    assert "start_point[0]" in shared_source
    assert "start_point[1]" in shared_source
    assert "end_point[0]" in shared_source
    assert "end_point[1]" in shared_source
    assert "start_point" not in inventory_source
    assert "end_point" not in inventory_source
    assert "node_span" in inventory_source
    assert 'node.type == "package_declaration"' not in inventory_source
    assert 'node.type == "import_declaration"' not in inventory_source
    assert "annotation_argument_list" not in inventory_source
    assert "java_declaration_syntax" in inventory_source
    assert "annotation_syntax_from_node" in inventory_source
