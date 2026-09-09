from __future__ import annotations

from itertools import product
from typing import Iterable

import numpy as np
from pydantic import BaseModel, Field

from .models import CheckerAnalysisContext, CheckerFinding, RegionEvidence, SourceCandidate
from .source_index import RuntimeFeature, SourceIndex


class LocalizedFinding(BaseModel):
    finding_id: str
    rule_id: str
    candidates: list[SourceCandidate] = Field(default_factory=list)
    ambiguity: list[str] = Field(default_factory=list)
    unresolved_reason: str | None = None


class LocalizationReport(BaseModel):
    version: int = 1
    source_sha256: str
    source_index_sha256: str
    findings: list[LocalizedFinding] = Field(default_factory=list)


def _clean_part_name(value: str) -> str:
    value = value.split("#", 1)[0]
    return value.rstrip("/").rsplit("/", 1)[-1]


def _candidate(
    feature: RuntimeFeature,
    *,
    method: str,
    score: float | None,
    evidence: list[str],
    ambiguous: bool = False,
) -> SourceCandidate:
    return SourceCandidate(
        feature_id=feature.feature_id,
        source_ids=feature.source_ids,
        source_locations=feature.source_locations,
        method=method,
        overlap_score=score,
        evidence=evidence,
        ambiguous=ambiguous or feature.resolution != "complete",
    )


def _context_for(
    finding: CheckerFinding,
    contexts: Iterable[CheckerAnalysisContext],
) -> CheckerAnalysisContext | None:
    checker = finding.finding_id.split(":", 1)[0]
    return next((context for context in contexts if context.checker == checker), None)


def _transform_point(matrix: np.ndarray, point: Iterable[float]) -> np.ndarray:
    homogeneous = np.ones(4, dtype=float)
    homogeneous[:3] = np.asarray(list(point), dtype=float)
    transformed = matrix @ homogeneous
    if abs(float(transformed[3])) < 1e-12:
        raise ValueError("coordinate transform produced a point at infinity")
    return transformed[:3] / transformed[3]


def _region_in_source_frame(
    region: RegionEvidence,
    context: CheckerAnalysisContext | None,
) -> tuple[np.ndarray | None, np.ndarray | None, str | None]:
    if region.kind not in {"point", "aabb"}:
        return None, None, None
    same_frame = region.frame in {None, "authored_scene"}
    inverse = None
    if not same_frame:
        if context is None or context.source_to_analysis is None:
            return None, None, "explicit analysis-to-source transform is unavailable"
        try:
            inverse = np.linalg.inv(np.asarray(context.source_to_analysis, dtype=float))
        except (ValueError, np.linalg.LinAlgError):
            return None, None, "analysis-to-source transform is invalid"
    if region.kind == "point" and region.point is not None:
        point = np.asarray(region.point, dtype=float)
        if inverse is not None:
            point = _transform_point(inverse, point)
        return point, point, None
    if region.kind == "aabb" and region.bounds is not None and len(region.bounds) == 2:
        lower = np.asarray(region.bounds[0], dtype=float)
        upper = np.asarray(region.bounds[1], dtype=float)
        if inverse is None:
            return lower, upper, None
        corners = [
            _transform_point(inverse, corner)
            for corner in product(*zip(lower, upper))
        ]
        return np.min(corners, axis=0), np.max(corners, axis=0), None
    return None, None, "region coordinates are incomplete"


def _overlap_score(
    query_lower: np.ndarray,
    query_upper: np.ndarray,
    feature: RuntimeFeature,
) -> float:
    if feature.bounds is None:
        return 0.0
    lower = np.asarray(feature.bounds[0], dtype=float)
    upper = np.asarray(feature.bounds[1], dtype=float)
    if np.allclose(query_lower, query_upper):
        tolerance = max(float(np.max(upper - lower)) * 1e-6, 1e-9)
        return 1.0 if np.all(query_lower >= lower - tolerance) and np.all(query_lower <= upper + tolerance) else 0.0
    intersection = np.maximum(np.minimum(query_upper, upper) - np.maximum(query_lower, lower), 0.0)
    intersection_volume = float(np.prod(intersection))
    query_volume = float(np.prod(np.maximum(query_upper - query_lower, 0.0)))
    return intersection_volume / query_volume if query_volume > 0 else 0.0


def _direct_candidates(
    region: RegionEvidence,
    features: list[RuntimeFeature],
) -> list[SourceCandidate]:
    names = {_clean_part_name(value) for value in region.part_names}
    matched = [
        feature
        for feature in features
        if feature.name in names
        or feature.semantic_path in region.part_names
        or _clean_part_name(feature.semantic_path) in names
    ]
    ambiguous = len(matched) > 1
    return [
        _candidate(
            feature,
            method="direct",
            score=1.0,
            evidence=[f"checker named part {feature.name!r}"],
            ambiguous=ambiguous,
        )
        for feature in matched
    ]


def _global_candidates(features: list[RuntimeFeature]) -> list[SourceCandidate]:
    bounded = [feature for feature in features if feature.bounds is not None and feature.parent_feature_id]
    if not bounded:
        return []
    global_min_z = min(float(feature.bounds[0][2]) for feature in bounded)
    global_max_z = max(float(feature.bounds[1][2]) for feature in bounded)
    height = max(global_max_z - global_min_z, 1e-9)
    ground_epsilon = max(height * 0.01, 1e-6)
    ground = [
        feature
        for feature in bounded
        if float(feature.bounds[0][2]) <= global_min_z + ground_epsilon
    ]

    def eccentricity(feature: RuntimeFeature) -> float:
        lower = np.asarray(feature.bounds[0], dtype=float)
        upper = np.asarray(feature.bounds[1], dtype=float)
        center = (lower + upper) / 2.0
        volume = float(np.prod(np.maximum(upper - lower, 0.0)))
        return float(np.linalg.norm(center[:2])) * max(volume, 1e-12)

    eccentric = sorted(bounded, key=eccentricity, reverse=True)[:3]
    output: list[SourceCandidate] = []
    seen: set[str] = set()
    for feature in [*ground, *eccentric]:
        if feature.feature_id in seen:
            continue
        seen.add(feature.feature_id)
        reasons = []
        if feature in ground:
            reasons.append("feature AABB touches the global support plane")
        if feature in eccentric:
            reasons.append("feature has a large volume-weighted horizontal offset")
        output.append(
            _candidate(
                feature,
                method="global_heuristic",
                score=None,
                evidence=reasons,
                ambiguous=True,
            )
        )
    return output


def localize_findings(
    findings: Iterable[CheckerFinding],
    source_index: SourceIndex,
    *,
    checker_contexts: Iterable[CheckerAnalysisContext] = (),
) -> tuple[list[CheckerFinding], LocalizationReport]:
    contexts = list(checker_contexts)
    localized_findings: list[CheckerFinding] = []
    reports: list[LocalizedFinding] = []
    for finding in findings:
        region = finding.region
        candidates: list[SourceCandidate] = []
        ambiguity: list[str] = []
        unresolved_reason = None
        if finding.repairability not in {"geometry", "design_variable"}:
            unresolved_reason = f"repairability={finding.repairability} is not source-editable"
        elif region is None or region.kind == "unknown":
            unresolved_reason = "checker did not provide a localizable region"
        elif region.kind == "parts":
            candidates = _direct_candidates(region, source_index.features)
            if not candidates:
                unresolved_reason = "named parts were not found in the runtime feature index"
        elif region.kind == "global":
            candidates = _global_candidates(source_index.features)
            ambiguity.append("global failure yields support/mass candidates, not a causal location")
        elif region.kind in {"point", "aabb"}:
            lower, upper, error = _region_in_source_frame(
                region, _context_for(finding, contexts)
            )
            if error:
                unresolved_reason = error
            elif lower is not None and upper is not None:
                scored = [
                    (feature, _overlap_score(lower, upper, feature))
                    for feature in source_index.features
                    if feature.parent_feature_id is not None
                ]
                matched = [(feature, score) for feature, score in scored if score > 0]
                candidates = [
                    _candidate(
                        feature,
                        method="geometric",
                        score=score,
                        evidence=["checker region overlaps the feature AABB after coordinate conversion"],
                        ambiguous=len(matched) > 1,
                    )
                    for feature, score in sorted(matched, key=lambda item: item[1], reverse=True)
                ]
                if not candidates:
                    unresolved_reason = "checker region does not overlap any indexed feature AABB"
        elif region.kind == "layers" and region.part_names:
            candidates = _direct_candidates(region, source_index.features)
            if not candidates:
                unresolved_reason = "active partial-build parts were not found in the feature index"
        else:
            unresolved_reason = f"region kind {region.kind} has no deterministic locator"

        if len(candidates) > 1:
            ambiguity.append("multiple source candidates match this finding")
        localized = finding.model_copy(update={"source_candidates": candidates})
        localized_findings.append(localized)
        reports.append(
            LocalizedFinding(
                finding_id=finding.finding_id,
                rule_id=finding.rule_id,
                candidates=candidates,
                ambiguity=ambiguity,
                unresolved_reason=unresolved_reason,
            )
        )
    return localized_findings, LocalizationReport(
        source_sha256=source_index.source_sha256,
        source_index_sha256=source_index.index_sha256,
        findings=reports,
    )


__all__ = ["LocalizationReport", "LocalizedFinding", "localize_findings"]
