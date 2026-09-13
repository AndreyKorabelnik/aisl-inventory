# Repository Inventory

Current implementation: **0.1.0a38**. Canonical observed contract: **`repository-inventory/v7`**. Derived reduction contract: **`repository-inventory-reduced/v3`**.

Operational shortcuts:

- `repository-inventory build ... --reduce` builds the canonical v7 artifact and `repository_inventory_reduced.json` in one invocation.
- `repository-inventory build-bitbucket-project ... --reduce` and `build-sourcecontrol-organization ... --reduce` do the same independently for every successful repository; resume can rebuild a missing/stale Reduced artifact from a verified reused v7 artifact without a new checkout.
- `repository-inventory export-selected <inventory> --reduced <reduced.json> ...` emits `repository-selected-source-export/v3`: Inventory-owned evidence bindings plus bounded exact source fragments from the immutable CAS. Exact spans receive context; file-localized evidence is exported only when the whole file is within the explicit byte limit.

Repository Inventory owns source acquisition, deterministic inventory extraction, repository-local structural compaction/reduction, verification, and selected source evidence export. It does not own portfolio/catalog-relative novelty classification.


## Repository-local HTTP half-wires

`0.1.0a31` adds a bounded `http_boundary_observation` projection to the existing `repository-inventory/v7` structural-family surface. It reuses the current OpenAPI/YAML/JSON and Tree-sitter Java parser passes and the existing Reduced v3 reducer. The projection records repository-local inbound/outbound HTTP method/path facts, exact config-backed path candidates when mechanically resolvable, and explicit ambiguity/unresolved status. It never searches another repository or builds cross-repository edges. One-hop Java wrappers are followed only when the wrapper directly forwards a path parameter into a recognized `RestTemplate.exchange` call; no whole-program call graph is built.

`0.1.0a32` extends only the Java inbound side with exact repository-local composition of `static final String` route constants and an explicit `annotatedService(prefix, service, ...)` registration. Only mechanically exact literals/references/`+` concatenations are resolved; multiple service prefixes remain ambiguity and dynamic expressions are not guessed. The same Tree-sitter Java parse pass is reused.

`0.1.0a33` enriches the same `http_boundary_observation` with mechanically bound request/response payload identities and shallow top-level local shapes. OpenAPI schema refs/fields come from the existing structured-document parse; Java payload identities/fields come from the same Tree-sitter pass and bounded direct/one-hop HTTP binding already used by the half-wire projection. Missing repository-local declarations remain explicit `unavailable_external_declaration` shape gaps. No recursive data-model extraction, new parser pass, cross-repository matcher, or second Reduced path is added.

`0.1.0a38` extends that same HTTP owner for a bounded standard JDK `HttpClient` / `HttpRequest.Builder` idiom. A boundary requires a mechanically associated builder plus `send(request.build(), ...)` in one method. Exact getter-chain config suffixes and exact Jackson request/response payload identities are retained when observed. Repository-local full URL/stub values are not promoted to production route paths; unresolved deployment identity stays explicit.

## Repository-local Kafka half-wires

`0.1.0a34` adds a bounded `kafka_boundary_observation` projection to the existing `repository-inventory/v7` structural-family surface. It reuses the same Tree-sitter Java parse pass and the existing Reduced v3 reducer. Consume observations mechanically bind exact repository-local `KafkaListener`/consumer configuration chains to a literal or config-key topic identity and a locally provable payload type. Publish observations mechanically bind repository-local `KafkaTemplate.send` / `ProducerRecord` calls to literal or exact config-key topic identities and locally provable payload types. Dynamic or ambiguous topic expressions remain unresolved; a config key is never promoted to a deployment topic value. Inventory does not search another repository, build Kafka edges, or add a second parser/reducer path.

`0.1.0a35` closes exact literal listener precedence: a mechanically observed `@KafkaListener(topics = "...")` literal is published directly as an observed literal topic before config-chain inference. Spring `#{...}` / `${...}` expressions remain unresolved. This is a projection-correctness fix inside the same Kafka owner; no new execution/parser path is introduced.


## Platform V SourceControl remote acquisition (0.1.0a36)

SourceControl organization discovery is an adapter inside the existing remote-acquisition owner. It does not introduce a second clone/build path. Discovery calls `GET /api/v1/orgs/{organization}/repos`, follows SourceControl page semantics, consumes the API-provided `clone_url` and `default_branch`, and then hands the same `RemoteRepositorySet` to the existing sequential remote batch execution used by Bitbucket.

Example for the observed `PPRB_CPC` organization while reusing existing credential environment names:

```bash
repository-inventory build-sourcecontrol-organization \
  --sourcecontrol-api-url https://api.sc-ci.sber.ru \
  --organization PPRB_CPC \
  --auth-mode basic \
  --username-env BITBUCKET_USERNAME \
  --password-env BITBUCKET_PASSWORD \
  --output outputs/kpk-repo-inventory \
  --reduce \
  --force \
  --repository-limit 10
```

Repositories observed by SourceControl as `empty=true` are not sent into Git checkout because they have no commit to inventory. The remote batch manifest records an explicit `sourcecontrol_repository_empty` discovery diagnostic. Archived repositories remain buildable unless the caller explicitly selects a different subset; Inventory does not invent an archive policy.

### Remote authentication hardening (0.1.0a37)

`--auth-mode auto` no longer silently chooses Bearer when both token and Basic credentials are configured. Ambiguous or incomplete credentials fail before network access. For the demonstrated SourceControl installation, credentials are currently stored in the existing `BITBUCKET_USERNAME` / `BITBUCKET_PASSWORD` environment variables, so the SourceControl command must name those variables explicitly as shown above. Inventory does not silently alias provider credential names. HTTP discovery errors include the provider, HTTP status and effective auth mode without including secret values.

## Runtime operations

- `build` / `build-bitbucket-project` / `build-sourcecontrol-organization` → `repository-inventory/v7`
- `verify`
- `reduce` / `verify-reduced` → `repository-inventory-reduced/v3`
- `export-selected` / `verify-selected-export` → exact referenced source evidence when captured content is available; `--reduced` exports the bounded union of structural-shape and observed-identity representative FamilyFileEvidence from an exactly linked Reduced artifact

`reduce` reads only an accepted v7 artifact; it does not reread repository source. Reduced Inventory separates name-independent structural shapes from concrete observed named identities. It preserves aggregate counts, bounded structural signals/provenance, diagnostics, and deterministic representative evidence for both layers. Full duplicate family membership remains only in canonical v7.

Catalog construction and catalog-relative Discovery are intentionally outside this module.

## Repository preflight signals
`repository_inventory.preflight_signals.collect_preflight_signals()` consumes only a verified `repository-inventory-reduced/v3` payload and emits atomic `repository-inventory-preflight-signals/v1` observations. These signals are routing evidence for the six repository concepts (`data_model`, `system_interaction`, `data_flow`, `persistence`, `workflow`, `reference_data`); they are not concept classifications or knowledge claims. Dependency-only and naming-only signals remain explicitly marked as such.

## Repository concept candidates
Build a routing-only candidate artifact offline from Reduced v3:

```bash
repository-inventory classify-concepts repository_inventory_reduced.json --output repository_concept_candidates.json
repository-inventory verify-concept-candidates repository_concept_candidates.json
```

The six candidates are `data_model`, `system_interaction`, `data_flow`, `persistence`, `workflow`, and `reference_data`. Strength describes observed routing evidence only. `not_observed` never proves absence.

### Automatic candidates and batch lookup
With `--reduce`, local and remote batch builds also produce `repository_concept_candidates.json`. Remote batches additionally produce `repository_concept_index.json`.

```bash
repository-inventory candidates ./ucp-repo-inventory --concept data_model --min-strength moderate
repository-inventory candidates ./ucp-repo-inventory --concept system_interaction --mechanism kafka
```

The batch index is a lookup projection only; it never recalculates classification.
