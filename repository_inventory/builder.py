from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from .canonical import fingerprint, pretty_json_bytes, stable_id
from .config import InventoryConfig
from .contracts import CONTRACT_FORMAT, SOURCE_CONTENT_STORE_FORMAT
from .scanner import build_source_primitives, scan_files
from .structured_probe import run_i3a_probes
from .java_probe import run_java_probes
from .build_config_probe import run_build_config_probes
from .sql_probe import run_sql_probe
from .compact_model import build_compact_model
from .http_half_wires import align_exact_http_boundary_payloads, project_inbound_java_http_boundaries, project_outbound_http_boundaries
from .kafka_half_wires import project_kafka_boundaries
from .version import __version__


_NOT_EVALUATED_PROBES = (
    "interface_observations",
)


def _validate_repository_and_output(repository: Path, output: Path) -> tuple[Path, Path]:
    repository = repository.expanduser().resolve()
    output = output.expanduser().resolve()
    if not repository.is_dir():
        raise ValueError(f"repository is not a directory: {repository}")
    try:
        output.relative_to(repository)
    except ValueError:
        pass
    else:
        raise ValueError("output directory must be outside the repository source root")
    return repository, output


def _source_snapshot_material(
    *,
    repository_id: str,
    source_scope_fingerprint: str,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "repository_id": repository_id,
        "scope": "in_scope_repository_files",
        "source_scope_fingerprint": source_scope_fingerprint,
        "files": [
            {
                "repository_relative_path": item["repository_relative_path"],
                "sha256": item["sha256"],
                "byte_size": item["byte_size"],
                "readable": item["readable"],
                "is_symlink": item["is_symlink"],
            }
            for item in files
        ],
    }


def _temporary_file_occurrences(
    *,
    repository_id: str,
    files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build transient parser-local file occurrences.

    These rows exist only while probes run so mature parser adapters can share exact
    localization/counting mechanics. They are deliberately not part of v7 persistence.
    """
    occurrences: list[dict[str, Any]] = []
    for item in files:
        occurrence_id = stable_id(
            "temporary_source_occurrence",
            repository_id,
            item["repository_relative_path"],
            "file",
            item["sha256"] or item["content_capture_status"],
        )
        occurrences.append({
            "occurrence_id": occurrence_id,
            "repository_id": repository_id,
            "repository_relative_path": item["repository_relative_path"],
            "localization_kind": "file",
            "line_start": None,
            "line_end": None,
            "content_sha256": item["sha256"],
            "provenance": {
                "probe_id": "file_inventory",
                "probe_version": "1",
                "basis": "direct_repository_file_observation",
            },
        })
    return occurrences

def _content_store_index(
    *,
    repository_id: str,
    source_snapshot_fingerprint: str,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    captured_files = [
        {
            "repository_relative_path": item["repository_relative_path"],
            "file_id": item["file_id"],
            "content_sha256": item["sha256"],
            "byte_size": item["byte_size"],
            "blob_path": f"source-content/blobs/{item['sha256']}",
        }
        for item in files
        if item["content_capture_status"] == "captured" and item["sha256"] is not None
    ]
    return {
        "schema_version": SOURCE_CONTENT_STORE_FORMAT,
        "repository_id": repository_id,
        "source_snapshot_fingerprint": source_snapshot_fingerprint,
        "capture_policy": "all_readable_in_scope_regular_files",
        "file_count": len(captured_files),
        "distinct_blob_count": len({item["content_sha256"] for item in captured_files}),
        "files": captured_files,
    }


def _probe_status(
    *,
    partial_file_inventory: bool,
    capture_enabled: bool,
    active_probe_status: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    file_status = "partial" if partial_file_inventory else "complete"
    rows = [
        {
            "probe_id": "file_inventory",
            "probe_version": "1",
            "status": file_status,
            "basis": {"kind": "direct_file_enumeration_and_hashing"},
        },
        {
            "probe_id": "source_content_capture",
            "probe_version": "1",
            "status": file_status if capture_enabled else "not_evaluated",
            "basis": {
                "kind": "content_addressed_capture" if capture_enabled else "disabled_by_inventory_configuration"
            },
        },
    ]
    for probe_id, status_row in sorted((active_probe_status or {}).items()):
        rows.append(
            {
                "probe_id": probe_id,
                "probe_version": "1",
                "status": status_row["status"],
                "basis": status_row["basis"],
            }
        )
    rows.extend(
        {
            "probe_id": probe,
            "probe_version": "0",
            "status": "not_evaluated",
            "basis": {"kind": "planned_for_later_inventory_block"},
        }
        for probe in _NOT_EVALUATED_PROBES
        if probe not in (active_probe_status or {})
    )
    return sorted(rows, key=lambda item: item["probe_id"])


def build_semantic_payload(
    *,
    repository: Path,
    repository_id: str,
    repository_name: str | None = None,
    repository_url: str | None = None,
    default_branch: str | None = None,
    source_kind: str = "repository",
    scope_id: str | None = None,
    config: InventoryConfig | None = None,
) -> tuple[dict[str, Any], dict[str, bytes], dict[str, Any]]:
    """Build the compact source-intrinsic v7 semantic payload without writing output files."""
    repository = repository.expanduser().resolve()
    if not repository.is_dir():
        raise ValueError(f"repository is not a directory: {repository}")
    repository_id = str(repository_id).strip()
    if not repository_id:
        raise ValueError("repository_id must be non-empty")
    config = config or InventoryConfig.create()

    files, diagnostics, captured, readable_source_by_path = scan_files(
        repository=repository,
        repository_id=repository_id,
        config=config,
    )
    source_snapshot_material = _source_snapshot_material(
        repository_id=repository_id,
        source_scope_fingerprint=config.source_scope_fingerprint,
        files=files,
    )
    source_snapshot_fingerprint = fingerprint(source_snapshot_material)
    occurrences = _temporary_file_occurrences(repository_id=repository_id, files=files)
    occurrence_by_path = {item["repository_relative_path"]: item["occurrence_id"] for item in occurrences}
    for diagnostic in diagnostics:
        source_ref = diagnostic.get("source_ref") or {}
        relative_path = str(source_ref.get("repository_relative_path") or "")
        diagnostic["source_occurrence_id"] = occurrence_by_path.get(relative_path)

    i3a = run_i3a_probes(
        repository_id=repository_id,
        files=files,
        source_bytes_by_path=readable_source_by_path,
        occurrence_by_path=occurrence_by_path,
        max_probe_file_bytes=config.structured_probe_max_bytes,
    )
    java = run_java_probes(
        repository_id=repository_id,
        files=files,
        source_bytes_by_path=readable_source_by_path,
        file_occurrence_by_path=occurrence_by_path,
        max_probe_file_bytes=config.java_probe_max_bytes,
    )
    build_config = run_build_config_probes(
        repository_id=repository_id,
        files=files,
        source_bytes_by_path=readable_source_by_path,
        file_occurrence_by_path=occurrence_by_path,
        max_probe_file_bytes=config.build_config_probe_max_bytes,
    )
    sql = run_sql_probe(
        repository_id=repository_id,
        files=files,
        source_bytes_by_path=readable_source_by_path,
        file_occurrence_by_path=occurrence_by_path,
        max_probe_file_bytes=config.sql_probe_max_bytes,
    )
    http_boundary_observations = [*i3a.http_boundary_observations]
    http_boundary_observations.extend(project_inbound_java_http_boundaries(
        repository_id=repository_id,
        java_facts=java.http_projection_facts,
    ))
    http_boundary_observations.extend(project_outbound_http_boundaries(
        repository_id=repository_id,
        java_facts=java.http_projection_facts,
        scalar_property_values=i3a.scalar_property_values,
    ))
    http_boundary_observations=align_exact_http_boundary_payloads(http_boundary_observations)
    kafka_boundary_observations = project_kafka_boundaries(
        repository_id=repository_id,
        java_facts=java.kafka_projection_facts,
    )
    occurrences.extend(java.source_occurrences)
    occurrences.extend(build_config.source_occurrences)
    partial = any(not item["readable"] for item in files if not item["is_symlink"])
    source_primitives = build_source_primitives(
        repository_id=repository_id,
        files=files,
        occurrence_by_path=occurrence_by_path,
        coverage_status="partial" if partial else "complete",
    )
    diagnostics.extend(i3a.diagnostics)
    diagnostics.extend(java.diagnostics)
    diagnostics.extend(build_config.diagnostics)
    diagnostics.extend(sql.diagnostics)
    diagnostics.sort(
        key=lambda item: (
            (item.get("source_ref") or {}).get("repository_relative_path") or "",
            (item.get("probe") or {}).get("probe_id") or "",
            item.get("code") or "",
            item.get("diagnostic_id") or "",
        )
    )
    compact_families, compact_members, family_file_evidence, file_observations = build_compact_model(
        repository_id=repository_id,
        files=files,
        occurrences=occurrences,
        source_primitives=source_primitives,
        source_formats=i3a.source_formats,
        dependency_observations=build_config.dependency_observations,
        import_namespace_observations=java.import_namespace_observations,
        annotation_observations=java.annotation_observations,
        api_call_observations=java.api_call_observations,
        config_key_observations=build_config.config_key_observations,
        structured_families=[*i3a.structured_families, *http_boundary_observations, *kafka_boundary_observations],
        structured_members=i3a.structured_members,
        xml_observations=i3a.xml_observations,
        sql_footprint=sql.sql_footprint,
        capture_enabled=config.capture_readable_content,
    )

    # v7 diagnostics remain path/localization based.  Legacy SourceOccurrence IDs are
    # transient probe implementation details and never cross the Inventory boundary.
    for diagnostic in diagnostics:
        diagnostic.pop("source_occurrence_id", None)

    content_index = _content_store_index(
        repository_id=repository_id,
        source_snapshot_fingerprint=source_snapshot_fingerprint,
        files=files,
    )
    payload: dict[str, Any] = {
        "format": CONTRACT_FORMAT,
        "run_provenance": {
            "component": "repository-inventory",
            "implementation_version": __version__,
            "configuration": config.to_semantic_dict(),
            "configuration_fingerprint": config.fingerprint,
            "source_scope_fingerprint": config.source_scope_fingerprint,
        },
        "identity": {
            "repository_id": repository_id,
            "repository_name": repository_name,
            "source_kind": source_kind,
            "repository_url": repository_url,
            "default_branch": default_branch,
            "scope_id": scope_id,
        },
        "source_snapshot": {
            "fingerprint": source_snapshot_fingerprint,
            "scope": "in_scope_repository_files",
            "file_count": len(files),
            "readable_file_count": sum(1 for item in files if item["readable"]),
            "unreadable_file_count": sum(1 for item in files if not item["readable"] and not item["is_symlink"]),
            "symlink_file_count": sum(1 for item in files if item["is_symlink"]),
        },
        "files": files,
        "structural_families": compact_families,
        "structural_members": compact_members,
        "family_file_evidence": family_file_evidence,
        "file_observations": file_observations,
        "observation_diagnostics": diagnostics,
        "probe_status": _probe_status(
            partial_file_inventory=partial,
            capture_enabled=config.capture_readable_content,
            active_probe_status={**i3a.probe_status, **java.probe_status, **build_config.probe_status, **sql.probe_status},
        ),
        "source_content_store": {
            "schema_version": SOURCE_CONTENT_STORE_FORMAT,
            "capture_status": (
                "partial" if partial else "complete"
            ) if config.capture_readable_content else "not_evaluated",
            "capture_policy": "all_readable_in_scope_regular_files" if config.capture_readable_content else "disabled",
            "index_relative_path": "source-content/index.json" if config.capture_readable_content else None,
            "blob_directory_relative_path": "source-content/blobs" if config.capture_readable_content else None,
            "captured_file_count": content_index["file_count"] if config.capture_readable_content else 0,
            "distinct_blob_count": content_index["distinct_blob_count"] if config.capture_readable_content else 0,
            "lifecycle": "local_inventory_work_product_not_research_export",
        },
        "semantic_policy": {
            "observed_source_state_only": True,
            "framework_capability_interpreted": False,
            "analyzer_frontier_interpreted": False,
            "business_semantics_inferred": False,
            "benchmark_candidate_selection_performed": False,
            "global_source_occurrence_graph_persisted": False,
            "provenance_budget": "one_deterministic_exemplar_per_family_file",
        },
    }
    # inventory_id is derived solely from stable source/config/identity values and is itself
    # part of the canonical semantic payload.
    payload["inventory_id"] = stable_id(
        "repository_inventory",
        CONTRACT_FORMAT,
        repository_id,
        config.fingerprint,
        source_snapshot_fingerprint,
    )
    payload["semantic_fingerprint"] = fingerprint(payload)
    return payload, captured, content_index


def _write_output(
    *,
    target: Path,
    payload: dict[str, Any],
    captured: dict[str, bytes],
    content_index: dict[str, Any],
) -> None:
    target.mkdir(parents=True, exist_ok=False)
    (target / "repository_inventory.json").write_bytes(pretty_json_bytes(payload))
    if payload["source_content_store"]["capture_status"] == "not_evaluated":
        if captured:
            raise ValueError("content capture is disabled but captured source bytes are present")
        return
    source_content = target / "source-content"
    source_content.mkdir()
    (source_content / "blobs").mkdir()
    (source_content / "index.json").write_bytes(pretty_json_bytes(content_index))
    for sha256, data in sorted(captured.items()):
        actual = hashlib.sha256(data).hexdigest()
        if actual != sha256:
            raise ValueError(f"captured source blob hash mismatch before write: expected={sha256} actual={actual}")
        (source_content / "blobs" / sha256).write_bytes(data)


def build_inventory(
    *,
    repository: Path,
    output: Path,
    repository_id: str,
    repository_name: str | None = None,
    repository_url: str | None = None,
    default_branch: str | None = None,
    source_kind: str = "repository",
    scope_id: str | None = None,
    config: InventoryConfig | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    repository, output = _validate_repository_and_output(repository, output)
    if output.exists() and not overwrite:
        raise FileExistsError(f"output already exists: {output}")

    payload, captured, content_index = build_semantic_payload(
        repository=repository,
        repository_id=repository_id,
        repository_name=repository_name,
        repository_url=repository_url,
        default_branch=default_branch,
        source_kind=source_kind,
        scope_id=scope_id,
        config=config,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    staging = temp / "payload"
    try:
        _write_output(target=staging, payload=payload, captured=captured, content_index=content_index)
        if output.exists():
            if output.is_dir():
                shutil.rmtree(output)
            else:
                output.unlink()
        os.replace(staging, output)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    return payload
