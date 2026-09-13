from __future__ import annotations

import json
import subprocess
from pathlib import Path

from repository_inventory.remote_acquisition import (
    RemoteRepositorySource,
    discover_bitbucket_project_repositories,
    discover_sourcecontrol_organization_repositories,
    sanitize_repository_url,
    temporary_repository_checkout,
)


class _Response:
    def __init__(self, payload: object, headers: dict[str, str] | None = None) -> None:
        self._raw = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_bitbucket_discovery_is_paginated_and_sanitizes_clone_urls() -> None:
    payloads = [
        {
            "values": [
                {
                    "id": 1,
                    "slug": "Service-One",
                    "name": "Service One",
                    "links": {
                        "clone": [
                            {"name": "http", "href": "https://user:secret@example.invalid/scm/abc/service-one.git"}
                        ]
                    },
                }
            ],
            "isLastPage": False,
            "nextPageStart": 1,
        },
        {
            "values": [
                {
                    "id": 2,
                    "slug": "service-two",
                    "links": {
                        "clone": [
                            {"name": "http", "href": "https://example.invalid/scm/abc/service-two.git"}
                        ]
                    },
                }
            ],
            "isLastPage": True,
        },
    ]
    seen_urls: list[str] = []

    def opener(request, **_kwargs):
        seen_urls.append(request.full_url)
        return _Response(payloads[len(seen_urls) - 1])

    discovered = discover_bitbucket_project_repositories(
        project_url="https://example.invalid/projects/ABC",
        auth_mode="none",
        page_size=1,
        opener=opener,
    )
    assert discovered.remote_system == "bitbucket-data-center"
    assert discovered.scope_kind == "project"
    assert discovered.scope_key == "ABC"
    assert discovered.observed_repository_count == 2
    assert [item.repository_id for item in discovered.repositories] == ["service_one", "service_two"]
    assert discovered.repositories[0].clone_url == "https://example.invalid/scm/abc/service-one.git"
    assert "start=0" in seen_urls[0]
    assert "start=1" in seen_urls[1]


def test_sourcecontrol_discovery_uses_organization_api_pagination_and_skips_empty() -> None:
    payloads = [
        ([
            {
                "id": 39537,
                "owner": {"login": "PPRB_CPC"},
                "name": "ai-universal-service-helper",
                "full_name": "PPRB_CPC/ai-universal-service-helper",
                "empty": True,
                "clone_url": "https://api.example.invalid/PPRB_CPC/ai-universal-service-helper.git",
                "default_branch": "develop",
                "archived": False,
            },
            {
                "id": 39538,
                "owner": {"login": "PPRB_CPC"},
                "name": "ai-universal-service-helper-pvdot",
                "full_name": "PPRB_CPC/ai-universal-service-helper-pvdot",
                "empty": False,
                "clone_url": "https://user:secret@api.example.invalid/PPRB_CPC/ai-universal-service-helper-pvdot.git",
                "default_branch": "develop",
                "archived": False,
            },
        ], {
            "Link": '<https://api.example.invalid/api/v1/orgs/PPRB_CPC/repos?limit=2&page=2>; rel="next",<https://api.example.invalid/api/v1/orgs/PPRB_CPC/repos?limit=2&page=2>; rel="last"',
            "X-Total-Count": "3",
        }),
        ([
            {
                "id": 39539,
                "owner": {"login": "PPRB_CPC"},
                "name": "cpc-service",
                "full_name": "PPRB_CPC/cpc-service",
                "empty": False,
                "clone_url": "https://api.example.invalid/PPRB_CPC/cpc-service.git",
                "default_branch": "main",
                "archived": True,
            },
        ], {"X-Total-Count": "3"}),
    ]
    seen_urls: list[str] = []

    def opener(request, **_kwargs):
        seen_urls.append(request.full_url)
        payload, headers = payloads[len(seen_urls) - 1]
        return _Response(payload, headers)

    discovered = discover_sourcecontrol_organization_repositories(
        api_url="https://api.example.invalid",
        organization="PPRB_CPC",
        auth_mode="none",
        page_size=2,
        opener=opener,
    )

    assert discovered.remote_system == "sourcecontrol"
    assert discovered.scope_kind == "organization"
    assert discovered.scope_key == "PPRB_CPC"
    assert discovered.observed_repository_count == 3
    assert [item.repository_id for item in discovered.repositories] == [
        "ai_universal_service_helper_pvdot",
        "cpc_service",
    ]
    assert discovered.repositories[0].clone_url == (
        "https://api.example.invalid/PPRB_CPC/ai-universal-service-helper-pvdot.git"
    )
    assert discovered.repositories[0].ref == "develop"
    assert discovered.repositories[1].metadata["archived"] is True
    assert discovered.discovery_diagnostics == ({
        "code": "sourcecontrol_repository_empty",
        "status": "skipped",
        "repository_id": "ai_universal_service_helper",
        "repository_name": "ai-universal-service-helper",
        "remote_repository_id": 39537,
        "clone_url": "https://api.example.invalid/PPRB_CPC/ai-universal-service-helper.git",
        "basis": "SourceControl API observed empty=true; repository has no commit to inventory",
    },)
    assert "/api/v1/orgs/PPRB_CPC/repos?limit=2&page=1" in seen_urls[0]
    assert "page=2" in seen_urls[1]


def test_sourcecontrol_discovery_uses_basic_authorization_header(monkeypatch) -> None:
    import base64

    monkeypatch.setenv("SOURCECONTROL_USERNAME", "23384415")
    monkeypatch.setenv("SOURCECONTROL_PASSWORD", "secret")
    seen_headers: dict[str, str] = {}

    def opener(request, **_kwargs):
        seen_headers.update(dict(request.header_items()))
        return _Response([
            {
                "id": 1,
                "name": "repo-one",
                "empty": False,
                "clone_url": "https://api.example.invalid/ORG/repo-one.git",
                "default_branch": "main",
            }
        ], {"X-Total-Count": "1"})

    discover_sourcecontrol_organization_repositories(
        api_url="https://api.example.invalid",
        organization="ORG",
        auth_mode="basic",
        opener=opener,
    )
    expected = base64.b64encode(b"23384415:secret").decode("ascii")
    assert seen_headers["Authorization"] == f"Basic {expected}"


def test_sanitize_repository_url_removes_http_credentials() -> None:
    assert sanitize_repository_url("https://user:password@example.invalid/repo.git") == "https://example.invalid/repo.git"


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


def test_temporary_checkout_is_shallow_and_removed_after_context(tmp_path: Path) -> None:
    source_repo = tmp_path / "source"
    source_repo.mkdir()
    _git("init", "-b", "main", cwd=source_repo)
    _git("config", "user.email", "inventory@example.invalid", cwd=source_repo)
    _git("config", "user.name", "Inventory Test", cwd=source_repo)
    (source_repo / "Main.java").write_text("class Main {}\n", encoding="utf-8")
    _git("add", "Main.java", cwd=source_repo)
    _git("commit", "-m", "initial", cwd=source_repo)
    expected_commit = _git("rev-parse", "HEAD", cwd=source_repo)

    work_dir = tmp_path / "temporary-work"
    source = RemoteRepositorySource(
        repository_id="demo",
        clone_url=source_repo.resolve().as_uri(),
        ref="main",
    )
    checkout_parent: Path | None = None
    with temporary_repository_checkout(
        source=source,
        work_dir=work_dir,
        auth_mode="none",
        retries=0,
        timeout_seconds=30,
    ) as (checkout, commit, attempts):
        checkout_parent = checkout.parent
        assert checkout.is_dir()
        assert (checkout / "Main.java").is_file()
        assert commit == expected_commit
        assert attempts == ({"attempt": 1, "status": "completed", "transient": False},)
    assert checkout_parent is not None
    assert not checkout_parent.exists()


def test_temporary_checkout_feeds_v7_builder_and_source_cas_survives_cleanup(tmp_path: Path) -> None:
    from repository_inventory.builder import build_inventory
    from repository_inventory.verify import verify_inventory_directory

    source_repo = tmp_path / "source-build"
    source_repo.mkdir()
    _git("init", "-b", "main", cwd=source_repo)
    _git("config", "user.email", "inventory@example.invalid", cwd=source_repo)
    _git("config", "user.name", "Inventory Test", cwd=source_repo)
    (source_repo / "Main.java").write_text("package demo; class Main { int value; }\n", encoding="utf-8")
    _git("add", "Main.java", cwd=source_repo)
    _git("commit", "-m", "initial", cwd=source_repo)

    output = tmp_path / "inventory-output"
    source = RemoteRepositorySource(
        repository_id="demo_build",
        clone_url=source_repo.resolve().as_uri(),
        ref="main",
        scope_key="ABC",
        repository_name="Demo Build",
    )
    checkout_parent: Path | None = None
    with temporary_repository_checkout(
        source=source,
        work_dir=tmp_path / "work",
        auth_mode="none",
        retries=0,
        timeout_seconds=30,
    ) as (checkout, _commit, _attempts):
        checkout_parent = checkout.parent
        payload = build_inventory(
            repository=checkout,
            output=output,
            repository_id=source.repository_id,
            repository_name=source.repository_name,
            repository_url=sanitize_repository_url(source.clone_url),
            default_branch=source.ref,
            source_kind="bitbucket-data-center",
            scope_id=source.scope_key,
        )
        assert payload["format"] == "repository-inventory/v7"
    assert checkout_parent is not None and not checkout_parent.exists()
    verification = verify_inventory_directory(output)
    assert verification["status"] == "pass"
    assert (output / "source-content" / "index.json").is_file()


def test_ls_remote_failure_preserves_git_diagnostic_without_secrets(tmp_path: Path, monkeypatch) -> None:
    import os
    import subprocess as sp
    import repository_inventory.remote_acquisition as acquisition

    source = RemoteRepositorySource(
        repository_id="demo",
        clone_url="https://example.invalid/scm/abc/demo.git",
        ref="main",
    )
    monkeypatch.setenv("BITBUCKET_USERNAME", "demo-user")
    monkeypatch.setenv("BITBUCKET_PASSWORD", "super-secret")

    def _failed_git(_command, *, env, timeout_seconds):
        return sp.CompletedProcess(
            _command,
            128,
            stdout="fatal: Authentication failed for demo-user using super-secret",
        )

    monkeypatch.setattr(acquisition, "_run_git", _failed_git)
    try:
        acquisition.resolve_remote_repository_commit(
            source=source,
            auth_mode="basic",
            timeout_seconds=10,
        )
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("ls-remote failure must raise")
    assert "Authentication failed" in message
    assert "demo-user" not in message
    assert "super-secret" not in message
    assert "<redacted>" in message


def test_basic_git_auth_does_not_require_executable_temp_askpass(monkeypatch) -> None:
    import base64
    import repository_inventory.remote_acquisition as acquisition

    monkeypatch.setenv("BITBUCKET_USERNAME", "23384415")
    monkeypatch.setenv("BITBUCKET_PASSWORD", "demo-password")
    env = acquisition._git_environment(
        auth_mode="basic",
        token_env="BITBUCKET_TOKEN",
        username_env="BITBUCKET_USERNAME",
        password_env="BITBUCKET_PASSWORD",
    )

    expected = base64.b64encode(b"23384415:demo-password").decode("ascii")
    assert env["GIT_CONFIG_KEY_0"] == "http.extraHeader"
    assert env["GIT_CONFIG_VALUE_0"] == f"Authorization: Basic {expected}"
    assert env["GIT_CONFIG_KEY_1"] == "credential.helper"
    assert env["GIT_CONFIG_VALUE_1"] == ""
    assert env["GIT_ASKPASS"] == "/bin/false"
    assert "INVENTORY_GIT_PASSWORD" not in env


def test_clone_basic_auth_passes_header_without_generated_askpass(tmp_path: Path, monkeypatch) -> None:
    import base64
    import repository_inventory.remote_acquisition as acquisition

    monkeypatch.setenv("BITBUCKET_USERNAME", "demo-user")
    monkeypatch.setenv("BITBUCKET_PASSWORD", "demo-password")
    seen_envs: list[dict[str, str]] = []

    def fake_run_git(command, *, env, timeout_seconds):
        seen_envs.append(dict(env))
        if command[:2] == ["git", "clone"]:
            target = Path(command[-1])
            target.mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(command, 0, stdout="")
        if "rev-parse" in command:
            return subprocess.CompletedProcess(command, 0, stdout="a" * 40 + "\n")
        raise AssertionError(command)

    monkeypatch.setattr(acquisition, "_run_git", fake_run_git)
    commit, attempts = acquisition.clone_repository(
        source=RemoteRepositorySource(
            repository_id="demo",
            clone_url="https://example.invalid/scm/abc/demo.git",
        ),
        target=tmp_path / "checkout",
        auth_mode="basic",
        retries=0,
        timeout_seconds=10,
    )
    expected = base64.b64encode(b"demo-user:demo-password").decode("ascii")
    assert commit == "a" * 40
    assert attempts == ({"attempt": 1, "status": "completed", "transient": False},)
    assert seen_envs
    for env in seen_envs:
        assert env["GIT_CONFIG_VALUE_0"] == f"Authorization: Basic {expected}"
        assert env["GIT_ASKPASS"] == "/bin/false"
    assert not (tmp_path / "git-askpass.sh").exists()


def test_auto_auth_rejects_ambiguous_token_and_basic_credentials(monkeypatch) -> None:
    monkeypatch.setenv("BITBUCKET_TOKEN", "stale-token")
    monkeypatch.setenv("BITBUCKET_USERNAME", "23384415")
    monkeypatch.setenv("BITBUCKET_PASSWORD", "secret")

    try:
        discover_bitbucket_project_repositories(
            project_url="https://example.invalid/projects/UCP",
            auth_mode="auto",
            opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("HTTP must not be called")),
        )
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("ambiguous auto auth must fail before HTTP")

    assert "ambiguous" in message
    assert "--auth-mode token" in message
    assert "--auth-mode basic" in message


def test_explicit_basic_discovery_ignores_configured_token(monkeypatch) -> None:
    import base64

    monkeypatch.setenv("BITBUCKET_TOKEN", "stale-token")
    monkeypatch.setenv("BITBUCKET_USERNAME", "23384415")
    monkeypatch.setenv("BITBUCKET_PASSWORD", "secret")
    seen_headers: dict[str, str] = {}

    def opener(request, **_kwargs):
        seen_headers.update(dict(request.header_items()))
        return _Response({
            "values": [{
                "id": 1,
                "slug": "repo-one",
                "name": "Repo One",
                "links": {"clone": [{"name": "http", "href": "https://example.invalid/UCP/repo-one.git"}]},
            }],
            "isLastPage": True,
        })

    discover_bitbucket_project_repositories(
        project_url="https://example.invalid/projects/UCP",
        auth_mode="basic",
        opener=opener,
    )
    expected = base64.b64encode(b"23384415:secret").decode("ascii")
    assert seen_headers["Authorization"] == f"Basic {expected}"


def test_sourcecontrol_http_error_reports_effective_auth_mode(monkeypatch) -> None:
    from io import BytesIO
    from urllib.error import HTTPError

    def opener(request, **_kwargs):
        raise HTTPError(request.full_url, 500, "Internal Server Error", {}, BytesIO(b"boom"))

    try:
        discover_sourcecontrol_organization_repositories(
            api_url="https://api.example.invalid",
            organization="PPRB_CPC",
            auth_mode="auto",
            opener=opener,
        )
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("HTTP failure must be contextualized")

    assert "SourceControl discovery HTTP 500" in message
    assert "effective auth mode=credential-helper" in message
    assert "--auth-mode explicitly" in message


def test_auto_auth_rejects_partial_basic_credentials(monkeypatch) -> None:
    monkeypatch.setenv("SOURCECONTROL_USERNAME", "23384415")
    monkeypatch.delenv("SOURCECONTROL_PASSWORD", raising=False)
    monkeypatch.delenv("SOURCECONTROL_TOKEN", raising=False)

    try:
        discover_sourcecontrol_organization_repositories(
            api_url="https://api.example.invalid",
            organization="PPRB_CPC",
            auth_mode="auto",
            opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("HTTP must not be called")),
        )
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("partial auto basic credentials must fail before HTTP")

    assert "incomplete basic credentials" in message
    assert "SOURCECONTROL_USERNAME" in message
    assert "SOURCECONTROL_PASSWORD" in message
