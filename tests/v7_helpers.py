from __future__ import annotations

from typing import Any


_ID_ALIAS = {
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
}


def evidence_for_family(payload: dict[str, Any], family_id: str) -> list[dict[str, Any]]:
    return [
        row for row in payload.get("family_file_evidence") or []
        if row.get("family_id") == family_id
    ]


def observed_rows(payload: dict[str, Any], section: str) -> list[dict[str, Any]]:
    """Test-only readable projection of the compact v7 family envelope.

    This does not recreate SourceOccurrence.  It merely flattens descriptor + family
    metadata so parser behavior tests can remain focused on observed syntax facts.
    """
    rows: list[dict[str, Any]] = []
    for family in payload.get("structural_families") or []:
        provenance = family.get("provenance") or {}
        if provenance.get("observed_section") != section:
            continue
        row = dict(family.get("descriptor") or {})
        row.update({
            "family_id": family["family_id"],
            "family_kind": family.get("family_kind"),
            "family_label": family.get("family_label"),
            "repository_id": family.get("repository_id"),
            "occurrence_count": family.get("occurrence_count"),
            "file_count": family.get("file_count"),
            "source_tree_scope_count": family.get("source_tree_scope_count"),
            "source_tree_scopes": family.get("source_tree_scopes"),
            "structural_salience_score": family.get("structural_salience_score"),
        })
        if "repository_local_salience" in family:
            row["repository_local_salience"] = family["repository_local_salience"]
        alias = _ID_ALIAS.get(section)
        if alias:
            row[alias] = family["family_id"]
        evidence = evidence_for_family(payload, family["family_id"])
        row["file_ids"] = [item["file_id"] for item in evidence]
        row["repository_relative_paths"] = [item["repository_relative_path"] for item in evidence]
        if len(evidence) == 1:
            row["file_id"] = evidence[0]["file_id"]
            row["repository_relative_path"] = evidence[0]["repository_relative_path"]
        for key, target in (
            ("probe_variants", "probe"),
            ("claim_variants", "claim"),
            ("basis_variants", "basis"),
        ):
            values = provenance.get(key) or []
            if len(values) == 1:
                row[target] = values[0]
        rows.append(row)
    return rows


def structural_members(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for member in payload.get("structural_members") or []:
        row = dict(member.get("descriptor") or {})
        row.update({
            "member_id": member["member_id"],
            "family_id": member["family_id"],
            "member_kind": member.get("member_kind"),
            "syntax": member.get("syntax"),
            "parse_status": member.get("parse_status"),
            "structure_signature": member.get("structure_signature"),
            "variant_signature": member.get("variant_signature"),
            "observed_file_count": member.get("observed_file_count"),
        })
        rows.append(row)
    return rows


def sql_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in payload.get("file_observations") or []:
        if item.get("observation_kind") != "sql_script_footprint":
            continue
        row = dict(item.get("descriptor") or {})
        row.update({
            "sql_footprint_id": item["file_observation_id"],
            "repository_id": item.get("repository_id"),
            "file_id": item.get("file_id"),
            "repository_relative_path": item.get("repository_relative_path"),
        })
        rows.append(row)
    for row in observed_rows(payload, "sql_statement_family"):
        row.setdefault("footprint_kind", "sql_statement_family")
        rows.append(row)
    return rows
