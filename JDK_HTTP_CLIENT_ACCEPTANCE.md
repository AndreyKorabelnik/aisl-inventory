# JDK HttpClient acceptance — Repository Inventory 0.1.0a38

Date: 2026-09-08

## Scope

Acceptance is bounded to production Java files in the three supplied KPK repositories that contain `HttpRequest.newBuilder`, plus repository-local structured config consumed by the existing structured parser pass. Test sources are excluded from the industrial count.

No repository-specific names are present in the production extraction logic. The same existing `java_probe -> http_half_wires -> StructuralFamily -> Reduced v3` path is used for every case.

## Industrial result

| Repository | Observed bounded JDK calls | Projected outbound half-wires |
| --- | ---: | ---: |
| `cpc-common-gateway-get-card-by-dpan` | 1 | 1 |
| `cpc-efs-gateway-get-trustedcards-by-client-id` | 5 | 5 |
| `cpc-twallet-gateway-get-cards-by-client-id` | 2 | 2 |
| **Total** | **8** | **8** |

All eight observations are `POST` and use the same `java_jdk_http_client_send` basis.

Observed config identities include:

- `http.masterSystem.crdw.url`;
- `http.cardBankAcctInqOnlineService.srvbankacctinq.url`;
- `http.cardOnlineService.srvgetpprbcardlist.url`;
- `http.getPermissions.cpcPermissions.url`;
- `http.uddk.uddkPermissions.url`;
- `http.ucp.cpcGet.url`;
- `http.cardBankAcctInqSyncer.srvbankacctinq.url`;
- `http.cardSyncer.srvgetpprbcardlist.url`.

## UCP Gold observation

Real source file:

`cpc-efs-gateway-get-trustedcards-by-client-id/src/main/java/com/sbt/cpc/efs/gateway/provider/UcpBatchCpcProfileProvider.java`

Full v7 HTTP descriptor from the targeted real-source Gold build:

- direction: `outbound`;
- protocol: `http`;
- method: `POST`;
- config identity / property key: `http.ucp.cpcGet.url`;
- path: none;
- path status: `unresolved_property`;
- request payload: `BulkGetIndividualRequest`;
- response payload: `BulkGetIndividualResponse`;
- request/response shape: `unavailable_external_declaration` because those DTO declarations are external to this repository subset.

Reduced v3 preserves:

- family label `http.ucp.cpcGet.url`;
- observed string facet `BulkGetIndividualRequest`;
- observed string facet `BulkGetIndividualResponse`.

The repository-local scalar is a localhost/stub full URL. It is deliberately not converted into a production route. No claim that `/cpcGet` is the deployment endpoint is made by Inventory.

## Automated affected tests

- `tests/test_http_half_wires.py`: 16/16 PASS in the reconstructed Tree-sitter harness.
- New positive test proves JDK builder/send + exact config getter-chain + Jackson request/response payload identities survive Full -> Reduced.
- New negative test proves `HttpRequest.newBuilder()` without a matching `send(request.build(), ...)` is not emitted as a boundary.
- Architecture/version targeted checks: 2/2 PASS.

The first-party package `source-syntax-primitives==0.1.0a7` is not present in this sandbox. The fresh parser-dependent acceptance therefore uses an out-of-source harness adapter over the supplied exact `tree_sitter==0.26.0` and `tree_sitter_java==0.23.5` wheels. The harness is not part of Source canonical.

## Topology consequence

Repository Topology `0.1.0a8` is unchanged. Its current matching policy only treats resolved HTTP paths and bounded ambiguous path candidates as matchable. `unresolved_property` is diagnosed as `http_outbound_identity_not_matchable`.

Therefore no Topology change is accepted from this step. Additional authoritative route/deployment evidence is required before a UCP↔KPK transport edge can be promoted.
