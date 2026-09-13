# Changes

## 0.1.0a38 — bounded JDK HttpClient outbound half-wires
- Extends the existing repository-local HTTP half-wire owner for the observed `java.net.http.HttpClient` / `HttpRequest.Builder` idiom; no second Java parser, source scan, HTTP owner, matcher, or reducer is introduced.
- Reuses the existing Tree-sitter Java pass to require a same-method `HttpRequest.newBuilder()` builder plus `httpClient.send(request.build(), ...)` before emitting an outbound boundary.
- Mechanically observes the HTTP method, URI expression, exact getter-chain config-key suffix when uniquely traceable, and exact Jackson `writeValueAsString` / `readValue(..., X.class)` payload identities when present.
- Full URL repository-local config values such as localhost/stub URLs are retained as config evidence and remain `unresolved_property`; they are not promoted to a production route.
- Real three-repository KPK acceptance emits 8/8 bounded production JDK HTTP calls (1 common, 5 EFS, 2 twallet). The UCP EFS call retains `http.ucp.cpcGet.url`, request `BulkGetIndividualRequest`, and response `BulkGetIndividualResponse` in Full and Reduced.
- Repository Topology `0.1.0a8` is intentionally unchanged: its existing policy classifies `unresolved_property` outbound HTTP identity as `http_outbound_identity_not_matchable`, so no justified exact/probable edge exists from the supplied repository-local config alone.

## 0.1.0a37 — remote authentication hardening
- Fixes ambiguous `auto` authentication: if both token and complete Basic credentials are configured, Inventory now fails locally and requires explicit `--auth-mode token` or `--auth-mode basic` instead of silently preferring Bearer.
- Rejects incomplete Basic credential pairs in `auto` before any network request.
- Explicit `--auth-mode basic` deterministically ignores a simultaneously configured token for both REST discovery and Git acquisition.
- Bitbucket and SourceControl HTTP discovery failures now preserve provider, HTTP status, request URL, and effective auth mode in the raised diagnostic without exposing credentials.
- SourceControl credential environment names remain explicit; Inventory does not silently reuse Bitbucket-named credentials. The demonstrated PPRB_CPC run therefore uses `--username-env BITBUCKET_USERNAME --password-env BITBUCKET_PASSWORD`.
- No repository discovery, clone, build, reduce, HTTP/Kafka half-wire, or knowledge contract semantics changed.

## 0.1.0a36 — Platform V SourceControl remote discovery
- Adds SourceControl organization discovery through the existing remote-acquisition owner: `GET /api/v1/orgs/{organization}/repos` with page/limit pagination, `Link`, and `X-Total-Count`.
- Adds `build-sourcecontrol-organization`; SourceControl and Bitbucket discovery both feed the same `RemoteRepositorySet` and the same sequential clone/build/reduce/resume execution path.
- Uses SourceControl-provided `clone_url` and `default_branch`; no clone URL synthesis and no second checkout owner are introduced.
- `empty=true` repositories are explicitly skipped at discovery with operational diagnostics because they have no commit to inventory; archived repositories are not silently filtered.
- Remote operational contracts advance to `repository-inventory-remote-batch-manifest/v6` and `repository-inventory-remote-repository-result/v6`; provider-specific `project_id` is replaced by provider-neutral `scope_key`.
- Canonical repository contracts remain `repository-inventory/v7` and `repository-inventory-reduced/v3`; HTTP/Kafka extraction is unchanged.

## 0.1.0a35 — exact Kafka listener literal precedence
- Fixes `kafka_boundary_observation` consume projection so a mechanically observed exact `@KafkaListener(topics = "...")` literal is published before constructor/config-chain inference.
- Exact literal listener topics are `observed_fact` with confidence 1.0; Spring `#{...}` / `${...}` expressions remain unresolved and config-key chains retain their existing inference semantics.
- Adds a two-independent-repository acceptance-only proof that Full and Reduced artifacts can be externally correlated on literal topic, payload identity and shallow fields without source reads or an Inventory-owned matcher.
- Real AT900 frozen subset remains 5/5 in Full and Reduced; no additional repository scan/parser pass is introduced.

## 0.1.0a34 — repository-local Kafka half-wires
- Adds `kafka_boundary_observation` through the existing v7 structural-family envelope and existing Reduced v3 projection; no new public contract is introduced.
- Reuses the same Tree-sitter Java pass; no second parser or repository scan.
- Mechanically binds selected repository-local Kafka consume/publish boundaries to literal/config-key/unresolved topic identities and locally provable payload identities.
- Keeps config keys distinct from deployment topic values and preserves dynamic/ambiguous topics without guessing.
- Real AT900 blind acceptance from Full and Reduced reaches 5/5 selected cases (3 consume + 2 publish); no cross-repository matching is performed.
- Same-environment performance versus frozen a33 is about +5.4% wall-clock and +0.24% peak RSS on the 1048-file AT900 corpus.
- Stops short of general Spring Kafka modeling, recursive event-schema extraction, or cross-repository edge construction.

## 0.1.0a33 — shallow HTTP payload binding
- Enriches the existing `http_boundary_observation`; no new runtime/public contract is introduced.
- Reuses the existing OpenAPI/structured-document parse and the same Tree-sitter Java pass used by HTTP topology.
- Records mechanically bound request/response payload identities and shallow top-level fields when declarations are local.
- Preserves a known external payload type with explicit `unavailable_external_declaration` rather than borrowing or guessing fields.
- Reuses the existing Reduced v3 projection; no second reducer or payload-specific reduction path is added.
- Four-repository blind acceptance from Full and Reduced reaches 6/6 request identities, 5/6 response identities, 2/2 expected field-correlatable relations, and preserves the expected external-shape gap.
- Stops short of recursive DTO/data-flow analysis because the remaining Java-only response identity is not required for repository relation reconstruction.

## 0.1.0a32 — exact registered Java inbound HTTP routes
- Extends the existing `http_boundary_observation` projection only for exact Java inbound routes.
- Reuses the same Tree-sitter Java parse pass; no second repository scan/parser pass is added.
- Resolves repository-local `static final String` literals/references/`+` concatenations only when mechanically exact.
- Composes route annotations with explicit `annotatedService(prefix, service, ...)` registration prefixes through exact service-type matching.
- Multiple exact service registrations remain `ambiguous_service_registration`; dynamic expressions are withheld rather than guessed.
- Preserves OpenAPI/Java collapse into one structural boundary identity and existing Reduced v3 projection.
- Real four-repository blind acceptance improves exact Full/Reduced edge reconstruction from 1/3 to 2/3 without introducing false exact edges.

## 0.1.0a31 — repository-local HTTP half-wires
- Adds `http_boundary_observation` through the existing v7 structural-family envelope and existing Reduced v3 projection.
- Reuses OpenAPI/YAML/JSON and Tree-sitter Java parser passes; no second parser or source scan.
- Projects exact OpenAPI/literal Java inbound routes and direct/one-hop `RestTemplate.exchange` outbound paths.
- Resolves exact `@Value` path properties only from parser-owned repository-local structured scalar declarations; conflicting declarations remain `ambiguous_declared_config`.
- Parser/source mechanics are provenance rather than separate boundary identities, so the same inbound route observed in OpenAPI and Java collapses to one half-wire with multiple evidence files.
- Adds real-corpus blind/performance acceptance against four supplied applications.


## 0.1.0a30 — build integration and batch concept index
- `build --reduce` now emits `repository_concept_candidates.json` using the same Reduced-only classifier.
- Bitbucket `--reduce` builds/verifies per-repository candidates and `repository_concept_index.json`; remote manifest/result contracts advance to v5.
- Resume can rebuild missing/stale candidates and the batch index from verified Reduced artifacts without repository checkout.
- Fixed an existing resume status mismatch that caused valid Reduced artifacts to be unnecessarily rebuilt (`verified` vs `pass`).
- Added `build-concept-index`, `verify-concept-index`, and `candidates` query CLI; the index only aggregates existing candidate classifications.

## 0.1.0a29 — concept candidate classifier
- Added `repository-inventory-concept-candidates/v1` and classifier `repository-inventory-concept-classifier/v1`.
- Classification consumes Reduced v3 only and emits exactly six routing concepts with `strong/moderate/weak/not_observed` signal strength.
- Strength is based on independent signal types, not raw occurrence quantity. Dependency-only and namespace-naming-only evidence cannot become strong by repetition.
- `not_observed` always carries `absence_proven=false`; no concept is confirmed by preflight.
- Added `classify-concepts` and `verify-concept-candidates` CLI operations.

## 0.1.0a28 — Reduced-v3 preflight signal vocabulary
- Added a single versioned `repository-inventory-preflight-signals/v1` extractor over Reduced v3 only.
- Atomic signals cover the six restored routing concepts without assigning concept strength yet.
- Signals preserve bounded representative evidence IDs and distinguish dependency-only/naming evidence from direct observed framework idioms.
- No raw-v7 fallback, source read, novelty claim, or confirmed concept claim was added.

## 0.1.0a27 — Evidence Pack + build-time Reduced

- Replaces `repository-selected-source-export/v3` with `repository-selected-source-export/v3`: Reduced evidence references resolve to provenance plus bounded exact-byte source fragments instead of copied full CAS files.
- Exact observed spans receive configurable context (default ±10 lines); overlapping contexts in one file are merged deterministically.
- File-localized evidence is exported only within an explicit byte ceiling (default 64 KiB); larger file-scope evidence produces a diagnostic rather than guessed localization or hidden full-file fallback.
- Adds `--reduce` to both `build` and `build-bitbucket-project`, reusing the same `repository-inventory-reduced/v3` reducer as the standalone `reduce` command.
- Bitbucket batch operational contracts advance to `repository-inventory-remote-batch-manifest/v4` / `repository-inventory-remote-repository-result/v4` to expose requested Reduced outputs and resume actions.

# Changes

## 0.1.0a26 — structural shape / named identity split

- Replaces `repository-inventory-reduced/v2` with breaking `repository-inventory-reduced/v3`.
- Structural exact/similarity identities no longer include concrete `family_label` names.
- Member-based structural identity uses normalized member descriptors instead of opaque name-bearing structure signatures.
- Adds `observed_identities`: concrete labels and masked descriptor/member string facets with aggregate counts and deterministic FamilyFileEvidence provenance.
- `export-selected --reduced` exports both structural-shape and observed-identity representatives.
- Catalog/Discovery remain outside Repository Inventory ownership.

## 0.1.0a25 — transferable Reduced v2

- Replaces `repository-inventory-reduced/v1` with breaking `repository-inventory-reduced/v2`.
- Removes lossless `family_membership`, complete `source_family_ids`, complete duplicate evidence/member ID lists, and duplicated similarity basis from exact rows.
- Preserves exact/similarity structural identities, aggregate source counts, diagnostics/probe status, one deterministic representative FamilyFileEvidence plus its representative source-family ID/observation per exact state, and compact aggregate salience/boundary summaries needed for downstream transparent ranking without restoring duplicate membership.
- Changes reducer algorithm to `repository-inventory-reducer/v4`; full duplicate membership remains only in canonical `repository-inventory/v7`.
- Writes Reduced v2 as compact canonical JSON rather than indented transport JSON.
- `export-selected --reduced` now exports only exact-state representative evidence instead of all source FamilyFileEvidence rows referenced by the original lossless mapping.

## 0.1.0a24 — Reduced-driven source evidence export

- Extends the existing `export-selected` mechanism with `--reduced`.
- Validates the Reduced contract and requires exact source inventory ID, semantic fingerprint, repository ID and format linkage before export.
- Deterministically exports the union of all `family_file_evidence_ids` referenced by Reduced, optionally unioned with explicit request/evidence IDs.
- Reuses the existing exact-CAS exporter; no second source transport mechanism, source reread, snippet reconstruction or workspace fallback is introduced.


## 0.1.0a23 — Catalog/Discovery ownership removal

- Removes `build-catalog`, `verify-catalog`, `discover`, and `verify-discovery` from Repository Inventory.
- Removes Inventory-owned concept-catalog/Discovery implementation and schemas.
- Keeps `build`, `reduce`, verification, and selected-source export semantics unchanged.
- `repository-inventory-reduced/v1` remains the transferable compact structural evidence boundary.

## 0.1.0a22 — explicit structural catalog and discovery

- Adds `repository-inventory-concept-catalog/v1`, built deterministically from one or more accepted `repository-inventory-reduced/v1` baselines.
- Adds `repository-inventory-discovery/v1`, generated only from Reduced Inventory plus an explicit versioned/fingerprinted catalog.
- Supports mechanical classifications: `known`, `variant_of_known`, `candidate_novel`, `unknown`, `unsupported`, `ambiguous`.
- Defines `known` as similarity + exact representation state present in baseline; `variant_of_known` as known similarity with a new exact state; `candidate_novel` only as structural similarity absent from the supplied catalog.
- Adds explicit catalog states `recognized`, `unsupported`, `unknown`; overlapping catalog concepts remain visible as `ambiguous` rather than being silently resolved.
- Preserves source family/evidence representatives, diagnostics and non-attribution gaps without inventing diagnostic-to-family relations.
- Adds `build-catalog`, `verify-catalog`, `discover`, and `verify-discovery` CLI commands and deterministic contract tests.
- No default/hidden catalog is used; novelty cannot be emitted without a valid explicit catalog fingerprint.

## 0.1.0a20 — Inventory-owned deterministic reduction

- Adds `repository-inventory-reduced/v1` as a derived mechanical projection of self-contained `repository-inventory/v7`.
- Moves the generic exact/similarity structural identity rules needed for reduction into Inventory ownership without adding any Miner dependency or repository/source read.
- Preserves every source `StructuralFamily` through `family_membership`, all `FamilyFileEvidence` IDs, structural-member IDs, exact occurrence/file cardinality, and one deterministic representative per exact variation state.
- Keeps the exact/similarity fingerprint identities compatible with Benchmark Miner 0.36.3 so the later consumer switch does not require identity drift.
- Deduplicates repeated diagnostic identities in the derived product while preserving source row count and conflicting variants.
- Adds `reduce` and `verify-reduced` CLI commands plus deterministic/fidelity tests.
- `repository-inventory/v7` remains unchanged and canonical; structural discovery/catalog is not introduced in this phase.

## 0.1.0a14 — shared Maven POM syntax ownership

- delegates raw Maven property/dependency XML projection to
  `source-syntax-primitives==0.1.0a7`;
- removes direct ElementTree parsing and duplicate dependency traversal;
- retains raw version/scope/type/classifier expressions, declaration context,
  coordinate completeness diagnostics, IDs, claims and v7 aggregation;
- preserves exact pre/post `repository-inventory/v7` families and diagnostics.

## 0.1.0a13 — shared Proto/HOCON/Properties ownership

- delegates Protocol Buffers, HOCON, and Java Properties parser invocation to
  `source-syntax-primitives==0.1.0a6`;
- preserves `repository-inventory/v7` identities, counts, and product projections;
- removes direct parser dependencies and private HOCON AST interpretation.

## 0.1.0a12 — shared SQL recover-all ownership

- delegates SQLGlot dialect/tokenizer/parser construction to
  `source-syntax-primitives==0.1.0a5` with explicit `recover_all` policy;
- removes the direct Inventory SQLGlot dependency and four direct parser calls;
- retains exact parser errors, `Command`/unparsed handling, SQL footprint families,
  stable IDs, v7 identity/counts, frequency, dispersion and salience;
- preserves exact 13-case parser and Inventory SQL product snapshots.

## 0.1.0a11 — S4.0 SQL ownership audit addendum

- identified four direct Inventory SQLGlot dialect/tokenizer/parser calls;
- compared current recover-all output with Core across thirteen cases;
- found four behavior-changing cases that prevent blind reuse of Core defaults;
- approved explicit neutral recovery policy as a prerequisite for S4.3;
- changed audit documentation only; v7, package bytes, Miner, D5 and LNS remain
  unchanged.

## 0.1.0a11 — S3 freeze acceptance addendum

- froze package/import, annotation, method-invocation and object-creation shared Java
  ownership after a residual-duplication audit;
- proved that remaining Inventory node checks are dispatch into shared projectors and
  retain only Inventory-owned IDs, claims, families and aggregation;
- retained exact S3.1/S3.2 product parity and byte-identical v7 identity owners;
- kept package version, `repository-inventory/v7`, Miner `0.36.3`, D5 and the accepted
  LNS replay unchanged.

## 0.1.0a11 — shared call and object-creation syntax

- Method name, receiver shape, argument count and exact span now come from
  `source-syntax-primitives==0.1.0a3`.
- Object-creation syntactic type, argument count and exact span use the same shared owner.
- Duplicate Inventory receiver-shape and argument-count helpers were removed.
- `repository-inventory/v7`, qualified-call filtering, stable IDs, family identity and
  aggregation remain unchanged.

## 0.1.0a10 — shared package/import and annotation syntax

- Package/import and per-node annotation mechanical projection now comes from `source-syntax-primitives==0.1.0a2`.
- Duplicate Inventory package/import parsing and annotation argument-shape traversal were removed.
- `repository-inventory/v7`, stable IDs, claims, fingerprints, families and aggregation remain unchanged.

## 0.1.0a9 — shared Java syntax primitives

- Repository Inventory no longer constructs/configures Tree-sitter Java directly.
- Java parser construction, parser invocation, AST traversal, grammar field lookup, UTF-8 byte slicing and Point normalization are delegated to `source-syntax-primitives==0.1.0a1`.
- Inventory product semantics remain unchanged: `repository-inventory/v7`, logical structural families, exact frequency/dispersion/salience and FamilyFileEvidence ownership remain local.
- Direct dependency on `tree-sitter` / `tree-sitter-java` is replaced by the shared syntax package.

## 0.1.0a8 — compact Inventory v7

- Replaced `repository-inventory/v6` with breaking `repository-inventory/v7`.
- Removed persisted global SourceOccurrence/object-occurrence graph and probe-specific raw observation sections.
- Added logical `StructuralFamily`, compact `StructuralMember`, exact `FamilyFileEvidence` (one row per family×file) and bounded `FileObservation`.
- Preserved exact total occurrence frequency, file/source-tree dispersion, parser provenance/status and explicit diagnostics while bounding provenance to one deterministic exemplar per family×file.
- Excluded repository-local frequency/dispersion/parser-state fields from structural identity.
- Added strict v7 verification of family/file references, exact count sums, dispersion, diagnostics and SHA/CAS integrity.
- Replaced selected-source export/request v1 SourceOccurrence addressing with v2 FamilyFileEvidence addressing.
- Retained parser-local exact occurrence events only as transient implementation material; they do not cross the Inventory product boundary.

## 0.1.0a7 — I4 reassessment / selected source evidence transport

- Cancelled mandatory pre-Miner Research Export v2 after field-level and industrial-volume reassessment.
- Kept direct `repository-inventory/v6 -> Benchmark Miner` as the canonical mining path.
- Added optional `repository-selected-source-export/v1` post-selection transport.
- Added generic `repository-selected-source-export-request/v1` containing only explicit upstream SourceOccurrence IDs.
- Added exact CAS SHA validation, blob deduplication, SHA256SUMS verification and explicit partial diagnostics with no repository/workspace fallback.
- Added CLI `export-selected` and `verify-selected-export`.
- Inventory does not import Miner and does not interpret selection/rarity/novelty semantics.

# Changes

## 0.1.0a6 — I3c SQL footprint + repository-local structural salience

- added SQLGlot 30.13.0 as the single standalone SQL footprint parser owner;
- added SQL file/top-level statement footprint with exact parser-owned expression types and source provenance;
- added SQLGlot recovery handling: recovered AST remains observable but forces explicit `partial` state; unsupported `Command` fallback remains explicit;
- deliberately does not guess SQL dialect and does not contain regexp/manual SQL parsing fallback;
- added sanitized SQL parser diagnostics without embedding source SQL snippets;
- hardened exceptional SQL parser diagnostics to remove exception-message text entirely; only exception type and parser-selection state remain;
- added repository-level SQL statement families with exact occurrence/file/source-tree dispersion;
- introduced shared repository-local structural salience for SQL, structured, Java call and file-extension families;
- explicitly marks salience as non-novelty; cross-repository rarity/clustering/selection remains Miner-owned;
- promoted source file-extension primitives into exact repository-local families with file/SourceOccurrence membership and source-tree dispersion; unknown extensions remain unclassified and semantically uninterpreted;
- bumped Inventory semantic configuration to `repository-inventory-configuration/v4` with `sql_probe_max_bytes`;
- validated 55/55 targeted tests plus compile/import;
- validated datamart: 490-file build+verify PASS, 306 SQL files, 23 complete / 283 partial / 0 failed, 1,402 parser entries, 1,304 structurally parsed, 98 unsupported/unparsed, 14 statement families;
- validated generic `.plp` structural-landscape behavior via synthetic CLI gate because raw LNS is unavailable in the current runtime; no LNS industrial PASS is claimed.
- closed the packaging-only `jproperties==2.1.2` artifact gap: official wheel accepted, clean offline dependency resolution + `pip check` PASS, 55/55 tests PASS in clean dependency context, and installed-wheel datamart build/verify reproduced the accepted semantic fingerprint exactly.

Main AISL, D5 release deliveries and Benchmark Miner were not changed.

## 0.1.0a2 — I3a generic structured/source-format observations

- added framework-agnostic source-format observations for OpenAPI, Protocol Buffers, XSD and JSON Schema;
- OpenAPI requires an exact top-level `openapi` or `swagger` marker in parsed/scanned JSON/YAML;
- Proto requires `.proto` plus non-comment syntax/declaration evidence; comment-only markers remain diagnostics;
- XSD requires XML Schema root/namespace evidence;
- JSON Schema uses explicit `$schema` URI as observed fact and only sufficiently strong structural markers as inference; weak markers remain ambiguity diagnostics;
- added deterministic JSON/YAML key-family and document-shape observations;
- added XML root/namespace/element/attribute observations;
- added explicit parse/scan diagnostics and per-probe `complete` / `partial` / `failed` / `not_applicable` states;
- separated transient probe reads from optional source-content capture so export configuration cannot change observation capability;
- added semantic `structured_probe_max_bytes` configuration with explicit partial-state diagnostic on skipped oversized files;
- compacted repeated structured-member occurrences into exact repository-file/key observations with occurrence/path counts and path fingerprints; no sampling/truncation fallback;
- fixed YAML scanning for sequence-of-object paths, multiline flow collections, quoted multiline scalars and nested multiline plain scalars;
- added negative controls preventing `.avsc` => JSON Schema and comment-only `.proto` => Proto false classification;
- preserved zero AISL runtime dependencies and byte-identical framework-present/framework-absent output.

Main AISL, D5 release deliveries and Benchmark Miner were not changed.

## 0.1.0a1 — I2 standalone foundation

- established independent `repository-inventory` project boundary;
- introduced semantics-breaking `repository-inventory/v6` contract;
- added deterministic file/source snapshot and semantic fingerprint;
- added SourceOccurrence/object-occurrence primitives;
- added local content-addressed readable-source capture and verifier;
- added explicit probe states/diagnostics;
- added executable AISL dependency/isolation and determinism tests;
- intentionally did not change main AISL or Benchmark Miner.

## 0.1.0a5 — I3b known-language/build/config observations

- added Java package/import observations with exact provenance;
- added Java annotation observations and mechanically observed arguments without semantic interpretation;
- added qualified method invocation and constructor families with exact frequency/file/source-tree dispersion;
- added explicit Java type-argument invocation support and nested constructor observation;
- added exact line/column SourceOccurrences for Java structural evidence;
- added Maven dependency declarations without effective-model/property resolution;
- observes Gradle source/build-system presence but intentionally does not parse dependency details without an independent mature Gradle parser owner; publishes an explicit `gradle_dependency_detail_not_evaluated` gap instead of regexp/manual fallback;
- added `.properties`, `.conf` and `.toml` config-key observations while withholding raw config values;
- added independent semantic size limits/partial diagnostics for Java and build/config probes;
- preserved zero AISL runtime dependencies;
- validated Java package/import/annotation/qualified-call/constructor counts against Tree-sitter Java on UCP API, UCP TSA-v4, AT900 and gateway with zero delta.

Main AISL, D5 release deliveries and Benchmark Miner were not changed.

## 0.1.0a5 acceptance addendum — Java Point compatibility

- closed the real Gateway Tree-sitter native crash without fallback by using the `Point` sequence coordinate API;
- rolled back unaccepted diagnostic-era Java receiver/argument/constructor semantic experiments;
- added a regression guard forbidding named `Point.row` / `Point.column` access;
- accepted 44/44 targeted tests and real Gateway build/verify;
- accepted Java-only industrial differential on Gateway, UCP API, UCP TSA-v4 and AT900 with no occurrence loss;
- proved direct standalone `repository-inventory/v6` consumption by Benchmark Miner 0.36 Level-1 without Prepared Runtime/source reads;
- recorded the separate Miner four-repository CLI JSON serialization size/performance limitation.

### I4/I5 acceptance closure

- accepted the Research Export simplification after industrial measurement;
- accepted five-repository post-selection exports: 10,351/10,351 occurrence IDs resolved, 1,389/2,809 files retained;
- accepted homogeneous a7 direct Inventory -> Miner available-corpus gate;
- field-level audit confirmed all 54,871 intended family observations are projected, including SQL statement families and source primitives;
- confirmed 124,717 projected evidence links with zero missing SourceOccurrence targets;
- retained raw LNS replay as an explicit validation gap rather than substituting historical results.

### 0.1.0a8 acceptance addendum — full LNS scale replay

- closed the previously external raw-LNS archive-reader gate using the supplied `tar.xz` corpus;
- processed all **22,800** repository files with production Inventory 0.1.0a8 in 16 deterministic bounded shards;
- production verifier: **16/16 PASS**;
- repository-global canonical result: **123,956 logical families / 704,413 family×file evidence / 2,462,417 physical occurrences**;
- selected post-Miner CAS verification: **PASS, 0 missing, 0 SHA mismatch**;
- no product code or semantic contract change was required for this acceptance closure.

## 0.1.0a15 — remote repository acquisition foundation

- Added framework-independent Bitbucket Data Center repository discovery to standalone Repository Inventory.
- Added shallow temporary Git checkout with retry, token/basic/credential-helper/SSH auth semantics and guaranteed context cleanup.
- HTTP clone URLs are sanitized before they leave acquisition discovery; embedded credentials are not persisted.
- No Runner/Core/KLC dependency was introduced; `repository-inventory/v7` remains unchanged.
- This step intentionally does not add batch inventory CLI yet. The next step wires project discovery + sequential temporary checkout to per-repository v7 builds.
- Known migration debt: the older Runner-owned acquisition implementation remains outside this package because this work item is constrained to modify Inventory only. It is not a second target architecture and must be removed/repointed by the separate legacy cleanup after Inventory owns the complete remote path.


## 0.1.0a17 — standalone Bitbucket project batch CLI

- Added `repository-inventory build-bitbucket-project` using the 0.1.0a15 Inventory-owned remote acquisition foundation.
- Processes Bitbucket Data Center repositories sequentially with one temporary shallow checkout at a time.
- Produces one independent `repository-inventory/v7` artifact per successful repository; no synthetic multi-repository Inventory is created.
- Keeps source-content CAS capture enabled by default so explicit `repository-selected-source-export/v3` remains available after checkout deletion.
- Adds operational `repository-inventory-remote-batch-manifest/v1` with resolved commit, per-repository status, explicit failure stage/code/message and cleanup policy.
- Continues after repository-level acquisition/build/verification failure; overall status is `partial` when at least one repository succeeds and at least one fails.
- CLI returns non-zero for `partial`/`failed`, preventing a partially completed batch from being reported as PASS.
- Supports `--repository-limit` (`--max-repositories` alias), existing auth/TLS/retry controls, optional work directory and the same Inventory probe/content configuration as local `build`.
- Existing local `repository-inventory build` and `repository-inventory/v7` semantics are unchanged.
- No Runner/Core/KLC/KCP code was changed.


## 0.1.0a17 — Step 3 reproducible selection and safe resume

- `build-bitbucket-project` supports repeatable `--repository` and `--repository-list`.
- Selection matches repository id, Bitbucket slug/name, or Bitbucket numeric id; missing/ambiguous selectors are explicit diagnostics.
- Unselected discovery is deterministic by normalized repository id before `--repository-limit`.
- Added `git ls-remote` commit resolution without source checkout.
- Added `--resume`: reuse requires exact resolved commit + repository identity/scope + Inventory config fingerprint + verified v7 artifact.
- Changed commit/config/identity or invalid existing artifact rebuilds that repository.
- Added mutually-exclusive `--force` full rebuild.
- Remote batch operational contracts move to v2; `repository-inventory/v7` itself is unchanged.

## 0.1.0a19 — Step 5 noexec-safe Git HTTP authentication

- Removed generated executable `git-askpass.sh` from explicit HTTP token/basic acquisition.
- Basic Git auth now uses the same explicit HTTP `Authorization: Basic ...` semantics as REST discovery via Git `http.extraHeader`.
- Token + username uses Basic username/token; token without username uses Bearer header.
- Explicit HTTP auth disables ambient credential helpers and terminal prompts; `/bin/false` is used only as a non-interactive fallback and no executable file is created under the temporary checkout root.
- Preserves remote batch manifest v3, resume-only commit resolution, verified rebuild staging, and repository-inventory/v7 unchanged.

## 0.1.0a18 — Step 4 live Bitbucket acquisition hardening

- Initial/forced Bitbucket project builds no longer require `git ls-remote`; checkout commit is recorded from the actual shallow checkout.
- `git ls-remote` is now resume-only and is used solely to decide whether reuse is safe.
- Resume commit-resolution failure explicitly forbids reuse and triggers rebuild instead of failing the repository before acquisition.
- Rebuilds use a verified staging artifact and replace an older v7 only after verification succeeds.
- Git `ls-remote`/clone failures preserve bounded diagnostics with configured credentials redacted.
- Remote operational contracts advance to `repository-inventory-remote-batch-manifest/v3` and `repository-inventory-remote-repository-result/v3`; `repository-inventory/v7` is unchanged.
- No Core, Runner, KLC, Miner or other delivery code changed.


## 0.1.0a22 — industrial representative-policy fidelity
- `repository-inventory-reducer/v2` preserves minimal per-family structural observation signals in Reduced v1: Inventory-owned structural salience, positive numeric descriptor evidence, and explicit outside-analyzer-frontier boolean.
- These fields do not participate in exact/similarity identity and do not compute benchmark ranking; they allow downstream Miner policy to remain reproducible after raw-v7 ownership removal.
