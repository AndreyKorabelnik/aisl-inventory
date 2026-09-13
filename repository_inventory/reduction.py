from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

from .canonical import canonical_json_bytes, fingerprint, stable_id
from .contracts import CONTRACT_FORMAT, REDUCED_INVENTORY_FORMAT

REDUCER_ALGORITHM = "repository-inventory-reducer/v4"
IDENTITY_PROFILE = "repository-structural-shape-and-named-identity/v1"
REPRESENTATIVE_POLICY = "one_min_family_file_evidence_per_exact_shape_and_named_identity"

_COUNT_TOKENS = ("count", "total", "files", "records", "rows", "columns", "fields", "edges", "nodes")
_STABLE_STRING_KEYS = {
    "coverage_status", "payload_section", "fact_type", "section", "format", "status",
    "artifact_kind", "schema_version", "observed_section", "syntax", "parse_status",
    "root_type", "value_type", "direction", "protocol", "method", "family_kind",
    "receiver_basis", "syntax_family", "language", "build_system", "declaration_context",
    "scope", "classification", "type",
}
_REDACT_STRING_KEYS = {
    "artifact_name", "path", "relative_path", "repository_relative_path", "repository_id",
    "repo_id", "system_id", "artifact_id", "member_id", "source_member_id", "file_name",
    "repository_url", "content_fingerprint", "content_sha256", "sha256",
}
_OPERATIONAL_KEY_TOKENS = ("frontier", "unreadable", "diagnostic", "failed", "error_count")
_FREQUENCY_METRIC_KEYS = {
    "occurrence_count", "record_count", "records_count", "file_count",
    "analyzer_eligible_file_count", "outside_analyzer_frontier_file_count",
    "family_member_count", "structure_family_occurrence_count",
}


class ReducedInventoryError(ValueError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{fingerprint(value)[:24]}"


def _numeric_bucket(value: float) -> str:
    value = abs(float(value))
    if value == 0:
        return "zero"
    if value <= 1:
        return "one"
    if value <= 4:
        return "2_4"
    if value <= 16:
        return "5_16"
    if value <= 64:
        return "17_64"
    if value <= 256:
        return "65_256"
    if value <= 1024:
        return "257_1024"
    return "gt_1024"


def _is_operational_key(key: str) -> bool:
    key_l = key.lower()
    return any(token in key_l for token in _OPERATIONAL_KEY_TOKENS)


def _is_frequency_metric(key: str) -> bool:
    key_l = key.lower()
    return key_l in _FREQUENCY_METRIC_KEYS or key_l.endswith("_occurrence_count")


def _normalize_string(value: str, *, key: str) -> str:
    key_l = key.lower()
    if key_l in _REDACT_STRING_KEYS:
        return "<observed_string>"
    if (
        key_l in _STABLE_STRING_KEYS
        or key_l.endswith("_status")
        or key_l.endswith("_kind")
        or key_l.endswith("_kinds")
    ):
        return value
    return "<observed_string>"


def _observed_string_facets(value: Any, *, key: str = "", path: str = "$") -> list[dict[str, str]]:
    """Extract concrete observed strings intentionally removed from structural shape identity.

    This is the complementary projection to ``_normalize_*``: values that are replaced with
    ``<observed_string>`` for shape comparison remain transferable as named-identity evidence.
    Structural enum/status/kind strings remain in the shape basis and are not duplicated here.
    """
    if isinstance(value, str):
        if _normalize_string(value, key=key) == "<observed_string>":
            return [{"path": path, "value": value}]
        return []
    if isinstance(value, Mapping):
        rows: list[dict[str, str]] = []
        for raw_key, child in sorted(value.items(), key=lambda item: str(item[0])):
            child_key = str(raw_key)
            if _is_operational_key(child_key):
                continue
            rows.extend(_observed_string_facets(child, key=child_key, path=f"{path}.{child_key}"))
        return rows
    if isinstance(value, list):
        rows: list[dict[str, str]] = []
        for index, child in enumerate(value):
            rows.extend(_observed_string_facets(child, key=key, path=f"{path}[{index}]"))
        return rows
    return []


def _normalize_exact_metric(value: Any, *, key: str = "") -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _normalize_string(value, key=key)
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for raw_key, child in sorted(value.items(), key=lambda item: str(item[0])):
            child_key = str(raw_key)
            if _is_operational_key(child_key) or _is_frequency_metric(child_key):
                continue
            out[child_key] = _normalize_exact_metric(child, key=child_key)
        return out
    if isinstance(value, list):
        normalized = [_normalize_exact_metric(item, key=key) for item in value]
        by_json = {_canonical(item): item for item in normalized}
        return [by_json[token] for token in sorted(by_json)]
    return f"<{type(value).__name__}>"


def _normalize_similarity_metric(value: Any, *, key: str = "") -> Any:
    key_l = key.lower()
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        if any(token in key_l for token in _COUNT_TOKENS):
            return "<count_metric>"
        return {"numeric_bucket": _numeric_bucket(float(value))}
    if isinstance(value, str):
        return _normalize_string(value, key=key)
    if isinstance(value, Mapping):
        return {
            str(k): _normalize_similarity_metric(v, key=str(k))
            for k, v in sorted(value.items(), key=lambda item: str(item[0]))
            if not _is_operational_key(str(k)) and not _is_frequency_metric(str(k))
        }
    if isinstance(value, list):
        normalized = [_normalize_similarity_metric(item, key=key) for item in value]
        by_json = {_canonical(item): item for item in normalized}
        return [by_json[token] for token in sorted(by_json)]
    return f"<{type(value).__name__}>"




def _positive_numeric_metrics(value: Any, path: str = "$") -> list[dict[str, Any]]:
    """Project positive numeric observed descriptor values without copying source strings.

    The projection is evidence-preserving support for downstream transparent ranking policy;
    it is not itself a ranking/complexity computation. Source traversal order is preserved.
    """
    rows: list[dict[str, Any]] = []
    if isinstance(value, bool) or value is None:
        return rows
    if isinstance(value, (int, float)):
        if float(value) > 0:
            rows.append({"path": path, "value": float(value)})
        return rows
    if isinstance(value, Mapping):
        for key, child in value.items():
            rows.extend(_positive_numeric_metrics(child, f"{path}.{key}"))
        return rows
    if isinstance(value, list):
        for index, child in enumerate(value):
            rows.extend(_positive_numeric_metrics(child, f"{path}[{index}]"))
    return rows

def _inventory_report_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    if candidate.is_dir():
        candidate = candidate / "repository_inventory.json"
    if not candidate.is_file():
        raise ReducedInventoryError(f"Repository Inventory v7 payload not found: {candidate}")
    return candidate


def load_source_inventory(path: str | Path) -> dict[str, Any]:
    report_path = _inventory_report_path(path)
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReducedInventoryError(f"invalid JSON inventory payload: {report_path}") from exc
    validate_source_inventory(payload)
    return payload


def validate_source_inventory(payload: Mapping[str, Any]) -> None:
    if payload.get("format") != CONTRACT_FORMAT:
        raise ReducedInventoryError(f"expected {CONTRACT_FORMAT!r}, got {payload.get('format')!r}")
    identity = payload.get("identity")
    if not isinstance(identity, Mapping) or not str(identity.get("repository_id") or ""):
        raise ReducedInventoryError("Repository Inventory v7 has no identity.repository_id")
    if not str(payload.get("inventory_id") or ""):
        raise ReducedInventoryError("Repository Inventory v7 has no inventory_id")
    expected = str(payload.get("semantic_fingerprint") or "")
    material = deepcopy(dict(payload))
    material.pop("semantic_fingerprint", None)
    actual = fingerprint(material)
    if expected != actual:
        raise ReducedInventoryError(
            f"Repository Inventory semantic fingerprint mismatch: expected={expected} actual={actual}"
        )

    families = payload.get("structural_families")
    members = payload.get("structural_members")
    evidence = payload.get("family_file_evidence")
    if not isinstance(families, list) or not isinstance(members, list) or not isinstance(evidence, list):
        raise ReducedInventoryError("Repository Inventory v7 compact structural sections are incomplete")

    family_by_id: dict[str, Mapping[str, Any]] = {}
    for row in families:
        if not isinstance(row, Mapping):
            raise ReducedInventoryError("invalid structural family row")
        family_id = str(row.get("family_id") or "")
        if not family_id or family_id in family_by_id:
            raise ReducedInventoryError(f"invalid or duplicate structural family identity: {family_id!r}")
        family_by_id[family_id] = row

    member_ids: set[str] = set()
    for row in members:
        if not isinstance(row, Mapping):
            raise ReducedInventoryError("invalid structural member row")
        member_id = str(row.get("member_id") or "")
        family_id = str(row.get("family_id") or "")
        if not member_id or member_id in member_ids or family_id not in family_by_id:
            raise ReducedInventoryError(f"invalid structural member identity/reference: {member_id!r}")
        member_ids.add(member_id)

    evidence_ids: set[str] = set()
    counts_by_family: Counter[str] = Counter()
    files_by_family: dict[str, set[str]] = defaultdict(set)
    for row in evidence:
        if not isinstance(row, Mapping):
            raise ReducedInventoryError("invalid FamilyFileEvidence row")
        evidence_id = str(row.get("evidence_id") or "")
        family_id = str(row.get("family_id") or "")
        file_id = str(row.get("file_id") or "")
        if not evidence_id or evidence_id in evidence_ids or family_id not in family_by_id or not file_id:
            raise ReducedInventoryError(f"invalid FamilyFileEvidence identity/reference: {evidence_id!r}")
        count = int(row.get("occurrence_count_in_file") or 0)
        if count <= 0:
            raise ReducedInventoryError(f"FamilyFileEvidence has non-positive occurrence count: {evidence_id!r}")
        evidence_ids.add(evidence_id)
        counts_by_family[family_id] += count
        files_by_family[family_id].add(file_id)

    for family_id, row in family_by_id.items():
        if counts_by_family[family_id] != int(row.get("occurrence_count") or 0):
            raise ReducedInventoryError(f"structural family occurrence_count mismatch: {family_id}")
        if len(files_by_family[family_id]) != int(row.get("file_count") or 0):
            raise ReducedInventoryError(f"structural family file_count mismatch: {family_id}")




def _member_representation_basis(
    members: list[Mapping[str, Any]], *, family_kind: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not members:
        return None, None

    syntaxes = sorted({str(row.get("syntax") or "") for row in members if str(row.get("syntax") or "")})
    parse_statuses = sorted({
        str(row.get("parse_status") or "") for row in members if str(row.get("parse_status") or "")
    })
    exact_descriptors = [
        _normalize_exact_metric(row.get("descriptor") if isinstance(row.get("descriptor"), Mapping) else {})
        for row in members
    ]
    similarity_descriptors = [
        _normalize_similarity_metric(row.get("descriptor") if isinstance(row.get("descriptor"), Mapping) else {})
        for row in members
    ]
    exact_by_json = {_canonical(item): item for item in exact_descriptors}
    similarity_by_json = {_canonical(item): item for item in similarity_descriptors}

    exact_basis = {
        "member_descriptors": [exact_by_json[token] for token in sorted(exact_by_json)],
        "syntaxes": syntaxes,
        "parse_statuses": parse_statuses,
    }
    similarity_basis = {
        "member_descriptors": [similarity_by_json[token] for token in sorted(similarity_by_json)],
        "syntaxes": syntaxes,
        "parse_statuses": parse_statuses,
    }
    return exact_basis, similarity_basis


def _family_identity_bases(
    family: Mapping[str, Any], members: list[Mapping[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    family_kind = str(family.get("family_kind") or "")
    exact_member_basis, similarity_member_basis = _member_representation_basis(
        members, family_kind=family_kind
    )
    has_member_representation = exact_member_basis is not None
    provenance = family.get("provenance") if isinstance(family.get("provenance"), Mapping) else {}
    evidence_basis: dict[str, Any] = {
        "artifact_kind": CONTRACT_FORMAT,
        "schema_version": CONTRACT_FORMAT,
    }
    if not has_member_representation:
        evidence_basis["observed_section"] = provenance.get("observed_section")

    common = {
        "family_kind": family_kind,
        "source_artifact_kind": CONTRACT_FORMAT,
        "source_schema_version": CONTRACT_FORMAT,
        "evidence_basis": [evidence_basis],
    }
    descriptor = family.get("descriptor") if isinstance(family.get("descriptor"), Mapping) else {}
    exact_basis = {
        **common,
        "observed_metrics": {} if has_member_representation else _normalize_exact_metric(descriptor),
        "member_representation": exact_member_basis,
    }
    similarity_basis = {
        **common,
        "observed_metrics": {} if has_member_representation else _normalize_similarity_metric(descriptor),
        "member_representation": similarity_member_basis,
    }
    return exact_basis, similarity_basis


def _family_named_identity(family: Mapping[str, Any], members: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Project concrete names/strings separately from name-independent structural shape.

    ``family_label`` is the Inventory-owned primary observed identity. Additional concrete string
    facets from family/member descriptors are retained when the structural normalizer masks them.
    Duplicate facet values equal to the primary label are omitted; the name is still preserved once.
    """
    family_kind = str(family.get("family_kind") or "")
    label = str(family.get("family_label") or "")
    descriptor = family.get("descriptor") if isinstance(family.get("descriptor"), Mapping) else {}
    facets = _observed_string_facets(descriptor)
    for member in members:
        member_descriptor = member.get("descriptor") if isinstance(member.get("descriptor"), Mapping) else {}
        facets.extend(_observed_string_facets(member_descriptor, path="$.member_descriptor"))
    unique = {
        (str(row.get("path") or ""), str(row.get("value") or "")): {
            "path": str(row.get("path") or ""),
            "value": str(row.get("value") or ""),
        }
        for row in facets
        if str(row.get("value") or "") and str(row.get("value") or "") != label
    }
    identity = {
        "family_kind": family_kind,
        "family_label": label or None,
        "string_facets": [unique[key] for key in sorted(unique)],
    }
    if identity["family_label"] is None and not identity["string_facets"]:
        return None
    return identity


def _flatten(value: Any, path: str = "$") -> dict[str, str]:
    if isinstance(value, Mapping):
        if not value:
            return {path: "{}"}
        out: dict[str, str] = {}
        for key, child in sorted(value.items(), key=lambda item: str(item[0])):
            out.update(_flatten(child, f"{path}.{key}"))
        return out
    if isinstance(value, list):
        if not value:
            return {path: "[]"}
        out: dict[str, str] = {}
        for index, child in enumerate(value):
            out.update(_flatten(child, f"{path}[{index}]"))
        return out
    return {path: _canonical(value)}


def _variation_dimensions(bases: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    flattened = [_flatten(base) for base in bases]
    if len(flattened) <= 1:
        return []
    all_paths = sorted({path for row in flattened for path in row})
    result = []
    missing = "<missing>"
    for path in all_paths:
        states = sorted({row.get(path, missing) for row in flattened})
        if len(states) > 1:
            result.append({"path": path, "distinct_state_count": len(states)})
    return result


def _diagnostic_projection(rows: Iterable[Any]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        explicit = str(row.get("diagnostic_id") or "")
        identity = explicit or stable_id("inventory_diagnostic", fingerprint(row))
        grouped[identity].append(row)
    result = []
    for identity, values in sorted(grouped.items()):
        variants_by_json = {_canonical(value): value for value in values}
        variants = [variants_by_json[token] for token in sorted(variants_by_json)]
        result.append({
            "diagnostic_identity": identity,
            "source_row_count": len(values),
            "distinct_variant_count": len(variants),
            "identity_conflict": len(variants) > 1,
            "variants": variants,
        })
    return result


def _structural_observation(family: Mapping[str, Any]) -> dict[str, Any]:
    descriptor = family.get("descriptor") if isinstance(family.get("descriptor"), Mapping) else {}
    return {
        "structural_salience_score": float(family.get("structural_salience_score") or 0.0),
        "positive_numeric_metrics": _positive_numeric_metrics(descriptor),
        "outside_analyzer_frontier_extension_family": bool(
            descriptor.get("outside_analyzer_frontier_extension_family")
        ),
    }


def reduce_inventory_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build the transferable deduplicated structural product.

    Reduced v3 separates name-independent structural shape from concrete observed identities.
    Source families with the same structural form are collapsed even when their labels/names differ;
    concrete names remain in a compact ``observed_identities`` section with bounded provenance.
    The source v7 remains the lossless canonical observed repository state.
    """
    validate_source_inventory(payload)
    identity = dict(payload.get("identity") or {})
    repository_id = str(identity.get("repository_id") or "")
    families = [dict(row) for row in payload.get("structural_families") or [] if isinstance(row, Mapping)]
    members = [dict(row) for row in payload.get("structural_members") or [] if isinstance(row, Mapping)]
    evidence = [dict(row) for row in payload.get("family_file_evidence") or [] if isinstance(row, Mapping)]

    members_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in members:
        members_by_family[str(row.get("family_id") or "")].append(row)
    evidence_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for row in evidence:
        evidence_by_family[str(row.get("family_id") or "")].append(row)
        evidence_by_id[str(row.get("evidence_id") or "")] = row

    exact_members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    exact_basis_by_id: dict[str, dict[str, Any]] = {}
    similarity_basis_by_id: dict[str, dict[str, Any]] = {}
    similarity_by_exact: dict[str, str] = {}
    identity_members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    identity_payload_by_id: dict[str, dict[str, Any]] = {}
    family_identity_id: dict[str, str] = {}

    for family in sorted(families, key=lambda row: str(row.get("family_id") or "")):
        family_id = str(family.get("family_id") or "")
        family_members = sorted(members_by_family.get(family_id, []), key=lambda row: str(row.get("member_id") or ""))
        family_evidence = sorted(evidence_by_family.get(family_id, []), key=lambda row: str(row.get("evidence_id") or ""))
        exact_basis, similarity_basis = _family_identity_bases(family, family_members)
        exact_id = _sha_id("exact-representation", exact_basis)
        similarity_id = _sha_id("structural-similarity", similarity_basis)
        named_identity = _family_named_identity(family, family_members)
        identity_id = _sha_id("observed-identity", named_identity) if named_identity is not None else ""

        if exact_id in exact_basis_by_id and _canonical(exact_basis_by_id[exact_id]) != _canonical(exact_basis):
            raise ReducedInventoryError(f"exact identity collision with different basis: {exact_id}")
        if similarity_id in similarity_basis_by_id and _canonical(similarity_basis_by_id[similarity_id]) != _canonical(similarity_basis):
            raise ReducedInventoryError(f"similarity identity collision with different basis: {similarity_id}")
        prior_similarity = similarity_by_exact.get(exact_id)
        if prior_similarity is not None and prior_similarity != similarity_id:
            raise ReducedInventoryError(f"exact representation maps to multiple similarity identities: {exact_id}")
        exact_basis_by_id[exact_id] = exact_basis
        similarity_basis_by_id[similarity_id] = similarity_basis
        similarity_by_exact[exact_id] = similarity_id
        if identity_id:
            if identity_id in identity_payload_by_id and _canonical(identity_payload_by_id[identity_id]) != _canonical(named_identity):
                raise ReducedInventoryError(f"observed identity collision with different payload: {identity_id}")
            identity_payload_by_id[identity_id] = dict(named_identity or {})
            family_identity_id[family_id] = identity_id

        evidence_ids = [str(row.get("evidence_id") or "") for row in family_evidence if str(row.get("evidence_id") or "")]
        member_row = {
            "family_id": family_id,
            "occurrence_count": int(family.get("occurrence_count") or 0),
            "file_count": int(family.get("file_count") or 0),
            "family_file_evidence_ids": evidence_ids,
            "structural_member_count": len(family_members),
            "structural_observation": _structural_observation(family),
            "observed_identity_id": identity_id or None,
        }
        exact_members[exact_id].append(member_row)
        if identity_id:
            identity_members[identity_id].append({
                **member_row,
                "exact_representation_id": exact_id,
                "structural_similarity_id": similarity_id,
            })

    exact_groups: list[dict[str, Any]] = []
    exact_ids_by_similarity: dict[str, list[str]] = defaultdict(list)
    for exact_id, source_families in sorted(exact_members.items()):
        source_families.sort(key=lambda row: str(row.get("family_id") or ""))
        similarity_id = similarity_by_exact[exact_id]
        exact_ids_by_similarity[similarity_id].append(exact_id)
        evidence_ids = sorted({eid for row in source_families for eid in row["family_file_evidence_ids"]})
        if not evidence_ids:
            raise ReducedInventoryError(f"exact representation has no FamilyFileEvidence representative: {exact_id}")
        representative_evidence_id = evidence_ids[0]
        representative_evidence = evidence_by_id.get(representative_evidence_id)
        representative_family_id = str((representative_evidence or {}).get("family_id") or "")
        representative_family = next(
            (row for row in source_families if str(row.get("family_id") or "") == representative_family_id),
            None,
        )
        if representative_family is None:
            raise ReducedInventoryError(f"representative evidence has no exact source family: {representative_evidence_id}")
        representative_identity_id = str(representative_family.get("observed_identity_id") or "")
        file_ids = {
            str(e.get("file_id") or "")
            for row in source_families
            for e in evidence_by_family.get(str(row["family_id"]), [])
            if str(e.get("file_id") or "")
        }
        salience_values = [
            float((row.get("structural_observation") or {}).get("structural_salience_score") or 0.0)
            for row in source_families
        ]
        outside_count = sum(
            1 for row in source_families
            if bool((row.get("structural_observation") or {}).get("outside_analyzer_frontier_extension_family"))
        )
        exact_groups.append({
            "exact_representation_id": exact_id,
            "structural_similarity_id": similarity_id,
            "exact_representation_basis": exact_basis_by_id[exact_id],
            "source_family_count": len(source_families),
            "occurrence_count": sum(int(row["occurrence_count"]) for row in source_families),
            "file_count": len(file_ids),
            "family_file_evidence_count": len(evidence_ids),
            "structural_member_count": sum(int(row["structural_member_count"]) for row in source_families),
            "structural_summary": {
                "source_family_structural_salience_min": min(salience_values, default=0.0),
                "source_family_structural_salience_max": max(salience_values, default=0.0),
                "source_family_structural_salience_sum": round(sum(salience_values), 6),
                "outside_analyzer_frontier_source_family_count": outside_count,
            },
            "representative_source_family_id": representative_family_id,
            "representative_evidence_id": representative_evidence_id,
            "representative_observed_identity_id": representative_identity_id or None,
            "observed_identity_count": len({
                str(row.get("observed_identity_id") or "")
                for row in source_families
                if str(row.get("observed_identity_id") or "")
            }),
            "structural_observation": dict(representative_family["structural_observation"]),
        })

    exact_group_by_id = {str(row["exact_representation_id"]): row for row in exact_groups}
    similarity_groups: list[dict[str, Any]] = []
    for similarity_id, exact_ids in sorted(exact_ids_by_similarity.items()):
        exact_ids = sorted(exact_ids)
        exact_rows = [exact_group_by_id[exact_id] for exact_id in exact_ids]
        source_family_count = sum(int(row["source_family_count"]) for row in exact_rows)
        occurrence_count = sum(int(row["occurrence_count"]) for row in exact_rows)
        evidence_count = sum(int(row["family_file_evidence_count"]) for row in exact_rows)
        member_count = sum(int(row["structural_member_count"]) for row in exact_rows)
        family_ids = {
            str(family.get("family_id") or "")
            for exact_id in exact_ids
            for family in exact_members.get(exact_id, [])
            if str(family.get("family_id") or "")
        }
        file_ids = {
            str(e.get("file_id") or "")
            for family_id in family_ids
            for e in evidence_by_family.get(family_id, [])
            if str(e.get("file_id") or "")
        }
        similarity_groups.append({
            "structural_similarity_id": similarity_id,
            "structural_similarity_basis": similarity_basis_by_id[similarity_id],
            "exact_representation_count": len(exact_ids),
            "exact_representation_ids": exact_ids,
            "source_family_count": source_family_count,
            "occurrence_count": occurrence_count,
            "file_count": len(file_ids),
            "family_file_evidence_count": evidence_count,
            "structural_member_count": member_count,
            "observed_identity_count": len({
                str(row.get("observed_identity_id") or "")
                for exact_id in exact_ids
                for row in exact_members.get(exact_id, [])
                if str(row.get("observed_identity_id") or "")
            }),
            "variation_dimensions": _variation_dimensions(exact_basis_by_id[eid] for eid in exact_ids),
        })

    observed_identities: list[dict[str, Any]] = []
    for identity_id, source_families in sorted(identity_members.items()):
        source_families.sort(key=lambda row: str(row.get("family_id") or ""))
        evidence_ids = sorted({
            eid
            for row in source_families
            for eid in row.get("family_file_evidence_ids") or []
            if str(eid)
        })
        if not evidence_ids:
            raise ReducedInventoryError(f"observed identity has no FamilyFileEvidence representative: {identity_id}")
        representative_evidence_id = evidence_ids[0]
        representative_evidence = evidence_by_id.get(representative_evidence_id) or {}
        representative_family_id = str(representative_evidence.get("family_id") or "")
        exact_ids = sorted({str(row.get("exact_representation_id") or "") for row in source_families if str(row.get("exact_representation_id") or "")})
        file_ids = {
            str(e.get("file_id") or "")
            for row in source_families
            for e in evidence_by_family.get(str(row.get("family_id") or ""), [])
            if str(e.get("file_id") or "")
        }
        observed_identities.append({
            "observed_identity_id": identity_id,
            "identity": identity_payload_by_id[identity_id],
            "exact_representation_ids": exact_ids,
            "source_family_count": len(source_families),
            "occurrence_count": sum(int(row.get("occurrence_count") or 0) for row in source_families),
            "file_count": len(file_ids),
            "family_file_evidence_count": len(evidence_ids),
            "representative_source_family_id": representative_family_id,
            "representative_evidence_id": representative_evidence_id,
        })

    exact_groups.sort(key=lambda row: (str(row["structural_similarity_id"]), str(row["exact_representation_id"])))
    similarity_groups.sort(key=lambda row: str(row["structural_similarity_id"]))
    observed_identities.sort(key=lambda row: (
        str((row.get("identity") or {}).get("family_kind") or ""),
        str((row.get("identity") or {}).get("family_label") or ""),
        str(row.get("observed_identity_id") or ""),
    ))

    reducer_config = {
        "algorithm": REDUCER_ALGORITHM,
        "identity_profile": IDENTITY_PROFILE,
        "representative_policy": REPRESENTATIVE_POLICY,
        "source_content_read": False,
        "source_repository_read": False,
        "lossless_source_membership_preserved": False,
        "concrete_names_participate_in_structural_shape_identity": False,
        "concrete_names_preserved_as_observed_identities": True,
    }
    config_fingerprint = fingerprint(reducer_config)
    diagnostics = _diagnostic_projection(payload.get("observation_diagnostics") or [])
    source_fingerprint = str(payload.get("semantic_fingerprint") or "")

    result: dict[str, Any] = {
        "format": REDUCED_INVENTORY_FORMAT,
        "source_inventory": {
            "format": CONTRACT_FORMAT,
            "inventory_id": str(payload.get("inventory_id") or ""),
            "semantic_fingerprint": source_fingerprint,
            "repository_id": repository_id,
            "source_snapshot_fingerprint": str((payload.get("source_snapshot") or {}).get("fingerprint") or ""),
        },
        "reducer": {**reducer_config, "configuration_fingerprint": config_fingerprint},
        "counts": {
            "source_structural_families": len(families),
            "source_structural_members": len(members),
            "source_family_file_evidence": len(evidence),
            "exact_representations": len(exact_groups),
            "structural_similarity_groups": len(similarity_groups),
            "observed_identities": len(observed_identities),
            "collapsed_source_families": max(0, len(families) - len(exact_groups)),
            "variant_exact_representations": sum(max(0, int(row["exact_representation_count"]) - 1) for row in similarity_groups),
            "representative_evidence": len(exact_groups),
            "unique_diagnostics": len(diagnostics),
            "source_diagnostic_rows": len([row for row in payload.get("observation_diagnostics") or [] if isinstance(row, Mapping)]),
        },
        "exact_representations": exact_groups,
        "structural_similarity_groups": similarity_groups,
        "observed_identities": observed_identities,
        "diagnostics": diagnostics,
        "probe_status": [dict(row) for row in payload.get("probe_status") or [] if isinstance(row, Mapping)],
        "claim_boundary": (
            "Mechanical deduplicated projection of repository-inventory/v7 structural state only. Structural shape identity "
            "does not include concrete observed names/labels; those are preserved separately as observed_identities with "
            "aggregate counts and deterministic FamilyFileEvidence provenance. Source family membership discarded by this "
            "product remains available only in canonical v7. Shape grouping and named-identity grouping do not assert semantic "
            "equivalence, business meaning, analyzer capability, or source equivalence."
        ),
    }
    result["reduced_inventory_id"] = stable_id(
        "repository_inventory_reduced", REDUCED_INVENTORY_FORMAT, source_fingerprint, config_fingerprint
    )
    result["semantic_fingerprint"] = fingerprint(result)
    validate_reduced_inventory(result)
    return result


def validate_reduced_inventory(payload: Mapping[str, Any]) -> None:
    if payload.get("format") != REDUCED_INVENTORY_FORMAT:
        raise ReducedInventoryError(f"expected {REDUCED_INVENTORY_FORMAT!r}, got {payload.get('format')!r}")
    source = payload.get("source_inventory")
    reducer = payload.get("reducer")
    if not isinstance(source, Mapping) or source.get("format") != CONTRACT_FORMAT:
        raise ReducedInventoryError("reduced inventory has no canonical source_inventory identity")
    if not isinstance(reducer, Mapping) or reducer.get("algorithm") != REDUCER_ALGORITHM:
        raise ReducedInventoryError("reduced inventory has unknown reducer algorithm")
    if reducer.get("lossless_source_membership_preserved") is not False:
        raise ReducedInventoryError("Reduced v3 must not preserve lossless source membership")
    if reducer.get("concrete_names_participate_in_structural_shape_identity") is not False:
        raise ReducedInventoryError("Reduced v3 structural shape identity must exclude concrete names")
    if reducer.get("concrete_names_preserved_as_observed_identities") is not True:
        raise ReducedInventoryError("Reduced v3 must preserve concrete names as observed identities")
    expected_config = str(reducer.get("configuration_fingerprint") or "")
    config_material = {str(k): v for k, v in reducer.items() if str(k) != "configuration_fingerprint"}
    if expected_config != fingerprint(config_material):
        raise ReducedInventoryError("reducer configuration fingerprint mismatch")

    expected_semantic = str(payload.get("semantic_fingerprint") or "")
    material = deepcopy(dict(payload))
    material.pop("semantic_fingerprint", None)
    if expected_semantic != fingerprint(material):
        raise ReducedInventoryError("reduced inventory semantic fingerprint mismatch")

    exact_rows = payload.get("exact_representations")
    similarity_rows = payload.get("structural_similarity_groups")
    identity_rows = payload.get("observed_identities")
    if not isinstance(exact_rows, list) or not isinstance(similarity_rows, list) or not isinstance(identity_rows, list):
        raise ReducedInventoryError("reduced inventory grouping sections are incomplete")
    if "family_membership" in payload:
        raise ReducedInventoryError("Reduced v3 must not contain lossless family_membership")

    exact_ids: set[str] = set()
    exact_to_similarity: dict[str, str] = {}
    representative_ids: set[str] = set()
    for row in exact_rows:
        if not isinstance(row, Mapping):
            raise ReducedInventoryError("invalid exact representation row")
        exact_id = str(row.get("exact_representation_id") or "")
        similarity_id = str(row.get("structural_similarity_id") or "")
        if not exact_id or exact_id in exact_ids or not similarity_id:
            raise ReducedInventoryError(f"invalid or duplicate exact representation: {exact_id!r}")
        if exact_id != _sha_id("exact-representation", row.get("exact_representation_basis") or {}):
            raise ReducedInventoryError(f"exact representation identity/basis mismatch: {exact_id}")
        representative_evidence_id = str(row.get("representative_evidence_id") or "")
        representative_family_id = str(row.get("representative_source_family_id") or "")
        if not representative_evidence_id or not representative_family_id:
            raise ReducedInventoryError(f"exact representation has no bounded representative provenance: {exact_id}")
        if representative_evidence_id in representative_ids:
            raise ReducedInventoryError(f"representative evidence is reused across exact representations: {representative_evidence_id}")
        summary = row.get("structural_summary")
        if not isinstance(summary, Mapping):
            raise ReducedInventoryError(f"exact representation has no structural_summary: {exact_id}")
        if int(summary.get("outside_analyzer_frontier_source_family_count") or 0) < 0:
            raise ReducedInventoryError(f"exact representation has invalid structural_summary: {exact_id}")
        observation = row.get("structural_observation")
        if not isinstance(observation, Mapping) or not isinstance(observation.get("positive_numeric_metrics"), list):
            raise ReducedInventoryError(f"exact representation has invalid structural_observation: {exact_id}")
        for metric in observation.get("positive_numeric_metrics") or []:
            if (
                not isinstance(metric, Mapping)
                or not str(metric.get("path") or "")
                or isinstance(metric.get("value"), bool)
                or not isinstance(metric.get("value"), (int, float))
                or float(metric.get("value")) <= 0
            ):
                raise ReducedInventoryError(f"exact representation has invalid numeric metric evidence: {exact_id}")
        exact_ids.add(exact_id)
        exact_to_similarity[exact_id] = similarity_id
        representative_ids.add(representative_evidence_id)

    similarity_ids: set[str] = set()
    for row in similarity_rows:
        if not isinstance(row, Mapping):
            raise ReducedInventoryError("invalid structural similarity group row")
        similarity_id = str(row.get("structural_similarity_id") or "")
        if not similarity_id or similarity_id in similarity_ids:
            raise ReducedInventoryError(f"invalid or duplicate structural similarity group: {similarity_id!r}")
        if similarity_id != _sha_id("structural-similarity", row.get("structural_similarity_basis") or {}):
            raise ReducedInventoryError(f"structural similarity identity/basis mismatch: {similarity_id}")
        referenced = [str(v) for v in row.get("exact_representation_ids") or []]
        if any(value not in exact_ids or exact_to_similarity.get(value) != similarity_id for value in referenced):
            raise ReducedInventoryError(f"structural similarity group has invalid exact membership: {similarity_id}")
        if len(referenced) != int(row.get("exact_representation_count") or 0):
            raise ReducedInventoryError(f"structural similarity exact count mismatch: {similarity_id}")
        similarity_ids.add(similarity_id)

    observed_identity_ids: set[str] = set()
    identity_representatives: set[str] = set()
    identity_exact_coverage: set[str] = set()
    for row in identity_rows:
        if not isinstance(row, Mapping):
            raise ReducedInventoryError("invalid observed identity row")
        identity_id = str(row.get("observed_identity_id") or "")
        identity = row.get("identity")
        if not identity_id or identity_id in observed_identity_ids or not isinstance(identity, Mapping):
            raise ReducedInventoryError(f"invalid or duplicate observed identity: {identity_id!r}")
        if identity_id != _sha_id("observed-identity", identity):
            raise ReducedInventoryError(f"observed identity identity/payload mismatch: {identity_id}")
        if not str(identity.get("family_kind") or ""):
            raise ReducedInventoryError(f"observed identity has no family_kind: {identity_id}")
        label = identity.get("family_label")
        if label is not None and not isinstance(label, str):
            raise ReducedInventoryError(f"observed identity has invalid family_label: {identity_id}")
        facets = identity.get("string_facets")
        if not isinstance(facets, list):
            raise ReducedInventoryError(f"observed identity has no string_facets: {identity_id}")
        for facet in facets:
            if not isinstance(facet, Mapping) or not str(facet.get("path") or "") or not isinstance(facet.get("value"), str):
                raise ReducedInventoryError(f"observed identity has invalid string facet: {identity_id}")
        listed_exact = [str(v) for v in row.get("exact_representation_ids") or [] if str(v)]
        if not listed_exact or any(v not in exact_ids for v in listed_exact):
            raise ReducedInventoryError(f"observed identity has unresolved exact representation: {identity_id}")
        rep = str(row.get("representative_evidence_id") or "")
        family_id = str(row.get("representative_source_family_id") or "")
        if not rep or not family_id:
            raise ReducedInventoryError(f"observed identity has no bounded representative provenance: {identity_id}")
        observed_identity_ids.add(identity_id)
        identity_representatives.add(rep)
        identity_exact_coverage.update(listed_exact)

    known_identity_ids = set(observed_identity_ids)
    for row in exact_rows:
        identity_id = str(row.get("representative_observed_identity_id") or "")
        if identity_id and identity_id not in known_identity_ids:
            raise ReducedInventoryError(
                f"exact representation references unknown representative observed identity: {row.get('exact_representation_id')}"
            )

    counts = payload.get("counts") if isinstance(payload.get("counts"), Mapping) else {}
    if len(exact_ids) != int(counts.get("exact_representations") or 0):
        raise ReducedInventoryError("exact representation count mismatch")
    if len(similarity_ids) != int(counts.get("structural_similarity_groups") or 0):
        raise ReducedInventoryError("structural similarity group count mismatch")
    if len(representative_ids) != int(counts.get("representative_evidence") or 0):
        raise ReducedInventoryError("representative evidence count mismatch")
    if len(observed_identity_ids) != int(counts.get("observed_identities") or 0):
        raise ReducedInventoryError("observed identity count mismatch")
    if int(counts.get("source_structural_families") or 0) < len(exact_ids):
        raise ReducedInventoryError("source structural family count cannot be smaller than exact representation count")


def build_reduced_inventory(
    *, inventory: str | Path, output: str | Path, overwrite: bool = False
) -> dict[str, Any]:
    payload = reduce_inventory_payload(load_source_inventory(inventory))
    target = Path(output).expanduser().resolve()
    if target.exists() and not overwrite:
        raise FileExistsError(f"output already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(canonical_json_bytes(payload) + b"\n")
    return payload


def verify_reduced_inventory(path: str | Path) -> dict[str, Any]:
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_file():
        raise ReducedInventoryError(f"reduced inventory not found: {candidate}")
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReducedInventoryError(f"invalid reduced inventory JSON: {candidate}") from exc
    validate_reduced_inventory(payload)
    return {
        "status": "verified",
        "format": payload["format"],
        "reduced_inventory_id": payload["reduced_inventory_id"],
        "semantic_fingerprint": payload["semantic_fingerprint"],
        **dict(payload.get("counts") or {}),
    }
