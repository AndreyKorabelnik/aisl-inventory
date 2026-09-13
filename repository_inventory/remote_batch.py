from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .builder import build_inventory
from .canonical import fingerprint, pretty_json_bytes, stable_id
from .config import InventoryConfig
from .remote_acquisition import (
    RemoteRepositorySet,
    RemoteRepositorySource,
    discover_bitbucket_project_repositories,
    discover_sourcecontrol_organization_repositories,
    resolve_remote_repository_commit,
    sanitize_repository_url,
    temporary_repository_checkout,
)
from .verify import verify_inventory_directory
from .reduction import build_reduced_inventory, verify_reduced_inventory
from .concept_candidates import build_concept_candidates, verify_concept_candidates
from .concept_index import build_concept_index
from .contracts import REDUCED_INVENTORY_FORMAT, CONCEPT_CANDIDATE_FORMAT, CONCEPT_INDEX_FORMAT
from .version import __version__


REMOTE_BATCH_MANIFEST_FORMAT = "repository-inventory-remote-batch-manifest/v6"
REMOTE_REPOSITORY_RESULT_FORMAT = "repository-inventory-remote-repository-result/v6"


@dataclass(frozen=True, slots=True)
class RemoteBatchRunResult:
    output: Path
    manifest: dict[str, Any]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_error_message(exc: BaseException, *, secret_values: tuple[str, ...]) -> str:
    message = str(exc) or exc.__class__.__name__
    for value in secret_values:
        if value:
            message = message.replace(value, "<redacted>")
    return message


def _prepare_output(output: str | Path, *, overwrite: bool, resume: bool) -> Path:
    root = Path(output).expanduser().resolve()
    if overwrite and resume:
        raise ValueError("overwrite and resume are mutually exclusive")
    if root.exists():
        if overwrite:
            if root.is_dir():
                shutil.rmtree(root)
            else:
                root.unlink()
            root.mkdir(parents=True, exist_ok=False)
            return root
        if resume:
            if not root.is_dir():
                raise ValueError(f"resume output is not a directory: {root}")
            return root
        raise FileExistsError(f"output already exists: {root}; use --resume or --force")
    root.mkdir(parents=True, exist_ok=False)
    return root


def _normalize_selector(value: str) -> str:
    return str(value or "").strip().casefold()


def load_repository_selectors(path: str | Path) -> tuple[str, ...]:
    source = Path(path).expanduser().resolve()
    rows: list[str] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        rows.append(value)
    if not rows:
        raise ValueError(f"repository selection file contains no selectors: {source}")
    return tuple(rows)


def _source_selector_values(source: RemoteRepositorySource) -> set[str]:
    metadata = dict(source.metadata or {})
    values = {
        source.repository_id,
        source.repository_name or "",
        str(metadata.get("slug") or ""),
        str(metadata.get("remote_name") or ""),
        str(metadata.get("remote_full_name") or ""),
        str(metadata.get("bitbucket_repository_id") or ""),
        str(metadata.get("remote_repository_id") or ""),
    }
    return {_normalize_selector(value) for value in values if _normalize_selector(value)}


def select_remote_repositories(
    discovered: RemoteRepositorySet,
    *,
    selectors: Iterable[str] = (),
    max_repositories: int | None = None,
) -> tuple[tuple[RemoteRepositorySource, ...], tuple[dict[str, Any], ...]]:
    """Select repositories deterministically by repository id or discovered remote metadata."""
    if max_repositories is not None and max_repositories < 1:
        raise ValueError("max_repositories must be at least 1")
    requested = [str(value).strip() for value in selectors if str(value).strip()]
    diagnostics: list[dict[str, Any]] = []
    by_selector: dict[str, list[RemoteRepositorySource]] = {}
    for source in discovered.repositories:
        for value in _source_selector_values(source):
            by_selector.setdefault(value, []).append(source)

    if requested:
        selected: list[RemoteRepositorySource] = []
        seen: set[str] = set()
        for raw in requested:
            key = _normalize_selector(raw)
            matches = by_selector.get(key, [])
            if not matches:
                diagnostics.append({
                    "code": "requested_repository_not_found",
                    "selector": raw,
                    "status": "unresolved",
                })
                continue
            unique = {item.repository_id: item for item in matches}
            if len(unique) != 1:
                diagnostics.append({
                    "code": "requested_repository_selector_ambiguous",
                    "selector": raw,
                    "status": "ambiguous",
                    "candidate_repository_ids": sorted(unique),
                })
                continue
            source = next(iter(unique.values()))
            if source.repository_id in seen:
                diagnostics.append({
                    "code": "requested_repository_duplicate",
                    "selector": raw,
                    "status": "ignored_duplicate",
                    "repository_id": source.repository_id,
                })
                continue
            selected.append(source)
            seen.add(source.repository_id)
    else:
        selected = sorted(discovered.repositories, key=lambda item: item.repository_id)

    if max_repositories is not None:
        selected = selected[:max_repositories]
    return tuple(selected), tuple(diagnostics)


def _load_previous_manifest(root: Path) -> dict[str, Any] | None:
    path = root / "remote_batch_manifest.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _previous_result_by_repository(manifest: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    rows = (manifest or {}).get("repository_results")
    if not isinstance(rows, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, Mapping) and row.get("repository_id"):
            result[str(row["repository_id"])] = dict(row)
    return result


def _expected_identity(
    source: RemoteRepositorySource, *, scope_id: str, source_kind: str
) -> dict[str, Any]:
    return {
        "repository_id": source.repository_id,
        "repository_name": source.repository_name,
        "source_kind": source_kind,
        "repository_url": sanitize_repository_url(source.clone_url),
        "default_branch": source.ref,
        "scope_id": scope_id,
    }


def _can_reuse_existing_inventory(
    *,
    repository_output: Path,
    source: RemoteRepositorySource,
    scope_id: str,
    config: InventoryConfig,
    resolved_commit: str,
    previous_result: Mapping[str, Any] | None,
    source_kind: str,
) -> tuple[bool, str, dict[str, Any] | None]:
    if previous_result is None:
        return False, "previous_manifest_result_missing", None
    if str(previous_result.get("resolved_commit") or "").casefold() != resolved_commit.casefold():
        return False, "resolved_commit_changed", None
    if str(previous_result.get("repository_url") or "") != sanitize_repository_url(source.clone_url):
        return False, "repository_url_changed", None
    if str(previous_result.get("requested_ref") or "") != str(source.ref or ""):
        return False, "requested_ref_changed", None
    inventory_file = repository_output / "repository_inventory.json"
    if not inventory_file.is_file():
        return False, "existing_inventory_missing", None
    try:
        payload = json.loads(inventory_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "existing_inventory_unreadable", None
    if payload.get("format") != "repository-inventory/v7":
        return False, "existing_inventory_wrong_contract", payload
    if dict(payload.get("identity") or {}) != _expected_identity(
        source, scope_id=scope_id, source_kind=source_kind
    ):
        return False, "repository_identity_changed", payload
    if str((payload.get("run_provenance") or {}).get("configuration_fingerprint") or "") != config.fingerprint:
        return False, "inventory_configuration_changed", payload
    try:
        verification = verify_inventory_directory(repository_output)
    except Exception:
        return False, "existing_inventory_verification_failed", payload
    if verification.get("status") != "pass":
        return False, "existing_inventory_verification_failed", payload
    return True, "all_reuse_conditions_match", payload


def _candidate_matches_reduced(candidate_path: Path, reduced_payload: Mapping[str, Any]) -> tuple[bool, dict[str, Any] | None]:
    if not candidate_path.is_file():
        return False, None
    try:
        verify_concept_candidates(candidate_path)
        payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    except Exception:
        return False, None
    source = dict(payload.get("source_reduced_inventory") or {})
    matches = (
        source.get("reduced_inventory_id") == reduced_payload.get("reduced_inventory_id")
        and source.get("semantic_fingerprint") == reduced_payload.get("semantic_fingerprint")
    )
    return matches, payload if matches else None


def _repository_result(
    *,
    source: RemoteRepositorySource,
    status: str,
    action: str,
    started_at: str,
    finished_at: str,
    output_relative_path: str,
    resolved_commit: str | None,
    clone_attempts: tuple[dict[str, Any], ...] = (),
    semantic_fingerprint: str | None = None,
    inventory_id: str | None = None,
    file_count: int | None = None,
    verification_status: str | None = None,
    reuse_basis: str | None = None,
    failure_stage: str | None = None,
    failure_code: str | None = None,
    failure_message: str | None = None,
    previous_inventory_preserved: bool = False,
    commit_resolution: Mapping[str, Any] | None = None,
    reduced_action: str | None = None,
    reduced_inventory_id: str | None = None,
    reduced_semantic_fingerprint: str | None = None,
    reduced_output_relative_path: str | None = None,
    candidate_action: str | None = None,
    concept_candidate_id: str | None = None,
    candidate_semantic_fingerprint: str | None = None,
    candidate_output_relative_path: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "format": REMOTE_REPOSITORY_RESULT_FORMAT,
        "repository_id": source.repository_id,
        "repository_name": source.repository_name,
        "repository_url": sanitize_repository_url(source.clone_url),
        "scope_key": source.scope_key,
        "requested_ref": source.ref,
        "resolved_commit": resolved_commit,
        "status": status,
        "action": action,
        "inventory_output": output_relative_path,
        "inventory_id": inventory_id,
        "semantic_fingerprint": semantic_fingerprint,
        "file_count": file_count,
        "verification_status": verification_status,
        "reuse_basis": reuse_basis,
        "clone_attempts": [dict(item) for item in clone_attempts],
        "failure_stage": failure_stage,
        "failure_code": failure_code,
        "failure_message": failure_message,
        "previous_inventory_preserved": previous_inventory_preserved,
        "commit_resolution": dict(commit_resolution or {}),
        "reduced": {
            "requested": reduced_action is not None,
            "action": reduced_action,
            "format": REDUCED_INVENTORY_FORMAT if reduced_action is not None else None,
            "reduced_inventory_id": reduced_inventory_id,
            "semantic_fingerprint": reduced_semantic_fingerprint,
            "output": reduced_output_relative_path,
        },
        "concept_candidates": {
            "requested": candidate_action is not None,
            "action": candidate_action,
            "format": CONCEPT_CANDIDATE_FORMAT if candidate_action is not None else None,
            "concept_candidate_id": concept_candidate_id,
            "semantic_fingerprint": candidate_semantic_fingerprint,
            "output": candidate_output_relative_path,
        },
        "temporary_checkout_policy": {
            "kind": "temporary_git_checkout",
            "checkout_removed_after_repository": True,
            "persistent_checkout_count": 0,
        },
        "source_metadata": dict(source.metadata or {}),
        "started_at": started_at,
        "finished_at": finished_at,
    }
    payload["result_fingerprint"] = fingerprint(payload)
    return payload


def _write_manifest(root: Path, manifest: Mapping[str, Any]) -> None:
    (root / "remote_batch_manifest.json").write_bytes(pretty_json_bytes(dict(manifest)))


def build_bitbucket_project_inventory(
    *,
    project_url: str,
    output: str | Path,
    scope_id: str | None = None,
    config: InventoryConfig | None = None,
    overwrite: bool = False,
    resume: bool = False,
    repository_selectors: Iterable[str] = (),
    auth_mode: str = "auto",
    token_env: str = "BITBUCKET_TOKEN",
    username_env: str = "BITBUCKET_USERNAME",
    password_env: str = "BITBUCKET_PASSWORD",
    api_base_path: str = "/rest/api/latest",
    ca_bundle: str | Path | None = None,
    insecure_skip_tls_verify: bool = False,
    timeout_seconds: float = 60.0,
    page_size: int = 100,
    max_repositories: int | None = None,
    clone_retries: int = 2,
    clone_timeout_seconds: float = 300.0,
    work_dir: str | Path | None = None,
    build_reduced: bool = False,
) -> RemoteBatchRunResult:
    discovered = discover_bitbucket_project_repositories(
        project_url=project_url,
        auth_mode=auth_mode,
        token_env=token_env,
        username_env=username_env,
        password_env=password_env,
        api_base_path=api_base_path,
        ca_bundle=ca_bundle,
        insecure_skip_tls_verify=insecure_skip_tls_verify,
        timeout_seconds=timeout_seconds,
        page_size=page_size,
        max_repositories=None,
    )
    return _build_discovered_remote_inventory(
        discovered=discovered,
        output=output,
        scope_id=scope_id,
        config=config,
        overwrite=overwrite,
        resume=resume,
        repository_selectors=repository_selectors,
        auth_mode=auth_mode,
        token_env=token_env,
        username_env=username_env,
        password_env=password_env,
        timeout_seconds=timeout_seconds,
        max_repositories=max_repositories,
        clone_retries=clone_retries,
        clone_timeout_seconds=clone_timeout_seconds,
        work_dir=work_dir,
        build_reduced=build_reduced,
    )


def build_sourcecontrol_organization_inventory(
    *,
    api_url: str,
    organization: str,
    output: str | Path,
    scope_id: str | None = None,
    config: InventoryConfig | None = None,
    overwrite: bool = False,
    resume: bool = False,
    repository_selectors: Iterable[str] = (),
    auth_mode: str = "auto",
    token_env: str = "SOURCECONTROL_TOKEN",
    username_env: str = "SOURCECONTROL_USERNAME",
    password_env: str = "SOURCECONTROL_PASSWORD",
    api_base_path: str = "/api/v1",
    ca_bundle: str | Path | None = None,
    insecure_skip_tls_verify: bool = False,
    timeout_seconds: float = 60.0,
    page_size: int = 100,
    max_repositories: int | None = None,
    clone_retries: int = 2,
    clone_timeout_seconds: float = 300.0,
    work_dir: str | Path | None = None,
    build_reduced: bool = False,
) -> RemoteBatchRunResult:
    discovered = discover_sourcecontrol_organization_repositories(
        api_url=api_url,
        organization=organization,
        auth_mode=auth_mode,
        token_env=token_env,
        username_env=username_env,
        password_env=password_env,
        api_base_path=api_base_path,
        ca_bundle=ca_bundle,
        insecure_skip_tls_verify=insecure_skip_tls_verify,
        timeout_seconds=timeout_seconds,
        page_size=page_size,
        max_repositories=None,
    )
    return _build_discovered_remote_inventory(
        discovered=discovered,
        output=output,
        scope_id=scope_id,
        config=config,
        overwrite=overwrite,
        resume=resume,
        repository_selectors=repository_selectors,
        auth_mode=auth_mode,
        token_env=token_env,
        username_env=username_env,
        password_env=password_env,
        timeout_seconds=timeout_seconds,
        max_repositories=max_repositories,
        clone_retries=clone_retries,
        clone_timeout_seconds=clone_timeout_seconds,
        work_dir=work_dir,
        build_reduced=build_reduced,
    )


def _build_discovered_remote_inventory(
    *,
    discovered: RemoteRepositorySet,
    output: str | Path,
    scope_id: str | None = None,
    config: InventoryConfig | None = None,
    overwrite: bool = False,
    resume: bool = False,
    repository_selectors: Iterable[str] = (),
    auth_mode: str = "auto",
    token_env: str,
    username_env: str,
    password_env: str,
    timeout_seconds: float = 60.0,
    max_repositories: int | None = None,
    clone_retries: int = 2,
    clone_timeout_seconds: float = 300.0,
    work_dir: str | Path | None = None,
    build_reduced: bool = False,
) -> RemoteBatchRunResult:
    """Build or safely resume one independent v7 artifact per repository, optionally with Reduced v3 + concept candidates."""
    if clone_retries < 0:
        raise ValueError("clone_retries must be non-negative")
    if clone_timeout_seconds <= 0:
        raise ValueError("clone_timeout_seconds must be > 0")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")
    if max_repositories is not None and max_repositories < 1:
        raise ValueError("max_repositories must be at least 1")

    repository_selectors = tuple(str(item).strip() for item in repository_selectors if str(item).strip())
    selected, selection_diagnostics = select_remote_repositories(
        discovered,
        selectors=repository_selectors,
        max_repositories=max_repositories,
    )
    if not selected and not selection_diagnostics:
        raise ValueError(f"remote scope contains no selected repositories: {discovered.scope_url}")

    root = _prepare_output(output, overwrite=overwrite, resume=resume)
    previous_manifest = _load_previous_manifest(root) if resume else None
    previous_results = _previous_result_by_repository(previous_manifest)
    started_at = _utc_now()
    effective_scope_id = str(scope_id or discovered.scope_key).strip()
    if not effective_scope_id:
        raise ValueError("scope_id must not be empty")

    config = config or InventoryConfig.create()
    repositories_root = root / "repositories"
    repositories_root.mkdir(exist_ok=True)
    results: list[dict[str, Any]] = []
    secret_values = (
        os.environ.get(token_env, ""),
        os.environ.get(username_env, ""),
        os.environ.get(password_env, ""),
    )

    for source in selected:
        repository_started_at = _utc_now()
        repository_output = repositories_root / source.repository_id
        output_relative = repository_output.relative_to(root).as_posix()
        resolved_commit: str | None = None
        attempts: tuple[dict[str, Any], ...] = ()
        previous_exists = repository_output.exists()
        commit_resolution: dict[str, Any] = {
            "status": "not_requested",
            "basis": "commit resolution is only required to evaluate --resume reuse",
        }

        if resume and not overwrite:
            try:
                resolved_commit = resolve_remote_repository_commit(
                    source=source,
                    auth_mode=auth_mode,
                    token_env=token_env,
                    username_env=username_env,
                    password_env=password_env,
                    timeout_seconds=timeout_seconds,
                )
                commit_resolution = {
                    "status": "resolved",
                    "resolved_commit": resolved_commit,
                    "reuse_decision": "evaluate_existing_inventory",
                }
            except Exception as exc:
                diagnostic = _safe_error_message(exc, secret_values=secret_values)
                commit_resolution = {
                    "status": "failed",
                    "reuse_decision": "reuse_forbidden_rebuild_required",
                    "diagnostic": diagnostic,
                }

            if resolved_commit is not None:
                can_reuse, reuse_basis, existing_payload = _can_reuse_existing_inventory(
                    repository_output=repository_output,
                    source=source,
                    scope_id=effective_scope_id,
                    config=config,
                    resolved_commit=resolved_commit,
                    previous_result=previous_results.get(source.repository_id),
                    source_kind=discovered.remote_system,
                )
                if can_reuse and existing_payload is not None:
                    reduced_payload = None
                    reduced_action = None
                    reduced_relative = None
                    if build_reduced:
                        reduced_path = repository_output / "repository_inventory_reduced.json"
                        reduced_relative = reduced_path.relative_to(root).as_posix()
                        rebuild_reduced = True
                        if reduced_path.is_file():
                            try:
                                verified_reduced = verify_reduced_inventory(reduced_path)
                                reduced_value = json.loads(reduced_path.read_text(encoding="utf-8"))
                                source_identity = dict(reduced_value.get("source_inventory") or {})
                                rebuild_reduced = not (
                                    source_identity.get("inventory_id") == existing_payload.get("inventory_id")
                                    and source_identity.get("semantic_fingerprint") == existing_payload.get("semantic_fingerprint")
                                    and verified_reduced.get("status") == "verified"
                                )
                                if not rebuild_reduced:
                                    reduced_payload = reduced_value
                                    reduced_action = "reused"
                            except Exception:
                                rebuild_reduced = True
                        if rebuild_reduced:
                            reduced_payload = build_reduced_inventory(
                                inventory=repository_output,
                                output=reduced_path,
                                overwrite=True,
                            )
                            verify_reduced_inventory(reduced_path)
                            reduced_action = "built_from_reused_inventory"
                    candidate_payload = None
                    candidate_action = None
                    candidate_relative = None
                    if build_reduced and reduced_payload is not None:
                        candidate_path = repository_output / "repository_concept_candidates.json"
                        candidate_relative = candidate_path.relative_to(root).as_posix()
                        matches, existing_candidate = _candidate_matches_reduced(candidate_path, reduced_payload)
                        if matches and existing_candidate is not None:
                            candidate_payload = existing_candidate
                            candidate_action = "reused"
                        else:
                            candidate_payload = build_concept_candidates(reduced_path, output=candidate_path, overwrite=True)
                            verify_concept_candidates(candidate_path)
                            candidate_action = "built_from_reused_reduced" if reduced_action == "reused" else "built_from_rebuilt_reduced"
                    results.append(
                        _repository_result(
                            source=source,
                            status="reused",
                            action="reused",
                            started_at=repository_started_at,
                            finished_at=_utc_now(),
                            output_relative_path=output_relative,
                            resolved_commit=resolved_commit,
                            semantic_fingerprint=str(existing_payload.get("semantic_fingerprint") or ""),
                            inventory_id=str(existing_payload.get("inventory_id") or ""),
                            file_count=int((existing_payload.get("source_snapshot") or {}).get("file_count") or 0),
                            verification_status="pass",
                            reuse_basis=reuse_basis,
                            commit_resolution=commit_resolution,
                            reduced_action=reduced_action,
                            reduced_inventory_id=str((reduced_payload or {}).get("reduced_inventory_id") or "") or None,
                            reduced_semantic_fingerprint=str((reduced_payload or {}).get("semantic_fingerprint") or "") or None,
                            reduced_output_relative_path=reduced_relative,
                            candidate_action=candidate_action,
                            concept_candidate_id=str((candidate_payload or {}).get("concept_candidate_id") or "") or None,
                            candidate_semantic_fingerprint=str((candidate_payload or {}).get("semantic_fingerprint") or "") or None,
                            candidate_output_relative_path=candidate_relative,
                        )
                    )
                    continue

        staging_output = repositories_root / f".{source.repository_id}.staging"
        shutil.rmtree(staging_output, ignore_errors=True)
        failure_stage = "acquisition"
        payload: dict[str, Any] | None = None
        try:
            with temporary_repository_checkout(
                source=source,
                work_dir=work_dir,
                auth_mode=auth_mode,
                token_env=token_env,
                username_env=username_env,
                password_env=password_env,
                retries=clone_retries,
                timeout_seconds=clone_timeout_seconds,
            ) as (checkout, checkout_commit, attempts):
                if resolved_commit is not None and checkout_commit.casefold() != resolved_commit.casefold():
                    raise RuntimeError(
                        f"remote commit changed between resolution and checkout for {source.repository_id}"
                    )
                if resolved_commit is None:
                    resolved_commit = checkout_commit
                failure_stage = "inventory_build"
                payload = build_inventory(
                    repository=checkout,
                    output=staging_output,
                    repository_id=source.repository_id,
                    repository_name=source.repository_name,
                    repository_url=sanitize_repository_url(source.clone_url),
                    default_branch=source.ref,
                    source_kind=discovered.remote_system,
                    scope_id=effective_scope_id,
                    config=config,
                    overwrite=False,
                )
                failure_stage = "inventory_verify"
                verification = verify_inventory_directory(staging_output)
                if verification.get("status") != "pass":
                    raise RuntimeError(
                        f"repository inventory verification did not pass: {verification.get('status')!r}"
                    )
                reduced_payload = None
                candidate_payload = None
                if build_reduced:
                    failure_stage = "reduced_build"
                    reduced_path = staging_output / "repository_inventory_reduced.json"
                    reduced_payload = build_reduced_inventory(
                        inventory=staging_output,
                        output=reduced_path,
                        overwrite=False,
                    )
                    failure_stage = "reduced_verify"
                    verify_reduced_inventory(reduced_path)
                    failure_stage = "concept_candidate_build"
                    candidate_path = staging_output / "repository_concept_candidates.json"
                    candidate_payload = build_concept_candidates(reduced_path, output=candidate_path)
                    failure_stage = "concept_candidate_verify"
                    verify_concept_candidates(candidate_path)

            # Replace an older artifact only after requested v7 + Reduced + candidate artifacts have verified successfully.
            if repository_output.exists():
                shutil.rmtree(repository_output)
            staging_output.replace(repository_output)
            results.append(
                _repository_result(
                    source=source,
                    status="completed",
                    action="rebuilt" if previous_exists else "built",
                    started_at=repository_started_at,
                    finished_at=_utc_now(),
                    output_relative_path=output_relative,
                    resolved_commit=resolved_commit,
                    clone_attempts=attempts,
                    semantic_fingerprint=str((payload or {}).get("semantic_fingerprint") or ""),
                    inventory_id=str((payload or {}).get("inventory_id") or ""),
                    file_count=int(((payload or {}).get("source_snapshot") or {}).get("file_count") or 0),
                    verification_status="pass",
                    reuse_basis=(
                        "commit_resolution_failed_rebuild_required"
                        if commit_resolution.get("status") == "failed"
                        else None
                    ),
                    commit_resolution=commit_resolution,
                    reduced_action="built" if build_reduced else None,
                    reduced_inventory_id=str((reduced_payload or {}).get("reduced_inventory_id") or "") or None,
                    reduced_semantic_fingerprint=str((reduced_payload or {}).get("semantic_fingerprint") or "") or None,
                    reduced_output_relative_path=(repository_output / "repository_inventory_reduced.json").relative_to(root).as_posix() if build_reduced else None,
                    candidate_action="built" if build_reduced else None,
                    concept_candidate_id=str((candidate_payload or {}).get("concept_candidate_id") or "") or None,
                    candidate_semantic_fingerprint=str((candidate_payload or {}).get("semantic_fingerprint") or "") or None,
                    candidate_output_relative_path=(repository_output / "repository_concept_candidates.json").relative_to(root).as_posix() if build_reduced else None,
                )
            )
        except Exception as exc:
            shutil.rmtree(staging_output, ignore_errors=True)
            results.append(
                _repository_result(
                    source=source,
                    status=f"{failure_stage}_failed",
                    action="none",
                    started_at=repository_started_at,
                    finished_at=_utc_now(),
                    output_relative_path=output_relative,
                    resolved_commit=resolved_commit,
                    clone_attempts=attempts,
                    failure_stage=failure_stage,
                    failure_code=f"remote_repository_{failure_stage}_failed",
                    failure_message=_safe_error_message(exc, secret_values=secret_values),
                    previous_inventory_preserved=previous_exists and repository_output.exists(),
                    commit_resolution=commit_resolution,
                )
            )

    successful_statuses = {"completed", "reused"}
    successful = sum(item["status"] in successful_statuses for item in results)
    reused = sum(item["status"] == "reused" for item in results)
    built = sum(item["status"] == "completed" for item in results)
    failed = len(results) - successful
    unresolved_selection = sum(
        item.get("status") in {"unresolved", "ambiguous"} for item in selection_diagnostics
    )
    total_failures = failed + unresolved_selection
    overall = "completed" if successful and not total_failures else "partial" if successful else "failed"
    selection_payload = {
        "requested_selectors": [str(item) for item in repository_selectors],
        "selected_repository_ids": [item.repository_id for item in selected],
        "repository_limit": max_repositories,
        "diagnostics": list(selection_diagnostics),
        "selection_is_reproducible": True,
        "selection_reproducibility_basis": (
            "explicit selector order" if tuple(repository_selectors)
            else "stable repository_id ordering before repository_limit"
        ),
    }
    concept_index_payload = None
    if build_reduced:
        successful_repository_ids = {str(item.get("repository_id") or "") for item in results if item.get("status") in successful_statuses}
        concept_index_payload = build_concept_index(root, repository_ids=successful_repository_ids)

    manifest: dict[str, Any] = {
        "format": REMOTE_BATCH_MANIFEST_FORMAT,
        "status": overall,
        "batch_id": stable_id(
            "repository_inventory_remote_batch",
            discovered.scope_key,
            effective_scope_id,
            config.fingerprint,
            fingerprint(selection_payload),
            build_reduced,
        ),
        "producer": {
            "component": "repository-inventory",
            "version": __version__,
            "inventory_contract": "repository-inventory/v7",
            "reduced_contract": REDUCED_INVENTORY_FORMAT if build_reduced else None,
            "concept_candidate_contract": CONCEPT_CANDIDATE_FORMAT if build_reduced else None,
            "concept_index_contract": CONCEPT_INDEX_FORMAT if build_reduced else None,
        },
        "source": {
            "remote_system": discovered.remote_system,
            "scope_kind": discovered.scope_kind,
            "scope_url": sanitize_repository_url(discovered.scope_url),
            "scope_key": discovered.scope_key,
            "scope_id": effective_scope_id,
            "observed_repository_count": discovered.observed_repository_count,
            "buildable_repository_count": len(discovered.repositories),
            "skipped_repository_count": max(
                0, discovered.observed_repository_count - len(discovered.repositories)
            ),
            "selected_repository_count": len(selected),
            "discovery_diagnostics": [dict(item) for item in discovered.discovery_diagnostics],
        },
        "selection": selection_payload,
        "inventory_configuration_fingerprint": config.fingerprint,
        "execution": {
            "mode": "sequential",
            "resume": resume,
            "force": overwrite,
            "max_concurrent_checkouts": 1,
            "temporary_checkout_removed_after_each_repository": True,
            "persistent_repository_checkout_count": 0,
            "content_capture_enabled": config.capture_readable_content,
            "remote_commit_resolution_policy": "resume_only",
            "commit_resolution_failure_policy": "explicit_no_reuse_then_rebuild",
            "verified_rebuild_staging_before_replace": True,
            "build_reduced": build_reduced,
            "reduced_filename": "repository_inventory_reduced.json" if build_reduced else None,
            "build_concept_candidates": build_reduced,
            "concept_candidate_filename": "repository_concept_candidates.json" if build_reduced else None,
            "concept_index_filename": "repository_concept_index.json" if build_reduced else None,
        },
        "summary": {
            "total": len(results),
            "built": built,
            "reused": reused,
            "successful": successful,
            "failed": failed,
            "selection_unresolved": unresolved_selection,
        },
        "concept_index": ({
            "format": CONCEPT_INDEX_FORMAT,
            "index_id": concept_index_payload.get("index_id"),
            "semantic_fingerprint": concept_index_payload.get("semantic_fingerprint"),
            "output": "repository_concept_index.json",
            "repository_count": concept_index_payload.get("summary", {}).get("repository_count"),
        } if concept_index_payload is not None else None),
        "repository_results": results,
        "claim_boundary": (
            "Operational batch orchestration only. Each successful repository owns an independent "
            "repository-inventory/v7 artifact and, when explicitly requested, its repository-local Reduced projection and routing-only concept candidates. "
            "The optional concept index only aggregates those existing candidate strengths for lookup; it does not reclassify repositories, merge source evidence, derive cross-repository knowledge, or create a synthetic multi-repository Inventory product."
        ),
        "started_at": started_at,
        "completed_at": _utc_now(),
    }
    manifest["batch_fingerprint"] = fingerprint(manifest)
    _write_manifest(root, manifest)
    return RemoteBatchRunResult(output=root, manifest=manifest)
