from __future__ import annotations

import json
from pathlib import Path

from repository_inventory.canonical import fingerprint
from repository_inventory.concept_candidates import build_concept_candidates_payload
from repository_inventory.concept_index import (
    build_concept_index,
    load_concept_index,
    query_concept_index,
    verify_concept_index,
)
from repository_inventory.reduction import reduce_inventory_payload


def _candidate(repository_id: str, specs: list[tuple[str,str,dict]]):
    families=[]; evidence=[]
    for i,(kind,label,descriptor) in enumerate(specs):
        fid=f"{repository_id}-f{i}"
        families.append({"family_id":fid,"repository_id":repository_id,"family_kind":kind,"family_label":label,"descriptor":descriptor,"occurrence_count":1,"file_count":1,"source_tree_scope_count":1,"source_tree_scopes":["."],"provenance":{"observed_section":kind},"structural_salience_score":0.0})
        evidence.append({"evidence_id":f"{repository_id}-e{i}","family_id":fid,"repository_id":repository_id,"file_id":f"{repository_id}-file{i}","repository_relative_path":f"F{i}.java","source_tree_scope":".","occurrence_count_in_file":1})
    source={"format":"repository-inventory/v7","run_provenance":{"component":"repository-inventory","implementation_version":"test"},"identity":{"repository_id":repository_id,"repository_name":repository_id},"source_snapshot":{"fingerprint":f"snapshot-{repository_id}","file_count":len(specs)},"files":[],"structural_families":families,"structural_members":[],"family_file_evidence":evidence,"file_observations":[],"observation_diagnostics":[],"probe_status":[],"source_content_store":{"capture_status":"not_evaluated"},"semantic_policy":{"observed_source_state_only":True},"inventory_id":f"inventory-{repository_id}"}
    source["semantic_fingerprint"]=fingerprint(source)
    return build_concept_candidates_payload(reduce_inventory_payload(source))


def test_batch_index_only_aggregates_candidate_results_and_queries_strength_mechanism(tmp_path: Path):
    root=tmp_path/"batch"; (root/"repositories"/"model").mkdir(parents=True); (root/"repositories"/"kafka").mkdir(parents=True)
    model=_candidate("model",[("annotation_observation","MetaEntity",{"annotation_name":"MetaEntity","language":"java"}),("import_namespace_observation","com.acme.model.Customer",{"namespace":"com.acme.model.Customer","language":"java"})])
    kafka=_candidate("kafka",[("annotation_observation","KafkaListener",{"annotation_name":"KafkaListener","language":"java"}),("api_call_observation","KafkaTemplate",{"callable_name":"KafkaTemplate","call_kind":"constructor","language":"java"})])
    (root/"repositories"/"model"/"repository_concept_candidates.json").write_text(json.dumps(model),encoding="utf-8")
    (root/"repositories"/"kafka"/"repository_concept_candidates.json").write_text(json.dumps(kafka),encoding="utf-8")
    index=build_concept_index(root)
    assert index["summary"]["repository_count"] == 2
    assert verify_concept_index(root)["status"] == "verified"
    loaded=load_concept_index(root)
    assert [r["repository_id"] for r in query_concept_index(loaded,concept="data_model",min_strength="moderate")] == ["model"]
    rows=query_concept_index(loaded,concept="system_interaction",min_strength="moderate",mechanism="kafka")
    assert [r["repository_id"] for r in rows] == ["kafka"]
    assert rows[0]["signal_strength"] == "strong"


def test_index_repository_filter_excludes_preserved_but_unsuccessful_repository(tmp_path: Path):
    root=tmp_path/"batch"; (root/"repositories"/"old").mkdir(parents=True); (root/"repositories"/"current").mkdir(parents=True)
    old=_candidate("old",[("annotation_observation","RestController",{"annotation_name":"RestController","language":"java"})])
    current=_candidate("current",[("annotation_observation","RestController",{"annotation_name":"RestController","language":"java"})])
    for name,payload in [("old",old),("current",current)]:
        (root/"repositories"/name/"repository_concept_candidates.json").write_text(json.dumps(payload),encoding="utf-8")
    index=build_concept_index(root,repository_ids={"current"})
    assert [r["repository_id"] for r in index["repositories"]] == ["current"]


def test_cli_candidates_query(tmp_path: Path, capsys):
    from repository_inventory.cli import main
    root=tmp_path/"batch"; (root/"repositories"/"kafka").mkdir(parents=True)
    payload=_candidate("kafka",[("annotation_observation","KafkaListener",{"annotation_name":"KafkaListener","language":"java"}),("api_call_observation","KafkaTemplate",{"callable_name":"KafkaTemplate","call_kind":"constructor","language":"java"})])
    (root/"repositories"/"kafka"/"repository_concept_candidates.json").write_text(json.dumps(payload),encoding="utf-8")
    assert main(["build-concept-index",str(root)]) == 0
    capsys.readouterr()
    assert main(["candidates",str(root),"--concept","system_interaction","--mechanism","kafka"]) == 0
    result=json.loads(capsys.readouterr().out)
    assert result["match_count"] == 1
    assert result["repositories"][0]["repository_id"] == "kafka"
