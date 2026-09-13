# Limitations — 0.1.0a38

- Reduced Inventory is structural evidence, not business meaning or framework capability. `not_observed`/missing evidence is not proof of absence.
- Reduced v3 is intentionally lossy with respect to complete duplicate source-family membership; canonical Full v7 retains source-family/evidence addressability.
- Concrete names remain observed identities, not semantic equivalence claims.

## JDK HTTP half-wire limits

- JDK HTTP support is deliberately bounded to a same-method `HttpRequest.Builder` initialized by `HttpRequest.newBuilder()`, exactly one recognized HTTP method call, exactly one `.uri(...)`, and a mechanically associated `send(request.build(), ...)`. Builder-only construction is not a boundary.
- Config tracing is limited to exact repository-local symbol aliases, `URI.create(x)`, and zero-argument JavaBean getter chains. Multiple assignments are not chosen heuristically.
- Config-key suffix matching uses exact structured scalar keys already produced by the existing structured parser pass. A config key is not a deployment endpoint.
- Full URL scalar values are not reinterpreted as route paths. Localhost/stub values therefore remain `unresolved_property` unless separate accepted evidence proves a route identity.
- Request/response payload enrichment is limited to exact same-method Jackson `writeValueAsString(...)` and `readValue(response.body(), X.class)` evidence. XML/helper serialization and general data-flow are not inferred.
- Payload shape remains shallow and repository-local. External DTO declarations retain identity with `unavailable_external_declaration`; fields are never borrowed from another repository.
- No cross-repository matching occurs in Inventory.

## Other existing limits

- Kafka observations remain repository-local half-wires; config keys are not promoted to deployment topics.
- Selected-source export can return only captured exact bytes and bounded Inventory-owned provenance.
- SourceControl remote support remains the already-demonstrated organization-discovery surface; no new remote behavior is introduced by a38.
