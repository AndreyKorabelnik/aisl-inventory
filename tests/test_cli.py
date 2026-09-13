from __future__ import annotations

import json
from pathlib import Path

from repository_inventory.cli import main


def test_cli_build_and_verify(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("hello", encoding="utf-8")
    out = tmp_path / "out"

    assert main(["build", str(repo), "--output", str(out), "--repository-id", "cli-repo"]) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["status"] == "built"
    assert built["format"] == "repository-inventory/v7"

    assert main(["verify", str(out)]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["status"] == "pass"



def test_cli_build_can_emit_reduced_in_same_build(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo-reduced"
    repo.mkdir()
    (repo / "a.txt").write_text("hello", encoding="utf-8")
    out = tmp_path / "out-reduced"

    assert main(["build", str(repo), "--output", str(out), "--repository-id", "cli-reduced", "--reduce"]) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["status"] == "built"
    assert built["reduced"]["format"] == "repository-inventory-reduced/v3"
    reduced = out / "repository_inventory_reduced.json"
    candidates = out / "repository_concept_candidates.json"
    assert reduced.is_file()
    assert candidates.is_file()
    assert built["concept_candidates"]["format"] == "repository-inventory-concept-candidates/v1"

    assert main(["verify-reduced", str(reduced)]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["status"] == "verified"

def test_cli_exposes_structured_probe_limit(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "large.json").write_text(json.dumps({"openapi": "3.0.0", "padding": "x" * 200}), encoding="utf-8")
    out = tmp_path / "out"
    assert main([
        "build", str(repo), "--output", str(out), "--repository-id", "cli-limit",
        "--structured-probe-max-bytes", "64"
    ]) == 0
    capsys.readouterr()
    payload = json.loads((out / "repository_inventory.json").read_text(encoding="utf-8"))
    assert payload["run_provenance"]["configuration"]["structured_probe_max_bytes"] == 64
    assert any(row["code"] == "inventory_probe_file_size_limit" for row in payload["observation_diagnostics"])


def test_cli_build_bitbucket_project_returns_nonzero_for_partial(monkeypatch, tmp_path: Path, capsys) -> None:
    from repository_inventory.remote_batch import RemoteBatchRunResult
    import repository_inventory.cli as cli_module

    output = tmp_path / "batch"
    manifest = {
        "format": "repository-inventory-remote-batch-manifest/v6",
        "status": "partial",
        "batch_id": "batch_demo",
        "summary": {"total": 3, "completed": 2, "failed": 1},
    }
    monkeypatch.setattr(
        cli_module,
        "build_bitbucket_project_inventory",
        lambda **_kwargs: RemoteBatchRunResult(output=output, manifest=manifest),
    )

    code = main([
        "build-bitbucket-project",
        "--bitbucket-project-url", "https://example.invalid/projects/ABC",
        "--output", str(output),
        "--repository-limit", "3",
        "--auth-mode", "none",
    ])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "partial"
    assert payload["completed"] == 2
    assert payload["failed"] == 1


def test_cli_build_sourcecontrol_organization_wires_provider_specific_coordinates(monkeypatch, tmp_path: Path, capsys) -> None:
    from repository_inventory.remote_batch import RemoteBatchRunResult
    import repository_inventory.cli as cli_module

    output = tmp_path / "sourcecontrol-batch"
    seen: dict[str, object] = {}

    def fake_build(**kwargs):
        seen.update(kwargs)
        return RemoteBatchRunResult(
            output=output,
            manifest={
                "format": "repository-inventory-remote-batch-manifest/v6",
                "status": "completed",
                "batch_id": "batch_sc",
                "summary": {"total": 2, "built": 2, "reused": 0, "successful": 2, "failed": 0, "selection_unresolved": 0},
            },
        )

    monkeypatch.setattr(cli_module, "build_sourcecontrol_organization_inventory", fake_build)
    code = main([
        "build-sourcecontrol-organization",
        "--sourcecontrol-api-url", "https://api.sc-ci.sber.ru",
        "--organization", "PPRB_CPC",
        "--output", str(output),
        "--repository-limit", "2",
        "--auth-mode", "basic",
        "--username-env", "BITBUCKET_USERNAME",
        "--password-env", "BITBUCKET_PASSWORD",
        "--reduce",
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "completed"
    assert seen["api_url"] == "https://api.sc-ci.sber.ru"
    assert seen["organization"] == "PPRB_CPC"
    assert seen["username_env"] == "BITBUCKET_USERNAME"
    assert seen["password_env"] == "BITBUCKET_PASSWORD"
    assert seen["max_repositories"] == 2
    assert seen["build_reduced"] is True
