# Current state — Repository Inventory 0.1.0a38

Canonical observed contract remains `repository-inventory/v7`; Reduced remains `repository-inventory-reduced/v3`.

## HTTP half-wire state

The existing HTTP owner now covers the bounded standard JDK client idiom observed in the supplied KPK repositories:

`HttpRequest.newBuilder()` → HTTP method → `.uri(...)` → `HttpClient.send(request.build(), ...)`.

The implementation reuses the single Tree-sitter Java observation pass. It does not add a parser, repository scan, matcher, compatibility path, or second HTTP projection owner.

Repository-local exact getter aliases can contribute a config-key suffix. Exact Jackson serialization/deserialization can contribute request/response payload identities. Deployment meaning is not inferred. A full URL scalar such as `http://localhost:8080/stub/ucp` is preserved as config evidence but does not become a production path.

## Fresh acceptance

Three independent supplied KPK repositories contain eight bounded production JDK HTTP calls and all eight project through the same owner:

- `cpc-common-gateway-get-card-by-dpan`: 1/1;
- `cpc-efs-gateway-get-trustedcards-by-client-id`: 5/5;
- `cpc-twallet-gateway-get-cards-by-client-id`: 2/2.

The EFS UCP call in `UcpBatchCpcProfileProvider.java` produces `POST`, config identity `http.ucp.cpcGet.url`, request `BulkGetIndividualRequest`, response `BulkGetIndividualResponse`, and `path_status=unresolved_property`. Full v7 and Reduced v3 preserve the config identity and both payload identities.

## Topology decision

Repository Topology `0.1.0a8` remains the current topology Source canonical and is byte-unchanged. Its existing matcher accepts exact resolved paths and bounded ambiguous path candidates; an outbound `unresolved_property` is intentionally diagnosed as `http_outbound_identity_not_matchable`.

Therefore this architectural step ends in Inventory. The supplied repository-local config does not justify changing Topology or inferring `/cpcGet` from a config-key name, localhost/stub value, DTO names, or repository names. Additional deployment/config evidence would be required before a transport edge can be promoted.

Attribute-level UCP→KPK lineage remains parked until transport identity evidence justifies the next step.
