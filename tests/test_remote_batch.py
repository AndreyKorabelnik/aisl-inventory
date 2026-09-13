from __future__ import annotations

import json
import subprocess
from pathlib import Path

import repository_inventory.remote_batch as remote_batch
from repository_inventory.remote_acquisition import RemoteRepositorySet, RemoteRepositorySource
from repository_inventory.remote_batch import (
    build_bitbucket_project_inventory,
    build_sourcecontrol_organization_inventory,
)
from repository_inventory.selected_source_export import (
    build_selected_source_export,
    verify_selected_source_export,
)
from repository_inventory.verify import verify_inventory_directory


def _git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _repository(root: Path, name: str, content: str) -> Path:
    repo = root / name
    repo.mkdir()
    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.email", "inventory@example.invalid", cwd=repo)
    _git("config", "user.name", "Inventory Test", cwd=repo)
    (repo / "sample.txt").write_text(content, encoding="utf-8")
    _git("add", "sample.txt", cwd=repo)
    _git("commit", "-m", "initial", cwd=repo)
    return repo


def _source(repo: Path, repository_id: str) -> RemoteRepositorySource:
    return RemoteRepositorySource(
        repository_id=repository_id,
        clone_url=repo.resolve().as_uri(),
        ref="main",
        scope_key="ABC",
        repository_name=repository_id.replace("_", " ").title(),
    )


def test_remote_batch_builds_three_independent_v7_artifacts_and_selected_export_survives_cleanup(
    tmp_path: Path, monkeypatch
) -> None:
    repo_a = _repository(tmp_path, "repo-a-source", "alpha\n")
    repo_b = _repository(tmp_path, "repo-b-source", "beta\n")
    repo_c = _repository(tmp_path, "repo-c-source", "gamma\n")
    discovered = RemoteRepositorySet(
        remote_system="bitbucket-data-center",
        scope_kind="project",
        scope_url="https://example.invalid/projects/ABC",
        scope_key="ABC",
        repositories=(
            _source(repo_a, "repo_a"),
            _source(repo_b, "repo_b"),
            _source(repo_c, "repo_c"),
        ),
        observed_repository_count=3,
    )
    monkeypatch.setattr(remote_batch, "discover_bitbucket_project_repositories", lambda **_kwargs: discovered)

    work = tmp_path / "temporary-work"
    output = tmp_path / "batch"
    result = build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        scope_id="ABC-pilot",
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
        work_dir=work,
    )

    assert result.manifest["status"] == "completed"
    assert result.manifest["summary"] == {"total": 3, "built": 3, "reused": 0, "successful": 3, "failed": 0, "selection_unresolved": 0}
    assert result.manifest["execution"]["max_concurrent_checkouts"] == 1
    assert result.manifest["claim_boundary"].startswith("Operational batch orchestration only")
    assert list(work.iterdir()) == []

    for repository_id in ("repo_a", "repo_b", "repo_c"):
        inventory = output / "repositories" / repository_id
        assert verify_inventory_directory(inventory)["status"] == "pass"
        payload = json.loads((inventory / "repository_inventory.json").read_text(encoding="utf-8"))
        assert payload["format"] == "repository-inventory/v7"
        assert payload["identity"]["scope_id"] == "ABC-pilot"
        assert payload["identity"]["repository_id"] == repository_id
        assert (inventory / "source-content" / "index.json").is_file()

    first_inventory = output / "repositories" / "repo_a"
    first_payload = json.loads((first_inventory / "repository_inventory.json").read_text(encoding="utf-8"))
    evidence_id = first_payload["family_file_evidence"][0]["evidence_id"]
    selected = tmp_path / "selected"
    export = build_selected_source_export(
        inventory=first_inventory,
        output=selected,
        evidence_ids=[evidence_id],
    )
    assert export["status"] == "complete"
    assert verify_selected_source_export(selected)["status"] == "pass"


def test_remote_batch_continues_after_repository_failure_and_reports_partial(tmp_path: Path, monkeypatch) -> None:
    repo_a = _repository(tmp_path, "repo-a-source", "alpha\n")
    repo_c = _repository(tmp_path, "repo-c-source", "gamma\n")
    missing = tmp_path / "does-not-exist"
    discovered = RemoteRepositorySet(
        remote_system="bitbucket-data-center",
        scope_kind="project",
        scope_url="https://example.invalid/projects/ABC",
        scope_key="ABC",
        repositories=(
            _source(repo_a, "repo_a"),
            RemoteRepositorySource(
                repository_id="repo_b",
                clone_url=missing.resolve().as_uri(),
                ref="main",
                scope_key="ABC",
                repository_name="Repo B",
            ),
            _source(repo_c, "repo_c"),
        ),
        observed_repository_count=3,
    )
    monkeypatch.setattr(remote_batch, "discover_bitbucket_project_repositories", lambda **_kwargs: discovered)

    work = tmp_path / "temporary-work"
    output = tmp_path / "batch"
    result = build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
        work_dir=work,
    )

    assert result.manifest["status"] == "partial"
    assert result.manifest["summary"] == {"total": 3, "built": 2, "reused": 0, "successful": 2, "failed": 1, "selection_unresolved": 0}
    statuses = {row["repository_id"]: row for row in result.manifest["repository_results"]}
    assert statuses["repo_a"]["status"] == "completed"
    assert statuses["repo_b"]["status"] == "acquisition_failed"
    assert statuses["repo_b"]["failure_stage"] == "acquisition"
    assert statuses["repo_b"]["failure_code"] == "remote_repository_acquisition_failed"
    assert statuses["repo_b"]["commit_resolution"]["status"] == "not_requested"
    assert statuses["repo_c"]["status"] == "completed"
    assert not (output / "repositories" / "repo_b").exists()
    assert verify_inventory_directory(output / "repositories" / "repo_c")["status"] == "pass"
    assert list(work.iterdir()) == []


def test_sourcecontrol_batch_reuses_same_remote_execution_path(tmp_path: Path, monkeypatch) -> None:
    repo = _repository(tmp_path, "sourcecontrol-repo", "alpha\n")
    source = RemoteRepositorySource(
        repository_id="cpc_service",
        clone_url=repo.resolve().as_uri(),
        ref="main",
        scope_key="PPRB_CPC",
        repository_name="cpc-service",
        metadata={"remote_repository_id": 39539, "remote_name": "cpc-service"},
    )
    discovered = RemoteRepositorySet(
        remote_system="sourcecontrol",
        scope_kind="organization",
        scope_url="https://api.example.invalid/api/v1/orgs/PPRB_CPC",
        scope_key="PPRB_CPC",
        repositories=(source,),
        observed_repository_count=2,
        discovery_diagnostics=({
            "code": "sourcecontrol_repository_empty",
            "status": "skipped",
            "repository_id": "empty_repo",
        },),
    )
    monkeypatch.setattr(
        remote_batch,
        "discover_sourcecontrol_organization_repositories",
        lambda **_kwargs: discovered,
    )
    output = tmp_path / "sourcecontrol-batch"
    result = build_sourcecontrol_organization_inventory(
        api_url="https://api.example.invalid",
        organization="PPRB_CPC",
        output=output,
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
    )
    assert result.manifest["format"] == "repository-inventory-remote-batch-manifest/v6"
    assert result.manifest["status"] == "completed"
    assert result.manifest["source"] == {
        "remote_system": "sourcecontrol",
        "scope_kind": "organization",
        "scope_url": "https://api.example.invalid/api/v1/orgs/PPRB_CPC",
        "scope_key": "PPRB_CPC",
        "scope_id": "PPRB_CPC",
        "observed_repository_count": 2,
        "buildable_repository_count": 1,
        "skipped_repository_count": 1,
        "selected_repository_count": 1,
        "discovery_diagnostics": [{
            "code": "sourcecontrol_repository_empty",
            "status": "skipped",
            "repository_id": "empty_repo",
        }],
    }
    payload = json.loads(
        (output / "repositories" / "cpc_service" / "repository_inventory.json").read_text(encoding="utf-8")
    )
    assert payload["identity"]["source_kind"] == "sourcecontrol"
    assert payload["identity"]["scope_id"] == "PPRB_CPC"
    assert payload["identity"]["default_branch"] == "main"
