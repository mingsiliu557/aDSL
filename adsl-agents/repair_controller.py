from __future__ import annotations

import json
from pathlib import Path
import shutil
import time
from typing import Any, Iterable

from .feedback_schema import sha256_file
from .models import CheckerFinding, RepairPolicy, RepairProposal
from .repair_policy import proposal_fingerprint
from .source_index import SourceIndex


class RepairController:
    def __init__(
        self,
        *,
        workspace: Path,
        round_root: Path,
        baseline_source: Path,
        source_index: SourceIndex,
        findings: Iterable[CheckerFinding],
        checker_specs_sha256: str,
        policy: RepairPolicy,
    ) -> None:
        self.workspace = workspace
        self.round_root = round_root
        self.baseline_source = baseline_source
        self.source_index = source_index
        self.findings = {finding.finding_id: finding for finding in findings}
        self.checker_specs_sha256 = checker_specs_sha256
        self.policy = policy
        self.history_path = workspace / "repair_history.jsonl"
        self.tried_fingerprints = self._load_fingerprints()
        self.started_at = self._history_started_at()

    def _history_started_at(self) -> float:
        if not self.history_path.is_file():
            return time.time()
        timestamps: list[float] = []
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
                timestamps.append(float(value["recorded_at"]))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
        return min(timestamps) if timestamps else time.time()

    def _load_fingerprints(self) -> set[str]:
        if not self.history_path.is_file():
            return set()
        output: set[str] = set()
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if value.get("fingerprint"):
                output.add(str(value["fingerprint"]))
        return output

    def _history_count(self) -> int:
        if not self.history_path.is_file():
            return 0
        return sum(1 for line in self.history_path.read_text(encoding="utf-8").splitlines() if line.strip())

    def budget_error(self) -> str | None:
        if self._history_count() >= self.policy.max_total_candidates:
            return "maximum total candidate budget reached"
        if time.time() - self.started_at >= self.policy.time_budget_seconds:
            return "repair time budget reached"
        return None

    def normalize_proposal(
        self, proposal: RepairProposal
    ) -> tuple[RepairProposal | None, list[str]]:
        errors: list[str] = []
        unknown_findings = sorted(set(proposal.finding_ids) - set(self.findings))
        if unknown_findings:
            errors.append(f"unknown finding ids: {unknown_findings}")
        targeted = [
            self.findings[finding_id]
            for finding_id in proposal.finding_ids
            if finding_id in self.findings
        ]
        if any(
            finding.repairability not in {"geometry", "design_variable"}
            for finding in targeted
        ):
            errors.append("proposal targets a non-source-editable finding")
        allowed_candidates = [
            candidate for finding in targeted for candidate in finding.source_candidates
        ]
        allowed_features = {candidate.feature_id for candidate in allowed_candidates}
        target_features = list(proposal.target.feature_ids)
        if not target_features and len(allowed_features) == 1:
            target_features = sorted(allowed_features)
        if not target_features:
            errors.append("proposal has no unambiguous feature target")
        elif not set(target_features).issubset(allowed_features):
            errors.append("proposal targets a feature outside localization evidence")

        bridge_features = {
            candidate.feature_id
            for candidate in allowed_candidates
            if candidate.relation_role == "bridge_parent"
        }
        if bridge_features:
            if proposal.action == "add_local_structure":
                if not set(target_features).issubset(bridge_features):
                    errors.append(
                        "add_local_structure must target the localized bridge_parent scope"
                    )
            elif set(target_features) & bridge_features:
                errors.append(
                    "endpoint geometry edits cannot target the bridge_parent scope"
                )
        target_candidates = [
            candidate
            for candidate in allowed_candidates
            if candidate.feature_id in target_features
        ]
        allowed_source_ids = {
            source_id
            for candidate in target_candidates
            for source_id in candidate.source_ids
        }
        source_ids = list(proposal.target.source_ids)
        if not source_ids:
            source_ids = sorted(allowed_source_ids)
        if not source_ids:
            errors.append("localized feature has no resolved source construct")
        elif not set(source_ids).issubset(allowed_source_ids):
            errors.append("proposal targets source outside localization evidence")
        else:
            # Include constructor/class dependencies recorded for the selected
            # feature. The model cannot expand its own edit authority: this
            # union comes only from deterministic localization evidence.
            source_ids = sorted(allowed_source_ids)

        nodes_by_id = {node.source_id: node for node in self.source_index.source_nodes}
        allowed_scopes = sorted(
            {
                scope
                for source_id in source_ids
                if (node := nodes_by_id.get(source_id)) is not None
                if (
                    scope := node.owner_class
                    or (node.name if node.kind == "class" else None)
                )
            }
        )
        if proposal.action == "change_print_orientation" and not self.policy.print_orientation_editable:
            errors.append("print orientation is fixed by repair policy")
        if proposal.action == "request_evidence":
            errors.append("request_evidence is not an executable geometry candidate")
        if errors:
            return None, errors
        normalized = proposal.model_copy(
            update={
                "target": proposal.target.model_copy(
                    update={
                        "feature_ids": target_features,
                        "source_ids": source_ids,
                        "allowed_scopes": allowed_scopes,
                    }
                )
            }
        )
        fingerprint = proposal_fingerprint(
            normalized,
            source_sha256=sha256_file(self.baseline_source),
            checker_specs_sha256=self.checker_specs_sha256,
        )
        if fingerprint in self.tried_fingerprints:
            return None, ["identical proposal was already tried for this source and context"]
        return normalized, []

    def prepare_candidate(self, proposal: RepairProposal, index: int) -> tuple[Path, Path, str]:
        safe_id = "".join(
            character if character.isalnum() or character in "_.-" else "_"
            for character in proposal.proposal_id
        ).strip("._-") or f"candidate_{index:02d}"
        candidate_root = self.round_root / "candidates" / f"{index:02d}_{safe_id}"
        candidate_root.mkdir(parents=True, exist_ok=False)
        candidate_source = candidate_root / "source.py"
        shutil.copy2(self.baseline_source, candidate_source)
        fingerprint = proposal_fingerprint(
            proposal,
            source_sha256=sha256_file(self.baseline_source),
            checker_specs_sha256=self.checker_specs_sha256,
        )
        (candidate_root / "proposal.json").write_text(
            proposal.model_dump_json(indent=2), encoding="utf-8"
        )
        return candidate_root, candidate_source, fingerprint

    def record(self, payload: dict[str, Any]) -> None:
        payload = {"recorded_at": time.time(), **payload}
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        fingerprint = payload.get("fingerprint")
        if fingerprint:
            self.tried_fingerprints.add(str(fingerprint))


__all__ = ["RepairController"]
