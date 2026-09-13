from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping

from .canonical import canonical_json_bytes, fingerprint, stable_id
from .preflight_signals import SIGNAL_CATALOG_VERSION, collect_preflight_signals
from .reduction import validate_reduced_inventory
from .contracts import REDUCED_INVENTORY_FORMAT, CONCEPT_CANDIDATE_FORMAT

CLASSIFIER_VERSION = "repository-inventory-concept-classifier/v1"
CONCEPTS = (
    "data_model",
    "system_interaction",
    "data_flow",
    "persistence",
    "workflow",
    "reference_data",
)
STRENGTHS = ("not_observed", "weak", "moderate", "strong")


class ConceptCandidateError(ValueError):
    pass


def _signal_map(signal_product: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(row.get("signal_id") or ""): row
        for row in signal_product.get("signals") or []
        if isinstance(row, Mapping) and str(row.get("signal_id") or "")
    }


def _active(signals: Mapping[str, Mapping[str, Any]], *ids: str) -> set[str]:
    return {signal_id for signal_id in ids if signal_id in signals}


def _classify_data_model(signals: Mapping[str, Mapping[str, Any]]) -> tuple[str, list[str], str]:
    direct = _active(signals, "declared_model_annotation", "formal_schema_format", "persistence_annotation")
    naming = _active(signals, "model_namespace_naming")
    if ("declared_model_annotation" in direct and naming) or len(direct) >= 2 or ("formal_schema_format" in direct and naming):
        return "strong", sorted(direct | naming), "multiple_independent_model_routing_signals"
    if direct:
        return "moderate", sorted(direct | naming), "direct_model_routing_signal_observed"
    if naming:
        return "weak", sorted(naming), "model_namespace_naming_only"
    return "not_observed", [], "no_matching_preflight_signal_observed"


def _classify_system_interaction(signals: Mapping[str, Mapping[str, Any]]) -> tuple[str, list[str], str]:
    direct = _active(signals, "rest_inbound_annotation", "rest_outbound_client", "kafka_consumer", "kafka_producer", "jms_messaging", "soap_webservice")
    dependency = _active(signals, "messaging_dependency")
    if len(direct) >= 2:
        return "strong", sorted(direct | dependency), "multiple_independent_interaction_signals"
    if direct:
        return "moderate", sorted(direct | dependency), "direct_interaction_signal_observed"
    if dependency:
        return "weak", sorted(dependency), "interaction_dependency_only"
    return "not_observed", [], "no_matching_preflight_signal_observed"


def _classify_data_flow(signals: Mapping[str, Mapping[str, Any]]) -> tuple[str, list[str], str]:
    annotation = _active(signals, "mapping_annotation")
    identity = _active(signals, "mapping_or_conversion_identity")
    if annotation and identity:
        return "strong", sorted(annotation | identity), "mapping_declaration_and_mapping_identity_observed"
    if annotation:
        return "moderate", sorted(annotation), "mapping_declaration_signal_observed"
    if identity:
        return "moderate", sorted(identity), "mapping_or_conversion_identity_observed"
    return "not_observed", [], "no_matching_preflight_signal_observed"


def _classify_persistence(signals: Mapping[str, Mapping[str, Any]]) -> tuple[str, list[str], str]:
    direct = _active(signals, "sql_structure", "persistence_annotation", "persistence_namespace")
    dependency = _active(signals, "persistence_dependency")
    if len(direct) >= 2:
        return "strong", sorted(direct | dependency), "multiple_independent_persistence_signals"
    if direct:
        return "moderate", sorted(direct | dependency), "direct_persistence_signal_observed"
    if dependency:
        return "weak", sorted(dependency), "persistence_dependency_only"
    return "not_observed", [], "no_matching_preflight_signal_observed"


def _classify_workflow(signals: Mapping[str, Mapping[str, Any]]) -> tuple[str, list[str], str]:
    annotation = _active(signals, "workflow_orchestration_annotation")
    identity = _active(signals, "workflow_orchestration_identity")
    if annotation and identity:
        return "strong", sorted(annotation | identity), "workflow_declaration_and_orchestration_identity_observed"
    if annotation or identity:
        return "moderate", sorted(annotation | identity), "direct_workflow_routing_signal_observed"
    return "not_observed", [], "no_matching_preflight_signal_observed"


def _classify_reference_data(signals: Mapping[str, Mapping[str, Any]]) -> tuple[str, list[str], str]:
    annotation = _active(signals, "reference_data_annotation")
    namespace = _active(signals, "reference_data_namespace")
    if annotation and namespace:
        return "strong", sorted(annotation | namespace), "reference_data_declaration_and_namespace_signals"
    if annotation:
        return "moderate", sorted(annotation), "reference_data_declaration_naming_signal"
    if namespace:
        return "weak", sorted(namespace), "reference_data_namespace_naming_only"
    return "not_observed", [], "no_matching_preflight_signal_observed"


_CLASSIFIERS = {
    "data_model": _classify_data_model,
    "system_interaction": _classify_system_interaction,
    "data_flow": _classify_data_flow,
    "persistence": _classify_persistence,
    "workflow": _classify_workflow,
    "reference_data": _classify_reference_data,
}


def build_concept_candidates_payload(reduced: Mapping[str, Any]) -> dict[str, Any]:
    validate_reduced_inventory(reduced)
    signal_product = collect_preflight_signals(reduced)
    signals = _signal_map(signal_product)
    candidates=[]
    for concept in CONCEPTS:
        strength, signal_ids, rule_basis = _CLASSIFIERS[concept](signals)
        basis_rows=[]
        for signal_id in signal_ids:
            row=signals[signal_id]
            basis_rows.append({
                "signal_id": signal_id,
                "mechanism": row.get("mechanism"),
                "basis": row.get("basis"),
                "observed_identity_count": int(row.get("observed_identity_count") or 0),
                "source_family_count": int(row.get("source_family_count") or 0),
                "occurrence_count": int(row.get("occurrence_count") or 0),
                "representative_evidence_count": int(row.get("representative_evidence_count") or 0),
                "representative_evidence_ids": list(row.get("representative_evidence_ids") or []),
                "evidence_sample_truncated": bool(row.get("evidence_sample_truncated")),
            })
        candidates.append({
            "concept": concept,
            "status": "candidate" if strength != "not_observed" else "not_observed",
            "signal_strength": strength,
            "rule_basis": rule_basis,
            "basis_signals": basis_rows,
            "absence_proven": False,
            "claim_level": "repository_routing_preflight",
        })
    classifier={
        "version": CLASSIFIER_VERSION,
        "signal_catalog_version": SIGNAL_CATALOG_VERSION,
        "strength_scale": list(STRENGTHS),
        "quantity_alone_promotes_strength": False,
        "raw_inventory_read": False,
        "source_repository_read": False,
    }
    result={
        "format": CONCEPT_CANDIDATE_FORMAT,
        "repository_id": str((reduced.get("source_inventory") or {}).get("repository_id") or ""),
        "source_reduced_inventory": {
            "format": REDUCED_INVENTORY_FORMAT,
            "reduced_inventory_id": str(reduced.get("reduced_inventory_id") or ""),
            "semantic_fingerprint": str(reduced.get("semantic_fingerprint") or ""),
        },
        "classifier": {**classifier, "configuration_fingerprint": fingerprint(classifier)},
        "concept_candidates": candidates,
        "diagnostics": [],
        "claim_boundary": (
            "Repository-level routing preflight only. strong/moderate/weak describe strength of observed routing signals, "
            "not truth of the concept. not_observed means no configured signal was observed and does not prove absence. "
            "No business meaning, data model, interaction, storage relation, workflow, or data flow is confirmed by this product."
        ),
    }
    result["concept_candidate_id"] = stable_id(
        "repository_concept_candidates",
        CONCEPT_CANDIDATE_FORMAT,
        result["source_reduced_inventory"]["semantic_fingerprint"],
        result["classifier"]["configuration_fingerprint"],
    )
    result["semantic_fingerprint"] = fingerprint(result)
    validate_concept_candidates(result)
    return result


def validate_concept_candidates(payload: Mapping[str, Any]) -> None:
    if payload.get("format") != CONCEPT_CANDIDATE_FORMAT:
        raise ConceptCandidateError(f"expected {CONCEPT_CANDIDATE_FORMAT!r}, got {payload.get('format')!r}")
    source=payload.get("source_reduced_inventory")
    if not isinstance(source, Mapping) or source.get("format") != REDUCED_INVENTORY_FORMAT:
        raise ConceptCandidateError("concept candidates have no valid Reduced-v3 source identity")
    if not str(source.get("reduced_inventory_id") or "") or not str(source.get("semantic_fingerprint") or ""):
        raise ConceptCandidateError("concept candidates have incomplete Reduced-v3 provenance")
    classifier=payload.get("classifier")
    if not isinstance(classifier, Mapping) or classifier.get("version") != CLASSIFIER_VERSION:
        raise ConceptCandidateError("unknown concept classifier version")
    if classifier.get("signal_catalog_version") != SIGNAL_CATALOG_VERSION:
        raise ConceptCandidateError("unexpected preflight signal catalog version")
    material={str(k):v for k,v in classifier.items() if str(k)!="configuration_fingerprint"}
    if str(classifier.get("configuration_fingerprint") or "") != fingerprint(material):
        raise ConceptCandidateError("classifier configuration fingerprint mismatch")
    rows=payload.get("concept_candidates")
    if not isinstance(rows,list) or len(rows)!=len(CONCEPTS):
        raise ConceptCandidateError("concept candidates must contain exactly the six canonical concepts")
    by_concept={}
    for row in rows:
        if not isinstance(row,Mapping):
            raise ConceptCandidateError("invalid concept candidate row")
        concept=str(row.get("concept") or "")
        if concept not in CONCEPTS or concept in by_concept:
            raise ConceptCandidateError(f"invalid or duplicate concept candidate: {concept!r}")
        strength=str(row.get("signal_strength") or "")
        if strength not in STRENGTHS:
            raise ConceptCandidateError(f"invalid signal strength for {concept}: {strength!r}")
        expected_status="not_observed" if strength=="not_observed" else "candidate"
        if row.get("status") != expected_status:
            raise ConceptCandidateError(f"candidate status/strength mismatch for {concept}")
        if row.get("absence_proven") is not False:
            raise ConceptCandidateError(f"absence must never be claimed by preflight: {concept}")
        by_concept[concept]=row
    if set(by_concept)!=set(CONCEPTS):
        raise ConceptCandidateError("canonical concept set mismatch")
    expected=str(payload.get("semantic_fingerprint") or "")
    material=deepcopy(dict(payload)); material.pop("semantic_fingerprint",None)
    if expected != fingerprint(material):
        raise ConceptCandidateError("concept candidate semantic fingerprint mismatch")


def build_concept_candidates(reduced_path: str | Path, *, output: str | Path | None = None, overwrite: bool = False) -> dict[str, Any]:
    path=Path(reduced_path).expanduser().resolve()
    if not path.is_file():
        raise ConceptCandidateError(f"Reduced inventory not found: {path}")
    try:
        reduced=json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConceptCandidateError(f"invalid Reduced JSON: {path}") from exc
    result=build_concept_candidates_payload(reduced)
    if output is not None:
        target=Path(output).expanduser().resolve(); target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and not overwrite:
            raise FileExistsError(f"concept candidate output already exists: {target}; use overwrite/--force")
        target.write_bytes(canonical_json_bytes(result))
    return result


def verify_concept_candidates(path: str | Path) -> dict[str, Any]:
    candidate=Path(path).expanduser().resolve()
    if not candidate.is_file():
        raise ConceptCandidateError(f"concept candidate artifact not found: {candidate}")
    try:
        payload=json.loads(candidate.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConceptCandidateError(f"invalid concept candidate JSON: {candidate}") from exc
    validate_concept_candidates(payload)
    return {
        "status":"verified",
        "format":payload["format"],
        "repository_id":payload["repository_id"],
        "concept_candidate_id":payload["concept_candidate_id"],
        "semantic_fingerprint":payload["semantic_fingerprint"],
    }
