# Next development plan after 0.1.0a38

1. Keep Repository Topology `0.1.0a8` unchanged unless new evidence makes the JDK outbound transport identity mechanically matchable.
2. On the next real Inventory run, inspect how many newly emitted JDK HTTP half-wires are `resolved`, `ambiguous_declared_config`, and `unresolved_property`; use those observed counts to decide whether another generic transport idiom is justified.
3. For UCP↔KPK specifically, look first for authoritative deployment/config evidence that binds `http.ucp.cpcGet.url` to a route/endpoint. Do not infer `/cpcGet` from the property name, DTO names, localhost/stub values, or repository names.
4. If such evidence exists within the accepted Inventory input boundary, extend the existing config/path resolution owner minimally and rerun Topology a8 before changing its matcher.
5. Do not automatically add WebClient, RestTemplate variants, Feign, gRPC, attribute lineage, business-specific UCP/KPK matching, or a second execution path. Each requires its own observed gap and acceptance evidence.
