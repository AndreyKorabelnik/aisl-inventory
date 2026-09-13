from __future__ import annotations

from collections import defaultdict
import json
from typing import Any, Iterable, Mapping

from .canonical import fingerprint, stable_id
from .landscape import repository_local_salience, source_tree_scope


# Row-local/provenance fields are not structural identity.  Semantic fields such as
# Maven group_id/artifact_id are deliberately *not* removed merely because they end
# in ``_id``.
_COMMON_NON_IDENTITY = {
    "repository_id",
    "file_id",
    "file_ids",
    "repository_relative_path",
    "repository_relative_paths",
    "source_occurrence_id",
    "source_occurrence_ids",
    "probe",
    "claim",
    "basis",
    "occurrence_count",
    "document_count",
    "file_count",
    "source_tree_scope_count",
    "source_tree_scopes",
    "structural_salience_score",
    "repository_local_salience",
    "parse_status",
    "recognition_status",
    "localization_precision",
    "line_start",
    "line_end",
    "column_start",
    "column_end",
    # Repository-local aggregation/frequency values are product metadata, not
    # cross-repository structural identity.
    "key_family_member_count",
    "key_occurrence_count",
    "distinct_path_count",
    "member_count",
    "file_local_path_count_sum",
    "value_kind_counts",
}

_OBJECT_ID_FIELDS = {
    "source_primitive": {"primitive_id"},
    "source_format_observation": {"source_format_observation_id"},
    "import_namespace_observation": {"import_namespace_observation_id"},
    "annotation_observation": {"annotation_observation_id"},
    "api_call_observation": {"api_call_observation_id"},
    "dependency_observation": {"dependency_observation_id"},
    "config_key_observation": {"config_key_observation_id"},
    "xml_observation": {"xml_observation_id"},
    "structured_family": {"family_id"},
    "sql_statement_family": {"sql_footprint_id"},
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _unique(values: Iterable[Any]) -> list[Any]:
    indexed = {_canonical(value): value for value in values if value not in (None, {}, [])}
    return [indexed[key] for key in sorted(indexed)]


def _descriptor(kind: str, row: Mapping[str, Any]) -> dict[str, Any]:
    excluded = _COMMON_NON_IDENTITY | _OBJECT_ID_FIELDS.get(kind, set())
    if kind == "structured_family":
        # The serialized family kind belongs on the envelope; it is not duplicated in
        # the structural descriptor. HTTP boundary evidence may be observed through
        # several existing parser owners; parser/source mechanics are provenance, not a
        # second boundary identity.
        excluded = excluded | {"family_kind"}
        if str(row.get("family_kind") or "") == "http_boundary_observation":
            excluded = excluded | {"syntax_family", "source_kind", "path_expression", "resolution_basis"}
        if str(row.get("family_kind") or "") == "kafka_boundary_observation":
            excluded = excluded | {"syntax_family", "source_kind", "topic_expression", "resolution_basis", "operation"}
    if kind == "source_primitive":
        excluded = excluded | {"primitive_kind"}
    if kind == "sql_statement_family":
        # sql_statement_family is encoded by the envelope kind.
        excluded = excluded | {"footprint_kind"}
    return {
        str(key): value
        for key, value in sorted(row.items(), key=lambda item: str(item[0]))
        if str(key) not in excluded
    }


def _family_kind(kind: str, row: Mapping[str, Any]) -> str:
    if kind == "structured_family":
        return str(row.get("family_kind") or "structured_family")
    if kind == "source_primitive":
        return "source_primitive"
    if kind == "sql_statement_family":
        return "sql_statement_family"
    return kind


def _family_label(kind: str, row: Mapping[str, Any]) -> str:
    if kind == "structured_family":
        return str(row.get("key") or row.get("syntax_family") or row.get("family_kind") or kind)
    if kind == "source_primitive":
        return str(row.get("extension") or row.get("language") or row.get("primitive_kind") or kind)
    if kind == "sql_statement_family":
        return str(row.get("statement_type") or kind)
    return str(
        row.get("source_format")
        or row.get("namespace")
        or row.get("annotation_name")
        or row.get("callable_name")
        or row.get("artifact_id")
        or row.get("full_key")
        or row.get("local_name")
        or kind
    )


def _old_object_id(kind: str, row: Mapping[str, Any]) -> str:
    field = {
        "source_primitive": "primitive_id",
        "source_format_observation": "source_format_observation_id",
        "import_namespace_observation": "import_namespace_observation_id",
        "annotation_observation": "annotation_observation_id",
        "api_call_observation": "api_call_observation_id",
        "dependency_observation": "dependency_observation_id",
        "config_key_observation": "config_key_observation_id",
        "xml_observation": "xml_observation_id",
        "structured_family": "family_id",
        "sql_statement_family": "sql_footprint_id",
    }[kind]
    return str(row.get(field) or "")


def _coverage_status(rows: Iterable[Mapping[str, Any]]) -> str:
    rank = {"complete": 0, "not_applicable": 0, "partial": 1, "failed": 2, "not_evaluated": 2}
    values = [str((row.get("repository_local_salience") or {}).get("coverage_status") or "") for row in rows]
    values = [value for value in values if value]
    if not values:
        return "complete"
    return max(values, key=lambda value: rank.get(value, 1))


def _best_exemplar(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    def key(row: Mapping[str, Any]) -> tuple[Any, ...]:
        kind = str(row.get("localization_kind") or "file")
        precision = 0 if kind == "exact_span" else 1
        return (
            precision,
            int(row.get("line_start") or 0),
            int(row.get("column_start") or 0),
            int(row.get("line_end") or 0),
            int(row.get("column_end") or 0),
            str(row.get("occurrence_id") or ""),
        )

    chosen = min((dict(row) for row in rows), key=key, default={})
    provenance = chosen.get("provenance")
    return {
        "localization_kind": str(chosen.get("localization_kind") or "file"),
        "line_start": chosen.get("line_start"),
        "line_end": chosen.get("line_end"),
        "column_start": chosen.get("column_start"),
        "column_end": chosen.get("column_end"),
        "provenance": provenance if isinstance(provenance, Mapping) else {},
    }


def build_compact_model(
    *,
    repository_id: str,
    files: list[dict[str, Any]],
    occurrences: list[dict[str, Any]],
    source_primitives: list[dict[str, Any]],
    source_formats: list[dict[str, Any]],
    dependency_observations: list[dict[str, Any]],
    import_namespace_observations: list[dict[str, Any]],
    annotation_observations: list[dict[str, Any]],
    api_call_observations: list[dict[str, Any]],
    config_key_observations: list[dict[str, Any]],
    structured_families: list[dict[str, Any]],
    structured_members: list[dict[str, Any]],
    xml_observations: list[dict[str, Any]],
    sql_footprint: list[dict[str, Any]],
    capture_enabled: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Collapse probe output to the repository-inventory/v7 information budget.

    Probe-local exact occurrences are allowed while producing the inventory because they are
    the parser owner's exact counting/localization material.  They are deliberately not part
    of the persisted v7 contract.  The persisted provenance scale is bounded by
    ``families × files`` plus structural members.
    """
    file_by_id = {str(row.get("file_id") or ""): row for row in files}
    file_by_path = {str(row.get("repository_relative_path") or ""): row for row in files}
    occurrence_by_id = {str(row.get("occurrence_id") or ""): row for row in occurrences}

    sql_scripts = [
        row for row in sql_footprint
        if str(row.get("footprint_kind") or "") == "sql_script"
    ]
    sql_families = [
        row for row in sql_footprint
        if str(row.get("footprint_kind") or "") == "sql_statement_family"
    ]
    structured_members_by_old_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in structured_members:
        structured_members_by_old_family[str(row.get("family_id") or "")].append(row)

    sections: list[tuple[str, list[dict[str, Any]], str]] = [
        ("source_primitive", source_primitives, "source_primitive"),
        ("source_format_observation", source_formats, "source_format_observation"),
        ("import_namespace_observation", import_namespace_observations, "import_namespace_observation"),
        ("annotation_observation", annotation_observations, "annotation_observation"),
        ("api_call_observation", api_call_observations, "api_call_observation"),
        ("dependency_observation", dependency_observations, "dependency_observation"),
        ("config_key_observation", config_key_observations, "config_key_observation"),
        ("xml_observation", xml_observations, "xml_observation"),
        ("structured_family", structured_families, "structured_family"),
        ("sql_statement_family", sql_families, "sql_statement_family"),
    ]

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for kind, rows, _ in sections:
        for row in rows:
            descriptor = _descriptor(kind, row)
            grouped[(kind, _family_kind(kind, row), _canonical(descriptor))].append(row)

    compact_families: list[dict[str, Any]] = []
    compact_members: list[dict[str, Any]] = []
    family_file_evidence: list[dict[str, Any]] = []
    old_to_new_family: dict[str, str] = {}

    def occurrence_rows(ids: Iterable[object]) -> list[dict[str, Any]]:
        return [occurrence_by_id[str(value)] for value in ids if str(value) in occurrence_by_id]

    for (origin_kind, family_kind, descriptor_json), rows in sorted(grouped.items()):
        descriptor = json.loads(descriptor_json)
        representative = sorted(rows, key=lambda row: _canonical(row))[0]
        family_label = _family_label(origin_kind, representative)
        family_id = stable_id(
            "structural_family",
            repository_id,
            origin_kind,
            family_kind,
            family_label,
            descriptor,
        )
        for row in rows:
            old_id = _old_object_id(origin_kind, row)
            if old_id:
                old_to_new_family[old_id] = family_id

        # Exact file-local frequency is accumulated from parser-owned rows/events, never
        # reconstructed from a bounded exemplar set.
        per_file_count: dict[str, int] = defaultdict(int)
        per_file_occurrences: dict[str, list[dict[str, Any]]] = defaultdict(list)

        if origin_kind == "structured_family" and family_kind == "structured_key_family":
            for row in rows:
                for member in structured_members_by_old_family.get(str(row.get("family_id") or ""), []):
                    path = str(member.get("repository_relative_path") or "")
                    if not path:
                        continue
                    per_file_count[path] += int(member.get("occurrence_count") or 0)
                    oid = str(member.get("source_occurrence_id") or "")
                    if oid in occurrence_by_id:
                        per_file_occurrences[path].append(occurrence_by_id[oid])
        elif origin_kind == "sql_statement_family":
            statement_type = str(representative.get("statement_type") or "")
            for script in sql_scripts:
                path = str(script.get("repository_relative_path") or "")
                count = int((script.get("statement_type_counts") or {}).get(statement_type) or 0)
                if count <= 0 or not path:
                    continue
                per_file_count[path] += count
                oid = str(script.get("source_occurrence_id") or "")
                if oid in occurrence_by_id:
                    per_file_occurrences[path].append(occurrence_by_id[oid])
        else:
            for row in rows:
                ids = [str(value) for value in row.get("source_occurrence_ids") or [] if str(value)]
                single = str(row.get("source_occurrence_id") or "")
                if single:
                    ids.append(single)
                occs = occurrence_rows(ids)
                if occs:
                    # For multi-ID aggregate rows the probes currently guarantee one ID per
                    # concrete occurrence (Java calls) or one per file where count==file_count
                    # (source primitive/document-shape).  Single-ID rows may carry an explicit
                    # file-local aggregate count (XML).
                    if len(occs) == 1 and not row.get("source_occurrence_ids"):
                        count = int(row.get("occurrence_count") or row.get("document_count") or 1)
                        path = str(occs[0].get("repository_relative_path") or "")
                        if path:
                            per_file_count[path] += count
                            per_file_occurrences[path].append(occs[0])
                    else:
                        for occ in occs:
                            path = str(occ.get("repository_relative_path") or "")
                            if path:
                                per_file_count[path] += 1
                                per_file_occurrences[path].append(occ)
                else:
                    path = str(row.get("repository_relative_path") or "")
                    if path in file_by_path:
                        per_file_count[path] += int(row.get("occurrence_count") or row.get("document_count") or 1)

        # Fail closed if a family could not be localized to any file.  This should not happen
        # for current probes, but retaining a family without recoverable evidence would violate
        # the v7 product contract.
        if not per_file_count:
            continue

        total_count = sum(per_file_count.values())
        evidence_rows: list[dict[str, Any]] = []
        for path in sorted(per_file_count):
            file_row = file_by_path.get(path)
            if file_row is None:
                continue
            exemplars = per_file_occurrences.get(path) or [{
                "localization_kind": "file",
                "line_start": None,
                "line_end": None,
                "column_start": None,
                "column_end": None,
                "provenance": {"basis": "repository_file_observation"},
            }]
            exemplar = _best_exemplar(exemplars)
            content_sha = file_row.get("sha256")
            evidence_id = stable_id("family_file_evidence", repository_id, family_id, file_row["file_id"])
            evidence = {
                "evidence_id": evidence_id,
                "repository_id": repository_id,
                "family_id": family_id,
                "file_id": file_row["file_id"],
                "repository_relative_path": path,
                "source_tree_scope": source_tree_scope(path),
                "occurrence_count_in_file": int(per_file_count[path]),
                "exemplar": exemplar,
                "content_sha256": content_sha,
                "cas_blob_path": (
                    f"source-content/blobs/{content_sha}"
                    if capture_enabled and content_sha and file_row.get("content_capture_status") == "captured"
                    else None
                ),
            }
            evidence_rows.append(evidence)
            family_file_evidence.append(evidence)

        scopes = sorted({row["source_tree_scope"] for row in evidence_rows})
        provenance = {
            "observed_section": origin_kind,
            "probe_variants": _unique(row.get("probe") for row in rows),
            "claim_variants": _unique(row.get("claim") for row in rows),
            "basis_variants": _unique(row.get("basis") for row in rows),
            "claim_boundary": "source_intrinsic_structure_only",
        }
        family = {
            "family_id": family_id,
            "repository_id": repository_id,
            "family_kind": family_kind,
            "family_label": family_label,
            "descriptor": descriptor,
            "occurrence_count": total_count,
            "file_count": len(evidence_rows),
            "source_tree_scope_count": len(scopes),
            "source_tree_scopes": scopes,
            "provenance": provenance,
        }
        if any("structural_salience_score" in row or "repository_local_salience" in row for row in rows):
            family.update(repository_local_salience(
                count=total_count,
                file_count=len(evidence_rows),
                source_tree_scopes=scopes,
                coverage_status=_coverage_status(rows),
            ))
        else:
            family["structural_salience_score"] = 0.0
        compact_families.append(family)

    # StructuralMember is compact structural identity, not provenance.  File-local member
    # observations are deduplicated into unique structural member descriptors per family.
    member_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in structured_members:
        new_family = old_to_new_family.get(str(row.get("family_id") or ""))
        if not new_family:
            continue
        member_descriptor = {
            str(key): value
            for key, value in sorted(row.items(), key=lambda item: str(item[0]))
            if str(key) not in {
                "member_id", "family_id", "repository_id", "file_id", "repository_relative_path",
                "source_occurrence_id", "occurrence_count", "path_count", "value_kind_counts",
                "probe", "basis", "localization_precision", "line_start", "line_end",
                "column_start", "column_end",
            }
        }
        member_groups[(new_family, _canonical(member_descriptor))].append(row)

    for (family_id, descriptor_json), rows in sorted(member_groups.items()):
        descriptor = json.loads(descriptor_json)
        structure_signature = str(descriptor.get("path_fingerprint") or fingerprint(descriptor))
        syntax = str(descriptor.get("syntax_family") or "")
        member_id = stable_id("structural_member", repository_id, family_id, descriptor)
        compact_members.append({
            "member_id": member_id,
            "family_id": family_id,
            "member_kind": str(rows[0].get("member_kind") or "structural_member"),
            "syntax": syntax,
            "parse_status": "complete",
            "structure_signature": structure_signature,
            "variant_signature": structure_signature,
            "descriptor": descriptor,
            "observed_file_count": len({str(row.get("file_id") or "") for row in rows if str(row.get("file_id") or "")}),
        })

    # SQL file parse footprint is retained as O(files) parser-state evidence, but is not a
    # structural family and does not participate in Miner identity.
    file_observations = []
    for row in sql_scripts:
        path = str(row.get("repository_relative_path") or "")
        file_row = file_by_path.get(path)
        if file_row is None:
            continue
        file_observations.append({
            "file_observation_id": stable_id("file_observation", repository_id, "sql_script_footprint", file_row["file_id"]),
            "repository_id": repository_id,
            "file_id": file_row["file_id"],
            "repository_relative_path": path,
            "observation_kind": "sql_script_footprint",
            "descriptor": {
                key: value for key, value in sorted(row.items())
                if key not in {"sql_footprint_id", "repository_id", "file_id", "repository_relative_path", "source_occurrence_id"}
            },
        })

    compact_families.sort(key=lambda row: (row["family_kind"], row["family_label"], _canonical(row["descriptor"]), row["family_id"]))
    compact_members.sort(key=lambda row: (row["family_id"], row["structure_signature"], row["member_id"]))
    family_file_evidence.sort(key=lambda row: (row["family_id"], row["repository_relative_path"], row["evidence_id"]))
    file_observations.sort(key=lambda row: (row["repository_relative_path"], row["observation_kind"], row["file_observation_id"]))
    return compact_families, compact_members, family_file_evidence, file_observations
