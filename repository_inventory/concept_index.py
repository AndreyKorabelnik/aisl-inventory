from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping

from .canonical import canonical_json_bytes, fingerprint, stable_id
from .concept_candidates import (
    CLASSIFIER_VERSION,
    CONCEPTS,
    STRENGTHS,
    validate_concept_candidates,
)
from .contracts import CONCEPT_CANDIDATE_FORMAT, CONCEPT_INDEX_FORMAT

STRENGTH_RANK = {value: index for index, value in enumerate(STRENGTHS)}


class ConceptIndexError(ValueError):
    pass


def _candidate_path(root: Path, repository_id: str) -> Path:
    return root / "repositories" / repository_id / "repository_concept_candidates.json"


def build_concept_index_payload(candidates: list[tuple[str, Mapping[str, Any]]]) -> dict[str, Any]:
    repositories=[]
    for relative_path,payload in sorted(candidates, key=lambda item: str(item[1].get("repository_id") or "")):
        validate_concept_candidates(payload)
        concept_rows={}
        for row in payload.get("concept_candidates") or []:
            concept=str(row.get("concept") or "")
            mechanisms=sorted({
                str(signal.get("mechanism") or "")
                for signal in row.get("basis_signals") or []
                if isinstance(signal,Mapping) and str(signal.get("mechanism") or "")
            })
            concept_rows[concept]={
                "status": str(row.get("status") or ""),
                "signal_strength": str(row.get("signal_strength") or ""),
                "mechanisms": mechanisms,
                "basis_signal_ids": sorted({
                    str(signal.get("signal_id") or "")
                    for signal in row.get("basis_signals") or []
                    if isinstance(signal,Mapping) and str(signal.get("signal_id") or "")
                }),
            }
        repositories.append({
            "repository_id": str(payload.get("repository_id") or ""),
            "candidate_artifact": relative_path,
            "concept_candidate_id": str(payload.get("concept_candidate_id") or ""),
            "candidate_semantic_fingerprint": str(payload.get("semantic_fingerprint") or ""),
            "source_reduced_inventory_id": str((payload.get("source_reduced_inventory") or {}).get("reduced_inventory_id") or ""),
            "source_reduced_semantic_fingerprint": str((payload.get("source_reduced_inventory") or {}).get("semantic_fingerprint") or ""),
            "concepts": concept_rows,
        })
    strength_counts: dict[str, Counter[str]] = {concept: Counter() for concept in CONCEPTS}
    for repository in repositories:
        for concept in CONCEPTS:
            strength_counts[concept][str(repository["concepts"][concept]["signal_strength"])] += 1
    summary={
        "repository_count": len(repositories),
        "strength_counts": {
            concept:{strength:int(strength_counts[concept].get(strength,0)) for strength in STRENGTHS}
            for concept in CONCEPTS
        },
    }
    result={
        "format": CONCEPT_INDEX_FORMAT,
        "candidate_format": CONCEPT_CANDIDATE_FORMAT,
        "classifier_version": CLASSIFIER_VERSION,
        "repositories": repositories,
        "summary": summary,
        "claim_boundary": (
            "Batch lookup index over already-classified repository concept candidate artifacts only. "
            "The index does not extract new evidence, change signal strength, or confirm any concept."
        ),
    }
    result["index_id"] = stable_id(
        "repository_concept_index",
        CONCEPT_INDEX_FORMAT,
        [(row["repository_id"],row["candidate_semantic_fingerprint"]) for row in repositories],
    )
    result["semantic_fingerprint"] = fingerprint(result)
    validate_concept_index(result)
    return result


def validate_concept_index(payload: Mapping[str, Any]) -> None:
    if payload.get("format") != CONCEPT_INDEX_FORMAT:
        raise ConceptIndexError(f"expected {CONCEPT_INDEX_FORMAT!r}, got {payload.get('format')!r}")
    if payload.get("candidate_format") != CONCEPT_CANDIDATE_FORMAT or payload.get("classifier_version") != CLASSIFIER_VERSION:
        raise ConceptIndexError("concept index candidate/classifier contract mismatch")
    rows=payload.get("repositories")
    if not isinstance(rows,list):
        raise ConceptIndexError("concept index repositories must be a list")
    seen=set()
    for row in rows:
        if not isinstance(row,Mapping):
            raise ConceptIndexError("invalid concept index repository row")
        repository_id=str(row.get("repository_id") or "")
        if not repository_id or repository_id in seen:
            raise ConceptIndexError(f"invalid or duplicate repository_id: {repository_id!r}")
        seen.add(repository_id)
        concepts=row.get("concepts")
        if not isinstance(concepts,Mapping) or set(concepts)!=set(CONCEPTS):
            raise ConceptIndexError(f"concept index canonical concept set mismatch: {repository_id}")
        for concept,value in concepts.items():
            if not isinstance(value,Mapping) or str(value.get("signal_strength") or "") not in STRENGTHS:
                raise ConceptIndexError(f"invalid indexed strength: {repository_id}/{concept}")
    expected=str(payload.get("semantic_fingerprint") or "")
    material=deepcopy(dict(payload)); material.pop("semantic_fingerprint",None)
    if expected != fingerprint(material):
        raise ConceptIndexError("concept index semantic fingerprint mismatch")


def build_concept_index(batch_root: str | Path, *, output: str | Path | None = None, repository_ids: set[str] | None = None) -> dict[str, Any]:
    root=Path(batch_root).expanduser().resolve()
    repositories_root=root/"repositories"
    if not repositories_root.is_dir():
        raise ConceptIndexError(f"batch repositories directory not found: {repositories_root}")
    candidates=[]
    for repository_dir in sorted(path for path in repositories_root.iterdir() if path.is_dir() and not path.name.startswith(".")):
        if repository_ids is not None and repository_dir.name not in repository_ids:
            continue
        candidate_path=repository_dir/"repository_concept_candidates.json"
        if not candidate_path.is_file():
            continue
        try:
            payload=json.loads(candidate_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConceptIndexError(f"invalid concept candidate JSON: {candidate_path}") from exc
        relative=candidate_path.relative_to(root).as_posix()
        candidates.append((relative,payload))
    result=build_concept_index_payload(candidates)
    target=Path(output).expanduser().resolve() if output is not None else root/"repository_concept_index.json"
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(canonical_json_bytes(result))
    return result


def verify_concept_index(path: str | Path) -> dict[str, Any]:
    candidate=Path(path).expanduser().resolve()
    if candidate.is_dir():
        candidate=candidate/"repository_concept_index.json"
    if not candidate.is_file():
        raise ConceptIndexError(f"concept index not found: {candidate}")
    try:
        payload=json.loads(candidate.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConceptIndexError(f"invalid concept index JSON: {candidate}") from exc
    validate_concept_index(payload)
    return {"status":"verified","format":payload["format"],"index_id":payload["index_id"],"semantic_fingerprint":payload["semantic_fingerprint"],"repository_count":payload["summary"]["repository_count"]}


def query_concept_index(index: Mapping[str, Any], *, concept: str, min_strength: str = "moderate", mechanism: str | None = None) -> list[dict[str, Any]]:
    validate_concept_index(index)
    if concept not in CONCEPTS:
        raise ConceptIndexError(f"unknown concept: {concept!r}")
    if min_strength not in STRENGTHS:
        raise ConceptIndexError(f"unknown minimum strength: {min_strength!r}")
    minimum=STRENGTH_RANK[min_strength]
    mechanism_norm=str(mechanism or "").strip().casefold()
    result=[]
    for row in index.get("repositories") or []:
        value=(row.get("concepts") or {}).get(concept) or {}
        strength=str(value.get("signal_strength") or "")
        if STRENGTH_RANK.get(strength,-1) < minimum:
            continue
        mechanisms=[str(item) for item in value.get("mechanisms") or []]
        if mechanism_norm and mechanism_norm not in {item.casefold() for item in mechanisms}:
            continue
        result.append({
            "repository_id": row["repository_id"],
            "concept": concept,
            "signal_strength": strength,
            "mechanisms": mechanisms,
            "basis_signal_ids": list(value.get("basis_signal_ids") or []),
            "candidate_artifact": row["candidate_artifact"],
        })
    result.sort(key=lambda row:(-STRENGTH_RANK[row["signal_strength"]],str(row["repository_id"])))
    return result


def load_concept_index(path: str | Path) -> dict[str, Any]:
    candidate=Path(path).expanduser().resolve()
    if candidate.is_dir(): candidate=candidate/"repository_concept_index.json"
    if not candidate.is_file(): raise ConceptIndexError(f"concept index not found: {candidate}")
    payload=json.loads(candidate.read_text(encoding="utf-8")); validate_concept_index(payload); return payload
