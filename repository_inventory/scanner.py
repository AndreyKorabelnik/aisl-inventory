from __future__ import annotations

from collections import defaultdict
import hashlib
import os
from pathlib import Path
from typing import Any

from .canonical import stable_id
from .config import InventoryConfig
from .contracts import EXTENSION_LANGUAGE_MAP
from .landscape import repository_local_salience, source_tree_scope


def normalize_relative_path(repository: Path, path: Path) -> str:
    return path.relative_to(repository).as_posix()


def extension_of(path: Path) -> str:
    suffix = path.suffix.lower()
    return suffix if suffix else "<none>"


def enumerate_in_scope_files(repository: Path, config: InventoryConfig) -> list[Path]:
    repository = repository.resolve()
    excluded = set(config.excluded_directory_names)
    files: list[Path] = []
    for root, directory_names, file_names in os.walk(repository, topdown=True, followlinks=False):
        directory_names[:] = sorted(name for name in directory_names if name not in excluded)
        root_path = Path(root)
        for name in sorted(file_names):
            if name == ".DS_Store" or name.startswith("._"):
                continue
            candidate = root_path / name
            # os.walk may report symlinked files. They remain observed file entries, but
            # content is not followed/captured under the v6 skeleton security policy.
            files.append(candidate)
    return sorted(files, key=lambda item: normalize_relative_path(repository, item))


def _diagnostic(
    *,
    repository_id: str,
    relative_path: str,
    code: str,
    severity: str,
    message: str,
    basis: dict[str, Any],
) -> dict[str, Any]:
    return {
        "diagnostic_id": stable_id("diagnostic", repository_id, relative_path, code, basis),
        "code": code,
        "severity": severity,
        "message": message,
        "source_ref": {"repository_relative_path": relative_path, "localization_kind": "file"},
        "source_occurrence_id": None,
        "basis": basis,
        "probe": {"probe_id": "file_inventory", "probe_version": "1"},
    }


def scan_files(
    *,
    repository: Path,
    repository_id: str,
    config: InventoryConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, bytes], dict[str, bytes]]:
    """Return deterministic file observations, diagnostics and captured bytes.

    AISL analyzer eligibility/support is deliberately absent. A file is either observed
    and read, observed but unreadable, or observed as a symlink not followed by policy.
    """
    files: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    captured: dict[str, bytes] = {}
    readable_source_by_path: dict[str, bytes] = {}

    for path in enumerate_in_scope_files(repository, config):
        relative = normalize_relative_path(repository, path)
        extension = extension_of(path)
        is_symlink = path.is_symlink()
        payload: bytes | None = None
        readable = False
        content_sha256: str | None = None
        byte_size: int | None = None
        capture_status = "not_requested"

        if is_symlink and not config.follow_symlinks:
            capture_status = "not_captured_symlink"
            diagnostics.append(
                _diagnostic(
                    repository_id=repository_id,
                    relative_path=relative,
                    code="repository_symlink_not_followed",
                    severity="info",
                    message="Repository symlink was observed but its target content was not followed.",
                    basis={"policy": "follow_symlinks_false"},
                )
            )
        else:
            try:
                payload = path.read_bytes()
            except OSError as exc:
                capture_status = "unreadable"
                diagnostics.append(
                    _diagnostic(
                        repository_id=repository_id,
                        relative_path=relative,
                        code="repository_file_unreadable",
                        severity="warning",
                        message="Repository file could not be read.",
                        basis={
                            "error_type": type(exc).__name__,
                            "errno": getattr(exc, "errno", None),
                        },
                    )
                )
            else:
                readable = True
                byte_size = len(payload)
                content_sha256 = hashlib.sha256(payload).hexdigest()
                capture_status = "captured" if config.capture_readable_content else "not_requested"
                readable_source_by_path[relative] = payload
                if config.capture_readable_content:
                    captured.setdefault(content_sha256, payload)

        language = EXTENSION_LANGUAGE_MAP.get(extension)
        classification = "classified" if language is not None else "unclassified"
        file_id = stable_id(
            "file",
            repository_id,
            relative,
            content_sha256 or capture_status,
        )
        files.append(
            {
                "file_id": file_id,
                "repository_relative_path": relative,
                "file_name": path.name,
                "extension": extension,
                "byte_size": byte_size,
                "sha256": content_sha256,
                "readable": readable,
                "is_symlink": is_symlink,
                "content_capture_status": capture_status,
                "source_classification": {
                    "classification": classification,
                    "language": language,
                    "basis": {
                        "kind": "deterministic_extension_mapping",
                        "extension": extension,
                    },
                },
            }
        )

    # Link diagnostics to a stable file-level occurrence later in builder. Keeping this
    # pass source-only avoids manufacturing localization before occurrence identity exists.
    return files, diagnostics, captured, readable_source_by_path


def build_source_primitives(
    *,
    repository_id: str,
    files: list[dict[str, Any]],
    occurrence_by_path: dict[str, str],
    coverage_status: str,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str | None], list[dict[str, Any]]] = defaultdict(list)
    for item in files:
        classification = item["source_classification"]
        groups[(item["extension"], classification["classification"], classification["language"])].append(item)

    result: list[dict[str, Any]] = []
    for (extension, classification, language), rows in sorted(
        groups.items(), key=lambda row: (row[0][0], row[0][1], row[0][2] or "")
    ):
        paths = sorted({row["repository_relative_path"] for row in rows})
        file_ids = sorted({row["file_id"] for row in rows})
        occurrence_ids = sorted({occurrence_by_path[path] for path in paths if path in occurrence_by_path})
        scopes = sorted({source_tree_scope(path) for path in paths})
        primitive = {
            "primitive_id": stable_id("source_primitive", repository_id, "file_extension", extension, classification, language),
            "repository_id": repository_id,
            "primitive_kind": "file_extension",
            "extension": extension,
            "classification": classification,
            "language": language,
            "occurrence_count": len(rows),
            "file_count": len(file_ids),
            "source_tree_scope_count": len(scopes),
            "source_tree_scopes": scopes,
            "file_ids": file_ids,
            "source_occurrence_ids": occurrence_ids,
            "basis": {
                "kind": "repository_file_extension_family",
                "semantic_meaning_inferred": False,
            },
        }
        primitive.update(
            repository_local_salience(
                count=len(rows),
                file_count=len(file_ids),
                source_tree_scopes=scopes,
                coverage_status=coverage_status,
            )
        )
        result.append(primitive)
    return result
