from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, Field

from .feedback_schema import sha256_file, stable_hash
from .models import CheckerFinding, CheckerResult, MetricEvidence, RepairPolicy, RepairProposal
from .source_index import SourceIndex


class ScopeValidation(BaseModel):
    valid: bool
    allowed_classes: list[str] = Field(default_factory=list)
    changed_symbols: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)


class CandidateDecision(BaseModel):
    accepted: bool
    reason: str
    target_improvements: list[str] = Field(default_factory=list)
    regressions: list[str] = Field(default_factory=list)
    unavailable_checks: list[str] = Field(default_factory=list)


def proposal_fingerprint(
    proposal: RepairProposal,
    *,
    source_sha256: str,
    checker_specs_sha256: str,
) -> str:
    return stable_hash(
        {
            "proposal": proposal.model_dump(),
            "source_sha256": source_sha256,
            "checker_specs_sha256": checker_specs_sha256,
        }
    )


def _module_symbols(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    symbols: dict[str, str] = {}
    import_index = 0
    expression_index = 0
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            key = f"class:{node.name}"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            key = f"function:{node.name}"
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            import_index += 1
            key = f"import:{import_index}"
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [target.id for target in targets if isinstance(target, ast.Name)]
            key = "assign:" + (",".join(names) if names else f"line:{node.lineno}")
        else:
            expression_index += 1
            key = f"statement:{expression_index}:{type(node).__name__}"
        symbols[key] = ast.dump(node, include_attributes=False)
    return symbols


def validate_patch_scope(
    baseline_source: str | Path,
    candidate_source: str | Path,
    *,
    proposal: RepairProposal,
    source_index: SourceIndex,
) -> ScopeValidation:
    baseline = Path(baseline_source)
    candidate = Path(candidate_source)
    nodes_by_id = {node.source_id: node for node in source_index.source_nodes}
    allowed_classes = {
        scope.split("/", 1)[0]
        for scope in proposal.target.allowed_scopes
        if scope and not scope.startswith("<")
    }
    for source_id in proposal.target.source_ids:
        node = nodes_by_id.get(source_id)
        if node is not None:
            scope = node.owner_class or (node.name if node.kind == "class" else None)
            if scope:
                allowed_classes.add(scope)
    if proposal.action == "change_print_orientation":
        return ScopeValidation(
            valid=False,
            allowed_classes=sorted(allowed_classes),
            violations=["print-orientation edits are disabled in the MVP workflow"],
        )
    before = _module_symbols(baseline)
    after = _module_symbols(candidate)
    changed = sorted(
        key
        for key in set(before) | set(after)
        if before.get(key) != after.get(key)
    )
    violations: list[str] = []
    if not changed:
        violations.append("candidate did not change the source")
    if not allowed_classes:
        violations.append("proposal has no resolved source scope")
    for symbol in changed:
        if not symbol.startswith("class:"):
            violations.append(f"change outside an allowed class: {symbol}")
            continue
        class_name = symbol.split(":", 1)[1]
        if class_name not in allowed_classes:
            violations.append(f"changed unapproved class: {class_name}")
    return ScopeValidation(
        valid=not violations,
        allowed_classes=sorted(allowed_classes),
        changed_symbols=changed,
        violations=violations,
    )


def immutable_inputs_match(expected: dict[str, str]) -> tuple[bool, list[str]]:
    changed: list[str] = []
    for path_value, expected_hash in expected.items():
        path = Path(path_value)
        if not path.is_file():
            changed.append(f"missing immutable input: {path}")
        elif sha256_file(path) != expected_hash:
            changed.append(f"immutable input changed: {path}")
    return not changed, changed


def _distance(metric: MetricEvidence) -> float | None:
    if metric.value is None or metric.threshold is None or metric.comparator is None:
        return None
    if isinstance(metric.value, bool) or isinstance(metric.threshold, bool):
        return 0.0 if metric.value == metric.threshold else 1.0
    value = float(metric.value)
    threshold = float(metric.threshold)
    if metric.comparator in {"lt", "le"}:
        return max(value - threshold, 0.0)
    if metric.comparator in {"gt", "ge"}:
        return max(threshold - value, 0.0)
    return abs(value - threshold)


def _tolerance(
    before: MetricEvidence,
    after: MetricEvidence,
    policy: RepairPolicy,
) -> float:
    numeric = [
        abs(float(value))
        for value in (before.value, before.threshold, after.value, after.threshold)
        if value is not None and not isinstance(value, bool)
    ]
    scale = max([1.0, *numeric])
    relative = max(
        before.relative_tolerance,
        after.relative_tolerance,
        policy.default_relative_tolerance,
    )
    absolute = max(
        before.absolute_tolerance,
        after.absolute_tolerance,
        policy.default_absolute_tolerance,
    )
    return max(absolute, relative * scale)


def _finding_map(results: Iterable[CheckerResult]) -> dict[tuple[str, str], CheckerFinding]:
    output: dict[tuple[str, str], CheckerFinding] = {}
    for result in results:
        for finding in result.findings:
            output[(result.checker, finding.rule_id)] = finding
    return output


def assess_candidate(
    baseline_results: Iterable[CheckerResult],
    candidate_results: Iterable[CheckerResult],
    *,
    target_finding_ids: Iterable[str],
    appearance_approved: bool,
    policy: RepairPolicy,
) -> CandidateDecision:
    baseline = {result.checker: result for result in baseline_results}
    candidate = {result.checker: result for result in candidate_results}
    unavailable = sorted(set(baseline) - set(candidate))
    regressions: list[str] = []
    if unavailable:
        return CandidateDecision(
            accepted=False,
            reason="one or more baseline checkers were not rerun",
            unavailable_checks=unavailable,
        )
    for checker, before in baseline.items():
        after = candidate[checker]
        if before.status == "PASS" and after.status != "PASS":
            regressions.append(f"{checker}: PASS -> {after.status}")
        if after.status == "ERROR":
            regressions.append(f"{checker}: candidate checker infrastructure error")
    if not appearance_approved:
        regressions.append("appearance/function preservation review did not approve")

    target_ids = set(target_finding_ids)
    before_findings = _finding_map(baseline.values())
    after_findings = _finding_map(candidate.values())
    target_improvements: list[str] = []
    for checker, before in baseline.items():
        after = candidate[checker]
        targeted_rules = {
            finding.rule_id
            for finding in before.findings
            if finding.finding_id in target_ids
        }
        if targeted_rules and after.status == "PASS":
            target_improvements.append(f"{checker}: targeted checker reached PASS")
        for rule_id in targeted_rules:
            old_finding = before_findings.get((checker, rule_id))
            new_finding = after_findings.get((checker, rule_id))
            if old_finding is None or old_finding.metric is None:
                continue
            if new_finding is None:
                if after.status == "PASS":
                    continue
                continue
            if new_finding.metric is None:
                continue
            old_distance = _distance(old_finding.metric)
            new_distance = _distance(new_finding.metric)
            if old_distance is None or new_distance is None:
                continue
            tolerance = _tolerance(old_finding.metric, new_finding.metric, policy)
            if old_distance - new_distance > tolerance:
                target_improvements.append(
                    f"{checker}/{rule_id}: violation distance {old_distance:g} -> {new_distance:g}"
                )

    for key, old_finding in before_findings.items():
        new_finding = after_findings.get(key)
        if old_finding.metric is None or new_finding is None or new_finding.metric is None:
            continue
        old_distance = _distance(old_finding.metric)
        new_distance = _distance(new_finding.metric)
        if old_distance is None or new_distance is None:
            continue
        tolerance = _tolerance(old_finding.metric, new_finding.metric, policy)
        if new_distance - old_distance > tolerance:
            regressions.append(
                f"{key[0]}/{key[1]}: violation distance {old_distance:g} -> {new_distance:g}"
            )

    if regressions:
        return CandidateDecision(
            accepted=False,
            reason="candidate introduced a required regression",
            target_improvements=target_improvements,
            regressions=regressions,
        )
    if not target_improvements:
        return CandidateDecision(
            accepted=False,
            reason="candidate did not produce a measurable target improvement",
        )
    return CandidateDecision(
        accepted=True,
        reason="candidate improved a target without required regressions",
        target_improvements=target_improvements,
    )


__all__ = [
    "CandidateDecision",
    "ScopeValidation",
    "assess_candidate",
    "immutable_inputs_match",
    "proposal_fingerprint",
    "validate_patch_scope",
]
