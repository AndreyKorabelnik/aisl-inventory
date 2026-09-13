from __future__ import annotations

import math
from typing import Any


def source_tree_scope(path: str) -> str:
    """Return a deterministic repository-local source-tree bucket.

    This is intentionally syntax/business agnostic. For conventional source trees it
    keeps the path prefix before ``src``; otherwise it uses the first path segment.
    """
    parts = str(path).split("/")
    if "src" in parts:
        idx = parts.index("src")
        return "/".join(parts[:idx]) or "."
    return parts[0] if len(parts) > 1 else "."


def structural_salience_score(*, count: int, coverage_status: str) -> float:
    """Repository-local ranking metadata; never a novelty score.

    The score preserves the existing Repository Inventory v5 semantic: repeated
    observed structure plus probe coverage create a bounded ranking signal. The old
    analyzer-frontier/diagnostic terms are deliberately omitted here because standalone
    v6 neither interprets framework capability nor promotes diagnostics into salience.
    """
    repetition = min(35.0, 8.0 * math.log2(max(1, int(count)) + 1))
    coverage = 20.0 if coverage_status == "complete" else 10.0 if coverage_status == "partial" else 5.0
    # Preserve the v5 baseline for an observed family without claiming framework-frontier
    # knowledge. In v5 the non-unsupported frontier term was 10.0.
    observed_family_baseline = 10.0
    return round(min(100.0, repetition + coverage + observed_family_baseline), 3)


def repository_local_salience(
    *,
    count: int,
    file_count: int,
    source_tree_scopes: list[str] | tuple[str, ...],
    coverage_status: str,
) -> dict[str, Any]:
    scopes = sorted({str(scope) for scope in source_tree_scopes})
    return {
        "structural_salience_score": structural_salience_score(
            count=count,
            coverage_status=coverage_status,
        ),
        "repository_local_salience": {
            "occurrence_count": int(count),
            "file_count": int(file_count),
            "source_tree_scope_count": len(scopes),
            "source_tree_scopes": scopes,
            "coverage_status": str(coverage_status),
            "novelty_claim": False,
            "novelty_ownership": "downstream_cross_repository_mining",
            "basis": "repository_local_observed_frequency_and_probe_coverage",
        },
    }
