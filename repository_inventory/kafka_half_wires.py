from __future__ import annotations

from collections import defaultdict
from typing import Any

from .canonical import stable_id


def _enum_constants(facts: dict[str, list[dict[str, Any]]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    out: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in facts.get("enum_constants") or []:
        owner = str(row.get("owner") or "")
        name = str(row.get("name") or "")
        if owner and name:
            out[(owner, name)].append(row)
    return out


def _enum_metadata(facts: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in facts.get("enum_metadata") or []:
        owner = str(row.get("owner") or "")
        if owner:
            out[owner].append(row)
    return out


def _unique_ref_constant(
    constants: dict[tuple[str, str], list[dict[str, Any]]],
    ref: dict[str, str] | None,
) -> dict[str, Any] | None:
    if not ref:
        return None
    owner = str(ref.get("owner") or "")
    name = str(ref.get("name") or "")
    if not name:
        return None
    if owner:
        rows = constants.get((owner, name)) or []
    else:
        rows = [row for (candidate_owner, candidate_name), values in constants.items() if candidate_name == name for row in values]
    return rows[0] if len(rows) == 1 else None


def _enum_ref_literal(
    constants: dict[tuple[str, str], list[dict[str, Any]]],
    ref: dict[str, str] | None,
) -> str | None:
    row = _unique_ref_constant(constants, ref)
    if row is None:
        return None
    values = sorted({str(value) for value in row.get("argument_literals") or [] if isinstance(value, str) and value})
    return values[0] if len(values) == 1 else None


def _listener_topic_resolution(
    *,
    listener: dict[str, Any],
    constructor_refs_by_class: dict[str, list[dict[str, Any]]],
    constants: dict[tuple[str, str], list[dict[str, Any]]],
    metadata: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    class_name = str(listener.get("class_name") or "")
    constructor_refs = constructor_refs_by_class.get(class_name) or []
    distinct = {
        (str((row.get("enum_ref") or {}).get("owner") or ""), str((row.get("enum_ref") or {}).get("name") or ""))
        for row in constructor_refs
        if (row.get("enum_ref") or {}).get("name")
    }
    if len(distinct) != 1:
        return {
            "topic_status": "unresolved_constructor_binding",
            "topic_identity_kind": "unresolved_expression",
            "topic_identity": None,
            "topic_candidates": [],
            "resolution_basis": "kafka_listener_without_unique_super_enum_reference",
        }
    enum_owner, enum_name = next(iter(distinct))
    constant_rows = constants.get((enum_owner, enum_name)) or []
    meta_rows = metadata.get(enum_owner) or []
    if len(constant_rows) != 1 or len(meta_rows) != 1:
        return {
            "topic_status": "unresolved_enum_binding",
            "topic_identity_kind": "unresolved_expression",
            "topic_identity": None,
            "topic_candidates": [],
            "resolution_basis": "kafka_listener_enum_reference_not_uniquely_resolved",
        }
    constant = constant_rows[0]
    meta = meta_rows[0]
    topics_literal = str(listener.get("topics_literal") or "")
    if not bool(meta.get("required_args_constructor")):
        return {
            "topic_status": "unresolved_enum_constructor_semantics",
            "topic_identity_kind": "unresolved_expression",
            "topic_identity": None,
            "topic_candidates": [],
            "resolution_basis": "enum_constructor_argument_to_field_mapping_not_source_proven",
        }
    accessors = [
        row for row in meta.get("string_accessors") or []
        if str(row.get("method_name") or "") and f".{row['method_name']}(" in topics_literal
    ]
    if len(accessors) != 1:
        return {
            "topic_status": "unresolved_listener_topic_accessor",
            "topic_identity_kind": "unresolved_expression",
            "topic_identity": None,
            "topic_candidates": [],
            "resolution_basis": "listener_topics_literal_does_not_select_one_enum_string_accessor",
        }
    field_name = str(accessors[0].get("field_name") or "")
    fields = [str(row.get("name") or "") for row in meta.get("required_fields") or []]
    if field_name not in fields:
        return {
            "topic_status": "unresolved_enum_field_position",
            "topic_identity_kind": "unresolved_expression",
            "topic_identity": None,
            "topic_candidates": [],
            "resolution_basis": "selected_enum_accessor_field_not_in_required_constructor_fields",
        }
    index = fields.index(field_name)
    refs = list(constant.get("argument_refs") or [])
    if index >= len(refs):
        return {
            "topic_status": "unresolved_enum_argument_position",
            "topic_identity_kind": "unresolved_expression",
            "topic_identity": None,
            "topic_candidates": [],
            "resolution_basis": "selected_enum_field_has_no_constant_argument",
        }
    config_ref = refs[index]
    config_key = _enum_ref_literal(constants, config_ref)
    if config_key is None:
        return {
            "topic_status": "unresolved_config_key_reference",
            "topic_identity_kind": "unresolved_expression",
            "topic_identity": None,
            "topic_candidates": [],
            "resolution_basis": "enum_topic_field_reference_has_no_unique_literal_value",
        }
    return {
        "topic_status": "resolved",
        "topic_identity_kind": "config_key",
        "topic_identity": config_key,
        "topic_candidates": [config_key],
        "resolution_basis": "kafka_listener_super_enum_plus_required_args_field_accessor_plus_literal_config_key",
    }


def _payload_descriptor(identity: str | None, declarations: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    if not identity:
        return {}
    out: dict[str, Any] = {"payload_identity": identity}
    rows = declarations.get(identity) or []
    if len(rows) == 1:
        fields = rows[0].get("fields") or []
        out["payload_shape_status"] = "available_local_declaration"
        if fields:
            out["payload_fields"] = fields
    elif not rows:
        out["payload_shape_status"] = "unavailable_external_declaration"
    else:
        signatures = {repr(row.get("fields") or []) for row in rows}
        if len(signatures) == 1:
            fields = rows[0].get("fields") or []
            out["payload_shape_status"] = "available_local_declaration"
            if fields:
                out["payload_fields"] = fields
        else:
            out["payload_shape_status"] = "ambiguous_local_declaration"
    return out


def project_kafka_boundaries(
    *,
    repository_id: str,
    java_facts: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Project repository-local Kafka half-wires from facts from the existing Java parse pass.

    This function owns no parser, performs no source scan and does no cross-repository matching.
    Exact config-key resolution is limited to source-proven local enum/config chains and exact
    method-local KafkaTemplate/ProducerRecord sends already captured by ``java_probe``.
    """
    constants = _enum_constants(java_facts)
    metadata = _enum_metadata(java_facts)
    constructors: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in java_facts.get("constructor_enum_refs") or []:
        class_name = str(row.get("class_name") or "")
        if class_name:
            constructors[class_name].append(row)
    declarations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in java_facts.get("type_declarations") or []:
        type_name = str(row.get("type_name") or "")
        if type_name:
            declarations[type_name].append(row)

    rows: list[dict[str, Any]] = []
    for listener in java_facts.get("listeners") or []:
        topics_literal = str(listener.get("topics_literal") or "")
        exact_topics_literal = topics_literal if topics_literal and "#{" not in topics_literal and "${" not in topics_literal else ""
        if exact_topics_literal:
            resolution = {
                "topic_status": "resolved",
                "topic_identity_kind": "literal",
                "topic_identity": exact_topics_literal,
                "topic_candidates": [exact_topics_literal],
                "resolution_basis": "kafka_listener_literal_topic",
            }
        else:
            resolution = _listener_topic_resolution(
                listener=listener,
                constructor_refs_by_class=constructors,
                constants=constants,
                metadata=metadata,
            )
        payload_candidates = sorted({str(value) for value in listener.get("payload_candidates") or [] if str(value)})
        payload_identity = payload_candidates[0] if len(payload_candidates) == 1 else None
        key = resolution.get("topic_identity") or str(listener.get("class_name") or "kafka")
        literal_resolved = resolution.get("topic_identity_kind") == "literal" and resolution.get("topic_status") == "resolved"
        claim = {
            "classification": "observed_fact" if literal_resolved else ("strongly_supported_inference" if resolution.get("topic_status") == "resolved" else "unresolved"),
            "confidence": 1.0 if literal_resolved else (0.95 if resolution.get("topic_status") == "resolved" else 0.0),
            "basis": str(resolution.get("resolution_basis") or "kafka_listener_projection"),
        }
        row = {
            "family_id": stable_id("kafka_boundary_observation", repository_id, "consume", key, listener.get("repository_relative_path"), listener.get("source_occurrence_id")),
            "repository_id": repository_id,
            "family_kind": "kafka_boundary_observation",
            "syntax_family": "java",
            "key": key,
            "direction": "consume",
            "protocol": "kafka",
            **resolution,
            "operation": f"{listener.get('class_name')}.{listener.get('method_name')}",
            "source_kind": "java_kafka_listener",
            "repository_relative_path": listener.get("repository_relative_path"),
            "source_occurrence_id": listener.get("source_occurrence_id"),
            "occurrence_count": 1,
            "claim": claim,
            "probe": {"probe_id": "kafka_boundary_observations", "probe_version": "1"},
            "basis": {"kind": str(resolution.get("resolution_basis") or "kafka_listener_projection"), "semantic_meaning_inferred": resolution.get("topic_status") == "resolved" and not literal_resolved},
        }
        if payload_identity:
            row.update(_payload_descriptor(payload_identity, declarations))
        elif payload_candidates:
            row["payload_candidates"] = payload_candidates
            row["payload_shape_status"] = "ambiguous_payload_identity"
        rows.append(row)

    for candidate in java_facts.get("publish_candidates") or []:
        topic_literal = candidate.get("topic_literal")
        config_ref = candidate.get("topic_config_ref")
        config_key = _enum_ref_literal(constants, config_ref) if config_ref else None
        if isinstance(topic_literal, str) and topic_literal:
            topic_identity_kind = "literal"
            topic_identity = topic_literal
            topic_status = "resolved"
            topic_candidates = [topic_literal]
            resolution_basis = "kafka_template_send_literal_topic"
        elif config_key:
            topic_identity_kind = "config_key"
            topic_identity = config_key
            topic_status = "resolved"
            topic_candidates = [config_key]
            resolution_basis = "kafka_template_send_method_local_config_lookup"
        else:
            topic_identity_kind = "unresolved_expression"
            topic_identity = None
            topic_status = "unresolved_expression"
            topic_candidates = []
            resolution_basis = "kafka_template_send_topic_expression_unresolved"
        key = topic_identity or str(candidate.get("topic_expression") or "") or str(candidate.get("class_name") or "kafka")
        row = {
            "family_id": stable_id("kafka_boundary_observation", repository_id, "publish", key, candidate.get("repository_relative_path"), candidate.get("source_occurrence_id")),
            "repository_id": repository_id,
            "family_kind": "kafka_boundary_observation",
            "syntax_family": "java",
            "key": key,
            "direction": "publish",
            "protocol": "kafka",
            "topic_identity_kind": topic_identity_kind,
            "topic_identity": topic_identity,
            "topic_status": topic_status,
            "topic_candidates": topic_candidates,
            "topic_expression": candidate.get("topic_expression"),
            "resolution_basis": resolution_basis,
            "operation": f"{candidate.get('class_name')}.{candidate.get('method_name')}",
            "source_kind": "java_kafka_template_send",
            "repository_relative_path": candidate.get("repository_relative_path"),
            "source_occurrence_id": candidate.get("source_occurrence_id"),
            "occurrence_count": 1,
            "claim": {
                "classification": "observed_fact" if topic_status == "resolved" else "unresolved",
                "confidence": 1.0 if topic_status == "resolved" else 0.0,
                "basis": resolution_basis,
            },
            "probe": {"probe_id": "kafka_boundary_observations", "probe_version": "1"},
            "basis": {"kind": resolution_basis, "semantic_meaning_inferred": False},
        }
        payload_identity = str(candidate.get("payload_type") or "") or None
        if payload_identity:
            row.update(_payload_descriptor(payload_identity, declarations))
        rows.append(row)

    rows.sort(key=lambda row: (str(row.get("direction")), str(row.get("topic_identity") or row.get("key")), str(row.get("repository_relative_path")), str(row.get("family_id"))))
    return rows
