# TODO03 Repository Inventory half-wire acceptance

Status: **ACCEPTED / TODO03 COMPLETE FOR CURRENT CORPUS**

## Consumer goal
Two repositories are inventoried independently. An external consumer receives only their Full or Reduced Inventory artifacts and can correlate compatible technical HTTP/Kafka half-wires. Inventory itself never searches another repository or creates cross-repository edges/islands.

## HTTP proof — real paired repositories
- Four supplied real applications were manually golded before Inventory changes.
- External artifact-only reconstruction: 3/3 frozen repository relations from both Full and Reduced (2 exact + 1 explicitly probable/ambiguous), with no invented exact relation on the corpus.
- Request payload identity readiness: 6/6; response identity: 5/6.
- Expected field-correlatable relations: 2/2.
- External payload declaration gaps remain explicit instead of borrowing fields from another repository.

## Kafka proof — real repository-local boundaries
Existing System Description Manual Gold for AT900 `client-profile` remains the gold owner. Frozen subset: 3 consume + 2 publish boundaries.
- Full: 5/5.
- Reduced: 5/5 when all linked exact representations are considered.
- Each case preserves direction, config-key topic identity and payload identity.
- AT900 I12 non-regression: 12.42 s / 434228 KB; accepted a34 reference 12.56 s / 433308 KB.

## Kafka exact cross-repository proof
Two independent synthetic repositories are built separately:
- producer: `KafkaTemplate.send("customer-events", CustomerEvent)`;
- consumer: `@KafkaListener(topics = "customer-events")` with exact local `CustomerEvent`.

An acceptance-only external matcher reads only completed artifacts. Full and Reduced both match exactly on:
- direction publish/consume;
- literal topic `customer-events`;
- payload `CustomerEvent`;
- shallow fields `customerId`, `tags`.

The matcher is test/acceptance code only and is not part of Inventory runtime.

## Evidence discipline
- Exact listener literal: observed fact, confidence 1.0.
- Config-key topic identity: strongly supported repository-local inference; never promoted to deployment value.
- Spring/dynamic expressions remain unresolved.
- Ambiguity is preserved rather than guessed.

## Architecture / performance
No new parser owner, repository scan, reducer, call graph, Kafka framework model, cross-repository matcher, edge builder or island builder exists. HTTP/Kafka half-wires reuse the existing Tree-sitter/OpenAPI owners and `StructuralFamily -> FamilyFileEvidence -> Reduced v3` path.

## Regression
- I12 affected suite before final version bump: 35/35 PASS.
- Full suite: 133 PASS / 1 pre-existing environment-only Java Properties/HOCON parser-owner failure; the same failure reproduces on untouched `0.1.0a34`.
- Package/install closure is required before Source freeze and is recorded in `TEST_STATUS.md`.

## Stop decision
TODO03 is closed for the currently supplied acceptance corpus. Do not broaden Inventory half-wire support until a new real consumer/corpus demonstrates a missing repository-local fact. Cross-repository correlation stays consumer-owned.
