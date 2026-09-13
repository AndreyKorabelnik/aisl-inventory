from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

from repository_inventory.contracts import FORBIDDEN_AISL_DISTRIBUTIONS, FORBIDDEN_AISL_IMPORT_PREFIXES


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_no_forbidden_aisl_runtime_dependency_or_import() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = [str(item).lower() for item in pyproject["project"].get("dependencies", [])]
    assert dependencies
    assert not any(
        forbidden in dependency
        for dependency in dependencies
        for forbidden in FORBIDDEN_AISL_DISTRIBUTIONS
    )

    violations: list[str] = []
    for path in sorted((PROJECT_ROOT / "repository_inventory").rglob("*.py")):
        for imported in _imports(path):
            if any(imported == prefix or imported.startswith(prefix + ".") for prefix in FORBIDDEN_AISL_IMPORT_PREFIXES):
                violations.append(f"{path.relative_to(PROJECT_ROOT)} -> {imported}")
    assert violations == []


def _run_build(repo: Path, output: Path, pythonpath: str) -> None:
    env = dict(os.environ)
    inherited = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(part for part in (pythonpath, inherited) if part)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "repository_inventory",
            "build",
            str(repo),
            "--output",
            str(output),
            "--repository-id",
            "isolation-fixture",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    status = json.loads(completed.stdout)
    assert status["status"] == "built"



def test_java_parser_dependency_is_owned_by_shared_syntax_package() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = [str(item).lower() for item in pyproject["project"].get("dependencies", [])]
    assert "source-syntax-primitives==0.1.0a7" in dependencies
    assert not any(item.startswith("tree-sitter-java") or item.startswith("tree-sitter==") for item in dependencies)
    assert not any(item.startswith("sqlglot") for item in dependencies)

    java_probe = (PROJECT_ROOT / "repository_inventory" / "java_probe.py").read_text(encoding="utf-8")
    assert "import tree_sitter_java" not in java_probe
    assert "from tree_sitter" not in java_probe
    assert "from source_syntax_primitives.java import" in java_probe
    assert 'node.type == "package_declaration"' not in java_probe
    assert 'node.type == "import_declaration"' not in java_probe
    assert '"import static" in' not in java_probe
    assert "annotation_argument_list" not in java_probe
    assert "java_declaration_syntax" in java_probe
    assert "annotation_syntax_from_node" in java_probe
    assert "method_invocation_syntax_from_node" in java_probe
    assert "object_creation_syntax_from_node" in java_probe
    assert "def _method_receiver" not in java_probe
    assert "def _arg_count" not in java_probe


def test_config_and_proto_parser_dependencies_are_owned_by_shared_package() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = [str(item).lower() for item in pyproject["project"].get("dependencies", [])]
    for dependency in ("proto-schema-parser", "antlr4-python3-runtime", "jproperties", "hocon-parser"):
        assert not any(item.startswith(dependency) for item in dependencies)

    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((PROJECT_ROOT / "repository_inventory").rglob("*.py"))
    )
    assert "from proto_schema_parser" not in sources
    assert "from hocon._internal" not in sources
    assert "from jproperties" not in sources
    assert "parse_proto_text" in sources
    assert "parse_hocon_text" in sources
    assert "parse_properties_text" in sources


def test_maven_xml_parser_is_owned_by_shared_package() -> None:
    source = (PROJECT_ROOT / "repository_inventory" / "build_config_probe.py").read_text(encoding="utf-8")
    assert "xml.etree.ElementTree" not in source
    assert "ET.fromstring" not in source
    assert "parse_maven_pom" in source

def test_framework_present_vs_absent_canonical_payload_is_identical(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Main.java").write_text("package demo; import q.R; @Flag(key = \"v\") class Main { void x(){ DSL.select(1); new Client(); } }\n", encoding="utf-8")
    (repo / "pom.xml").write_text("<project><dependencies><dependency><groupId>g</groupId><artifactId>a</artifactId></dependency></dependencies></project>\n", encoding="utf-8")
    (repo / "application.properties").write_text("feature.enabled=true\n", encoding="utf-8")
    (repo / "mystery.xyz").write_text("opaque\n", encoding="utf-8")

    fake_framework = tmp_path / "fake-framework"
    fake_framework.mkdir()
    # Make every forbidden package importable. Importing one raises immediately, proving
    # presence cannot become a hidden branch or fallback.
    for package in FORBIDDEN_AISL_IMPORT_PREFIXES:
        directory = fake_framework / package
        directory.mkdir()
        (directory / "__init__.py").write_text(
            "raise RuntimeError('repository-inventory imported forbidden AISL package')\n",
            encoding="utf-8",
        )

    absent = tmp_path / "absent"
    present = tmp_path / "present"
    project_path = str(PROJECT_ROOT)
    _run_build(repo, absent, project_path)
    _run_build(repo, present, os.pathsep.join((str(fake_framework), project_path)))

    assert (absent / "repository_inventory.json").read_bytes() == (present / "repository_inventory.json").read_bytes()
    assert (absent / "source-content" / "index.json").read_bytes() == (present / "source-content" / "index.json").read_bytes()
    assert sorted(path.name for path in (absent / "source-content" / "blobs").iterdir()) == sorted(
        path.name for path in (present / "source-content" / "blobs").iterdir()
    )


def test_version_and_contract_self_description_are_consistent() -> None:
    from repository_inventory import CONTRACT_FORMAT, __version__

    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__ == "0.1.0a38"
    assert CONTRACT_FORMAT == "repository-inventory/v7"
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    versions = (PROJECT_ROOT / "VERSIONS.md").read_text(encoding="utf-8")
    assert __version__ in readme and __version__ in versions
    assert "repository-inventory/v7" in readme and "repository-inventory/v7" in versions


def test_catalog_and_discovery_are_not_inventory_runtime_owned() -> None:
    package = PROJECT_ROOT / "repository_inventory"
    assert not (package / "discovery.py").exists()
    assert not (package / "resources" / "repository-inventory-concept-catalog-v1.schema.json").exists()
    assert not (package / "resources" / "repository-inventory-discovery-v1.schema.json").exists()
    from repository_inventory.cli import _parser
    commands = set(_parser()._subparsers._group_actions[0].choices)
    assert {"build-catalog", "verify-catalog", "discover", "verify-discovery"}.isdisjoint(commands)
    assert {"build", "reduce", "verify-reduced", "export-selected", "verify-selected-export"} <= commands
