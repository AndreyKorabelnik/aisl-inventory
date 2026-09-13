from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .canonical import fingerprint
from .contracts import CONTRACT_FORMAT, LOCALIZATION_KINDS, SOURCE_CONTENT_STORE_FORMAT
from .landscape import source_tree_scope

_VALID_PROBE_STATES = frozenset({"complete", "partial", "failed", "not_applicable", "not_evaluated"})
_FORBIDDEN_V7_TOP_LEVEL = frozenset({
    "source_occurrences",
    "object_occurrence_links",
    "source_primitives",
    "source_formats",
    "dependency_observations",
    "import_namespace_observations",
    "annotation_observations",
    "api_call_observations",
    "config_key_observations",
    "xml_observations",
    "sql_footprint",
})


def _source_snapshot_material(payload: dict[str, Any]) -> dict[str, Any]:
    run = payload.get("run_provenance") or {}
    identity = payload.get("identity") or {}
    return {
        "repository_id": identity.get("repository_id"),
        "scope": "in_scope_repository_files",
        "source_scope_fingerprint": run.get("source_scope_fingerprint"),
        "files": [
            {
                "repository_relative_path": item.get("repository_relative_path"),
                "sha256": item.get("sha256"),
                "byte_size": item.get("byte_size"),
                "readable": item.get("readable"),
                "is_symlink": item.get("is_symlink"),
            }
            for item in payload.get("files") or []
        ],
    }


def _unique_index(rows: Iterable[Mapping[str, Any]], key: str, label: str) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        value = str(row.get(key) or "")
        if not value:
            raise ValueError(f"{label} row has no {key}")
        if value in out:
            raise ValueError(f"duplicate {label} {key}: {value}")
        out[value] = row
    return out


def _verify_compact_contract(payload: dict[str, Any], output: Path) -> None:
    leaked = sorted(_FORBIDDEN_V7_TOP_LEVEL.intersection(payload))
    if leaked:
        raise ValueError(f"v7 payload leaks superseded occurrence/raw sections: {leaked}")

    identity = payload.get("identity") or {}
    repository_id = str(identity.get("repository_id") or "")
    if not repository_id:
        raise ValueError("inventory identity has no repository_id")

    files = payload.get("files") or []
    families = payload.get("structural_families") or []
    members = payload.get("structural_members") or []
    evidence = payload.get("family_file_evidence") or []
    file_observations = payload.get("file_observations") or []

    file_by_id = _unique_index(files, "file_id", "file")
    file_by_path = _unique_index(files, "repository_relative_path", "file path")
    family_by_id = _unique_index(families, "family_id", "structural family")
    _unique_index(members, "member_id", "structural member")
    _unique_index(evidence, "evidence_id", "family-file evidence")
    _unique_index(file_observations, "file_observation_id", "file observation")

    if int((payload.get("source_snapshot") or {}).get("file_count") or 0) != len(files):
        raise ValueError("source_snapshot.file_count does not match files")

    evidence_by_family: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    seen_family_file: set[tuple[str, str]] = set()
    for row in evidence:
        if str(row.get("repository_id") or "") != repository_id:
            raise ValueError(f"family-file evidence repository mismatch: {row.get('evidence_id')}")
        family_id = str(row.get("family_id") or "")
        file_id = str(row.get("file_id") or "")
        family = family_by_id.get(family_id)
        file_row = file_by_id.get(file_id)
        if family is None:
            raise ValueError(f"family-file evidence references unknown family: {family_id}")
        if file_row is None:
            raise ValueError(f"family-file evidence references unknown file: {file_id}")
        pair = (family_id, file_id)
        if pair in seen_family_file:
            raise ValueError(f"duplicate family×file evidence: family={family_id} file={file_id}")
        seen_family_file.add(pair)

        path = str(row.get("repository_relative_path") or "")
        if path != str(file_row.get("repository_relative_path") or ""):
            raise ValueError(f"family-file evidence path differs from referenced file: {row.get('evidence_id')}")
        if str(row.get("source_tree_scope") or "") != source_tree_scope(path):
            raise ValueError(f"family-file evidence source-tree scope mismatch: {row.get('evidence_id')}")
        try:
            count = int(row.get("occurrence_count_in_file") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid family-file evidence occurrence count: {row.get('evidence_id')}") from exc
        if count <= 0:
            raise ValueError(f"family-file evidence occurrence count must be positive: {row.get('evidence_id')}")

        if row.get("content_sha256") != file_row.get("sha256"):
            raise ValueError(f"family-file evidence SHA differs from referenced file: {row.get('evidence_id')}")
        cas_path = row.get("cas_blob_path")
        if cas_path is not None:
            if not isinstance(cas_path, str) or not cas_path:
                raise ValueError(f"invalid CAS path on family-file evidence: {row.get('evidence_id')}")
            expected = f"source-content/blobs/{row.get('content_sha256')}"
            if cas_path != expected:
                raise ValueError(f"non-canonical CAS path on family-file evidence: {row.get('evidence_id')}")
            if not (output / cas_path).is_file():
                raise ValueError(f"family-file evidence CAS blob missing: {cas_path}")

        exemplar = row.get("exemplar") or {}
        kind = str(exemplar.get("localization_kind") or "")
        if kind not in LOCALIZATION_KINDS:
            raise ValueError(f"invalid exemplar localization kind: {kind!r}")
        line_start = exemplar.get("line_start")
        line_end = exemplar.get("line_end")
        column_start = exemplar.get("column_start")
        column_end = exemplar.get("column_end")
        if kind == "exact_span":
            if not all(isinstance(v, int) and v > 0 for v in (line_start, line_end, column_start, column_end)):
                raise ValueError(f"exact_span exemplar has incomplete coordinates: {row.get('evidence_id')}")
            if (line_end, column_end) < (line_start, column_start):
                raise ValueError(f"exact_span exemplar coordinates are reversed: {row.get('evidence_id')}")
        evidence_by_family[family_id].append(row)

    for family_id, family in family_by_id.items():
        if str(family.get("repository_id") or "") != repository_id:
            raise ValueError(f"structural family repository mismatch: {family_id}")
        rows = evidence_by_family.get(family_id, [])
        if not rows:
            raise ValueError(f"structural family has no recoverable family×file evidence: {family_id}")
        occurrence_count = sum(int(row.get("occurrence_count_in_file") or 0) for row in rows)
        if int(family.get("occurrence_count") or 0) != occurrence_count:
            raise ValueError(f"structural family occurrence_count mismatch: {family_id}")
        if int(family.get("file_count") or 0) != len(rows):
            raise ValueError(f"structural family file_count mismatch: {family_id}")
        scopes = sorted({str(row.get("source_tree_scope") or "") for row in rows})
        if sorted(family.get("source_tree_scopes") or []) != scopes:
            raise ValueError(f"structural family source_tree_scopes mismatch: {family_id}")
        if int(family.get("source_tree_scope_count") or 0) != len(scopes):
            raise ValueError(f"structural family source_tree_scope_count mismatch: {family_id}")
        descriptor = family.get("descriptor")
        if not isinstance(descriptor, Mapping):
            raise ValueError(f"structural family descriptor is not an object: {family_id}")

    for row in members:
        family_id = str(row.get("family_id") or "")
        if family_id not in family_by_id:
            raise ValueError(f"structural member references unknown family: {family_id}")
        if not isinstance(row.get("descriptor"), Mapping):
            raise ValueError(f"structural member descriptor is not an object: {row.get('member_id')}")

    for row in file_observations:
        if str(row.get("repository_id") or "") != repository_id:
            raise ValueError(f"file observation repository mismatch: {row.get('file_observation_id')}")
        file_id = str(row.get("file_id") or "")
        file_row = file_by_id.get(file_id)
        if file_row is None:
            raise ValueError(f"file observation references unknown file: {file_id}")
        if str(row.get("repository_relative_path") or "") != str(file_row.get("repository_relative_path") or ""):
            raise ValueError(f"file observation path mismatch: {row.get('file_observation_id')}")

    for row in payload.get("probe_status") or []:
        status = str(row.get("status") or "")
        if status not in _VALID_PROBE_STATES:
            raise ValueError(f"invalid probe status: {status!r}")

    # Diagnostic source references must resolve by path, not by the removed global
    # SourceOccurrence graph.
    for row in payload.get("observation_diagnostics") or []:
        if "source_occurrence_id" in row:
            raise ValueError(f"diagnostic leaks SourceOccurrence ID: {row.get('diagnostic_id')}")
        source_ref = row.get("source_ref") or {}
        path = str(source_ref.get("repository_relative_path") or "")
        if path and path not in file_by_path:
            raise ValueError(f"diagnostic references unknown repository file: {path}")


def verify_inventory_directory(output: Path) -> dict[str, Any]:
    output = output.expanduser().resolve()
    inventory_path = output / "repository_inventory.json"
    if not inventory_path.is_file():
        raise ValueError(f"missing inventory payload: {inventory_path}")
    payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    if payload.get("format") != CONTRACT_FORMAT:
        raise ValueError(f"unexpected inventory format: {payload.get('format')!r}")

    snapshot = payload.get("source_snapshot") or {}
    actual_snapshot = fingerprint(_source_snapshot_material(payload))
    expected_snapshot = str(snapshot.get("fingerprint") or "")
    if expected_snapshot != actual_snapshot:
        raise ValueError(
            f"source snapshot fingerprint mismatch: expected={expected_snapshot} actual={actual_snapshot}"
        )

    expected_semantic = str(payload.get("semantic_fingerprint") or "")
    material = deepcopy(payload)
    material.pop("semantic_fingerprint", None)
    actual_semantic = fingerprint(material)
    if expected_semantic != actual_semantic:
        raise ValueError(
            f"semantic fingerprint mismatch: expected={expected_semantic} actual={actual_semantic}"
        )

    _verify_compact_contract(payload, output)

    store = payload.get("source_content_store") or {}
    verified_blobs = 0
    if store.get("capture_status") != "not_evaluated":
        index_path = output / str(store.get("index_relative_path") or "")
        if not index_path.is_file():
            raise ValueError(f"missing source content index: {index_path}")
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if index.get("schema_version") != SOURCE_CONTENT_STORE_FORMAT:
            raise ValueError(f"unexpected source content store format: {index.get('schema_version')!r}")
        if index.get("source_snapshot_fingerprint") != payload["source_snapshot"]["fingerprint"]:
            raise ValueError("source content index snapshot fingerprint does not match inventory")
        by_path = {item["repository_relative_path"]: item for item in payload.get("files") or []}
        seen_shas: set[str] = set()
        for item in index.get("files") or []:
            path = item["repository_relative_path"]
            source_row = by_path.get(path)
            if source_row is None:
                raise ValueError(f"source content index references unknown file: {path}")
            if source_row.get("sha256") != item.get("content_sha256"):
                raise ValueError(f"source content SHA differs from file observation: {path}")
            blob = output / item["blob_path"]
            if not blob.is_file():
                raise ValueError(f"missing source content blob: {item['blob_path']}")
            data = blob.read_bytes()
            actual = hashlib.sha256(data).hexdigest()
            if actual != item["content_sha256"]:
                raise ValueError(f"source content blob SHA mismatch: {item['blob_path']}")
            if len(data) != item["byte_size"]:
                raise ValueError(f"source content blob size mismatch: {item['blob_path']}")
            seen_shas.add(actual)
        verified_blobs = len(seen_shas)
        if int(index.get("distinct_blob_count") or 0) != verified_blobs:
            raise ValueError("source content index distinct_blob_count mismatch")

    return {
        "status": "pass",
        "format": payload["format"],
        "inventory_id": payload["inventory_id"],
        "semantic_fingerprint": expected_semantic,
        "file_count": payload["source_snapshot"]["file_count"],
        "structural_family_count": len(payload.get("structural_families") or []),
        "family_file_evidence_count": len(payload.get("family_file_evidence") or []),
        "verified_distinct_blob_count": verified_blobs,
    }
