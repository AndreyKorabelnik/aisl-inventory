from __future__ import annotations

import base64
import json
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import quote, urlencode, urlparse, urlunparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class RemoteRepositorySource:
    repository_id: str
    clone_url: str
    ref: str | None = None
    scope_key: str | None = None
    repository_name: str | None = None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RemoteRepositorySet:
    remote_system: str
    scope_kind: str
    scope_url: str
    scope_key: str
    repositories: tuple[RemoteRepositorySource, ...]
    observed_repository_count: int
    discovery_diagnostics: tuple[Mapping[str, Any], ...] = ()


def normalize_repository_id(value: str) -> str:
    normalized = re.sub(r"([a-z])([A-Z])", r"\1_\2", str(value or ""))
    normalized = normalized.replace("-", "_").replace(" ", "_")
    normalized = re.sub(r"[^a-zA-Z0-9_]", "_", normalized)
    return re.sub(r"_+", "_", normalized).strip("_").lower()


def sanitize_repository_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("repository URL must not be empty")
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https", "ssh"} and parsed.hostname:
        host = parsed.hostname
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        return urlunparse((parsed.scheme, host, parsed.path, parsed.params, parsed.query, parsed.fragment))
    return raw


def _project_coordinates(project_url: str) -> tuple[str, str]:
    parsed = urlparse(str(project_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"unsupported Bitbucket project URL: {project_url!r}")
    segments = [segment for segment in parsed.path.split("/") if segment]
    project_key: str | None = None
    for index, segment in enumerate(segments[:-1]):
        if segment.lower() == "projects":
            project_key = segments[index + 1]
            break
    if not project_key:
        raise ValueError(
            "Bitbucket project URL must contain /projects/<project-key>: "
            f"{project_url!r}"
        )
    origin = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    return origin.rstrip("/"), project_key


def _resolve_auth_mode(
    *,
    auth_mode: str,
    token_env: str,
    username_env: str,
    password_env: str,
) -> str:
    mode = str(auth_mode or "auto").strip().lower()
    if mode not in {"auto", "token", "basic", "credential-helper", "ssh", "none"}:
        raise ValueError(f"unsupported auth_mode: {auth_mode!r}")
    token = os.environ.get(token_env, "")
    username = os.environ.get(username_env, "")
    password = os.environ.get(password_env, "")
    if mode != "auto":
        return mode
    if bool(username) != bool(password):
        raise ValueError(
            "auto authentication found incomplete basic credentials; both environment variables are required: "
            f"{username_env}, {password_env}"
        )
    has_basic = bool(username and password)
    has_token = bool(token)
    if has_basic and has_token:
        raise ValueError(
            "auto authentication is ambiguous because both token and basic credentials are configured; "
            "choose --auth-mode token or --auth-mode basic explicitly"
        )
    if has_token:
        return "token"
    if has_basic:
        return "basic"
    return "credential-helper"


def _authorization_headers(
    *,
    auth_mode: str,
    token_env: str,
    username_env: str,
    password_env: str,
) -> dict[str, str]:
    mode = _resolve_auth_mode(
        auth_mode=auth_mode,
        token_env=token_env,
        username_env=username_env,
        password_env=password_env,
    )
    token = os.environ.get(token_env, "")
    username = os.environ.get(username_env, "")
    password = os.environ.get(password_env, "")
    if mode == "token":
        if not token:
            raise ValueError(f"authentication token environment variable is empty: {token_env}")
        return {"Authorization": f"Bearer {token}"}
    if mode == "basic":
        if not username or not password:
            raise ValueError(
                "basic authentication requires both environment variables: "
                f"{username_env}, {password_env}"
            )
        encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}
    return {}


def _remote_http_error(*, system: str, url: str, exc: HTTPError, auth_mode: str) -> RuntimeError:
    return RuntimeError(
        f"{system} discovery HTTP {exc.code} for {url}; effective auth mode={auth_mode}. "
        "If the endpoint requires credentials, choose --auth-mode explicitly and verify the configured credential environment variables."
    )


def _ssl_context(*, ca_bundle: Path | None, insecure_skip_tls_verify: bool) -> ssl.SSLContext:
    if insecure_skip_tls_verify:
        return ssl._create_unverified_context()  # noqa: SLF001 - explicit CLI opt-in
    if ca_bundle is not None:
        return ssl.create_default_context(cafile=str(ca_bundle))
    return ssl.create_default_context()


def _clone_url_from_repository(row: Mapping[str, Any], *, auth_mode: str) -> str:
    links = row.get("links")
    clone_rows = links.get("clone") if isinstance(links, Mapping) else None
    candidates: list[tuple[str, str]] = []
    if isinstance(clone_rows, list):
        for item in clone_rows:
            if not isinstance(item, Mapping):
                continue
            href = str(item.get("href") or "").strip()
            name = str(item.get("name") or "").strip().lower()
            if href:
                candidates.append((name, href))
    preferred = "ssh" if str(auth_mode).strip().lower() == "ssh" else "http"
    for name, href in candidates:
        if name == preferred or (preferred == "http" and name == "https"):
            return sanitize_repository_url(href)
    if candidates:
        return sanitize_repository_url(candidates[0][1])
    raise ValueError(f"Bitbucket repository has no clone links: {row.get('slug') or row.get('name')}")


def discover_bitbucket_project_repositories(
    *,
    project_url: str,
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
    opener: Callable[..., Any] = urlopen,
) -> RemoteRepositorySet:
    """Discover Bitbucket Data Center repositories without acquiring repository contents."""
    if page_size < 1:
        raise ValueError("page_size must be at least 1")
    if max_repositories is not None and max_repositories < 1:
        raise ValueError("max_repositories must be at least 1")
    origin, project_key = _project_coordinates(project_url)
    headers = {
        "Accept": "application/json",
        **_authorization_headers(
            auth_mode=auth_mode,
            token_env=token_env,
            username_env=username_env,
            password_env=password_env,
        ),
    }
    context = _ssl_context(
        ca_bundle=Path(ca_bundle).expanduser().resolve() if ca_bundle is not None else None,
        insecure_skip_tls_verify=insecure_skip_tls_verify,
    )
    start = 0
    repositories: list[RemoteRepositorySource] = []
    seen_ids: set[str] = set()
    while True:
        query = urlencode({"limit": page_size, "start": start})
        url = f"{origin}{api_base_path.rstrip('/')}/projects/{quote(project_key, safe='')}/repos?{query}"
        request = Request(url, headers=headers, method="GET")
        try:
            with opener(request, timeout=timeout_seconds, context=context) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise _remote_http_error(
                system="Bitbucket",
                url=url,
                exc=exc,
                auth_mode=_resolve_auth_mode(
                    auth_mode=auth_mode,
                    token_env=token_env,
                    username_env=username_env,
                    password_env=password_env,
                ),
            ) from exc
        if not isinstance(payload, Mapping):
            raise ValueError("Bitbucket repository response must be a JSON object")
        values = payload.get("values")
        if not isinstance(values, list):
            raise ValueError("Bitbucket repository response has no values array")
        for row in values:
            if not isinstance(row, Mapping):
                continue
            slug = str(row.get("slug") or row.get("name") or "").strip()
            repository_id = normalize_repository_id(slug)
            if not repository_id:
                raise ValueError(f"Bitbucket repository has invalid identity: {slug!r}")
            if repository_id in seen_ids:
                raise ValueError(
                    f"duplicate repository id after normalization in project {project_key}: {repository_id}"
                )
            seen_ids.add(repository_id)
            display_name = str(row.get("name") or slug).strip()
            repositories.append(
                RemoteRepositorySource(
                    repository_id=repository_id,
                    clone_url=_clone_url_from_repository(row, auth_mode=auth_mode),
                    ref=None,
                    scope_key=project_key,
                    repository_name=display_name,
                    metadata={
                        "bitbucket_repository_id": row.get("id"),
                        "slug": slug,
                        "name": display_name,
                        "public": row.get("public"),
                        "forkable": row.get("forkable"),
                    },
                )
            )
            if max_repositories is not None and len(repositories) >= max_repositories:
                break
        if max_repositories is not None and len(repositories) >= max_repositories:
            break
        if bool(payload.get("isLastPage")):
            break
        next_start = payload.get("nextPageStart")
        if next_start is None:
            if not values:
                break
            next_start = start + len(values)
        start = int(next_start)
    if not repositories:
        raise ValueError(f"Bitbucket project contains no repositories: {project_url}")
    return RemoteRepositorySet(
        remote_system="bitbucket-data-center",
        scope_kind="project",
        scope_url=project_url,
        scope_key=project_key,
        repositories=tuple(repositories),
        observed_repository_count=len(repositories),
    )


def _http_origin(value: str, *, label: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"unsupported {label} URL: {value!r}")
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", "")).rstrip("/")


def _header_value(response: Any, name: str) -> str:
    headers = getattr(response, "headers", None)
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name)
        if value is None:
            value = getter(name.lower())
        return str(value or "").strip()
    return ""


def discover_sourcecontrol_organization_repositories(
    *,
    api_url: str,
    organization: str,
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
    opener: Callable[..., Any] = urlopen,
) -> RemoteRepositorySet:
    """Discover Platform V SourceControl repositories in one organization."""
    if page_size < 1:
        raise ValueError("page_size must be at least 1")
    if max_repositories is not None and max_repositories < 1:
        raise ValueError("max_repositories must be at least 1")
    organization_key = str(organization or "").strip()
    if not organization_key:
        raise ValueError("SourceControl organization must not be empty")
    origin = _http_origin(api_url, label="SourceControl API")
    headers = {
        "Accept": "application/json",
        **_authorization_headers(
            auth_mode=auth_mode,
            token_env=token_env,
            username_env=username_env,
            password_env=password_env,
        ),
    }
    context = _ssl_context(
        ca_bundle=Path(ca_bundle).expanduser().resolve() if ca_bundle is not None else None,
        insecure_skip_tls_verify=insecure_skip_tls_verify,
    )
    page = 1
    repositories: list[RemoteRepositorySource] = []
    diagnostics: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    observed_repository_count = 0
    total_count: int | None = None
    while True:
        query = urlencode({"limit": page_size, "page": page})
        url = (
            f"{origin}{api_base_path.rstrip('/')}/orgs/"
            f"{quote(organization_key, safe='')}/repos?{query}"
        )
        request = Request(url, headers=headers, method="GET")
        try:
            with opener(request, timeout=timeout_seconds, context=context) as response:
                payload = json.loads(response.read().decode("utf-8"))
                link_header = _header_value(response, "Link")
                total_header = _header_value(response, "X-Total-Count")
        except HTTPError as exc:
            raise _remote_http_error(
                system="SourceControl",
                url=url,
                exc=exc,
                auth_mode=_resolve_auth_mode(
                    auth_mode=auth_mode,
                    token_env=token_env,
                    username_env=username_env,
                    password_env=password_env,
                ),
            ) from exc
        if not isinstance(payload, list):
            raise ValueError("SourceControl repository response must be a JSON array")
        if total_header:
            try:
                total_count = int(total_header)
            except ValueError as exc:
                raise ValueError(
                    f"SourceControl X-Total-Count must be an integer: {total_header!r}"
                ) from exc
        observed_repository_count += len(payload)
        for row in payload:
            if not isinstance(row, Mapping):
                continue
            name = str(row.get("name") or "").strip()
            repository_id = normalize_repository_id(name)
            if not repository_id:
                raise ValueError(f"SourceControl repository has invalid identity: {name!r}")
            if repository_id in seen_ids:
                raise ValueError(
                    "duplicate repository id after normalization in SourceControl organization "
                    f"{organization_key}: {repository_id}"
                )
            seen_ids.add(repository_id)
            clone_url = str(row.get("clone_url") or "").strip()
            if bool(row.get("empty")):
                diagnostics.append({
                    "code": "sourcecontrol_repository_empty",
                    "status": "skipped",
                    "repository_id": repository_id,
                    "repository_name": name,
                    "remote_repository_id": row.get("id"),
                    "clone_url": sanitize_repository_url(clone_url) if clone_url else None,
                    "basis": "SourceControl API observed empty=true; repository has no commit to inventory",
                })
                continue
            if not clone_url:
                diagnostics.append({
                    "code": "sourcecontrol_repository_clone_url_missing",
                    "status": "skipped",
                    "repository_id": repository_id,
                    "repository_name": name,
                    "remote_repository_id": row.get("id"),
                    "basis": "SourceControl API did not provide clone_url",
                })
                continue
            default_branch = str(row.get("default_branch") or "").strip() or None
            owner = row.get("owner") if isinstance(row.get("owner"), Mapping) else {}
            repositories.append(
                RemoteRepositorySource(
                    repository_id=repository_id,
                    clone_url=sanitize_repository_url(clone_url),
                    ref=default_branch,
                    scope_key=organization_key,
                    repository_name=name,
                    metadata={
                        "remote_repository_id": row.get("id"),
                        "remote_name": name,
                        "remote_full_name": row.get("full_name"),
                        "sourcecontrol_owner": owner.get("login") or owner.get("username"),
                        "empty": row.get("empty"),
                        "private": row.get("private"),
                        "fork": row.get("fork"),
                        "archived": row.get("archived"),
                        "default_branch": default_branch,
                        "html_url": row.get("html_url"),
                    },
                )
            )
            if max_repositories is not None and len(repositories) >= max_repositories:
                break
        if max_repositories is not None and len(repositories) >= max_repositories:
            break
        if total_count is not None and observed_repository_count >= total_count:
            break
        if link_header:
            if 'rel="next"' not in link_header:
                break
        elif len(payload) < page_size:
            break
        if not payload:
            break
        page += 1
    if not repositories:
        raise ValueError(
            f"SourceControl organization contains no buildable repositories: {organization_key}"
        )
    scope_url = (
        f"{origin}{api_base_path.rstrip('/')}/orgs/{quote(organization_key, safe='')}"
    )
    return RemoteRepositorySet(
        remote_system="sourcecontrol",
        scope_kind="organization",
        scope_url=scope_url,
        scope_key=organization_key,
        repositories=tuple(repositories),
        observed_repository_count=(
            total_count if total_count is not None else observed_repository_count
        ),
        discovery_diagnostics=tuple(diagnostics),
    )


def _git_environment(
    *,
    auth_mode: str,
    token_env: str,
    username_env: str,
    password_env: str,
) -> dict[str, str]:
    """Build non-interactive Git auth environment without executable temp helpers.

    Explicit HTTP credentials are transported as Git http.extraHeader entries. This
    keeps REST discovery and Git acquisition on one auth contract and works when the
    temporary filesystem is mounted noexec. Ambient credential helpers are disabled
    for explicit token/basic modes so they cannot silently substitute other credentials.
    """
    mode = str(auth_mode or "auto").strip().lower()
    token = os.environ.get(token_env, "")
    username = os.environ.get(username_env, "")
    password = os.environ.get(password_env, "")
    env: dict[str, str] = {
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    effective = _resolve_auth_mode(
        auth_mode=mode,
        token_env=token_env,
        username_env=username_env,
        password_env=password_env,
    )

    authorization: str | None = None
    if effective == "token":
        if not token:
            raise ValueError(f"authentication token environment variable is empty: {token_env}")
        if username:
            encoded = base64.b64encode(f"{username}:{token}".encode("utf-8")).decode("ascii")
            authorization = f"Basic {encoded}"
        else:
            authorization = f"Bearer {token}"
    elif effective == "basic":
        if not username or not password:
            raise ValueError(
                "basic authentication requires both environment variables: "
                f"{username_env}, {password_env}"
            )
        encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        authorization = f"Basic {encoded}"
    elif effective == "ssh":
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    elif effective not in {"credential-helper", "none"}:
        raise ValueError(f"unsupported auth_mode: {auth_mode!r}")

    if authorization is not None:
        env.update(
            {
                "GIT_CONFIG_COUNT": "2",
                "GIT_CONFIG_KEY_0": "http.extraHeader",
                "GIT_CONFIG_VALUE_0": f"Authorization: {authorization}",
                "GIT_CONFIG_KEY_1": "credential.helper",
                "GIT_CONFIG_VALUE_1": "",
                # Never execute a generated helper from /tmp: corporate environments
                # commonly mount it noexec. /bin/false also prevents fallback prompts
                # if the explicit HTTP header is rejected.
                "GIT_ASKPASS": "/bin/false",
                "SSH_ASKPASS": "/bin/false",
            }
        )
    return env


def _safe_git_diagnostic(
    output: str,
    *,
    token_env: str,
    username_env: str,
    password_env: str,
    max_chars: int = 4000,
) -> str:
    """Return bounded Git diagnostics with configured credentials redacted."""
    text = str(output or "").strip()
    for env_name in (token_env, username_env, password_env):
        secret = os.environ.get(env_name, "")
        if secret:
            text = text.replace(secret, "<redacted>")
    text = re.sub(r"(?i)(Authorization:\s*(?:Basic|Bearer))\s+\S+", r"\1 <redacted>", text)
    if len(text) > max_chars:
        text = text[:max_chars] + "…<truncated>"
    return text


def _transient_clone_failure(log_text: str) -> bool:
    normalized = log_text.casefold()
    permanent = (
        "authentication failed",
        "access denied",
        "repository not found",
        "not found",
        "couldn't find remote ref",
        "remote branch",
        "does not appear to be a git repository",
    )
    if any(value in normalized for value in permanent):
        return False
    transient = (
        "timed out",
        "timeout",
        "connection reset",
        "connection refused",
        "temporary failure",
        "remote end hung up",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
        "the requested url returned error: 5",
    )
    return any(value in normalized for value in transient)


def _run_git(command: list[str], *, env: Mapping[str, str], timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, **dict(env)},
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return subprocess.CompletedProcess(command, 124, stdout=f"{output}\nTIMEOUT")


def resolve_remote_repository_commit(
    *,
    source: RemoteRepositorySource,
    auth_mode: str = "auto",
    token_env: str = "BITBUCKET_TOKEN",
    username_env: str = "BITBUCKET_USERNAME",
    password_env: str = "BITBUCKET_PASSWORD",
    timeout_seconds: float = 60.0,
) -> str:
    """Resolve the remote commit for the requested ref without creating a checkout."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")
    clone_url = sanitize_repository_url(source.clone_url)
    commit_ref = bool(source.ref and re.fullmatch(r"[0-9a-fA-F]{7,64}", source.ref))
    if commit_ref:
        return str(source.ref).lower()
    env = _git_environment(
        auth_mode=auth_mode,
        token_env=token_env,
        username_env=username_env,
        password_env=password_env,
    )
    ref = f"refs/heads/{source.ref}" if source.ref else "HEAD"
    completed = _run_git(
        ["git", "ls-remote", clone_url, ref],
        env=env,
        timeout_seconds=timeout_seconds,
    )
    if completed.returncode != 0:
        diagnostic = _safe_git_diagnostic(
            str(completed.stdout or ""),
            token_env=token_env,
            username_env=username_env,
            password_env=password_env,
        )
        suffix = f": {diagnostic}" if diagnostic else ""
        raise RuntimeError(
            f"git ls-remote failed for {source.repository_id} "
            f"(exit={completed.returncode}){suffix}"
        )
    rows = [line.strip() for line in str(completed.stdout or "").splitlines() if line.strip()]
    if not rows:
        raise RuntimeError(f"git ls-remote returned no commit for {source.repository_id} ref={ref}")
    commit = rows[0].split(None, 1)[0].strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise RuntimeError(f"git ls-remote returned invalid commit for {source.repository_id}")
    return commit

def clone_repository(
    *,
    source: RemoteRepositorySource,
    target: str | Path,
    auth_mode: str = "auto",
    token_env: str = "BITBUCKET_TOKEN",
    username_env: str = "BITBUCKET_USERNAME",
    password_env: str = "BITBUCKET_PASSWORD",
    retries: int = 2,
    timeout_seconds: float = 300.0,
) -> tuple[str, tuple[dict[str, Any], ...]]:
    """Shallow-clone exactly one repository into a caller-owned temporary target."""
    if retries < 0:
        raise ValueError("clone retries must be non-negative")
    target_path = Path(target).expanduser().resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    env = _git_environment(
        auth_mode=auth_mode,
        token_env=token_env,
        username_env=username_env,
        password_env=password_env,
    )
    clone_url = sanitize_repository_url(source.clone_url)
    attempts: list[dict[str, Any]] = []
    commit_ref = bool(source.ref and re.fullmatch(r"[0-9a-fA-F]{7,64}", source.ref))
    for attempt in range(1, retries + 2):
        shutil.rmtree(target_path, ignore_errors=True)
        if commit_ref:
            target_path.mkdir(parents=True)
            commands = [
                ["git", "init", "--quiet", str(target_path)],
                ["git", "-C", str(target_path), "remote", "add", "origin", clone_url],
                ["git", "-C", str(target_path), "fetch", "--depth", "1", "--no-tags", "origin", str(source.ref)],
                ["git", "-C", str(target_path), "checkout", "--detach", "--quiet", "FETCH_HEAD"],
            ]
        else:
            command = ["git", "clone", "--depth", "1", "--single-branch", "--no-tags"]
            if source.ref:
                command += ["--branch", source.ref]
            command += [clone_url, str(target_path)]
            commands = [command]
        succeeded = True
        combined_output = ""
        for command in commands:
            completed = _run_git(command, env=env, timeout_seconds=timeout_seconds)
            combined_output += str(completed.stdout or "")
            if completed.returncode != 0:
                succeeded = False
                break
        transient = False if succeeded else _transient_clone_failure(combined_output)
        attempt_row: dict[str, Any] = {
            "attempt": attempt,
            "status": "completed" if succeeded else "failed",
            "transient": transient,
        }
        if not succeeded:
            diagnostic = _safe_git_diagnostic(
                combined_output,
                token_env=token_env,
                username_env=username_env,
                password_env=password_env,
            )
            if diagnostic:
                attempt_row["diagnostic"] = diagnostic
        attempts.append(attempt_row)
        if succeeded:
            completed = _run_git(
                ["git", "-C", str(target_path), "rev-parse", "HEAD"],
                env=env,
                timeout_seconds=timeout_seconds,
            )
            if completed.returncode != 0:
                raise RuntimeError(f"git rev-parse failed after clone for {source.repository_id}")
            return str(completed.stdout or "").strip(), tuple(attempts)
        if attempt > retries or not transient:
            diagnostic = _safe_git_diagnostic(
                combined_output,
                token_env=token_env,
                username_env=username_env,
                password_env=password_env,
            )
            suffix = f": {diagnostic}" if diagnostic else ""
            raise RuntimeError(
                f"git clone failed for {source.repository_id} after {attempt} attempt(s){suffix}"
            )
        time.sleep(min(2.0, 0.25 * (2 ** (attempt - 1))))
    raise AssertionError("unreachable")


@contextmanager
def temporary_repository_checkout(
    *,
    source: RemoteRepositorySource,
    work_dir: str | Path | None = None,
    auth_mode: str = "auto",
    token_env: str = "BITBUCKET_TOKEN",
    username_env: str = "BITBUCKET_USERNAME",
    password_env: str = "BITBUCKET_PASSWORD",
    retries: int = 2,
    timeout_seconds: float = 300.0,
) -> Iterator[tuple[Path, str, tuple[dict[str, Any], ...]]]:
    """Acquire one shallow checkout and guarantee removal when the context exits."""
    base = Path(work_dir).expanduser().resolve() if work_dir is not None else None
    if base is not None:
        base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="repository-inventory-", dir=str(base) if base else None) as temp:
        root = Path(temp)
        target = root / source.repository_id
        commit, attempts = clone_repository(
            source=source,
            target=target,
            auth_mode=auth_mode,
            token_env=token_env,
            username_env=username_env,
            password_env=password_env,
            retries=retries,
            timeout_seconds=timeout_seconds,
        )
        yield target, commit, attempts
