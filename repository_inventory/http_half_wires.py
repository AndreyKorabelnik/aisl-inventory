from __future__ import annotations

from collections import defaultdict
from typing import Any

from .canonical import stable_id

_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE"})


def _literal_string(expression: str) -> str | None:
    text = str(expression).strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"' and "\\" not in text:
        return text[1:-1]
    return None


def _http_method(expression: str) -> str | None:
    token = str(expression).strip().rsplit(".", 1)[-1]
    return token if token in _HTTP_METHODS else None


def _symbol(expression: str) -> str | None:
    text = str(expression).strip()
    if text.startswith("this."):
        text = text[5:]
    if not text or not (text[0].isalpha() or text[0] in "_$"):
        return None
    if not all(ch.isalnum() or ch in "_$" for ch in text):
        return None
    return text


def _resolve_path(
    *,
    expression: str,
    repository_relative_path: str,
    property_bindings: dict[tuple[str, str], set[str]],
    scalar_property_values: dict[str, list[dict[str, Any]]],
    explicit_property_keys: list[str] | None = None,
) -> dict[str, Any]:
    literal = _literal_string(expression)
    if literal is not None:
        return {
            "path_status": "resolved",
            "path": literal,
            "path_candidates": [literal],
            "path_expression": expression,
            "property_keys": [],
            "resolution_basis": "java_string_literal",
        }

    symbol = _symbol(expression)
    property_keys = sorted(set(explicit_property_keys or []) | property_bindings.get((repository_relative_path, symbol or ""), set()))
    if not property_keys:
        return {
            "path_status": "unresolved_expression",
            "path": None,
            "path_candidates": [],
            "path_expression": expression,
            "property_keys": [],
            "resolution_basis": "unresolved_java_expression",
        }

    candidates: set[str] = set()
    for property_key in property_keys:
        for row in scalar_property_values.get(property_key, []):
            value = row.get("value")
            if isinstance(value, str) and value.startswith("/"):
                candidates.add(value)
    ordered = sorted(candidates)
    if len(ordered) == 1:
        return {
            "path_status": "resolved",
            "path": ordered[0],
            "path_candidates": ordered,
            "path_expression": expression,
            "property_keys": property_keys,
            "resolution_basis": "java_value_binding_plus_exact_structured_scalar",
        }
    if len(ordered) > 1:
        return {
            "path_status": "ambiguous_declared_config",
            "path": None,
            "path_candidates": ordered,
            "path_expression": expression,
            "property_keys": property_keys,
            "resolution_basis": "java_value_binding_with_multiple_declared_structured_scalars",
        }
    return {
        "path_status": "unresolved_property",
        "path": None,
        "path_candidates": [],
        "path_expression": expression,
        "property_keys": property_keys,
        "resolution_basis": "java_value_binding_without_repository_local_path_scalar",
    }




def _payload_type_identity(type_text: Any) -> str | None:
    text=str(type_text or '').strip().replace('java.lang.','').replace('java.util.','')
    if not text:
        return None
    for wrapper in ('HttpEntity','RequestEntity','ResponseEntity','ParameterizedTypeReference'):
        prefix=wrapper+'<'
        if text.startswith(prefix) and text.endswith('>'):
            return text[len(prefix):-1].strip() or None
    return text

def _payload_descriptor(identity: str | None, declarations: dict[str,list[dict[str,Any]]], *, prefix: str) -> dict[str,Any]:
    if not identity:
        return {}
    out={f'{prefix}_payload':identity}
    rows=declarations.get(identity) or []
    variants={_expression_key(row.get('fields') or []):row.get('fields') or [] for row in rows}
    if len(variants)==1:
        fields=next(iter(variants.values()))
        out[f'{prefix}_shape_status']='available_local_declaration'
        if fields:
            out[f'{prefix}_fields']=fields
    else:
        out[f'{prefix}_shape_status']='unavailable_external_declaration' if not rows else 'ambiguous_local_declaration'
    return out

def _java_type_declarations(java_facts: dict[str,list[dict[str,Any]]]) -> dict[str,list[dict[str,Any]]]:
    out=defaultdict(list)
    for row in java_facts.get('type_declarations') or []:
        name=str(row.get('type_name') or '')
        if name:
            out[name].append(row)
    return out

def align_exact_http_boundary_payloads(rows: list[dict[str,Any]]) -> list[dict[str,Any]]:
    """Align payload descriptors for evidence rows that already share one exact boundary.

    This performs no matching beyond the existing repository-local boundary identity
    (direction, method, resolved path). If exactly one non-empty payload descriptor is
    observed for a boundary, it is copied to sibling provenance rows so the compact
    model keeps one family. Conflicting payload descriptors remain separate.
    """
    groups=defaultdict(list)
    for row in rows:
        path=row.get('path')
        if row.get('path_status')=='resolved' and isinstance(path,str):
            groups[(row.get('direction'),row.get('method'),path)].append(row)
    payload_keys=('request_payload','request_shape_status','request_fields','response_payload','response_shape_status','response_fields')
    for siblings in groups.values():
        openapi_descriptors={}
        descriptors={}
        for row in siblings:
            descriptor={key:row[key] for key in payload_keys if key in row}
            if descriptor:
                descriptors[_expression_key(descriptor)]=descriptor
                if row.get('syntax_family')=='openapi':
                    openapi_descriptors[_expression_key(descriptor)]=descriptor
        # When an exact OpenAPI operation exists for the same repository-local route,
        # its wire schema is the transport payload truth. Java evidence remains provenance.
        chosen=None
        if len(openapi_descriptors)==1:
            chosen=next(iter(openapi_descriptors.values()))
        elif len(descriptors)==1:
            chosen=next(iter(descriptors.values()))
        if chosen is None:
            continue
        for row in siblings:
            for key in payload_keys:
                row.pop(key,None)
            row.update(chosen)
    return rows


def _property_keys_for_suffix(
    suffix: str | None,
    scalar_property_values: dict[str, list[dict[str, Any]]],
) -> list[str]:
    text=str(suffix or "").strip(".")
    if not text:
        return []
    return sorted(
        key for key in scalar_property_values
        if key == text or key.endswith("." + text)
    )

def project_outbound_http_boundaries(
    *,
    repository_id: str,
    java_facts: dict[str, list[dict[str, Any]]],
    scalar_property_values: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Project repository-local outbound HTTP half-wires from already parsed Java/config facts.

    This function owns no parser and performs no source scan. It accepts only facts produced by
    the existing Java and structured parser passes. One-hop wrapper use is bounded to a method
    whose body directly contains a recognized ``exchange(pathParam, METHOD, ...)`` call.
    """
    bindings: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in java_facts.get("property_bindings") or []:
        path = str(row.get("repository_relative_path") or "")
        symbol = str(row.get("symbol") or "")
        key = str(row.get("property_key") or "")
        if path and symbol and key:
            bindings[(path, symbol)].add(key)

    summaries: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in java_facts.get("wrapper_summaries") or []:
        name = str(row.get("callable_name") or "")
        count = int(row.get("argument_count") or 0)
        method = str(row.get("method") or "")
        if name and count > 0 and method in _HTTP_METHODS:
            summaries[(name, count)].append(row)
    declarations=_java_type_declarations(java_facts)

    wrapper_forwarding_parameters = {
        (str(row.get("repository_relative_path") or ""), str(row.get("path_parameter_name") or ""))
        for row in java_facts.get("wrapper_summaries") or []
        if str(row.get("path_parameter_name") or "")
    }

    rows: list[dict[str, Any]] = []
    for call in java_facts.get("jdk_http_calls") or []:
        method=str(call.get("method") or "")
        path_expression=str(call.get("uri_expression") or "")
        repository_relative_path=str(call.get("repository_relative_path") or "")
        if method not in _HTTP_METHODS or not path_expression or not repository_relative_path:
            continue
        explicit_keys=_property_keys_for_suffix(call.get("config_key_suffix"),scalar_property_values)
        resolved=_resolve_path(
            expression=path_expression,
            repository_relative_path=repository_relative_path,
            property_bindings=bindings,
            scalar_property_values=scalar_property_values,
            explicit_property_keys=explicit_keys,
        )
        classification="ambiguity" if resolved["path_status"] == "ambiguous_declared_config" else "observed_fact"
        confidence=0.5 if classification == "ambiguity" else 1.0
        identity=resolved["path"] or (resolved["property_keys"][0] if len(resolved["property_keys"])==1 else path_expression)
        rows.append({
            "family_id":stable_id(
                "http_boundary_observation",repository_id,"java_jdk_http_client",repository_relative_path,
                call.get("source_occurrence_id"),method,identity,resolved["path_status"],resolved["path_candidates"],
            ),
            "repository_id":repository_id,
            "family_kind":"http_boundary_observation",
            "syntax_family":"java",
            "key":identity,
            "direction":"outbound",
            "protocol":"http",
            "method":method,
            **resolved,
            **_payload_descriptor(_payload_type_identity(call.get("request_payload")),declarations,prefix="request"),
            **_payload_descriptor(_payload_type_identity(call.get("response_payload")),declarations,prefix="response"),
            "source_kind":"java_jdk_http_client_send",
            "repository_relative_path":repository_relative_path,
            "source_occurrence_id":call.get("source_occurrence_id"),
            "occurrence_count":1,
            "claim":{"classification":classification,"confidence":confidence,"basis":resolved["resolution_basis"]},
            "probe":{"probe_id":"http_boundary_observations","probe_version":"1"},
            "basis":{
                "kind":"java_jdk_http_client_send",
                "resolution_basis":resolved["resolution_basis"],
                "config_key_suffix":call.get("config_key_suffix"),
                "semantic_meaning_inferred":False,
                "cross_repository_matching_performed":False,
            },
        })
    for invocation in java_facts.get("invocations") or []:
        name = str(invocation.get("callable_name") or "")
        args = [str(value) for value in invocation.get("arguments") or []]
        method: str | None = None
        path_expression: str | None = None
        source_kind: str | None = None
        request_payload: str | None = None
        response_payload: str | None = None
        arg_types=[value if isinstance(value,str) else None for value in invocation.get("argument_declared_types") or []]

        if name == "exchange" and len(args) >= 2:
            # A direct exchange whose path is the wrapper's own path parameter is an
            # internal forwarding implementation. The repository-local boundary is
            # emitted at the concrete wrapper call-site instead.
            if (str(invocation.get("repository_relative_path") or ""), _symbol(args[0]) or "") in wrapper_forwarding_parameters:
                continue
            method = _http_method(args[1])
            path_expression = args[0]
            source_kind = "java_direct_resttemplate_exchange"
            if len(arg_types) >= 3:
                request_payload=_payload_type_identity(arg_types[2])
            if len(args) >= 4:
                if args[3].endswith('.class'):
                    response_payload=args[3][:-6].rsplit('.',1)[-1]
                elif len(arg_types) >= 4:
                    response_payload=_payload_type_identity(arg_types[3])
        else:
            candidates = summaries.get((name, len(args))) or []
            if len(candidates) == 1:
                summary=candidates[0]
                index=int(summary.get('path_parameter_index') or 0)
                method=str(summary.get('method') or '')
                if 0 <= index < len(args):
                    path_expression = args[index]
                    source_kind = "java_one_hop_http_wrapper_call"
                    body_index=summary.get('body_parameter_index')
                    if isinstance(body_index,int) and 0 <= body_index < len(arg_types):
                        request_payload=_payload_type_identity(arg_types[body_index])
                    fixed_response=str(summary.get('fixed_response_payload') or '')
                    if fixed_response:
                        response_payload=fixed_response
                    response_index=summary.get('response_parameter_index')
                    if response_payload is None and isinstance(response_index,int) and 0 <= response_index < len(arg_types):
                        response_payload=_payload_type_identity(arg_types[response_index])

        if method is None or path_expression is None or source_kind is None:
            continue

        repository_relative_path = str(invocation.get("repository_relative_path") or "")
        resolved = _resolve_path(
            expression=path_expression,
            repository_relative_path=repository_relative_path,
            property_bindings=bindings,
            scalar_property_values=scalar_property_values,
        )
        classification = "ambiguity" if resolved["path_status"] == "ambiguous_declared_config" else "observed_fact"
        confidence = 0.5 if classification == "ambiguity" else 1.0
        identity = resolved["path"] or (resolved["property_keys"][0] if len(resolved["property_keys"]) == 1 else path_expression)
        rows.append({
            "family_id": stable_id(
                "http_boundary_observation",
                repository_id,
                "java_outbound",
                repository_relative_path,
                invocation.get("source_occurrence_id"),
                method,
                identity,
                resolved["path_status"],
                resolved["path_candidates"],
            ),
            "repository_id": repository_id,
            "family_kind": "http_boundary_observation",
            "syntax_family": "java",
            "key": identity,
            "direction": "outbound",
            "protocol": "http",
            "method": method,
            **resolved,
            **_payload_descriptor(request_payload,declarations,prefix="request"),
            **_payload_descriptor(response_payload,declarations,prefix="response"),
            "source_kind": source_kind,
            "repository_relative_path": repository_relative_path,
            "source_occurrence_id": invocation.get("source_occurrence_id"),
            "occurrence_count": 1,
            "claim": {
                "classification": classification,
                "confidence": confidence,
                "basis": resolved["resolution_basis"],
            },
            "probe": {"probe_id": "http_boundary_observations", "probe_version": "1"},
            "basis": {
                "kind": source_kind,
                "resolution_basis": resolved["resolution_basis"],
                "semantic_meaning_inferred": False,
                "cross_repository_matching_performed": False,
            },
        })

    rows.sort(
        key=lambda row: (
            row["repository_relative_path"],
            row["method"],
            str(row.get("path") or ""),
            str(row.get("path_expression") or ""),
            row["family_id"],
        )
    )
    return rows


def _expression_key(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _resolve_exact_string_expression(
    expression: dict[str, Any],
    *,
    declarations: dict[tuple[str, str], list[dict[str, Any]]],
    cache: dict[tuple[str, str], tuple[str, tuple[str, ...]] | None],
    visiting: set[tuple[str, str]],
) -> tuple[str, tuple[str, ...]] | None:
    kind = str(expression.get("kind") or "")
    if kind == "literal":
        value = expression.get("value")
        return (value, ()) if isinstance(value, str) else None
    if kind == "concat":
        parts = expression.get("parts") or []
        if not isinstance(parts, list) or not parts:
            return None
        values: list[str] = []
        provenance: set[str] = set()
        for part in parts:
            if not isinstance(part, dict):
                return None
            resolved = _resolve_exact_string_expression(
                part, declarations=declarations, cache=cache, visiting=visiting
            )
            if resolved is None:
                return None
            values.append(resolved[0])
            provenance.update(resolved[1])
        return "".join(values), tuple(sorted(provenance))
    if kind != "reference":
        return None
    owner = str(expression.get("owner") or "")
    name = str(expression.get("name") or "")
    key = (owner, name)
    if not owner or not name:
        return None
    if key in cache:
        return cache[key]
    if key in visiting:
        cache[key] = None
        return None
    rows = declarations.get(key) or []
    # Multiple declarations with the same simple owner/name are safe only when their
    # exact AST-normalized expressions are identical. Distinct definitions stay unresolved.
    variants = {_expression_key(row.get("expression")): row for row in rows if isinstance(row.get("expression"), dict)}
    if len(variants) != 1:
        cache[key] = None
        return None
    chosen = next(iter(variants.values()))
    visiting.add(key)
    resolved = _resolve_exact_string_expression(
        chosen["expression"], declarations=declarations, cache=cache, visiting=visiting
    )
    visiting.remove(key)
    if resolved is None:
        cache[key] = None
        return None
    occurrence_id = str(chosen.get("source_occurrence_id") or "")
    provenance = set(resolved[1])
    if occurrence_id:
        provenance.add(occurrence_id)
    answer = (resolved[0], tuple(sorted(provenance)))
    cache[key] = answer
    return answer


def _join_http_path(prefix: str, route: str) -> str:
    if not prefix:
        return route or "/"
    if not route:
        return prefix or "/"
    if prefix == "/":
        return "/" + route.lstrip("/")
    suffix = route.lstrip("/")
    if not suffix:
        return prefix.rstrip("/") + "/"
    return prefix.rstrip("/") + "/" + suffix


def project_inbound_java_http_boundaries(
    *,
    repository_id: str,
    java_facts: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Project exact Java inbound HTTP half-wires from the existing Tree-sitter pass.

    Only exact repository-local mechanics are supported: literal/static-final String
    expressions and an explicit service type registered through ``annotatedService``.
    This is deliberately not a Java symbol solver and does not read source files.
    """
    declarations: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    payload_declarations=_java_type_declarations(java_facts)
    for row in java_facts.get("string_constants") or []:
        owner = str(row.get("owner") or "")
        name = str(row.get("name") or "")
        if owner and name and isinstance(row.get("expression"), dict):
            declarations[(owner, name)].append(row)
    cache: dict[tuple[str, str], tuple[str, tuple[str, ...]] | None] = {}

    registrations_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in java_facts.get("service_registrations") or []:
        service_type = str(row.get("service_type") or "")
        if service_type:
            registrations_by_type[service_type].append(row)

    rows: list[dict[str, Any]] = []
    for route in java_facts.get("route_annotations") or []:
        expression = route.get("expression")
        if not isinstance(expression, dict):
            continue
        route_resolved = _resolve_exact_string_expression(
            expression, declarations=declarations, cache=cache, visiting=set()
        )
        if route_resolved is None:
            continue
        route_value, route_constant_occurrences = route_resolved
        interfaces = {str(value) for value in route.get("interfaces") or [] if str(value)}
        registrations = [
            row
            for interface in interfaces
            for row in registrations_by_type.get(interface, [])
        ]

        candidates: dict[str, set[str]] = defaultdict(set)
        if registrations:
            for registration in registrations:
                prefix_expression = registration.get("prefix_expression")
                if not isinstance(prefix_expression, dict):
                    continue
                prefix_resolved = _resolve_exact_string_expression(
                    prefix_expression, declarations=declarations, cache=cache, visiting=set()
                )
                if prefix_resolved is None:
                    continue
                prefix, prefix_constant_occurrences = prefix_resolved
                path = _join_http_path(prefix, route_value)
                candidates[path].update(route_constant_occurrences)
                candidates[path].update(prefix_constant_occurrences)
                registration_occurrence = str(registration.get("source_occurrence_id") or "")
                if registration_occurrence:
                    candidates[path].add(registration_occurrence)
        else:
            candidates[route_value].update(route_constant_occurrences)

        if not candidates:
            continue
        ordered = sorted(candidates)
        exact = len(ordered) == 1
        path = ordered[0] if exact else None
        route_occurrence = str(route.get("source_occurrence_id") or "")
        provenance_ids: set[str] = set()
        for ids in candidates.values():
            provenance_ids.update(ids)
        if route_occurrence:
            provenance_ids.add(route_occurrence)
        path_status = "resolved" if exact else "ambiguous_service_registration"
        classification = "observed_fact" if exact else "ambiguity"
        confidence = 1.0 if exact else 0.5
        key = path if path is not None else route_value
        deserialize_types=[str(value) for value in route.get('manual_deserialize_types') or [] if str(value)]
        request_identity=deserialize_types[0] if len(set(deserialize_types))==1 else None
        rows.append({
            "family_id": stable_id(
                "http_boundary_observation",
                repository_id,
                "java_inbound_exact_projection",
                route.get("class_name"),
                route.get("method"),
                path_status,
                ordered,
            ),
            "repository_id": repository_id,
            "family_kind": "http_boundary_observation",
            "syntax_family": "java",
            "key": key,
            "direction": "inbound",
            "protocol": "http",
            "method": str(route.get("method") or ""),
            "path": path,
            "path_status": path_status,
            **({"path_candidates": ordered} if not exact else {}),
            "source_kind": "java_exact_registered_route" if registrations else "java_exact_route_annotation",
            "repository_relative_path": str(route.get("repository_relative_path") or ""),
            "source_occurrence_ids": sorted(provenance_ids),
            "occurrence_count": 1,
            **_payload_descriptor(request_identity,payload_declarations,prefix="request"),
            "claim": {
                "classification": classification,
                "confidence": confidence,
                "basis": "tree_sitter_java_exact_route_plus_explicit_service_registration" if registrations else "tree_sitter_java_exact_route_annotation",
            },
            "probe": {"probe_id": "http_boundary_observations", "probe_version": "1"},
            "basis": {
                "kind": "java_exact_registered_route" if registrations else "java_exact_route_annotation",
                "explicit_service_type_match": bool(registrations),
                "semantic_meaning_inferred": False,
                "cross_repository_matching_performed": False,
            },
        })

    rows.sort(
        key=lambda row: (
            row["method"],
            str(row.get("path") or ""),
            str(row.get("key") or ""),
            row["family_id"],
        )
    )
    return rows
