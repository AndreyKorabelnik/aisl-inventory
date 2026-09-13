from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from .canonical import fingerprint, pretty_json_bytes, stable_id
from .contracts import (
    CONTRACT_FORMAT,
    REDUCED_INVENTORY_FORMAT,
    SELECTED_SOURCE_EXPORT_FORMAT,
    SELECTED_SOURCE_EXPORT_REQUEST_FORMAT,
)
from .reduction import validate_reduced_inventory

DEFAULT_FRAGMENT_CONTEXT_LINES = 10
DEFAULT_FILE_SCOPE_MAX_BYTES = 64 * 1024


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _normalize_evidence_ids(values: Iterable[object]) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value).strip()})


def load_export_request(path: str | Path) -> list[str]:
    request_path = Path(path).expanduser().resolve()
    value = _read_json(request_path)
    if value.get("format") != SELECTED_SOURCE_EXPORT_REQUEST_FORMAT:
        raise ValueError(
            f"expected {SELECTED_SOURCE_EXPORT_REQUEST_FORMAT!r}, got {value.get('format')!r}"
        )
    evidence_ids = value.get("evidence_ids")
    if not isinstance(evidence_ids, list):
        raise ValueError("selected-source export request has no evidence_ids list")
    return _normalize_evidence_ids(evidence_ids)


def _inventory_root(path: str | Path) -> tuple[Path, Path]:
    candidate = Path(path).expanduser().resolve()
    if candidate.is_file() and candidate.name == "repository_inventory.json":
        root = candidate.parent
        report = candidate
    elif candidate.is_dir() and (candidate / "repository_inventory.json").is_file():
        root = candidate
        report = root / "repository_inventory.json"
    else:
        raise ValueError(f"{CONTRACT_FORMAT} artifact not found: {candidate}")
    return root, report


def _diagnostic(kind: str, **basis: Any) -> dict[str, Any]:
    return {
        "diagnostic_id": stable_id("selected_source_export_diagnostic", kind, fingerprint(basis)),
        "diagnostic_kind": kind,
        "basis": basis,
    }


def _source_index(root: Path, report: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]]]:
    store = report.get("source_content_store") or {}
    if store.get("capture_status") == "not_evaluated":
        return None, {}
    relative = str(store.get("index_relative_path") or "")
    if not relative:
        return None, {}
    index_path = root / relative
    if not index_path.is_file():
        return None, {}
    index = _read_json(index_path)
    by_path = {
        str(row.get("repository_relative_path") or ""): dict(row)
        for row in index.get("files") or []
        if isinstance(row, Mapping) and str(row.get("repository_relative_path") or "")
    }
    return index, by_path


def _checksum_rows(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(item for item in root.rglob("*") if item.is_file() and item.name != "SHA256SUMS")
    ]


def _write_sha256sums(root: Path, rows: list[dict[str, Any]]) -> None:
    (root / "SHA256SUMS").write_text(
        "".join(f"{row['sha256']}  {row['path']}\n" for row in rows), encoding="utf-8"
    )


def _reduced_evidence_ids_for_inventory(reduced: str | Path, report: Mapping[str, Any]) -> list[str]:
    value = _read_json(Path(reduced).expanduser().resolve())
    validate_reduced_inventory(value)
    source = value.get("source_inventory") if isinstance(value.get("source_inventory"), Mapping) else {}
    identity = report.get("identity") if isinstance(report.get("identity"), Mapping) else {}
    expected = {
        "format": CONTRACT_FORMAT,
        "inventory_id": str(report.get("inventory_id") or ""),
        "semantic_fingerprint": str(report.get("semantic_fingerprint") or ""),
        "repository_id": str(identity.get("repository_id") or ""),
    }
    actual = {
        "format": str(source.get("format") or ""),
        "inventory_id": str(source.get("inventory_id") or ""),
        "semantic_fingerprint": str(source.get("semantic_fingerprint") or ""),
        "repository_id": str(source.get("repository_id") or ""),
    }
    if actual != expected:
        raise ValueError(
            "Reduced Inventory source identity does not match export Inventory: "
            f"expected={expected!r} actual={actual!r}"
        )
    evidence_ids = _normalize_evidence_ids([
        *(row.get("representative_evidence_id") for row in value.get("exact_representations") or [] if isinstance(row, Mapping)),
        *(row.get("representative_evidence_id") for row in value.get("observed_identities") or [] if isinstance(row, Mapping)),
    ])
    if not evidence_ids:
        raise ValueError(f"{REDUCED_INVENTORY_FORMAT} contains no representative FamilyFileEvidence IDs")
    return evidence_ids


def _fragment_request_from_evidence(
    *, source_bytes: bytes, evidence: Mapping[str, Any], context_lines: int, file_scope_max_bytes: int
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    exemplar = evidence.get("exemplar") if isinstance(evidence.get("exemplar"), Mapping) else {}
    kind = str(exemplar.get("localization_kind") or "")
    line_start = exemplar.get("line_start")
    line_end = exemplar.get("line_end")
    path = str(evidence.get("repository_relative_path") or "")
    evidence_id = str(evidence.get("evidence_id") or "")
    lines = source_bytes.splitlines(keepends=True)
    if not lines and source_bytes:
        lines = [source_bytes]

    if isinstance(line_start, int) and isinstance(line_end, int):
        if line_start < 1 or line_end < line_start or line_start > len(lines):
            return None, _diagnostic(
                "family_file_evidence_span_out_of_bounds",
                evidence_id=evidence_id,
                repository_relative_path=path,
                localization_kind=kind or None,
                line_start=line_start,
                line_end=line_end,
            )
        bounded_end = min(line_end, len(lines))
        return {
            "scope": "localized_span_with_context",
            "requested_line_start": max(1, line_start - context_lines),
            "requested_line_end": min(len(lines), bounded_end + context_lines),
        }, None

    if kind == "file":
        if len(source_bytes) > file_scope_max_bytes:
            return None, _diagnostic(
                "file_scope_evidence_exceeds_fragment_limit",
                evidence_id=evidence_id,
                repository_relative_path=path,
                source_bytes=len(source_bytes),
                file_scope_max_bytes=file_scope_max_bytes,
            )
        return {
            "scope": "file_scope",
            "requested_line_start": 1 if lines else None,
            "requested_line_end": len(lines) if lines else None,
        }, None

    return None, _diagnostic(
        "family_file_evidence_has_no_exportable_span",
        evidence_id=evidence_id,
        repository_relative_path=path,
        localization_kind=kind or None,
        line_start=line_start,
        line_end=line_end,
    )


def _merge_requests(requests: list[dict[str, Any]], *, source_bytes: bytes) -> list[dict[str, Any]]:
    if not requests:
        return []
    if any(row["scope"] == "file_scope" for row in requests):
        lines = source_bytes.splitlines(keepends=True)
        if not lines and source_bytes:
            lines = [source_bytes]
        return [{
            "fragment_scope": "file_scope",
            "exported_line_start": 1 if lines else None,
            "exported_line_end": len(lines) if lines else None,
            "evidence_ids": sorted(row["evidence_id"] for row in requests),
        }]

    ordered = sorted(requests, key=lambda row: (row["requested_line_start"], row["requested_line_end"], row["evidence_id"]))
    merged: list[dict[str, Any]] = []
    for row in ordered:
        start = int(row["requested_line_start"])
        end = int(row["requested_line_end"])
        if not merged or start > int(merged[-1]["exported_line_end"]) + 1:
            merged.append({
                "fragment_scope": "localized_context_union",
                "exported_line_start": start,
                "exported_line_end": end,
                "evidence_ids": [row["evidence_id"]],
            })
        else:
            merged[-1]["exported_line_end"] = max(int(merged[-1]["exported_line_end"]), end)
            merged[-1]["evidence_ids"].append(row["evidence_id"])
    for row in merged:
        row["evidence_ids"] = sorted(set(row["evidence_ids"]))
    return merged


def _slice_lines(source_bytes: bytes, start: int | None, end: int | None) -> bytes:
    if start is None or end is None:
        return source_bytes
    lines = source_bytes.splitlines(keepends=True)
    if not lines and source_bytes:
        lines = [source_bytes]
    return b"".join(lines[start - 1 : end])


def build_selected_source_export(
    *,
    inventory: str | Path,
    output: str | Path,
    evidence_ids: Iterable[object],
    reduced: str | Path | None = None,
    overwrite: bool = False,
    context_lines: int = DEFAULT_FRAGMENT_CONTEXT_LINES,
    file_scope_max_bytes: int = DEFAULT_FILE_SCOPE_MAX_BYTES,
) -> dict[str, Any]:
    """Resolve Inventory evidence IDs to provenance plus bounded fragments from immutable CAS bytes."""
    if context_lines < 0:
        raise ValueError("context_lines must be non-negative")
    if file_scope_max_bytes < 1:
        raise ValueError("file_scope_max_bytes must be positive")

    root, report_path = _inventory_root(inventory)
    report = _read_json(report_path)
    if report.get("format") != CONTRACT_FORMAT:
        raise ValueError(f"expected {CONTRACT_FORMAT!r}, got {report.get('format')!r}")

    selected_values = list(evidence_ids)
    if reduced is not None:
        selected_values.extend(_reduced_evidence_ids_for_inventory(reduced, report))
    selected_ids = _normalize_evidence_ids(selected_values)
    if not selected_ids:
        raise ValueError("at least one FamilyFileEvidence ID or --reduced input is required")

    output = Path(output).expanduser().resolve()
    if output.exists() and not overwrite:
        raise ValueError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    evidence_by_id = {
        str(row.get("evidence_id") or ""): dict(row)
        for row in report.get("family_file_evidence") or []
        if isinstance(row, Mapping) and str(row.get("evidence_id") or "")
    }
    source_index, index_by_path = _source_index(root, report)
    diagnostics: list[dict[str, Any]] = []
    if source_index is None:
        diagnostics.append(_diagnostic(
            "source_content_store_unavailable",
            capture_status=(report.get("source_content_store") or {}).get("capture_status"),
        ))

    source_files: dict[str, dict[str, Any]] = {}
    source_bytes_by_ref: dict[str, bytes] = {}
    provenance_catalog: dict[str, dict[str, Any]] = {}
    bindings: list[dict[str, Any]] = []
    binding_by_id: dict[str, dict[str, Any]] = {}
    requests_by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for evidence_id in selected_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            diagnostics.append(_diagnostic("family_file_evidence_not_found", evidence_id=evidence_id))
            continue
        path = str(evidence.get("repository_relative_path") or "")
        evidence_sha = str(evidence.get("content_sha256") or "")
        exemplar = dict(evidence.get("exemplar") or {})
        provenance = dict(exemplar.pop("provenance", {}) or {})
        provenance_ref = None
        if provenance:
            provenance_ref = stable_id("evidence_provenance", fingerprint(provenance))
            provenance_catalog.setdefault(provenance_ref, {"provenance_id": provenance_ref, **provenance})
        localization = {**exemplar, "provenance_ref": provenance_ref}
        binding: dict[str, Any] = {
            "evidence_id": evidence_id,
            "family_id": evidence.get("family_id"),
            "source_file_ref": None,
            "occurrence_count_in_file": evidence.get("occurrence_count_in_file"),
            "observed_localization": localization,
            "fragment_status": "unavailable",
            "fragment_id": None,
            "fragment_ref": None,
        }
        bindings.append(binding)
        binding_by_id[evidence_id] = binding

        if not path or not evidence_sha:
            diagnostics.append(_diagnostic(
                "family_file_evidence_has_no_exact_blob_identity",
                evidence_id=evidence_id,
                repository_relative_path=path or None,
                content_sha256=evidence_sha or None,
            ))
            continue
        index_row = index_by_path.get(path)
        if index_row is None:
            diagnostics.append(_diagnostic(
                "source_content_index_entry_missing",
                evidence_id=evidence_id,
                repository_relative_path=path,
                expected_sha256=evidence_sha,
            ))
            continue
        index_sha = str(index_row.get("content_sha256") or "")
        if index_sha != evidence_sha:
            diagnostics.append(_diagnostic(
                "family_file_evidence_sha_mismatch",
                evidence_id=evidence_id,
                repository_relative_path=path,
                evidence_sha256=evidence_sha,
                index_sha256=index_sha,
            ))
            continue
        blob_relative = str(evidence.get("cas_blob_path") or index_row.get("blob_path") or "")
        blob = root / blob_relative
        if not blob.is_file():
            diagnostics.append(_diagnostic(
                "source_content_blob_missing",
                evidence_id=evidence_id,
                repository_relative_path=path,
                expected_sha256=evidence_sha,
            ))
            continue
        source_bytes = blob.read_bytes()
        actual_sha = _sha256_bytes(source_bytes)
        if actual_sha != evidence_sha:
            diagnostics.append(_diagnostic(
                "source_content_blob_sha_mismatch",
                evidence_id=evidence_id,
                repository_relative_path=path,
                expected_sha256=evidence_sha,
                actual_sha256=actual_sha,
            ))
            continue

        source_file_ref = stable_id("evidence_source_file", path, evidence_sha)
        source_files.setdefault(source_file_ref, {
            "source_file_id": source_file_ref,
            "repository_relative_path": path,
            "content_sha256": evidence_sha,
            "byte_size": int(index_row.get("byte_size") or len(source_bytes)),
        })
        source_bytes_by_ref.setdefault(source_file_ref, source_bytes)
        binding["source_file_ref"] = source_file_ref

        request, diagnostic = _fragment_request_from_evidence(
            source_bytes=source_bytes,
            evidence=evidence,
            context_lines=context_lines,
            file_scope_max_bytes=file_scope_max_bytes,
        )
        if diagnostic is not None:
            diagnostics.append(diagnostic)
            continue
        assert request is not None
        requests_by_file[source_file_ref].append({"evidence_id": evidence_id, **request})

    fragments: dict[str, dict[str, Any]] = {}
    fragment_blobs: dict[str, bytes] = {}
    for source_file_ref in sorted(requests_by_file):
        source_row = source_files[source_file_ref]
        source_bytes = source_bytes_by_ref[source_file_ref]
        for merged in _merge_requests(requests_by_file[source_file_ref], source_bytes=source_bytes):
            start = merged["exported_line_start"]
            end = merged["exported_line_end"]
            fragment_bytes = _slice_lines(source_bytes, start, end)
            fragment_sha = _sha256_bytes(fragment_bytes)
            fragment_id = stable_id(
                "repository_source_fragment",
                source_file_ref,
                merged["fragment_scope"],
                start,
                end,
            )
            fragment_path = f"fragments/blobs/{fragment_sha}"
            fragments[fragment_id] = {
                "fragment_id": fragment_id,
                "source_file_ref": source_file_ref,
                "fragment_scope": merged["fragment_scope"],
                "exported_line_start": start,
                "exported_line_end": end,
                "fragment_sha256": fragment_sha,
                "fragment_bytes": len(fragment_bytes),
                "fragment_blob_path": fragment_path,
                "evidence_count": len(merged["evidence_ids"]),
            }
            fragment_blobs.setdefault(fragment_sha, fragment_bytes)
            for evidence_id in merged["evidence_ids"]:
                binding = binding_by_id[evidence_id]
                binding["fragment_status"] = "available"
                binding["fragment_id"] = fragment_id
                binding["fragment_ref"] = fragment_path

    resolved_ids = sorted(row["evidence_id"] for row in bindings if row["fragment_status"] == "available")
    unresolved_ids = sorted(set(selected_ids) - set(resolved_ids))
    status = "complete" if not unresolved_ids else "partial"
    fragment_policy = {
        "localized_span_context_lines": context_lines,
        "file_scope_max_bytes": file_scope_max_bytes,
        "overlapping_contexts_merged_per_source_file": True,
        "localized_span_source": "Inventory-owned FamilyFileEvidence exemplar line span",
        "file_scope_source": "Inventory-owned localization_kind=file",
        "large_file_scope_fallback_allowed": False,
    }
    inventory_identity = dict(report.get("identity") or {})
    manifest: dict[str, Any] = {
        "format": SELECTED_SOURCE_EXPORT_FORMAT,
        "status": status,
        "export_id": stable_id(
            "selected_source_export",
            report.get("inventory_id"),
            report.get("semantic_fingerprint"),
            fingerprint(selected_ids),
            fingerprint(fragment_policy),
        ),
        "source_inventory": {
            "format": report.get("format"),
            "inventory_id": report.get("inventory_id"),
            "semantic_fingerprint": report.get("semantic_fingerprint"),
            "repository_id": inventory_identity.get("repository_id"),
            "repository_inventory_report_sha256": _sha256_file(report_path),
        },
        "request": {
            "requested_evidence_count": len(selected_ids),
            "requested_evidence_ids": selected_ids,
            "requested_evidence_ids_fingerprint": fingerprint(selected_ids),
        },
        "fragment_policy": fragment_policy,
        "provenance_catalog": sorted(provenance_catalog.values(), key=lambda row: row["provenance_id"]),
        "source_files": sorted(source_files.values(), key=lambda row: row["source_file_id"]),
        "evidence_bindings": sorted(bindings, key=lambda row: row["evidence_id"]),
        "fragments": sorted(fragments.values(), key=lambda row: row["fragment_id"]),
        "diagnostics": sorted(diagnostics, key=lambda row: (row["diagnostic_kind"], row["diagnostic_id"])),
        "counts": {
            "requested_evidence": len(selected_ids),
            "resolved_evidence": len(resolved_ids),
            "unresolved_evidence": len(unresolved_ids),
            "source_files_touched": len(source_files),
            "fragments": len(fragments),
            "distinct_fragment_blobs": len(fragment_blobs),
            "fragment_bytes": sum(len(value) for value in fragment_blobs.values()),
        },
        "transport_policy": {
            "family_file_evidence_ids_are_inventory_owned": True,
            "reduced_references_consumed": reduced is not None,
            "selection_semantics_interpreted": False,
            "repository_source_read": False,
            "workspace_fallback_allowed": False,
            "source_cas_sha256_validated": True,
            "fragments_are_exact_source_byte_slices": True,
            "full_source_file_blobs_copied": False,
            "redaction_performed": False,
            "security_review_performed": False,
        },
        "claim_boundary": (
            "Post-selection evidence transport only. The exporter resolves explicit Inventory-owned "
            "FamilyFileEvidence IDs against immutable repository-inventory/v7 CAS bytes, validates source "
            "SHA-256, and emits bounded exact byte fragments using only Inventory-observed localization. "
            "It may merge overlapping requested contexts from the same observed source file, but does not "
            "select candidates, infer novelty, parse or re-localize source, redact source, perform security "
            "review, or reopen repository/workspace source."
        ),
    }
    manifest["export_fingerprint"] = fingerprint(manifest)

    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=str(output.parent)))
    try:
        (staging / "fragments" / "blobs").mkdir(parents=True)
        for sha256, content in sorted(fragment_blobs.items()):
            (staging / "fragments" / "blobs" / sha256).write_bytes(content)
        (staging / "export_manifest.json").write_bytes(pretty_json_bytes(manifest))
        _write_sha256sums(staging, _checksum_rows(staging))
        if output.exists():
            shutil.rmtree(output)
        staging.replace(output)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


def verify_selected_source_export(path: str | Path) -> dict[str, Any]:
    root = Path(path).expanduser().resolve()
    manifest_path = root / "export_manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"selected source export has no export_manifest.json: {root}")
    manifest = _read_json(manifest_path)
    if manifest.get("format") != SELECTED_SOURCE_EXPORT_FORMAT:
        raise ValueError(f"expected {SELECTED_SOURCE_EXPORT_FORMAT!r}, got {manifest.get('format')!r}")
    expected_fingerprint = str(manifest.get("export_fingerprint") or "")
    without = dict(manifest)
    without.pop("export_fingerprint", None)
    if expected_fingerprint != fingerprint(without):
        raise ValueError("selected source export fingerprint mismatch")

    source_file_ids = {str(row.get("source_file_id") or "") for row in manifest.get("source_files") or [] if isinstance(row, Mapping)}
    provenance_ids = {str(row.get("provenance_id") or "") for row in manifest.get("provenance_catalog") or [] if isinstance(row, Mapping)}
    fragment_by_id: dict[str, Mapping[str, Any]] = {}
    seen_blob_shas: set[str] = set()
    for row in manifest.get("fragments") or []:
        if not isinstance(row, Mapping):
            raise ValueError("selected source export contains invalid fragment row")
        fragment_id = str(row.get("fragment_id") or "")
        if str(row.get("source_file_ref") or "") not in source_file_ids:
            raise ValueError(f"fragment references unknown source file: {fragment_id!r}")
        expected_sha = str(row.get("fragment_sha256") or "")
        fragment = root / str(row.get("fragment_blob_path") or "")
        if not fragment_id or not expected_sha or not fragment.is_file():
            raise ValueError(f"selected source export fragment missing: {row.get('fragment_blob_path')!r}")
        actual_sha = _sha256_file(fragment)
        if actual_sha != expected_sha:
            raise ValueError(f"selected source export fragment SHA mismatch: {row.get('fragment_blob_path')!r}")
        if int(row.get("fragment_bytes") or -1) != fragment.stat().st_size:
            raise ValueError(f"selected source export fragment size mismatch: {row.get('fragment_blob_path')!r}")
        fragment_by_id[fragment_id] = row
        seen_blob_shas.add(expected_sha)

    available_bindings = 0
    for row in manifest.get("evidence_bindings") or []:
        if not isinstance(row, Mapping):
            raise ValueError("selected source export contains invalid evidence binding")
        source_file_ref = row.get("source_file_ref")
        if source_file_ref is not None and str(source_file_ref) not in source_file_ids:
            raise ValueError(f"evidence binding references unknown source file: {row.get('evidence_id')!r}")
        provenance_ref = (row.get("observed_localization") or {}).get("provenance_ref") if isinstance(row.get("observed_localization"), Mapping) else None
        if provenance_ref is not None and str(provenance_ref) not in provenance_ids:
            raise ValueError(f"evidence binding references unknown provenance: {row.get('evidence_id')!r}")
        if row.get("fragment_status") == "available":
            available_bindings += 1
            fragment_id = str(row.get("fragment_id") or "")
            fragment = fragment_by_id.get(fragment_id)
            if fragment is None:
                raise ValueError(f"evidence binding references unknown fragment: {fragment_id!r}")
            if str(row.get("fragment_ref") or "") != str(fragment.get("fragment_blob_path") or ""):
                raise ValueError(f"evidence binding fragment_ref mismatch: {row.get('evidence_id')!r}")

    counts = manifest.get("counts") or {}
    if int(counts.get("resolved_evidence") or 0) != available_bindings:
        raise ValueError("selected source export resolved_evidence count mismatch")
    if int(counts.get("fragments") or 0) != len(fragment_by_id):
        raise ValueError("selected source export fragments count mismatch")
    if int(counts.get("distinct_fragment_blobs") or 0) != len(seen_blob_shas):
        raise ValueError("selected source export distinct_fragment_blobs count mismatch")
    blob_dir = root / "fragments" / "blobs"
    actual_fragment_bytes = sum(path.stat().st_size for path in blob_dir.iterdir() if path.is_file())
    if int(counts.get("fragment_bytes") or 0) != actual_fragment_bytes:
        raise ValueError("selected source export fragment_bytes count mismatch")

    sums_path = root / "SHA256SUMS"
    if not sums_path.is_file():
        raise ValueError("selected source export has no SHA256SUMS")
    expected_rows = {row["path"]: row for row in _checksum_rows(root) if row["path"] != "SHA256SUMS"}
    listed: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            sha, relative = line.split("  ", 1)
            listed[relative] = sha
    if set(listed) != set(expected_rows):
        raise ValueError("selected source export SHA256SUMS path set mismatch")
    for relative, row in expected_rows.items():
        if listed[relative] != row["sha256"]:
            raise ValueError(f"selected source export SHA256SUMS mismatch: {relative}")

    return {
        "status": "pass",
        "format": manifest["format"],
        "export_id": manifest.get("export_id"),
        "export_status": manifest.get("status"),
        "requested_evidence": int(counts.get("requested_evidence") or 0),
        "resolved_evidence": int(counts.get("resolved_evidence") or 0),
        "source_files_touched": int(counts.get("source_files_touched") or 0),
        "fragments": int(counts.get("fragments") or 0),
        "distinct_fragment_blobs": len(seen_blob_shas),
        "fragment_bytes": int(counts.get("fragment_bytes") or 0),
    }
