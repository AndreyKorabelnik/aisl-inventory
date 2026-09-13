from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Pattern

from .reduction import validate_reduced_inventory

SIGNAL_CATALOG_VERSION = "repository-inventory-preflight-signals/v1"
MAX_EVIDENCE_SAMPLES_PER_SIGNAL = 12


@dataclass(frozen=True)
class SignalRule:
    signal_id: str
    supports_concepts: tuple[str, ...]
    family_kinds: tuple[str, ...]
    label_pattern: Pattern[str] | None = None
    facet_pattern: Pattern[str] | None = None
    mechanism: str | None = None
    basis: str = "observed_identity_metadata_match"


def _rx(pattern: str) -> Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# Rules operate only on Reduced-v3 observed identity metadata. They do not parse source,
# infer business meaning, or assert that a concept is present. Matching names/namespaces
# are candidate-routing evidence whose strength is decided by a separate classifier.
SIGNAL_RULES: tuple[SignalRule, ...] = (
    SignalRule(
        "declared_model_annotation",
        ("data_model",),
        ("annotation_observation",),
        label_pattern=_rx(r"(?:^|.*)(?:Entity|RootEntity|Embeddable|MappedSuperclass|Document)$"),
    ),
    SignalRule(
        "formal_schema_format",
        ("data_model",),
        ("source_format_observation",),
        label_pattern=_rx(r"^(?:openapi|json[-_ ]?schema|xsd|proto(?:buf)?|avro)$"),
    ),
    SignalRule(
        "model_namespace_naming",
        ("data_model",),
        ("import_namespace_observation",),
        label_pattern=_rx(r"(?:^|\.)(?:model|domain|entity|dto)(?:\.|$)"),
        basis="observed_namespace_naming_signal",
    ),
    SignalRule(
        "rest_inbound_annotation",
        ("system_interaction",),
        ("annotation_observation",),
        label_pattern=_rx(r"^(?:RestController|Controller|RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)$"),
        mechanism="rest",
    ),
    SignalRule(
        "rest_outbound_client",
        ("system_interaction",),
        ("annotation_observation", "import_namespace_observation", "api_call_observation"),
        label_pattern=_rx(r"(?:FeignClient|WebClient|RestTemplate|HttpClient|OkHttpClient|Retrofit)$"),
        facet_pattern=_rx(r"(?:FeignClient|WebClient|RestTemplate|HttpClient|OkHttpClient|Retrofit)"),
        mechanism="rest",
    ),
    SignalRule(
        "kafka_consumer",
        ("system_interaction",),
        ("annotation_observation", "import_namespace_observation", "api_call_observation"),
        label_pattern=_rx(r"(?:KafkaListener|KafkaConsumer|ConsumerRecord|ConsumerFactory)$"),
        facet_pattern=_rx(r"(?:KafkaConsumer|ConsumerRecord|ConsumerFactory)"),
        mechanism="kafka",
    ),
    SignalRule(
        "kafka_producer",
        ("system_interaction",),
        ("import_namespace_observation", "api_call_observation"),
        label_pattern=_rx(r"(?:KafkaTemplate|KafkaProducer|ProducerRecord|ProducerFactory)$"),
        facet_pattern=_rx(r"(?:KafkaTemplate|KafkaProducer|ProducerRecord|ProducerFactory)"),
        mechanism="kafka",
    ),
    SignalRule(
        "jms_messaging",
        ("system_interaction",),
        ("annotation_observation", "import_namespace_observation", "api_call_observation"),
        label_pattern=_rx(r"(?:JmsListener|JmsTemplate|MessageListener)$|(?:^|\.)(?:jakarta|javax)\.jms(?:\.|$)"),
        facet_pattern=_rx(r"(?:JmsTemplate|MessageListener|(?:jakarta|javax)\.jms)"),
        mechanism="jms",
    ),
    SignalRule(
        "soap_webservice",
        ("system_interaction",),
        ("annotation_observation", "import_namespace_observation", "api_call_observation"),
        label_pattern=_rx(r"(?:WebServiceClient|WebServiceTemplate|SoapAction|WebService)$|(?:^|\.)(?:jakarta|javax)\.xml\.ws(?:\.|$)"),
        facet_pattern=_rx(r"(?:WebServiceClient|WebServiceTemplate|SoapAction|(?:jakarta|javax)\.xml\.ws)"),
        mechanism="soap",
    ),
    SignalRule(
        "messaging_dependency",
        ("system_interaction",),
        ("dependency_observation",),
        label_pattern=_rx(r"(?:kafka|spring-kafka|activemq|artemis|jms|cxf|soap|webservice)"),
        basis="observed_dependency_only",
    ),
    SignalRule(
        "mapping_annotation",
        ("data_flow",),
        ("annotation_observation",),
        label_pattern=_rx(r"^(?:Mapper|Mapping|Mappings|AfterMapping|BeforeMapping)$"),
    ),
    SignalRule(
        "mapping_or_conversion_identity",
        ("data_flow",),
        ("import_namespace_observation", "api_call_observation"),
        label_pattern=_rx(r"(?:Mapper|Converter|Transformer)$|^(?:map|convert|transform|serialize|deserialize)$"),
        facet_pattern=_rx(r"(?:Mapper|Converter|Transformer)"),
        basis="observed_mapping_or_conversion_naming_signal",
    ),
    SignalRule(
        "sql_structure",
        ("persistence",),
        ("sql_statement_family",),
        label_pattern=_rx(r".+"),
        basis="observed_sql_statement_family",
    ),
    SignalRule(
        "persistence_annotation",
        ("persistence", "data_model"),
        ("annotation_observation",),
        label_pattern=_rx(r"^(?:Entity|Table|Column|Id|EmbeddedId|Embeddable|MappedSuperclass|Repository|Document)$"),
    ),
    SignalRule(
        "persistence_namespace",
        ("persistence",),
        ("import_namespace_observation",),
        label_pattern=_rx(r"^(?:jakarta|javax)\.persistence(?:\.|$)|^org\.jooq(?:\.|$)|^org\.springframework\.(?:jdbc|data)(?:\.|$)|(?:^|\.)mybatis(?:\.|$)"),
    ),
    SignalRule(
        "persistence_dependency",
        ("persistence",),
        ("dependency_observation",),
        label_pattern=_rx(r"(?:jooq|hibernate|jdbc|spring-data|liquibase|flyway|mybatis|postgres|oracle|mysql|mariadb)"),
        basis="observed_dependency_only",
    ),
    SignalRule(
        "workflow_orchestration_annotation",
        ("workflow",),
        ("annotation_observation",),
        label_pattern=_rx(r"^(?:ServiceActivator|Router|Transformer|Scheduled|EventListener|Saga|StateMachine)$"),
    ),
    SignalRule(
        "workflow_orchestration_identity",
        ("workflow",),
        ("import_namespace_observation", "api_call_observation"),
        label_pattern=_rx(r"(?:IntegrationFlow|ServiceActivator|MessageRouter|StateMachine|ProcessManager|Workflow|Saga)$"),
        facet_pattern=_rx(r"(?:IntegrationFlow|ServiceActivator|MessageRouter|StateMachine|ProcessManager|Workflow|Saga)"),
    ),
    SignalRule(
        "reference_data_annotation",
        ("reference_data",),
        ("annotation_observation",),
        label_pattern=_rx(r"(?:Dictionary|ReferenceData|CodeList|Classifier|Lookup)"),
        basis="observed_reference_data_naming_signal",
    ),
    SignalRule(
        "reference_data_namespace",
        ("reference_data",),
        ("import_namespace_observation",),
        label_pattern=_rx(r"(?:^|\.)(?:dictionary|dictionaries|reference|referencedata|codelist|lookup)(?:\.|$)|(?:^|\.)nsi(?:\.|$)"),
        basis="observed_reference_data_namespace_naming_signal",
    ),
)


def _identity_strings(identity: Mapping[str, Any]) -> tuple[str, list[str]]:
    label = str(identity.get("family_label") or "")
    facets = [
        str(row.get("value") or "")
        for row in identity.get("string_facets") or []
        if isinstance(row, Mapping) and str(row.get("value") or "")
    ]
    return label, facets


def _matches(rule: SignalRule, row: Mapping[str, Any]) -> bool:
    identity = row.get("identity") if isinstance(row.get("identity"), Mapping) else {}
    family_kind = str(identity.get("family_kind") or "")
    if family_kind not in rule.family_kinds:
        return False
    label, facets = _identity_strings(identity)
    if rule.label_pattern is not None and label and rule.label_pattern.search(label):
        return True
    if rule.facet_pattern is not None and any(rule.facet_pattern.search(value) for value in facets):
        return True
    return False


def collect_preflight_signals(reduced: Mapping[str, Any]) -> dict[str, Any]:
    """Collect atomic repository-routing signals from Reduced v3 only.

    Returned rows are mechanically supported observations. They are not concept candidates and
    carry no strong/moderate/weak classification. The classifier owns that later interpretation.
    """
    validate_reduced_inventory(reduced)
    identities = [row for row in reduced.get("observed_identities") or [] if isinstance(row, Mapping)]
    rows: list[dict[str, Any]] = []
    for rule in SIGNAL_RULES:
        matched = [row for row in identities if _matches(rule, row)]
        if not matched:
            continue
        evidence_ids = sorted({
            str(row.get("representative_evidence_id") or "")
            for row in matched
            if str(row.get("representative_evidence_id") or "")
        })
        identity_ids = sorted({
            str(row.get("observed_identity_id") or "")
            for row in matched
            if str(row.get("observed_identity_id") or "")
        })
        rows.append({
            "signal_id": rule.signal_id,
            "supports_concepts": list(rule.supports_concepts),
            "mechanism": rule.mechanism,
            "basis": rule.basis,
            "observed_identity_count": len(identity_ids),
            "source_family_count": sum(int(row.get("source_family_count") or 0) for row in matched),
            "occurrence_count": sum(int(row.get("occurrence_count") or 0) for row in matched),
            "file_count_sum": sum(int(row.get("file_count") or 0) for row in matched),
            "representative_evidence_count": len(evidence_ids),
            "representative_evidence_ids": evidence_ids[:MAX_EVIDENCE_SAMPLES_PER_SIGNAL],
            "evidence_sample_truncated": len(evidence_ids) > MAX_EVIDENCE_SAMPLES_PER_SIGNAL,
            "observed_identity_ids": identity_ids[:MAX_EVIDENCE_SAMPLES_PER_SIGNAL],
            "identity_sample_truncated": len(identity_ids) > MAX_EVIDENCE_SAMPLES_PER_SIGNAL,
        })
    rows.sort(key=lambda row: str(row["signal_id"]))
    return {
        "signal_catalog_version": SIGNAL_CATALOG_VERSION,
        "source_reduced_inventory_id": str(reduced.get("reduced_inventory_id") or ""),
        "source_reduced_semantic_fingerprint": str(reduced.get("semantic_fingerprint") or ""),
        "repository_id": str((reduced.get("source_inventory") or {}).get("repository_id") or ""),
        "signals": rows,
        "claim_boundary": (
            "Atomic mechanically matched repository-routing signals over Reduced v3 observed identities only. "
            "Signals do not confirm a data model, interaction, workflow, persistence semantics, data flow, or reference data."
        ),
    }
