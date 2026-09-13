from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from source_syntax_primitives.sql import SQL_RECOVERY_ALL, parse_sql_syntax

from .canonical import stable_id
from .landscape import repository_local_salience, source_tree_scope


@dataclass(frozen=True)
class SqlProbeResult:
    sql_footprint: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]]
    probe_status: dict[str, dict[str, Any]]


def _diag(
    *,
    repository_id: str,
    path: str,
    occurrence_id: str | None,
    code: str,
    message: str,
    basis: dict[str, Any],
) -> dict[str, Any]:
    return {
        "diagnostic_id": stable_id("diagnostic", repository_id, path, "sql_footprint", code, basis),
        "code": code,
        "severity": "warning",
        "message": message,
        "source_ref": {"repository_relative_path": path, "localization_kind": "file"},
        "source_occurrence_id": occurrence_id,
        "basis": basis,
        "probe": {"probe_id": "sql_footprint", "probe_version": "1"},
    }


def _parse_sql(text: str) -> tuple[list[Any], list[dict[str, Any]]]:
    # The neutral owner performs SQLGlot construction and recover-all parsing. Inventory
    # retains its exact footprint/error projection and all structural-family semantics.
    result = parse_sql_syntax(text, recovery_policy=SQL_RECOVERY_ALL)
    if result.status in {"failed", "unavailable"}:
        for diagnostic in result.diagnostics:
            if isinstance(diagnostic.detail_handle, Exception):
                raise diagnostic.detail_handle
        raise RuntimeError(result.diagnostics[0].error_type if result.diagnostics else result.status)
    sanitized_errors = []
    for diagnostic in result.diagnostics:
        item: dict[str, Any] = {"error_type": diagnostic.error_type or "ParseError"}
        if diagnostic.description is not None:
            item.update(
                {
                    "description": diagnostic.description,
                    "line": diagnostic.line,
                    "column": diagnostic.column,
                }
            )
        sanitized_errors.append(item)
    return list(result.expressions), sanitized_errors


def _status(*, applicable: int, complete: int, partial: int, failed: int, skipped: int) -> dict[str, Any]:
    if applicable == 0:
        status = "not_applicable"
        kind = "no_sql_candidate_files"
    elif failed == applicable:
        status = "failed"
        kind = "sqlglot_failed_for_all_sql_files"
    elif partial or failed or skipped:
        status = "partial"
        kind = "sqlglot_sql_files_with_explicit_gaps"
    else:
        status = "complete"
        kind = "sqlglot_top_level_ast_observations"
    return {
        "status": status,
        "basis": {
            "kind": kind,
            "candidate_file_count": applicable,
            "complete_file_count": complete,
            "partial_file_count": partial,
            "failed_file_count": failed,
            "skipped_large_file_count": skipped,
            "dialect_selection": "none",
        },
    }


def run_sql_probe(
    *,
    repository_id: str,
    files: list[dict[str, Any]],
    source_bytes_by_path: dict[str, bytes],
    file_occurrence_by_path: dict[str, str],
    max_probe_file_bytes: int,
) -> SqlProbeResult:
    sql_files = [row for row in files if row.get("extension") == ".sql"]
    scripts: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    family_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    complete = partial = failed = skipped = 0

    for file_row in sql_files:
        path = file_row["repository_relative_path"]
        occurrence_id = file_occurrence_by_path.get(path)
        raw = source_bytes_by_path.get(path)
        if raw is None:
            failed += 1
            diagnostics.append(
                _diag(
                    repository_id=repository_id,
                    path=path,
                    occurrence_id=occurrence_id,
                    code="sql_source_unreadable",
                    message="SQL source bytes were unavailable; no statement facts were inferred.",
                    basis={"kind": "source_bytes_unavailable"},
                )
            )
            scripts.append({
                "sql_footprint_id": stable_id("sql_script_footprint", repository_id, file_row["file_id"]),
                "repository_id": repository_id,
                "footprint_kind": "sql_script",
                "file_id": file_row["file_id"],
                "repository_relative_path": path,
                "source_occurrence_id": occurrence_id,
                "parse_status": "failed",
                "statement_count": 0,
                "structurally_parsed_statement_count": 0,
                "unsupported_statement_count": 0,
                "parser_error_count": 0,
                "statement_type_counts": {},
                "dialect_selection": "none",
                "basis": {"kind": "sql_file_observed_but_source_unavailable", "semantic_meaning_inferred": False},
                "probe": {"probe_id": "sql_footprint", "probe_version": "1"},
            })
            continue
        if len(raw) > max_probe_file_bytes:
            skipped += 1
            diagnostics.append(
                _diag(
                    repository_id=repository_id,
                    path=path,
                    occurrence_id=occurrence_id,
                    code="sql_probe_file_too_large",
                    message="SQL footprint parser skipped an oversized file; no statement facts were inferred.",
                    basis={"byte_size": len(raw), "max_probe_file_bytes": max_probe_file_bytes},
                )
            )
            scripts.append({
                "sql_footprint_id": stable_id("sql_script_footprint", repository_id, file_row["file_id"]),
                "repository_id": repository_id,
                "footprint_kind": "sql_script",
                "file_id": file_row["file_id"],
                "repository_relative_path": path,
                "source_occurrence_id": occurrence_id,
                "parse_status": "partial",
                "statement_count": 0,
                "structurally_parsed_statement_count": 0,
                "unsupported_statement_count": 0,
                "parser_error_count": 0,
                "statement_type_counts": {},
                "dialect_selection": "none",
                "basis": {"kind": "sql_file_observed_probe_size_gap", "semantic_meaning_inferred": False},
                "probe": {"probe_id": "sql_footprint", "probe_version": "1"},
            })
            continue
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            failed += 1
            diagnostics.append(
                _diag(
                    repository_id=repository_id,
                    path=path,
                    occurrence_id=occurrence_id,
                    code="sql_utf8_decode_failed",
                    message="SQL source could not be decoded as UTF-8; no statement facts were inferred.",
                    basis={"reason": exc.reason},
                )
            )
            scripts.append({
                "sql_footprint_id": stable_id("sql_script_footprint", repository_id, file_row["file_id"]),
                "repository_id": repository_id,
                "footprint_kind": "sql_script",
                "file_id": file_row["file_id"],
                "repository_relative_path": path,
                "source_occurrence_id": occurrence_id,
                "parse_status": "failed",
                "statement_count": 0,
                "structurally_parsed_statement_count": 0,
                "unsupported_statement_count": 0,
                "parser_error_count": 0,
                "statement_type_counts": {},
                "dialect_selection": "none",
                "basis": {"kind": "sql_utf8_decode_failed", "semantic_meaning_inferred": False},
                "probe": {"probe_id": "sql_footprint", "probe_version": "1"},
            })
            continue
        try:
            expressions, parser_errors = _parse_sql(text)
        except Exception as exc:
            failed += 1
            diagnostics.append(
                _diag(
                    repository_id=repository_id,
                    path=path,
                    occurrence_id=occurrence_id,
                    code="sqlglot_parse_failed",
                    message="SQLGlot failed to parse the SQL file; no statement facts were inferred.",
                    basis={"error_type": type(exc).__name__, "dialect_selection": "none"},
                )
            )
            scripts.append({
                "sql_footprint_id": stable_id("sql_script_footprint", repository_id, file_row["file_id"]),
                "repository_id": repository_id,
                "footprint_kind": "sql_script",
                "file_id": file_row["file_id"],
                "repository_relative_path": path,
                "source_occurrence_id": occurrence_id,
                "parse_status": "failed",
                "statement_count": 0,
                "structurally_parsed_statement_count": 0,
                "unsupported_statement_count": 0,
                "parser_error_count": 0,
                "statement_type_counts": {},
                "dialect_selection": "none",
                "basis": {"kind": "sqlglot_parse_failed", "semantic_meaning_inferred": False},
                "probe": {"probe_id": "sql_footprint", "probe_version": "1"},
            })
            continue

        type_counts: Counter[str] = Counter()
        unsupported = 0
        parser_error_count = len(parser_errors)
        for expression in expressions:
            if expression is None:
                expression_type = "unparsed"
                parser_support = "unparsed"
                unsupported += 1
            else:
                expression_type = str(expression.key)
                parser_support = "fallback_command" if expression_type == "command" else ("recovered_ast" if parser_error_count else "structured_ast")
                if parser_support == "fallback_command":
                    unsupported += 1
            type_counts[expression_type] += 1
            family_events[expression_type].append({
                "repository_relative_path": path,
                "file_id": file_row["file_id"],
                "source_occurrence_id": occurrence_id,
                "source_tree_scope": source_tree_scope(path),
                "parser_support": parser_support,
            })

        parse_status = "partial" if unsupported or parser_error_count else "complete"
        if parse_status == "partial":
            partial += 1
            if unsupported:
                diagnostics.append(
                    _diag(
                        repository_id=repository_id,
                        path=path,
                        occurrence_id=occurrence_id,
                        code="sqlglot_unsupported_statement_fallback",
                        message="SQLGlot emitted unsupported/unparsed top-level statement nodes; only the parser-owned footprint is published.",
                        basis={
                            "unsupported_statement_count": unsupported,
                            "statement_count": len(expressions),
                            "fallback_types": sorted(k for k in type_counts if k in {"command", "unparsed"}),
                            "dialect_selection": "none",
                        },
                    )
                )
            if parser_error_count:
                diagnostics.append(
                    _diag(
                        repository_id=repository_id,
                        path=path,
                        occurrence_id=occurrence_id,
                        code="sqlglot_parser_recovery",
                        message="SQLGlot recovered an AST while reporting parser errors; recovered expression types are published as partial parser evidence only.",
                        basis={
                            "parser_error_count": parser_error_count,
                            "errors": parser_errors[:10],
                            "errors_truncated": parser_error_count > 10,
                            "dialect_selection": "none",
                        },
                    )
                )
        else:
            complete += 1

        scripts.append({
            "sql_footprint_id": stable_id("sql_script_footprint", repository_id, file_row["file_id"]),
            "repository_id": repository_id,
            "footprint_kind": "sql_script",
            "file_id": file_row["file_id"],
            "repository_relative_path": path,
            "source_occurrence_id": occurrence_id,
            "parse_status": parse_status,
            "statement_count": len(expressions),
            "structurally_parsed_statement_count": len(expressions) - unsupported,
            "unsupported_statement_count": unsupported,
            "parser_error_count": parser_error_count,
            "statement_type_counts": {k: type_counts[k] for k in sorted(type_counts)},
            "dialect_selection": "none",
            "basis": {
                "kind": "sqlglot_top_level_ast_file_footprint",
                "localization_precision": "file_level",
                "semantic_meaning_inferred": False,
            },
            "probe": {"probe_id": "sql_footprint", "probe_version": "1"},
        })

    probe_status = _status(
        applicable=len(sql_files),
        complete=complete,
        partial=partial,
        failed=failed,
        skipped=skipped,
    )
    coverage_status = probe_status["status"]
    families: list[dict[str, Any]] = []
    for expression_type, events in sorted(family_events.items()):
        paths = sorted({event["repository_relative_path"] for event in events})
        file_ids = sorted({event["file_id"] for event in events})
        occurrence_ids = sorted({event["source_occurrence_id"] for event in events if event["source_occurrence_id"]})
        scopes = sorted({event["source_tree_scope"] for event in events})
        parser_support_values = sorted({event["parser_support"] for event in events})
        if parser_support_values == ["structured_ast"]:
            basis_kind = "sqlglot_top_level_ast_expression_family"
        elif "fallback_command" in parser_support_values or "unparsed" in parser_support_values:
            basis_kind = "sqlglot_unsupported_top_level_expression_family"
        else:
            basis_kind = "sqlglot_recovered_top_level_ast_expression_family"
        count = len(events)
        family = {
            "sql_footprint_id": stable_id("sql_statement_family", repository_id, expression_type),
            "repository_id": repository_id,
            "footprint_kind": "sql_statement_family",
            "statement_type": expression_type,
            "parser_support": parser_support_values,
            "occurrence_count": count,
            "file_count": len(paths),
            "source_tree_scope_count": len(scopes),
            "source_tree_scopes": scopes,
            "file_ids": file_ids,
            "source_occurrence_ids": occurrence_ids,
            "claim": {
                "classification": "observed_fact",
                "confidence": 1.0,
                "basis": "sqlglot_parser_owned_top_level_expression_type",
            },
            "basis": {
                "kind": basis_kind,
                "dialect_selection": "none",
                "semantic_meaning_inferred": False,
            },
            "probe": {"probe_id": "sql_footprint", "probe_version": "1"},
        }
        family.update(
            repository_local_salience(
                count=count,
                file_count=len(paths),
                source_tree_scopes=scopes,
                coverage_status=coverage_status,
            )
        )
        families.append(family)

    rows = sorted(
        scripts + families,
        key=lambda row: (
            row["footprint_kind"],
            row.get("repository_relative_path") or "",
            row.get("statement_type") or "",
            row["sql_footprint_id"],
        ),
    )
    diagnostics.sort(key=lambda row: (row["source_ref"]["repository_relative_path"], row["code"], row["diagnostic_id"]))
    return SqlProbeResult(sql_footprint=rows, diagnostics=diagnostics, probe_status={"sql_footprint": probe_status})
