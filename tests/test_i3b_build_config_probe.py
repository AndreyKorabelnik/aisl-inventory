from __future__ import annotations

from pathlib import Path

from v7_helpers import observed_rows

from repository_inventory.builder import build_semantic_payload
from repository_inventory.config import InventoryConfig


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_maven_dependency_declarations_are_observed_without_resolution(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "pom.xml",
        '''<project xmlns="http://maven.apache.org/POM/4.0.0">
  <dependencyManagement><dependencies><dependency>
    <groupId>io.netty</groupId><artifactId>netty-bom</artifactId>
    <version>${netty.version}</version><type>pom</type><scope>import</scope>
  </dependency></dependencies></dependencyManagement>
  <dependencies><dependency>
    <groupId>org.apache.commons</groupId><artifactId>commons-lang3</artifactId>
  </dependency></dependencies>
</project>''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="maven")
    rows = observed_rows(payload, "dependency_observation")
    assert len(rows) == 2
    bom = next(r for r in rows if r["artifact_id"] == "netty-bom")
    assert bom["declaration_context"] == "dependency_management"
    assert bom["version_expression"] == "${netty.version}"
    assert bom["scope"] == "import"
    assert bom["resolution_status"] == "not_resolved"
    direct = next(r for r in rows if r["artifact_id"] == "commons-lang3")
    assert direct["declaration_context"] == "dependency"
    assert direct["version_expression"] is None
    status = {r["probe_id"]: r["status"] for r in payload["probe_status"]}
    assert status["dependency_observations"] == "complete"


def test_gradle_dependency_detail_is_explicit_gap_without_regex_fallback(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "gradle/dependencies.gradle", 'ext { libs = [jooq: "org.jooq:jooq:$jooqVer"] }\n')
    _write(repo, "app/build.gradle", 'dependencies { implementation "org.slf4j:slf4j-api:2.0.17" }\n')
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="gradle")
    assert observed_rows(payload, "dependency_observation") == []
    assert any(r["code"] == "gradle_dependency_detail_not_evaluated" for r in payload["observation_diagnostics"])
    status = {r["probe_id"]: r["status"] for r in payload["probe_status"]}
    assert status["dependency_observations"] == "partial"


def test_config_keys_are_observed_but_values_are_not_emitted(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "application.properties", "server.port=8080\nsecret.token = abc123\n")
    _write(repo, "service.conf", "feature.enabled: true\n")
    _write(repo, "tool.toml", "[database]\nhost = \"localhost\"\nport = 5432\n")
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="config")
    rows = observed_rows(payload, "config_key_observation")
    keys = {r["full_key"] for r in rows}
    assert {"server.port", "secret.token", "feature.enabled", "database.host", "database.port"} <= keys
    secret = next(r for r in rows if r["full_key"] == "secret.token")
    assert secret["value_present"] is True
    assert "abc123" not in str(secret)
    assert secret["value_fingerprint"] is not None
    status = {r["probe_id"]: r["status"] for r in payload["probe_status"]}
    assert status["config_key_observations"] == "complete"


def test_malformed_pom_is_partial_with_diagnostic_not_empty_success(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "pom.xml", "<project><dependencies><dependency></project>")
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="bad-pom")
    status = {r["probe_id"]: r["status"] for r in payload["probe_status"]}
    assert status["dependency_observations"] == "partial"
    assert any(r["code"] == "maven_pom_parse_failed" for r in payload["observation_diagnostics"])


def test_build_config_probe_size_limit_is_explicit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "build.gradle", 'implementation "g:a:1"\n' + " " * 100)
    payload, _, _ = build_semantic_payload(
        repository=repo,
        repository_id="build-limit",
        config=InventoryConfig.create(build_config_probe_max_bytes=16),
    )
    status = {r["probe_id"]: r["status"] for r in payload["probe_status"]}
    assert status["dependency_observations"] == "partial"
    assert any(r["code"] == "build_config_probe_file_too_large" for r in payload["observation_diagnostics"])


def test_gradle_comments_do_not_trigger_hidden_coordinate_fallback(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "build.gradle", """// implementation 'fake:one:1'
/* api \"fake:two:2\" */
implementation 'real:artifact:3'
""")
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="gradle-comments")
    assert observed_rows(payload, "dependency_observation") == []
    assert any(r["code"] == "gradle_dependency_detail_not_evaluated" for r in payload["observation_diagnostics"])

