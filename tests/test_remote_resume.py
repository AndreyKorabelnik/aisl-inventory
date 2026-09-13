from __future__ import annotations

import json
import subprocess
from pathlib import Path

import repository_inventory.remote_batch as remote_batch
from repository_inventory.config import InventoryConfig
from repository_inventory.remote_acquisition import RemoteRepositorySet, RemoteRepositorySource
from repository_inventory.remote_batch import (
    build_bitbucket_project_inventory,
    load_repository_selectors,
    select_remote_repositories,
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


def _commit(repo: Path, content: str) -> str:
    (repo / "sample.txt").write_text(content, encoding="utf-8")
    _git("add", "sample.txt", cwd=repo)
    _git("commit", "-m", "change", cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo)


def _source(repo: Path, repository_id: str, *, slug: str | None = None, bitbucket_id: int | None = None) -> RemoteRepositorySource:
    actual_slug = slug or repository_id.replace("_", "-")
    return RemoteRepositorySource(
        repository_id=repository_id,
        clone_url=repo.resolve().as_uri(),
        ref="main",
        scope_key="ABC",
        repository_name=actual_slug.title(),
        metadata={"slug": actual_slug, "bitbucket_repository_id": bitbucket_id},
    )


def _discovered(*sources: RemoteRepositorySource) -> RemoteRepositorySet:
    return RemoteRepositorySet(
        remote_system="bitbucket-data-center",
        scope_kind="project",
        scope_url="https://example.invalid/projects/ABC",
        scope_key="ABC",
        repositories=tuple(sources),
        observed_repository_count=len(sources),
    )


def test_selector_supports_explicit_slug_id_numeric_id_and_deterministic_limit(tmp_path: Path) -> None:
    repo_a = _repository(tmp_path, "a", "a\n")
    repo_b = _repository(tmp_path, "b", "b\n")
    repo_c = _repository(tmp_path, "c", "c\n")
    discovered = _discovered(
        _source(repo_c, "repo_c", slug="gamma", bitbucket_id=103),
        _source(repo_a, "repo_a", slug="alpha", bitbucket_id=101),
        _source(repo_b, "repo_b", slug="beta", bitbucket_id=102),
    )

    selected, diagnostics = select_remote_repositories(discovered, selectors=("beta", "101"))
    assert [item.repository_id for item in selected] == ["repo_b", "repo_a"]
    assert diagnostics == ()

    limited, diagnostics = select_remote_repositories(discovered, max_repositories=2)
    assert [item.repository_id for item in limited] == ["repo_a", "repo_b"]
    assert diagnostics == ()


def test_repository_selection_file_ignores_blank_lines_and_comments(tmp_path: Path) -> None:
    path = tmp_path / "repos.txt"
    path.write_text("# pilot\nalpha\n\n102\n", encoding="utf-8")
    assert load_repository_selectors(path) == ("alpha", "102")


def test_resume_reuses_verified_unchanged_inventory_without_checkout(tmp_path: Path, monkeypatch) -> None:
    repo = _repository(tmp_path, "repo-source", "alpha\n")
    discovered = _discovered(_source(repo, "repo_a", slug="alpha", bitbucket_id=101))
    monkeypatch.setattr(remote_batch, "discover_bitbucket_project_repositories", lambda **_kwargs: discovered)
    output = tmp_path / "batch"

    first = build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        repository_selectors=("alpha",),
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
    )
    assert first.manifest["summary"]["built"] == 1

    def _checkout_must_not_run(**_kwargs):
        raise AssertionError("unchanged verified inventory must be reused without checkout")

    monkeypatch.setattr(remote_batch, "temporary_repository_checkout", _checkout_must_not_run)
    second = build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        repository_selectors=("alpha",),
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
        resume=True,
    )
    assert second.manifest["status"] == "completed"
    assert second.manifest["summary"] == {
        "total": 1,
        "built": 0,
        "reused": 1,
        "successful": 1,
        "failed": 0,
        "selection_unresolved": 0,
    }
    row = second.manifest["repository_results"][0]
    assert row["status"] == "reused"
    assert row["action"] == "reused"
    assert row["reuse_basis"] == "all_reuse_conditions_match"
    assert verify_inventory_directory(output / "repositories" / "repo_a")["status"] == "pass"


def test_resume_rebuilds_only_repository_whose_commit_changed(tmp_path: Path, monkeypatch) -> None:
    repo_a = _repository(tmp_path, "repo-a-source", "alpha\n")
    repo_b = _repository(tmp_path, "repo-b-source", "beta\n")
    discovered = _discovered(
        _source(repo_a, "repo_a", slug="alpha"),
        _source(repo_b, "repo_b", slug="beta"),
    )
    monkeypatch.setattr(remote_batch, "discover_bitbucket_project_repositories", lambda **_kwargs: discovered)
    output = tmp_path / "batch"

    first = build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        repository_selectors=("alpha", "beta"),
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
    )
    before_a = json.loads((output / "repositories" / "repo_a" / "repository_inventory.json").read_text())
    before_b = json.loads((output / "repositories" / "repo_b" / "repository_inventory.json").read_text())
    assert first.manifest["summary"]["built"] == 2

    _commit(repo_b, "beta changed\n")
    second = build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        repository_selectors=("alpha", "beta"),
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
        resume=True,
    )
    rows = {row["repository_id"]: row for row in second.manifest["repository_results"]}
    assert rows["repo_a"]["status"] == "reused"
    assert rows["repo_b"]["status"] == "completed"
    assert rows["repo_b"]["action"] == "rebuilt"
    after_a = json.loads((output / "repositories" / "repo_a" / "repository_inventory.json").read_text())
    after_b = json.loads((output / "repositories" / "repo_b" / "repository_inventory.json").read_text())
    assert after_a["semantic_fingerprint"] == before_a["semantic_fingerprint"]
    assert after_b["semantic_fingerprint"] != before_b["semantic_fingerprint"]


def test_resume_rebuilds_when_inventory_configuration_changes(tmp_path: Path, monkeypatch) -> None:
    repo = _repository(tmp_path, "repo-source", "alpha\n")
    discovered = _discovered(_source(repo, "repo_a", slug="alpha"))
    monkeypatch.setattr(remote_batch, "discover_bitbucket_project_repositories", lambda **_kwargs: discovered)
    output = tmp_path / "batch"
    base = InventoryConfig.create(java_probe_max_bytes=1024)
    changed = InventoryConfig.create(java_probe_max_bytes=2048)

    build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        repository_selectors=("alpha",),
        config=base,
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
    )
    second = build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        repository_selectors=("alpha",),
        config=changed,
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
        resume=True,
    )
    row = second.manifest["repository_results"][0]
    assert row["status"] == "completed"
    assert row["action"] == "rebuilt"
    payload = json.loads((output / "repositories" / "repo_a" / "repository_inventory.json").read_text())
    assert payload["run_provenance"]["configuration_fingerprint"] == changed.fingerprint


def test_missing_requested_repository_is_explicit_and_batch_is_partial(tmp_path: Path, monkeypatch) -> None:
    repo = _repository(tmp_path, "repo-source", "alpha\n")
    discovered = _discovered(_source(repo, "repo_a", slug="alpha"))
    monkeypatch.setattr(remote_batch, "discover_bitbucket_project_repositories", lambda **_kwargs: discovered)
    output = tmp_path / "batch"

    result = build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        repository_selectors=("alpha", "missing-repo"),
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
    )
    assert result.manifest["status"] == "partial"
    assert result.manifest["summary"]["successful"] == 1
    assert result.manifest["summary"]["selection_unresolved"] == 1
    assert result.manifest["selection"]["diagnostics"] == [{
        "code": "requested_repository_not_found",
        "selector": "missing-repo",
        "status": "unresolved",
    }]


def test_resume_rebuilds_when_existing_inventory_fails_verification(tmp_path: Path, monkeypatch) -> None:
    repo = _repository(tmp_path, "repo-source-corrupt", "alpha\n")
    discovered = _discovered(_source(repo, "repo_a", slug="alpha"))
    monkeypatch.setattr(remote_batch, "discover_bitbucket_project_repositories", lambda **_kwargs: discovered)
    output = tmp_path / "batch-corrupt"
    build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        repository_selectors=("alpha",),
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
    )
    inventory_file = output / "repositories" / "repo_a" / "repository_inventory.json"
    payload = json.loads(inventory_file.read_text(encoding="utf-8"))
    payload["semantic_fingerprint"] = "corrupted"
    inventory_file.write_text(json.dumps(payload), encoding="utf-8")
    try:
        verify_inventory_directory(output / "repositories" / "repo_a")
    except ValueError:
        pass
    else:
        raise AssertionError("corrupted inventory must fail verification")

    result = build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        repository_selectors=("alpha",),
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
        resume=True,
    )
    assert result.manifest["repository_results"][0]["action"] == "rebuilt"
    assert verify_inventory_directory(output / "repositories" / "repo_a")["status"] == "pass"


def test_force_rebuilds_existing_batch_from_scratch(tmp_path: Path, monkeypatch) -> None:
    repo = _repository(tmp_path, "repo-source-force", "alpha\n")
    discovered = _discovered(_source(repo, "repo_a", slug="alpha"))
    monkeypatch.setattr(remote_batch, "discover_bitbucket_project_repositories", lambda **_kwargs: discovered)
    output = tmp_path / "batch-force"
    build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        repository_selectors=("alpha",),
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
    )
    marker = output / "stale-marker.txt"
    marker.write_text("stale", encoding="utf-8")
    result = build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        repository_selectors=("alpha",),
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
        overwrite=True,
    )
    assert result.manifest["repository_results"][0]["action"] == "built"
    assert not marker.exists()
    assert verify_inventory_directory(output / "repositories" / "repo_a")["status"] == "pass"


def test_initial_build_does_not_require_remote_commit_resolution(tmp_path: Path, monkeypatch) -> None:
    repo = _repository(tmp_path, "repo-initial-no-lsremote", "alpha\n")
    discovered = _discovered(_source(repo, "repo_a", slug="alpha"))
    monkeypatch.setattr(remote_batch, "discover_bitbucket_project_repositories", lambda **_kwargs: discovered)

    def _must_not_resolve(**_kwargs):
        raise AssertionError("initial build must not call git ls-remote")

    monkeypatch.setattr(remote_batch, "resolve_remote_repository_commit", _must_not_resolve)
    output = tmp_path / "batch-initial-no-lsremote"
    result = build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        repository_selectors=("alpha",),
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
    )
    row = result.manifest["repository_results"][0]
    assert row["status"] == "completed"
    assert row["commit_resolution"]["status"] == "not_requested"
    assert row["resolved_commit"]
    assert verify_inventory_directory(output / "repositories" / "repo_a")["status"] == "pass"


def test_resume_commit_resolution_failure_forbids_reuse_and_rebuilds(tmp_path: Path, monkeypatch) -> None:
    repo = _repository(tmp_path, "repo-resume-resolution-fail", "alpha\n")
    discovered = _discovered(_source(repo, "repo_a", slug="alpha"))
    monkeypatch.setattr(remote_batch, "discover_bitbucket_project_repositories", lambda **_kwargs: discovered)
    output = tmp_path / "batch-resume-resolution-fail"
    build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        repository_selectors=("alpha",),
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
    )

    monkeypatch.setattr(
        remote_batch,
        "resolve_remote_repository_commit",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("git ls-remote failed: auth unavailable")),
    )
    result = build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        repository_selectors=("alpha",),
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
        resume=True,
    )
    row = result.manifest["repository_results"][0]
    assert row["status"] == "completed"
    assert row["action"] == "rebuilt"
    assert row["reuse_basis"] == "commit_resolution_failed_rebuild_required"
    assert row["commit_resolution"]["status"] == "failed"
    assert row["commit_resolution"]["reuse_decision"] == "reuse_forbidden_rebuild_required"
    assert "auth unavailable" in row["commit_resolution"]["diagnostic"]
    assert verify_inventory_directory(output / "repositories" / "repo_a")["status"] == "pass"


def test_failed_resume_rebuild_preserves_previous_verified_inventory(tmp_path: Path, monkeypatch) -> None:
    repo = _repository(tmp_path, "repo-resume-preserve", "alpha\n")
    discovered = _discovered(_source(repo, "repo_a", slug="alpha"))
    monkeypatch.setattr(remote_batch, "discover_bitbucket_project_repositories", lambda **_kwargs: discovered)
    output = tmp_path / "batch-resume-preserve"
    build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        repository_selectors=("alpha",),
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
    )
    before = (output / "repositories" / "repo_a" / "repository_inventory.json").read_bytes()

    monkeypatch.setattr(
        remote_batch,
        "resolve_remote_repository_commit",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("resolution unavailable")),
    )
    monkeypatch.setattr(
        remote_batch,
        "build_inventory",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("forced build failure")),
    )
    result = build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        repository_selectors=("alpha",),
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
        resume=True,
    )
    row = result.manifest["repository_results"][0]
    assert row["status"] == "inventory_build_failed"
    assert row["previous_inventory_preserved"] is True
    assert (output / "repositories" / "repo_a" / "repository_inventory.json").read_bytes() == before
    assert verify_inventory_directory(output / "repositories" / "repo_a")["status"] == "pass"


def test_remote_build_can_emit_reduced_and_resume_repairs_missing_reduced_without_checkout(tmp_path: Path, monkeypatch) -> None:
    repo = _repository(tmp_path, "repo-source-reduced", "alpha\n")
    discovered = _discovered(_source(repo, "repo_a", slug="alpha", bitbucket_id=101))
    monkeypatch.setattr(remote_batch, "discover_bitbucket_project_repositories", lambda **_kwargs: discovered)
    output = tmp_path / "batch-reduced"

    first = build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        repository_selectors=("alpha",),
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
        build_reduced=True,
    )
    reduced = output / "repositories" / "repo_a" / "repository_inventory_reduced.json"
    assert reduced.is_file()
    row = first.manifest["repository_results"][0]
    assert row["reduced"]["requested"] is True
    assert row["reduced"]["action"] == "built"

    reduced.unlink()

    def _checkout_must_not_run(**_kwargs):
        raise AssertionError("verified unchanged inventory must not be checked out just to rebuild Reduced")

    monkeypatch.setattr(remote_batch, "temporary_repository_checkout", _checkout_must_not_run)
    second = build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        repository_selectors=("alpha",),
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
        resume=True,
        build_reduced=True,
    )
    assert second.manifest["status"] == "completed"
    row = second.manifest["repository_results"][0]
    assert row["status"] == "reused"
    assert row["reduced"]["action"] == "built_from_reused_inventory"
    assert reduced.is_file()


def test_remote_resume_repairs_missing_candidates_and_index_without_checkout(tmp_path: Path, monkeypatch) -> None:
    repo = _repository(tmp_path, "repo-source-candidates", "alpha\n")
    discovered = _discovered(_source(repo, "repo_a", slug="alpha", bitbucket_id=101))
    monkeypatch.setattr(remote_batch, "discover_bitbucket_project_repositories", lambda **_kwargs: discovered)
    output = tmp_path / "batch-candidates"

    first = build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        repository_selectors=("alpha",),
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
        build_reduced=True,
    )
    candidate = output / "repositories" / "repo_a" / "repository_concept_candidates.json"
    index = output / "repository_concept_index.json"
    assert candidate.is_file() and index.is_file()
    assert first.manifest["repository_results"][0]["concept_candidates"]["action"] == "built"

    candidate.unlink()
    index.unlink()

    def _checkout_must_not_run(**_kwargs):
        raise AssertionError("verified unchanged inventory/reduced must not be checked out just to rebuild candidates/index")

    monkeypatch.setattr(remote_batch, "temporary_repository_checkout", _checkout_must_not_run)
    second = build_bitbucket_project_inventory(
        project_url=discovered.scope_url,
        output=output,
        repository_selectors=("alpha",),
        auth_mode="none",
        clone_retries=0,
        clone_timeout_seconds=30,
        resume=True,
        build_reduced=True,
    )
    row = second.manifest["repository_results"][0]
    assert row["status"] == "reused"
    assert row["reduced"]["action"] == "reused"
    assert row["concept_candidates"]["action"] == "built_from_reused_reduced"
    assert candidate.is_file() and index.is_file()
    assert second.manifest["concept_index"]["repository_count"] == 1
