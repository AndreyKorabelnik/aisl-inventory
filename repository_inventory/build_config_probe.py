from __future__ import annotations

from dataclasses import dataclass
import json
import tomllib
from typing import Any, Iterable

from source_syntax_primitives import (
    MavenPomSyntaxError,
    parse_hocon_text,
    parse_maven_pom,
    parse_properties_text,
)

from .canonical import stable_id, fingerprint


@dataclass
class BuildConfigProbeResult:
    dependency_observations: list[dict[str, Any]]
    config_key_observations: list[dict[str, Any]]
    source_occurrences: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]]
    probe_status: dict[str, dict[str, Any]]


def _diag(*, repository_id: str, path: str, occurrence_id: str | None, code: str, message: str, basis: dict[str, Any]) -> dict[str, Any]:
    return {
        "diagnostic_id": stable_id("diagnostic", repository_id, path, "build_config_observations", code, basis),
        "code": code,
        "severity": "warning",
        "message": message,
        "source_ref": {"repository_relative_path": path, "localization_kind": "file"},
        "source_occurrence_id": occurrence_id,
        "basis": basis,
        "probe": {"probe_id": "build_config_observations", "probe_version": "2"},
    }


def _flatten_mapping(value: Any, prefix: tuple[str, ...] = ()) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _flatten_mapping(child, prefix + (str(key),))
        return
    if prefix:
        yield ".".join(prefix), value


def _hocon_items(text: str) -> list[tuple[str, Any]]:
    result = parse_hocon_text(text)
    rows: list[tuple[str, Any]] = []
    list_paths = {node.path for node in result.nodes if node.node_kind == "list"}
    for node in result.nodes:
        if node.path == "$" or node.node_kind in {"mapping", "include"}:
            continue
        if any(node.path.startswith(f"{path}[") for path in list_paths):
            continue
        if node.node_kind in {"string", "number", "boolean", "null"}:
            value = {
                "kind": "scalar",
                "raw": node.value_expression,
                "value_type": node.node_kind,
            }
        elif node.node_kind == "list":
            value = {"kind": "array", "item_count": node.child_count}
        elif node.node_kind == "substitution":
            value = {
                "kind": "subst",
                "optional": bool(node.substitution_optional),
                "segments": list(node.substitution_segments),
            }
        elif node.node_kind == "concatenation":
            value = {"kind": "concat", "node_count": node.expression_node_count}
        else:
            value = {"kind": node.node_kind}
        rows.append((node.path, value))
    return rows


def _config_items(ext: str, text: str) -> list[tuple[str, Any]]:
    if ext == ".toml":
        return list(_flatten_mapping(tomllib.loads(text)))
    if ext == ".properties":
        return [(str(key), value) for key, value in parse_properties_text(text).values.items()]
    if ext == ".conf":
        return _hocon_items(text)
    raise ValueError(f"unsupported config extension: {ext}")


def run_build_config_probes(*, repository_id: str, files: list[dict[str, Any]], source_bytes_by_path: dict[str, bytes], file_occurrence_by_path: dict[str, str], max_probe_file_bytes: int) -> BuildConfigProbeResult:
    deps: list[dict[str, Any]] = []
    config: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    build_candidate_count = config_candidate_count = 0
    build_gap = config_gap = False

    for file_row in files:
        path = file_row["repository_relative_path"]
        name = file_row["file_name"]
        ext = file_row["extension"]
        raw = source_bytes_by_path.get(path)
        if raw is None:
            continue
        is_pom = name == "pom.xml"
        is_gradle = ext in {".gradle", ".kts"} and (name.endswith(".gradle") or name.endswith(".gradle.kts"))
        is_config = ext in {".properties", ".conf", ".toml"}
        if is_pom or is_gradle:
            build_candidate_count += 1
        if is_config:
            config_candidate_count += 1
        if not (is_pom or is_gradle or is_config):
            continue
        if len(raw) > max_probe_file_bytes:
            build_gap |= is_pom or is_gradle
            config_gap |= is_config
            diagnostics.append(_diag(repository_id=repository_id,path=path,occurrence_id=file_occurrence_by_path.get(path),code="build_config_probe_file_too_large",message="Build/config parser did not inspect an oversized file.",basis={"byte_size":len(raw),"max_probe_file_bytes":max_probe_file_bytes}))
            continue
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            build_gap |= is_pom or is_gradle
            config_gap |= is_config
            diagnostics.append(_diag(repository_id=repository_id,path=path,occurrence_id=file_occurrence_by_path.get(path),code="build_config_utf8_decode_failed",message="Build/config source could not be decoded as UTF-8.",basis={"reason":exc.reason}))
            continue

        if is_pom:
            try:
                syntax = parse_maven_pom(text)
            except MavenPomSyntaxError as exc:
                build_gap = True
                diagnostics.append(_diag(repository_id=repository_id,path=path,occurrence_id=file_occurrence_by_path.get(path),code="maven_pom_parse_failed",message="Maven POM could not be parsed.",basis={"error":str(exc)}))
            else:
                for dep in syntax.dependencies:
                    context = dep.declaration_context
                    group, artifact = dep.group_id, dep.artifact_id
                    if not group or not artifact:
                        build_gap=True
                        diagnostics.append(_diag(repository_id=repository_id,path=path,occurrence_id=file_occurrence_by_path.get(path),code="maven_dependency_missing_coordinate_part",message="Maven dependency lacked groupId or artifactId.",basis={"declaration_context":context}))
                        continue
                    version, scope, dep_type, classifier = dep.version_expression, dep.scope, dep.dependency_type, dep.classifier
                    deps.append({"dependency_observation_id":stable_id("dependency_observation",repository_id,path,"maven",context,group,artifact,version,scope,dep_type,classifier),"repository_id":repository_id,"file_id":file_row["file_id"],"repository_relative_path":path,"source_occurrence_id":file_occurrence_by_path.get(path),"build_system":"maven","observation_kind":"dependency_declaration","declaration_context":context,"group_id":group,"artifact_id":artifact,"version_expression":version,"scope":scope,"type":dep_type,"classifier":classifier,"resolution_status":"not_resolved","claim":{"classification":"observed_fact","confidence":1.0,"basis":"maven_dependency_xml_declaration"},"probe":{"probe_id":"dependency_observations","probe_version":"2"}})

        if is_gradle:
            # F3-complete rule: do not reintroduce a second Groovy/Gradle parser in standalone Inventory.
            build_gap = True
            diagnostics.append(_diag(repository_id=repository_id,path=path,occurrence_id=file_occurrence_by_path.get(path),code="gradle_dependency_detail_not_evaluated",message="Gradle file observed, but dependency details are not parsed by standalone Inventory because no independent mature Gradle parser owner is configured.",basis={"format":"gradle","policy":"explicit_gap_no_regex_fallback"}))

        if is_config:
            try:
                items = _config_items(ext, text)
            except Exception as exc:
                config_gap=True
                diagnostics.append(_diag(repository_id=repository_id,path=path,occurrence_id=file_occurrence_by_path.get(path),code="config_parser_failed",message="Parser-owned config extraction failed; no config-key facts were emitted for this file.",basis={"format":ext.lstrip('.'),"error_type":type(exc).__name__,"error":str(exc)}))
            else:
                for full_key, value in items:
                    section, _, key = full_key.rpartition(".")
                    value_present = value is not None and value != ""
                    config.append({"config_key_observation_id":stable_id("config_key_observation",repository_id,path,full_key),"repository_id":repository_id,"file_id":file_row["file_id"],"repository_relative_path":path,"source_occurrence_id":file_occurrence_by_path.get(path),"format":ext.lstrip("."),"section":section or None,"key":key or full_key,"full_key":full_key,"value_present":value_present,"value_fingerprint":fingerprint(value) if value_present else None,"claim":{"classification":"observed_fact","confidence":1.0,"basis":"parser_owned_config_key"},"probe":{"probe_id":"config_key_observations","probe_version":"2"}})

    def status(count: int, gap: bool, kind: str) -> dict[str, Any]:
        if not count:
            return {"status":"not_applicable","basis":{"kind":f"no_{kind}_candidate_files"}}
        return {"status":"partial" if gap else "complete","basis":{"kind":f"{kind}_candidate_files_with_probe_gaps" if gap else f"{kind}_candidate_files_observed","candidate_file_count":count}}
    deps.sort(key=lambda r:(r["repository_relative_path"],r["build_system"],r["group_id"],r["artifact_id"],r.get("version_expression") or "",r["dependency_observation_id"]))
    config.sort(key=lambda r:(r["repository_relative_path"],r["full_key"],r["config_key_observation_id"]))
    diagnostics.sort(key=lambda r:(r["source_ref"]["repository_relative_path"],r["code"],r["diagnostic_id"]))
    return BuildConfigProbeResult(deps,config,[],diagnostics,{"dependency_observations":status(build_candidate_count,build_gap,"build_dependency"),"config_key_observations":status(config_candidate_count,config_gap,"config")})
