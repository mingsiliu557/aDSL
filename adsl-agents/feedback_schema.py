from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .models import (
    AnalysisContext,
    CheckerAnalysisContext,
    CheckerFinding,
    CheckerResult,
    RegionEvidence,
)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _legacy_category(status: str, code: str) -> tuple[str, str]:
    upper = code.upper()
    if status == "ERROR":
        return "infrastructure_error", "analysis"
    if "SEMANTIC" in upper or "CRITICAL_SURFACE" in upper and "MISSING" in upper:
        return "missing_semantics", "semantics"
    if status == "INDETERMINATE" or any(
        marker in upper for marker in ("NOT_CONVERGED", "CLIP_FAILURE", "NO_LOAD_PATH")
    ):
        return "evidence_insufficient", "analysis"
    if status == "FAIL":
        return "physical_violation", "geometry"
    return "evidence_insufficient", "unknown"


def canonicalize_result(result: CheckerResult, *, required: bool) -> CheckerResult:
    """Return a v2 result without inventing evidence absent from a v1 payload."""

    if result.findings or result.status == "PASS":
        findings = [
            finding.model_copy(update={"required": required})
            for finding in result.findings
        ]
        return result.model_copy(update={"version": max(result.version, 2), "findings": findings})

    findings: list[CheckerFinding] = []
    violations = result.violations or [{"code": result.status, "message": result.summary}]
    for index, violation in enumerate(violations, 1):
        code = str(violation.get("code") or f"{result.checker.upper()}_{result.status}")
        category, repairability = _legacy_category(result.status, code)
        part_names = violation.get("active_geometry_names") or violation.get("part_names") or []
        region = None
        if part_names:
            region = RegionEvidence(
                kind="parts",
                frame="authored_scene",
                part_names=[str(value) for value in part_names],
            )
        findings.append(
            CheckerFinding(
                finding_id=f"{result.checker}:{code}:{index}",
                rule_id=code,
                category=category,
                applicability="applicable" if result.status == "FAIL" else "unknown",
                applicability_basis="derived conservatively from legacy checker status",
                region=region,
                evidence_refs=list(result.artifacts.values()),
                required=required,
                repairability=repairability,
                message=str(violation.get("message") or result.summary),
                domain=dict(violation),
            )
        )
    context = result.analysis_context or CheckerAnalysisContext(
        checker=result.checker,
        checker_version=result.version,
        details={"assumptions": result.assumptions, "legacy_context": True},
    )
    return result.model_copy(
        update={"version": 2, "analysis_context": context, "findings": findings}
    )


def build_analysis_context(
    *,
    source_path: Path,
    geometry_path: Path,
    source_index_path: Path | None,
    checker_runs: Iterable[Any],
    print_orientation_editable: bool,
) -> AnalysisContext:
    runs = list(checker_runs)
    immutable_inputs: dict[str, str] = {}
    spec_payloads: list[dict[str, Any]] = []
    contexts: list[CheckerAnalysisContext] = []
    for run in runs:
        spec_payloads.append(run.spec.model_dump())
        if run.result.analysis_context is not None:
            contexts.append(run.result.analysis_context)
        else:
            contexts.append(
                CheckerAnalysisContext(
                    checker=run.spec.name,
                    checker_version=run.result.version,
                    details={"assumptions": run.result.assumptions},
                )
            )
        for token in run.command:
            candidate = Path(token)
            if candidate.is_file():
                immutable_inputs[str(candidate.resolve())] = sha256_file(candidate)
    return AnalysisContext(
        source_sha256=sha256_file(source_path),
        geometry_sha256=sha256_file(geometry_path),
        source_index_sha256=(
            sha256_file(source_index_path)
            if source_index_path is not None and source_index_path.is_file()
            else None
        ),
        checker_specs_sha256=stable_hash(spec_payloads),
        required_checkers=[run.spec.name for run in runs if run.spec.required],
        checker_contexts=contexts,
        immutable_inputs=immutable_inputs,
        print_orientation_editable=print_orientation_editable,
    )


def findings_payload(results: Iterable[CheckerResult]) -> dict[str, Any]:
    findings = [
        finding.model_dump()
        for result in results
        for finding in result.findings
    ]
    return {"version": 1, "findings": findings}


__all__ = [
    "build_analysis_context",
    "canonicalize_result",
    "findings_payload",
    "sha256_file",
    "stable_hash",
]
