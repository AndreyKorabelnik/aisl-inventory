from __future__ import annotations

import ast
from pathlib import Path

from v7_helpers import evidence_for_family, observed_rows, sql_rows

from repository_inventory.builder import build_semantic_payload
from repository_inventory.config import InventoryConfig


def _statuses(payload: dict) -> dict[str, str]:
    return {row["probe_id"]: row["status"] for row in payload["probe_status"]}


def test_sqlglot_footprint_complete_without_dialect_guessing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "flow.sql").write_text(
        "SELECT 1; INSERT INTO target_table SELECT 2;\n",
        encoding="utf-8",
    )

    payload, _, _ = build_semantic_payload(repository=repo, repository_id="sql-complete")

    assert _statuses(payload)["sql_footprint"] == "complete"
    scripts = [row for row in sql_rows(payload) if row["footprint_kind"] == "sql_script"]
    families = [row for row in sql_rows(payload) if row["footprint_kind"] == "sql_statement_family"]
    assert len(scripts) == 1
    assert scripts[0]["parse_status"] == "complete"
    assert scripts[0]["statement_count"] == 2
    assert scripts[0]["unsupported_statement_count"] == 0
    assert scripts[0]["statement_type_counts"] == {"insert": 1, "select": 1}
    assert scripts[0]["dialect_selection"] == "none"
    assert {row["statement_type"] for row in families} == {"select", "insert"}
    assert all(row["basis"]["dialect_selection"] == "none" for row in families)
    assert all(row["claim"]["classification"] == "observed_fact" for row in families)
    assert all(row["structural_salience_score"] > 0 for row in families)
    assert all(row["repository_local_salience"]["novelty_claim"] is False for row in families)
    assert not [row for row in payload["observation_diagnostics"] if row["probe"]["probe_id"] == "sql_footprint"]

    sql_family_ids = {row["family_id"] for row in observed_rows(payload, "sql_statement_family")}
    linked = [row for row in payload["family_file_evidence"] if row["family_id"] in sql_family_ids]
    assert len(linked) == 2
    assert all(row["repository_relative_path"] == "flow.sql" for row in linked)


def test_sqlglot_command_fallback_is_partial_and_explicit_gap(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "templated.sql").write_text(
        'if $x = "y" then run_sql_hdfs("child.sql")\n',
        encoding="utf-8",
    )

    payload, _, _ = build_semantic_payload(repository=repo, repository_id="sql-partial")

    assert _statuses(payload)["sql_footprint"] == "partial"
    script = next(row for row in sql_rows(payload) if row["footprint_kind"] == "sql_script")
    assert script["parse_status"] == "partial"
    assert script["statement_type_counts"] == {"command": 1}
    assert script["structurally_parsed_statement_count"] == 0
    assert script["unsupported_statement_count"] == 1
    family = next(row for row in sql_rows(payload) if row.get("statement_type") == "command")
    assert family["parser_support"] == ["fallback_command"]
    assert family["basis"]["semantic_meaning_inferred"] is False
    diagnostic = next(row for row in payload["observation_diagnostics"] if row["code"] == "sqlglot_unsupported_statement_fallback")
    assert "source_occurrence_id" not in diagnostic
    assert diagnostic["source_ref"]["repository_relative_path"] == script["repository_relative_path"]
    assert diagnostic["basis"]["dialect_selection"] == "none"


def test_sqlglot_parser_recovery_is_partial_not_total_failure_and_does_not_embed_source(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source_text = "let x = 1; select ${foo}.bar from x;\n"
    (repo / "templated.sql").write_text(source_text, encoding="utf-8")

    payload, _, _ = build_semantic_payload(repository=repo, repository_id="sql-recovery")

    assert _statuses(payload)["sql_footprint"] == "partial"
    script = next(row for row in sql_rows(payload) if row["footprint_kind"] == "sql_script")
    assert script["parse_status"] == "partial"
    assert script["parser_error_count"] > 0
    assert script["statement_count"] > 0
    assert script["structurally_parsed_statement_count"] > 0
    diagnostic = next(row for row in payload["observation_diagnostics"] if row["code"] == "sqlglot_parser_recovery")
    assert diagnostic["basis"]["parser_error_count"] > 0
    assert all(set(item) <= {"error_type", "description", "line", "column"} for item in diagnostic["basis"]["errors"])
    # Diagnostics may expose parser category/coordinates, never source snippets or SQL text.
    assert source_text.strip() not in str(diagnostic)



def test_sqlglot_exception_diagnostic_does_not_embed_exception_message_or_source(tmp_path: Path, monkeypatch) -> None:
    import repository_inventory.sql_probe as sql_probe

    repo = tmp_path / "repo"
    repo.mkdir()
    source_text = "SELECT secret_customer_value FROM confidential_table;\n"
    (repo / "broken.sql").write_text(source_text, encoding="utf-8")

    def fail_with_source(_text: str):
        raise RuntimeError(f"parser exploded near source: {source_text.strip()}")

    monkeypatch.setattr(sql_probe, "_parse_sql", fail_with_source)
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="sql-exception-redaction")

    diagnostic = next(row for row in payload["observation_diagnostics"] if row["code"] == "sqlglot_parse_failed")
    assert diagnostic["basis"] == {"error_type": "RuntimeError", "dialect_selection": "none"}
    assert "message" not in diagnostic["basis"]
    assert source_text.strip() not in str(diagnostic)
    script = next(row for row in sql_rows(payload) if row["footprint_kind"] == "sql_script")
    assert script["parse_status"] == "failed"


def test_sql_probe_size_limit_is_gap_not_absence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "large.sql").write_text("SELECT 123456789;\n", encoding="utf-8")
    config = InventoryConfig.create(sql_probe_max_bytes=4)

    payload, _, _ = build_semantic_payload(repository=repo, repository_id="sql-large", config=config)

    assert _statuses(payload)["sql_footprint"] == "partial"
    script = next(row for row in sql_rows(payload) if row["footprint_kind"] == "sql_script")
    assert script["parse_status"] == "partial"
    assert script["statement_count"] == 0
    assert any(row["code"] == "sql_probe_file_too_large" for row in payload["observation_diagnostics"])
    assert not [row for row in sql_rows(payload) if row["footprint_kind"] == "sql_statement_family"]


def test_sql_probe_not_applicable_is_explicit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("nothing to parse\n", encoding="utf-8")

    payload, _, _ = build_semantic_payload(repository=repo, repository_id="no-sql")

    assert _statuses(payload)["sql_footprint"] == "not_applicable"
    assert sql_rows(payload) == []


def test_structured_cross_file_families_have_exact_dispersion_and_repository_local_salience(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "one").mkdir(parents=True)
    (repo / "two").mkdir(parents=True)
    (repo / "one" / "a.json").write_text('{"shared": 1, "left": true}\n', encoding="utf-8")
    (repo / "two" / "b.json").write_text('{"shared": 2, "right": true}\n', encoding="utf-8")

    payload, _, _ = build_semantic_payload(repository=repo, repository_id="structured-dispersion")

    family = next(
        row for row in observed_rows(payload, "structured_family")
        if row["family_kind"] == "structured_key_family" and row["key"] == "shared"
    )
    assert family["occurrence_count"] == 2
    assert family["file_count"] == 2
    assert family["source_tree_scope_count"] == 2
    assert family["source_tree_scopes"] == ["one", "two"]
    assert len(family["file_ids"]) == 2
    assert len(evidence_for_family(payload, family["family_id"])) == 2
    assert family["structural_salience_score"] > 0
    assert family["repository_local_salience"] == {
        "occurrence_count": 2,
        "file_count": 2,
        "source_tree_scope_count": 2,
        "source_tree_scopes": ["one", "two"],
        "coverage_status": "complete",
        "novelty_claim": False,
        "novelty_ownership": "downstream_cross_repository_mining",
        "basis": "repository_local_observed_frequency_and_probe_coverage",
    }


def test_java_call_families_receive_same_repository_local_salience_contract(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "module-a" / "src").mkdir(parents=True)
    (repo / "module-b" / "src").mkdir(parents=True)
    java = "class A { void x(){ Client.call(1); } }\n"
    (repo / "module-a" / "src" / "A.java").write_text(java, encoding="utf-8")
    (repo / "module-b" / "src" / "B.java").write_text(java.replace("class A", "class B"), encoding="utf-8")

    payload, _, _ = build_semantic_payload(repository=repo, repository_id="java-salience")

    call = next(row for row in observed_rows(payload, "api_call_observation") if row["callable_name"] == "call")
    assert call["occurrence_count"] == 2
    assert call["file_count"] == 2
    assert call["source_tree_scopes"] == ["module-a", "module-b"]
    assert call["structural_salience_score"] > 0
    assert call["repository_local_salience"]["novelty_claim"] is False




def test_unclassified_extension_family_is_generic_structural_landscape_not_business_semantics(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "logical").mkdir(parents=True)
    (repo / "physical").mkdir(parents=True)
    (repo / "logical" / "one.plp").write_text("opaque-one\n", encoding="utf-8")
    (repo / "physical" / "two.plp").write_text("opaque-two\n", encoding="utf-8")

    payload, _, _ = build_semantic_payload(repository=repo, repository_id="generic-plp")

    primitive = next(row for row in observed_rows(payload, "source_primitive") if row["extension"] == ".plp")
    assert primitive["classification"] == "unclassified"
    assert primitive["language"] is None
    assert primitive["occurrence_count"] == primitive["file_count"] == 2
    assert primitive["source_tree_scopes"] == ["logical", "physical"]
    assert len(evidence_for_family(payload, primitive["family_id"])) == 2
    assert primitive["structural_salience_score"] > 0
    assert primitive["repository_local_salience"]["novelty_claim"] is False
    assert primitive["basis"]["semantic_meaning_inferred"] is False
    assert not any("plp" in str(row.get("language") or "").lower() for row in observed_rows(payload, "source_primitive"))

    evidence = evidence_for_family(payload, primitive["family_id"])
    assert {row["repository_relative_path"] for row in evidence} == {"logical/one.plp", "physical/two.plp"}


def test_sql_probe_has_one_mature_parser_owner_and_no_regex_fallback() -> None:
    source = (Path(__file__).resolve().parents[1] / "repository_inventory" / "sql_probe.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "source_syntax_primitives.sql" in imports
    assert "sqlglot" not in imports
    assert "re" not in imports
    assert "parse_sql_syntax" in source
    assert "SQL_RECOVERY_ALL" in source
    assert "Dialect.get_or_raise(None)" not in source
    assert "dialect.parser" not in source
    assert "re.compile" not in source
    assert '"dialect_selection": "none"' in source


def test_cli_exposes_sql_probe_size_limit() -> None:
    from repository_inventory.cli import _parser
    args = _parser().parse_args([
        "build", ".", "--output", "out", "--repository-id", "r", "--sql-probe-max-bytes", "1234"
    ])
    assert args.sql_probe_max_bytes == 1234
