# Repository Inventory architecture — 0.1.0a35 / v7 + Reduced v3

```text
Repository source
  -> file scan + mature parser owners
  -> transient parser-local exact events/counts
  -> compact aggregation
       RepositoryFile
       StructuralFamily
       StructuralMember (only when structural identity requires it)
       FamilyFileEvidence (one family×file row)
       FileObservation (bounded parser-state/file footprint)
       diagnostics + probe_status
  -> repository-inventory/v7
       + optional immutable file CAS
```

The aggregation boundary is intentional: Inventory describes the repository structural landscape and inspectable exemplars; Core remains the owner of exhaustive exact facts and semantic/cross-source relationships. Repo-local frequency, dispersion and salience are metadata and are excluded from structural identity used across repositories.

Repository-local reduction consumes verified v7 without rereading source; downstream offline comparison consumes Reduced v3 rather than raw v7. Java parser mechanics plus package/import,
per-node annotation, method-invocation and object-creation syntax are delegated to
neutral `source-syntax-primitives==0.1.0a7`. Generic SQL tokenizer/parser invocation is
also delegated to its explicit `recover_all` policy. This does not change the product boundary:
shared code owns source-intrinsic syntax mechanics, while Inventory owns stable IDs,
claims, fingerprints, structural families and repository aggregation.
Protocol Buffers, HOCON and Java Properties parser invocation is also delegated to the
shared owner; Inventory projects those mechanical results into unchanged v7 observations.
Raw Maven POM XML projection is likewise shared; coordinate completeness, diagnostics,
IDs, claims and aggregation remain Inventory-owned.

## Remote repository acquisition boundary (0.1.0a15)

Remote acquisition is source transport, not repository knowledge. Inventory may discover repositories from a Bitbucket Data Center project or a Platform V SourceControl organization and create one shallow temporary checkout at a time. Provider-specific discovery adapters both produce the same `RemoteRepositorySet`; the checkout is only an ephemeral input to the unchanged repository-scoped `repository-inventory/v7` builder and must be removed after use. Remote-scope discovery must not create a synthetic multi-repository inventory or semantic relationship between repositories.


## Remote Bitbucket project batch orchestration (0.1.0a16)

The standalone Inventory CLI owns remote-scope orchestration. Bitbucket project discovery and SourceControl organization discovery are provider adapters inside the same acquisition owner. Repositories are processed by one shared sequential execution path with at most one temporary checkout, and every successful repository produces its own independent `repository-inventory/v7` artifact.

The persisted `repository-inventory-remote-batch-manifest/v6` is operational metadata only: it records source selection, resolved commits, per-repository status, output locations, failures and cleanup policy. It is not a multi-repository Inventory contract, does not merge structural families across repositories and does not infer cross-repository relationships.

Content capture stays enabled by default so `repository-selected-source-export/v3` remains usable after each checkout is deleted. The export resolves Inventory-owned evidence IDs to compact source/provenance catalogs and bounded fragment blobs; it does not copy the source CAS files wholesale. A per-repository failure is explicit and does not prevent later repositories from being inventoried; the overall batch status becomes `partial`.


## Remote batch reproducibility (0.1.0a17)

Remote project orchestration remains Inventory-owned transport/runtime behavior and does not alter `repository-inventory/v7`. Repository selection is explicit/deterministic. Resume uses remote commit identity plus verified existing Inventory state; it never infers source sameness from names or timestamps and never reuses an unverified artifact.

## Live Bitbucket acquisition hardening (0.1.0a18)

Initial and forced builds do not depend on `git ls-remote`; they acquire the repository directly and record the checked-out commit. Resume uses `git ls-remote` only to decide whether a verified existing artifact may be reused. If commit resolution fails, reuse is explicitly forbidden and Inventory performs a fresh checkout/build instead of silently reusing stale knowledge or failing before acquisition. Rebuilds are produced in a staging directory and replace an older verified v7 artifact only after the new artifact passes verification. Git failures retain bounded diagnostics with configured credentials redacted.
## Historical Derived reduction boundary (0.1.0a20; superseded by Reduced v2/v3)

`repository-inventory/v7` remains the only canonical observed repository state. The former `repository-inventory-reduced/v1` is a deterministic consumer-neutral projection produced from v7 only; it is not a second parser path or source of truth. The reducer owns normalized exact/similarity structural identities, family membership mapping, variant-state representatives and diagnostic compaction. It never reads repository source/CAS and has no Benchmark Miner dependency.

Exact/similarity grouping is mechanical, not semantic equivalence. Every source family remains addressable through `family_membership`, and each similarity group retains representative evidence for every distinct exact representation. Structural discovery/catalog classification is a separate later phase.
## Reduced transfer boundary (0.1.0a23)

Inventory owns repository-local mechanical reduction and selected source evidence export. Catalog construction and catalog-relative Discovery are downstream/offline concerns and are not Inventory runtime operations. Reduced Inventory is self-contained structural evidence with provenance/diagnostics and stable evidence references; downstream consumers must not require repository source to compare it with an external baseline.

## Transfer reduction v2 boundary (0.1.0a25)

`repository-inventory-reduced/v2` is a deliberately deduplicated transferable product, not a lossless index of v7. Exact duplicate `StructuralFamily` rows are collapsed to aggregate source-family/occurrence/file/evidence/member counts and one deterministic `FamilyFileEvidence` representative per exact structural identity. The product does not contain `family_membership` or complete duplicate source-family/evidence lists. Full addressability remains only in canonical `repository-inventory/v7`.

Similarity groups store one similarity basis plus exact IDs and aggregate counts; exact rows do not repeat the similarity basis. `export-selected --reduced` follows only the bounded exact representatives. Catalog/Discovery remain downstream/offline and no benchmark ranking policy is moved into Inventory.


## Structural shape / named identity transfer boundary (0.1.0a27)

`repository-inventory-reduced/v3` replaces v2 and separates structural form from concrete observed identity. Exact and similarity structural IDs are computed from normalized name-independent family/member descriptors; concrete `family_label` values and concrete string facets masked out of structural identity are retained in a separate `observed_identities` section with aggregate counts and deterministic FamilyFileEvidence provenance. Opaque member signatures that can encode names are not used as structural identity.

This is a mechanical representation boundary, not semantic equivalence. Two named observations may share one structural shape without implying the same API, dependency, annotation, XML meaning, or business meaning. Conversely, a new concrete name alone is not evidence of a new structural concept. Canonical `repository-inventory/v7` remains the lossless observed source of truth.

`export-selected --reduced` follows the bounded union of structural-shape and named-identity representatives so transferred concrete names remain grounded in source evidence. Catalog/Discovery remains downstream/offline.


## Evidence Pack and build-time reduction (0.1.0a27)

`repository-selected-source-export/v3` is the transfer artifact paired with Reduced v3. Reduced stores `representative_evidence_id` references only. Evidence Pack resolves those references against the verified immutable Inventory CAS into: source-file metadata, deduplicated provenance records, evidence bindings, and exact-byte fragment blobs. Localized spans are expanded by a configured context line count and overlapping contexts from the same source file are merged. Evidence observed only at `localization_kind=file` may use the whole file as its observed scope only below the configured byte ceiling; larger files remain explicit unresolved diagnostics. No source parsing or re-localization happens during export.

Local `build` and both remote batch commands accept `--reduce`. This calls the same canonical reducer used by the standalone `reduce` command after the v7 artifact verifies. The shared remote resume path may create or repair Reduced from an unchanged verified inventory without acquiring the repository again.


## Repository preflight routing
After Reduced v3, Inventory may derive routing-only `repository-inventory-concept-candidates/v1` for exactly six concepts. This step reads Reduced only. A batch `repository-inventory-concept-index/v1` merely aggregates already-classified candidate strengths/mechanisms and never reclassifies repositories. `not_observed` is not an absence claim. Cross-repository novelty remains Benchmark Miner ownership.

## HTTP half-wire projection (0.1.0a31)

Inventory owns only repository-local transport boundary description. `http_boundary_observation` is a mechanical projection over already parsed source material and persists through the existing `StructuralFamily -> FamilyFileEvidence` contract. OpenAPI/YAML/JSON and Tree-sitter Java remain the parser owners. The projection may follow one method wrapper hop only when the wrapper directly forwards a formal path parameter into a recognized HTTP client call. It does not build a general call graph.

Exact configuration resolution is limited to an explicit Java `@Value` binding plus exact scalar declarations already parsed from repository-local structured files. Multiple declarations remain ambiguity. Cross-repository edge/island construction is forbidden and belongs to an external consumer.

`0.1.0a32` keeps the same parser pass and adds only exact Java inbound route composition from repository-local `static final String` expressions plus an explicit `annotatedService` service-type registration prefix. This is bounded syntax projection, not general symbol resolution: unsupported expressions stay unresolved and multiple exact registration prefixes stay ambiguous.

`0.1.0a33` enriches the same boundary descriptor with mechanically bound request/response payload identity and shallow repository-local fields. OpenAPI wire schemas remain authoritative when they are present; Java contributes only exact repository-local payload observations/provenance. Known external types retain an explicit unavailable-shape gap. The enrichment reuses the existing parser passes and Reduced v3 path and does not introduce recursive DTO modelling or data-flow analysis.


## Repository-local Kafka half-wire projection (0.1.0a34)

Kafka half-wires reuse the existing Java parser owner and aggregation path:

```text
existing Tree-sitter Java pass
  -> transient repository-local Kafka syntax/config facts
  -> bounded mechanical kafka_boundary_observation projection
  -> existing StructuralFamily / FamilyFileEvidence
  -> repository-inventory/v7
  -> existing Reduced v3 reducer
```

The projection does not parse Java a second time and does not introduce a Kafka-specific source scanner, reducer, registry, or cross-repository matcher. Topic identities are typed as literal/config-key/unresolved according to exact repository-local evidence; config keys are not treated as deployment values. Consume binding is limited to mechanically provable listener/config chains, while publish binding is limited to mechanically provable local `KafkaTemplate.send` / `ProducerRecord` patterns. Dynamic or ambiguous expressions remain gaps.

The external consumer owns any matching of independently produced repository half-wires. I10 real-corpus acceptance therefore proves 5/5 selected AT900 half-wires in both Full and Reduced, but deliberately makes no cross-repository edge claim without a supplied counterpart repository.


## Exact Kafka listener literal precedence (0.1.0a35)

When the existing Tree-sitter Java observation for `@KafkaListener` contains an exact literal topic, `kafka_boundary_observation` publishes that literal directly as an `observed_fact` before attempting constructor/config-chain inference. Spring expression forms (`#{...}`, `${...}`) are not literals and continue through the existing bounded inference/unresolved path. This changes projection precedence only; parser ownership, scan count, Reduced v3 ownership and downstream matching boundaries are unchanged.
