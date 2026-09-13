# Test status — 0.1.0a38

## Fresh affected acceptance

- `tests/test_http_half_wires.py`: **16/16 PASS** in the reconstructed Tree-sitter harness. This includes the new positive JDK builder/send Full→Reduced test and a negative builder-without-send test.
- Architecture/version targeted checks: **2/2 PASS** (`test_java_parser_dependency_is_owned_by_shared_syntax_package`, `test_version_and_contract_self_description_are_consistent`).
- Modified production modules compile: `java_probe.py`, `http_half_wires.py` — PASS.
- Real supplied KPK production corpus, bounded JDK-call subset: **8/8 projected** through the same existing HTTP owner (1 common + 5 EFS + 2 twallet).
- Real EFS UCP Gold Full/Reduced: `POST`, `http.ucp.cpcGet.url`, request `BulkGetIndividualRequest`, response `BulkGetIndividualResponse`; Full `path_status=unresolved_property`; Reduced preserves config family label and both payload identity facets.

## Harness status

The canonical dependency `source-syntax-primitives==0.1.0a7` is not supplied in this sandbox. Fresh affected tests were therefore executed with an out-of-source harness adapter backed by the supplied exact `tree_sitter==0.26.0` and `tree_sitter_java==0.23.5` wheels. The harness is test-only, is not copied into Source canonical, and production code still imports only `source_syntax_primitives`.

A full suite is not claimed as freshly replayed under the canonical first-party dependency. This is a harness limitation, not converted into a product PASS. Existing a37 historical acceptance remains supporting evidence only for byte-unchanged surfaces.

## Packaging

Affected package wheel `repository_inventory-0.1.0a38-py3-none-any.whl` built and installed with `--no-deps` into a clean target; neutral-cwd import reports `0.1.0a38` / `repository-inventory/v7`. Wheel SHA-256: `fbda6c58861a062123703a1c0f67761893d8ae536b30a0f72a5150c2a6c888cc`. AISL Release canonical is unchanged; no four-delivery Release checkpoint is triggered by this standalone Inventory source step.
