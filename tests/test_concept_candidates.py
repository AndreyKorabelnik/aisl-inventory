from __future__ import annotations

import json
from pathlib import Path

import pytest

from repository_inventory.canonical import fingerprint
from repository_inventory.cli import main
from repository_inventory.concept_candidates import (
    ConceptCandidateError,
    build_concept_candidates_payload,
    validate_concept_candidates,
)
from repository_inventory.reduction import reduce_inventory_payload


def _source(specs: list[tuple[str,str,dict]]) -> dict:
    families=[]; evidence=[]
    for i,(kind,label,descriptor) in enumerate(specs):
        fid=f"f{i}"
        families.append({
            "family_id":fid,"repository_id":"repo","family_kind":kind,"family_label":label,"descriptor":descriptor,
            "occurrence_count":1,"file_count":1,"source_tree_scope_count":1,"source_tree_scopes":["."],
            "provenance":{"observed_section":kind},"structural_salience_score":0.0,
        })
        evidence.append({"evidence_id":f"e{i}","family_id":fid,"repository_id":"repo","file_id":f"file{i}","repository_relative_path":f"F{i}.java","source_tree_scope":".","occurrence_count_in_file":1})
    payload={"format":"repository-inventory/v7","run_provenance":{"component":"repository-inventory","implementation_version":"test"},"identity":{"repository_id":"repo","repository_name":"repo"},"source_snapshot":{"fingerprint":"snapshot","file_count":len(specs)},"files":[],"structural_families":families,"structural_members":[],"family_file_evidence":evidence,"file_observations":[],"observation_diagnostics":[],"probe_status":[],"source_content_store":{"capture_status":"not_evaluated"},"semantic_policy":{"observed_source_state_only":True},"inventory_id":"inventory"}
    payload["semantic_fingerprint"]=fingerprint(payload)
    return payload


def _row(result, concept):
    return next(row for row in result["concept_candidates"] if row["concept"]==concept)


def test_independent_interaction_signals_are_strong_but_dependency_only_is_weak():
    direct=reduce_inventory_payload(_source([
        ("annotation_observation","KafkaListener",{"annotation_name":"KafkaListener","language":"java"}),
        ("api_call_observation","KafkaTemplate",{"callable_name":"KafkaTemplate","call_kind":"constructor","language":"java"}),
    ]))
    result=build_concept_candidates_payload(direct)
    assert _row(result,"system_interaction")["signal_strength"] == "strong"

    dependency=reduce_inventory_payload(_source([
        ("dependency_observation","spring-kafka",{"group_id":"org.springframework.kafka","artifact_id":"spring-kafka","build_system":"maven"}),
    ]))
    result=build_concept_candidates_payload(dependency)
    row=_row(result,"system_interaction")
    assert row["signal_strength"] == "weak"
    assert row["rule_basis"] == "interaction_dependency_only"


def test_naming_only_data_model_signal_is_weak_and_not_absence_claim():
    reduced=reduce_inventory_payload(_source([
        ("import_namespace_observation","com.example.model.Customer",{"namespace":"com.example.model.Customer","language":"java"}),
    ]))
    result=build_concept_candidates_payload(reduced)
    dm=_row(result,"data_model")
    assert dm["signal_strength"] == "weak"
    assert dm["absence_proven"] is False
    persistence=_row(result,"persistence")
    assert persistence["signal_strength"] == "not_observed"
    assert persistence["status"] == "not_observed"
    assert persistence["absence_proven"] is False


def test_multiple_model_signals_are_strong_and_quantity_alone_does_not_promote():
    reduced=reduce_inventory_payload(_source([
        ("annotation_observation","MetaEntity",{"annotation_name":"MetaEntity","language":"java"}),
        ("import_namespace_observation","com.example.model.Customer",{"namespace":"com.example.model.Customer","language":"java"}),
    ]))
    result=build_concept_candidates_payload(reduced)
    assert _row(result,"data_model")["signal_strength"] == "strong"
    assert result["classifier"]["quantity_alone_promotes_strength"] is False


def test_reference_data_requires_independent_signal_types_for_strong():
    reduced=reduce_inventory_payload(_source([
        ("annotation_observation","MetaDictionary",{"annotation_name":"MetaDictionary","language":"java"}),
        ("import_namespace_observation","com.example.dictionary.Country",{"namespace":"com.example.dictionary.Country","language":"java"}),
    ]))
    result=build_concept_candidates_payload(reduced)
    assert _row(result,"reference_data")["signal_strength"] == "strong"


def test_validation_detects_tamper():
    reduced=reduce_inventory_payload(_source([
        ("annotation_observation","RestController",{"annotation_name":"RestController","language":"java"}),
    ]))
    result=build_concept_candidates_payload(reduced)
    result["concept_candidates"][0]["signal_strength"]="strong"
    with pytest.raises(ConceptCandidateError):
        validate_concept_candidates(result)


def test_cli_classify_and_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    reduced=reduce_inventory_payload(_source([
        ("annotation_observation","RestController",{"annotation_name":"RestController","language":"java"}),
    ]))
    reduced_path=tmp_path/"reduced.json"; reduced_path.write_text(json.dumps(reduced),encoding="utf-8")
    out=tmp_path/"candidates.json"
    assert main(["classify-concepts",str(reduced_path),"--output",str(out)]) == 0
    assert out.is_file()
    assert main(["verify-concept-candidates",str(out)]) == 0
    printed=capsys.readouterr().out
    assert "repository-inventory-concept-candidates/v1" in printed
