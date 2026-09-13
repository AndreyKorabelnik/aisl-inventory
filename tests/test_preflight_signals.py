from __future__ import annotations

from repository_inventory.canonical import fingerprint
from repository_inventory.preflight_signals import (
    SIGNAL_CATALOG_VERSION,
    SIGNAL_RULES,
    collect_preflight_signals,
)
from repository_inventory.reduction import reduce_inventory_payload


def _source_inventory() -> dict:
    specs = [
        ("annotation_observation", "Entity", {"annotation_name": "Entity", "language": "java"}),
        ("annotation_observation", "RestController", {"annotation_name": "RestController", "language": "java"}),
        ("annotation_observation", "KafkaListener", {"annotation_name": "KafkaListener", "language": "java"}),
        ("api_call_observation", "KafkaTemplate", {"callable_name": "KafkaTemplate", "call_kind": "constructor", "language": "java"}),
        ("annotation_observation", "Mapper", {"annotation_name": "Mapper", "language": "java"}),
        ("import_namespace_observation", "org.jooq.DSLContext", {"namespace": "org.jooq.DSLContext", "language": "java"}),
        ("annotation_observation", "Scheduled", {"annotation_name": "Scheduled", "language": "java"}),
        ("annotation_observation", "MetaDictionary", {"annotation_name": "MetaDictionary", "language": "java"}),
        ("dependency_observation", "spring-kafka", {"group_id": "org.springframework.kafka", "artifact_id": "spring-kafka", "build_system": "maven"}),
    ]
    families=[]
    evidence=[]
    for i,(kind,label,descriptor) in enumerate(specs):
        fid=f"f{i}"
        families.append({
            "family_id":fid,"repository_id":"repo","family_kind":kind,"family_label":label,
            "descriptor":descriptor,"occurrence_count":1,"file_count":1,
            "source_tree_scope_count":1,"source_tree_scopes":["."],
            "provenance":{"observed_section":kind},"structural_salience_score":0.0,
        })
        evidence.append({
            "evidence_id":f"e{i}","family_id":fid,"repository_id":"repo","file_id":f"file{i}",
            "repository_relative_path":f"src/F{i}.java","source_tree_scope":".","occurrence_count_in_file":1,
        })
    payload={
        "format":"repository-inventory/v7",
        "run_provenance":{"component":"repository-inventory","implementation_version":"test"},
        "identity":{"repository_id":"repo","repository_name":"repo"},
        "source_snapshot":{"fingerprint":"snapshot","file_count":len(specs)},
        "files":[],"structural_families":families,"structural_members":[],"family_file_evidence":evidence,
        "file_observations":[],"observation_diagnostics":[],"probe_status":[],
        "source_content_store":{"capture_status":"not_evaluated"},
        "semantic_policy":{"observed_source_state_only":True},"inventory_id":"inventory",
    }
    payload["semantic_fingerprint"]=fingerprint(payload)
    return payload


def test_signal_catalog_is_explicit_and_has_six_target_concepts():
    assert SIGNAL_CATALOG_VERSION == "repository-inventory-preflight-signals/v1"
    concepts={c for rule in SIGNAL_RULES for c in rule.supports_concepts}
    assert concepts == {"data_model","system_interaction","data_flow","persistence","workflow","reference_data"}
    assert len({rule.signal_id for rule in SIGNAL_RULES}) == len(SIGNAL_RULES)


def test_collects_atomic_signals_without_concept_strength_claims():
    reduced=reduce_inventory_payload(_source_inventory())
    result=collect_preflight_signals(reduced)
    by_id={row["signal_id"]:row for row in result["signals"]}
    assert result["repository_id"] == "repo"
    assert "declared_model_annotation" in by_id
    assert "rest_inbound_annotation" in by_id
    assert "kafka_consumer" in by_id
    assert "kafka_producer" in by_id
    assert "mapping_annotation" in by_id
    assert "persistence_namespace" in by_id
    assert "workflow_orchestration_annotation" in by_id
    assert "reference_data_annotation" in by_id
    assert by_id["messaging_dependency"]["basis"] == "observed_dependency_only"
    assert all("signal_strength" not in row for row in result["signals"])
    assert all("status" not in row for row in result["signals"])
    assert "do not confirm" in result["claim_boundary"]


def test_signal_rows_keep_bounded_evidence_provenance():
    reduced=reduce_inventory_payload(_source_inventory())
    result=collect_preflight_signals(reduced)
    entity=next(row for row in result["signals"] if row["signal_id"] == "declared_model_annotation")
    assert entity["representative_evidence_count"] == 1
    assert entity["representative_evidence_ids"]
    assert entity["observed_identity_ids"]
    assert entity["evidence_sample_truncated"] is False
