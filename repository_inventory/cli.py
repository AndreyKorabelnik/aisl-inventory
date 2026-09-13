from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .builder import build_inventory
from .config import InventoryConfig
from .contracts import DEFAULT_EXCLUDED_DIRECTORY_NAMES
from .verify import verify_inventory_directory
from .selected_source_export import (
    build_selected_source_export,
    load_export_request,
    verify_selected_source_export,
)
from .version import __version__
from .reduction import build_reduced_inventory, verify_reduced_inventory
from .concept_candidates import build_concept_candidates, verify_concept_candidates
from .concept_index import build_concept_index, verify_concept_index, load_concept_index, query_concept_index
from .remote_batch import (
    build_bitbucket_project_inventory,
    build_sourcecontrol_organization_inventory,
    load_repository_selectors,
)



def _add_remote_batch_arguments(
    remote: argparse.ArgumentParser,
    *,
    token_env: str,
    username_env: str,
    password_env: str,
    selector_help: str,
) -> None:
    remote.add_argument("--output", type=Path, required=True)
    remote.add_argument("--scope-id")
    remote.add_argument("--repository", action="append", default=[], help=selector_help)
    remote.add_argument("--repository-list", type=Path, help="UTF-8 file with one repository selector per line; # comments allowed")
    remote.add_argument("--repository-limit", "--max-repositories", dest="repository_limit", type=int)
    remote.add_argument("--auth-mode", default="auto", choices=("auto", "token", "basic", "credential-helper", "ssh", "none"))
    remote.add_argument("--token-env", default=token_env)
    remote.add_argument("--username-env", default=username_env)
    remote.add_argument("--password-env", default=password_env)
    remote.add_argument("--ca-bundle", type=Path)
    remote.add_argument("--insecure-skip-tls-verify", action="store_true")
    remote.add_argument("--timeout-seconds", type=float, default=60.0)
    remote.add_argument("--page-size", type=int, default=100)
    remote.add_argument("--clone-retries", type=int, default=2)
    remote.add_argument("--clone-timeout-seconds", type=float, default=300.0)
    remote.add_argument("--work-dir", type=Path)
    remote.add_argument("--exclude-directory", action="append", default=None)
    remote.add_argument("--no-content-capture", action="store_true")
    remote.add_argument("--structured-probe-max-bytes", type=int, default=8 * 1024 * 1024)
    remote.add_argument("--java-probe-max-bytes", type=int, default=8 * 1024 * 1024)
    remote.add_argument("--build-config-probe-max-bytes", type=int, default=8 * 1024 * 1024)
    remote.add_argument("--sql-probe-max-bytes", type=int, default=8 * 1024 * 1024)
    remote.add_argument("--reduce", action="store_true", help="Also build repository_inventory_reduced.json for every successful repository")
    remote_mode = remote.add_mutually_exclusive_group()
    remote_mode.add_argument("--resume", action="store_true", help="Reuse verified unchanged inventories and rebuild changed repositories")
    remote_mode.add_argument("--force", action="store_true", help="Delete the existing batch output and rebuild selected repositories")

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repository-inventory")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build a deterministic repository-inventory/v7 artifact")
    build.add_argument("repository", type=Path)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--repository-id", required=True)
    build.add_argument("--repository-name")
    build.add_argument("--repository-url")
    build.add_argument("--default-branch")
    build.add_argument("--source-kind", default="repository")
    build.add_argument("--scope-id")
    build.add_argument("--exclude-directory", action="append", default=None)
    build.add_argument("--no-content-capture", action="store_true")
    build.add_argument("--structured-probe-max-bytes", type=int, default=8 * 1024 * 1024)
    build.add_argument("--java-probe-max-bytes", type=int, default=8 * 1024 * 1024)
    build.add_argument("--build-config-probe-max-bytes", type=int, default=8 * 1024 * 1024)
    build.add_argument("--sql-probe-max-bytes", type=int, default=8 * 1024 * 1024)
    build.add_argument("--reduce", action="store_true", help="Also build repository_inventory_reduced.json after the v7 inventory verifies")
    build.add_argument("--force", action="store_true")

    remote = sub.add_parser(
        "build-bitbucket-project",
        help="Build independent repository-inventory/v7 artifacts for repositories in a Bitbucket Data Center project",
    )
    remote.add_argument("--bitbucket-project-url", required=True)
    remote.add_argument("--api-base-path", default="/rest/api/latest")
    _add_remote_batch_arguments(
        remote,
        token_env="BITBUCKET_TOKEN",
        username_env="BITBUCKET_USERNAME",
        password_env="BITBUCKET_PASSWORD",
        selector_help="Select a repository by id, slug, name, or remote numeric id; repeatable",
    )

    sourcecontrol = sub.add_parser(
        "build-sourcecontrol-organization",
        help="Build independent repository-inventory/v7 artifacts for repositories in a Platform V SourceControl organization",
    )
    sourcecontrol.add_argument("--sourcecontrol-api-url", required=True)
    sourcecontrol.add_argument("--organization", required=True)
    sourcecontrol.add_argument("--api-base-path", default="/api/v1")
    _add_remote_batch_arguments(
        sourcecontrol,
        token_env="SOURCECONTROL_TOKEN",
        username_env="SOURCECONTROL_USERNAME",
        password_env="SOURCECONTROL_PASSWORD",
        selector_help="Select a repository by id, name, full name, or remote numeric id; repeatable",
    )

    verify = sub.add_parser("verify", help="Verify semantic fingerprint and captured source blobs")
    verify.add_argument("inventory", type=Path)

    reduce_cmd = sub.add_parser(
        "reduce",
        help="Build repository-inventory-reduced/v3 from repository-inventory/v7 without source reads",
    )
    reduce_cmd.add_argument("inventory", type=Path)
    reduce_cmd.add_argument("--output", type=Path, required=True)
    reduce_cmd.add_argument("--force", action="store_true")

    verify_reduced = sub.add_parser("verify-reduced", help="Verify repository-inventory-reduced/v3")
    verify_reduced.add_argument("reduced", type=Path)

    classify = sub.add_parser("classify-concepts", help="Build repository-inventory-concept-candidates/v1 from Reduced v3 only")
    classify.add_argument("reduced", type=Path)
    classify.add_argument("--output", type=Path, required=True)
    classify.add_argument("--force", action="store_true")

    verify_candidates = sub.add_parser("verify-concept-candidates", help="Verify repository-inventory-concept-candidates/v1")
    verify_candidates.add_argument("candidates", type=Path)

    build_index = sub.add_parser("build-concept-index", help="Aggregate verified per-repository concept candidates into a batch lookup index")
    build_index.add_argument("batch", type=Path)
    build_index.add_argument("--output", type=Path)

    verify_index = sub.add_parser("verify-concept-index", help="Verify repository-inventory-concept-index/v1")
    verify_index.add_argument("index", type=Path)

    candidates = sub.add_parser("candidates", help="Query a repository concept index")
    candidates.add_argument("index", type=Path, help="Batch directory or repository_concept_index.json")
    candidates.add_argument("--concept", required=True, choices=("data_model","system_interaction","data_flow","persistence","workflow","reference_data"))
    candidates.add_argument("--min-strength", default="moderate", choices=("not_observed","weak","moderate","strong"))
    candidates.add_argument("--mechanism")

    export = sub.add_parser(
        "export-selected",
        help="Export Inventory-owned evidence as bounded exact CAS fragments without reading repository source",
    )
    export.add_argument("inventory", type=Path)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--evidence-id", action="append", default=[])
    export.add_argument("--request", type=Path)
    export.add_argument("--reduced", type=Path, help="Export bounded representative FamilyFileEvidence IDs from an exactly linked Reduced Inventory")
    export.add_argument("--context-lines", type=int, default=10, help="Context lines around Inventory-localized evidence spans")
    export.add_argument("--file-scope-max-bytes", type=int, default=64 * 1024, help="Maximum bytes for evidence localized only to a whole file")
    export.add_argument("--force", action="store_true")

    verify_export = sub.add_parser("verify-selected-export", help="Verify a selected source evidence export")
    verify_export.add_argument("export", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "build":
        excluded = (
            DEFAULT_EXCLUDED_DIRECTORY_NAMES
            if args.exclude_directory is None
            else tuple(args.exclude_directory)
        )
        config = InventoryConfig.create(
            excluded_directory_names=excluded,
            capture_readable_content=not args.no_content_capture,
            structured_probe_max_bytes=args.structured_probe_max_bytes,
            java_probe_max_bytes=args.java_probe_max_bytes,
            build_config_probe_max_bytes=args.build_config_probe_max_bytes,
            sql_probe_max_bytes=args.sql_probe_max_bytes,
        )
        payload = build_inventory(
            repository=args.repository,
            output=args.output,
            repository_id=args.repository_id,
            repository_name=args.repository_name,
            repository_url=args.repository_url,
            default_branch=args.default_branch,
            source_kind=args.source_kind,
            scope_id=args.scope_id,
            config=config,
            overwrite=args.force,
        )
        reduced_payload = None
        candidate_payload = None
        reduced_path = args.output / "repository_inventory_reduced.json"
        candidate_path = args.output / "repository_concept_candidates.json"
        if args.reduce:
            reduced_payload = build_reduced_inventory(
                inventory=args.output,
                output=reduced_path,
                overwrite=args.force,
            )
            candidate_payload = build_concept_candidates(reduced_path, output=candidate_path)
        response = {
            "status": "built",
            "format": payload["format"],
            "inventory_id": payload["inventory_id"],
            "semantic_fingerprint": payload["semantic_fingerprint"],
            "file_count": payload["source_snapshot"]["file_count"],
        }
        if reduced_payload is not None:
            response["reduced"] = {
                "format": reduced_payload["format"],
                "reduced_inventory_id": reduced_payload["reduced_inventory_id"],
                "semantic_fingerprint": reduced_payload["semantic_fingerprint"],
                "path": str(reduced_path),
            }
        if candidate_payload is not None:
            response["concept_candidates"] = {
                "format": candidate_payload["format"],
                "concept_candidate_id": candidate_payload["concept_candidate_id"],
                "semantic_fingerprint": candidate_payload["semantic_fingerprint"],
                "path": str(candidate_path),
            }
        print(json.dumps(response, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command in {"build-bitbucket-project", "build-sourcecontrol-organization"}:
        excluded = (
            DEFAULT_EXCLUDED_DIRECTORY_NAMES
            if args.exclude_directory is None
            else tuple(args.exclude_directory)
        )
        config = InventoryConfig.create(
            excluded_directory_names=excluded,
            capture_readable_content=not args.no_content_capture,
            structured_probe_max_bytes=args.structured_probe_max_bytes,
            java_probe_max_bytes=args.java_probe_max_bytes,
            build_config_probe_max_bytes=args.build_config_probe_max_bytes,
            sql_probe_max_bytes=args.sql_probe_max_bytes,
        )
        repository_selectors = list(args.repository or [])
        if args.repository_list is not None:
            repository_selectors.extend(load_repository_selectors(args.repository_list))
        common = dict(
            output=args.output,
            scope_id=args.scope_id,
            config=config,
            overwrite=args.force,
            resume=args.resume,
            repository_selectors=tuple(repository_selectors),
            auth_mode=args.auth_mode,
            token_env=args.token_env,
            username_env=args.username_env,
            password_env=args.password_env,
            api_base_path=args.api_base_path,
            ca_bundle=args.ca_bundle,
            insecure_skip_tls_verify=args.insecure_skip_tls_verify,
            timeout_seconds=args.timeout_seconds,
            page_size=args.page_size,
            max_repositories=args.repository_limit,
            clone_retries=args.clone_retries,
            clone_timeout_seconds=args.clone_timeout_seconds,
            work_dir=args.work_dir,
            build_reduced=args.reduce,
        )
        if args.command == "build-bitbucket-project":
            result = build_bitbucket_project_inventory(
                project_url=args.bitbucket_project_url,
                **common,
            )
        else:
            result = build_sourcecontrol_organization_inventory(
                api_url=args.sourcecontrol_api_url,
                organization=args.organization,
                **common,
            )
        summary = dict(result.manifest.get("summary") or {})
        print(json.dumps({
            "status": result.manifest["status"],
            "format": result.manifest["format"],
            "batch_id": result.manifest["batch_id"],
            "output": str(result.output),
            **summary,
        }, ensure_ascii=False, sort_keys=True))
        if result.manifest["status"] == "completed":
            return 0
        if result.manifest["status"] == "partial":
            return 2
        return 1
    if args.command == "verify":
        result = verify_inventory_directory(args.inventory)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "reduce":
        result = build_reduced_inventory(
            inventory=args.inventory,
            output=args.output,
            overwrite=args.force,
        )
        print(json.dumps({
            "status": "reduced",
            "format": result["format"],
            "reduced_inventory_id": result["reduced_inventory_id"],
            "semantic_fingerprint": result["semantic_fingerprint"],
            **dict(result.get("counts") or {}),
        }, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "verify-reduced":
        result = verify_reduced_inventory(args.reduced)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "classify-concepts":
        result = build_concept_candidates(args.reduced, output=args.output, overwrite=args.force)
        print(json.dumps({"status": "classified", "format": result["format"], "repository_id": result["repository_id"], "concept_candidate_id": result["concept_candidate_id"], "semantic_fingerprint": result["semantic_fingerprint"], "path": str(args.output)}, ensure_ascii=False, sort_keys=True))
        return 0

    if args.command == "verify-concept-candidates":
        print(json.dumps(verify_concept_candidates(args.candidates), ensure_ascii=False, sort_keys=True))
        return 0

    if args.command == "build-concept-index":
        result = build_concept_index(args.batch, output=args.output)
        print(json.dumps({"status":"indexed","format":result["format"],"index_id":result["index_id"],"semantic_fingerprint":result["semantic_fingerprint"],"repository_count":result["summary"]["repository_count"],"path":str(args.output or (args.batch / "repository_concept_index.json"))}, ensure_ascii=False, sort_keys=True))
        return 0

    if args.command == "verify-concept-index":
        print(json.dumps(verify_concept_index(args.index), ensure_ascii=False, sort_keys=True))
        return 0

    if args.command == "candidates":
        index = load_concept_index(args.index)
        rows = query_concept_index(index, concept=args.concept, min_strength=args.min_strength, mechanism=args.mechanism)
        print(json.dumps({"status":"ok","format":index["format"],"concept":args.concept,"min_strength":args.min_strength,"mechanism":args.mechanism,"match_count":len(rows),"repositories":rows}, ensure_ascii=False, sort_keys=True))
        return 0

    if args.command == "export-selected":
        evidence_ids = list(args.evidence_id or [])
        if args.request is not None:
            evidence_ids.extend(load_export_request(args.request))
        result = build_selected_source_export(
            inventory=args.inventory,
            output=args.output,
            evidence_ids=evidence_ids,
            reduced=args.reduced,
            overwrite=args.force,
            context_lines=args.context_lines,
            file_scope_max_bytes=args.file_scope_max_bytes,
        )
        print(json.dumps({
            "status": result["status"],
            "format": result["format"],
            "export_id": result["export_id"],
            **dict(result.get("counts") or {}),
        }, ensure_ascii=False, sort_keys=True))
        return 0
    result = verify_selected_source_export(args.export)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
