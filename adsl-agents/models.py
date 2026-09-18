from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .utils.usage import UsageTotals


class ObjectComponent(BaseModel):
    name: str
    description: str


class ObjectPlan(BaseModel):
    object_name: str
    components: list[ObjectComponent]
    relations: list[str]
    critic_checklist: list[str]


class PrintPartPlan(BaseModel):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]*$')
    components: list[str] = Field(min_length=1)

    @model_validator(mode='after')
    def valid_print_part_name(self):
        from adsl.core.assembly import _print_part_identifier
        _print_part_identifier(self.id)
        return self


class FixedConnectionPlan(BaseModel):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]*$')
    tab_part: str
    slot_part: str
    tab_port: str
    slot_port: str
    parameter_name: str
    interface_type: Literal['tab_slot']
    insertion_direction: str
    fit_intent: str


class FixedAssemblyPlan(ObjectPlan):
    print_parts: list[PrintPartPlan]
    connections: list[FixedConnectionPlan]
    root_part: str
    mm_per_unit: float = Field(gt=0)
    final_size_mm: list[float] = Field(min_length=3, max_length=3)

    @model_validator(mode='after')
    def valid_assembly(self):
        import math
        ids = [p.id for p in self.print_parts]
        if len(ids) != len(set(ids)) or self.root_part not in ids:
            raise ValueError('unique print-part IDs and an existing root are required')
        members = [c for p in self.print_parts for c in p.components]
        components = [c.name for c in self.components]
        if len(members) != len(set(members)) or sorted(members) != sorted(components):
            raise ValueError('print-part membership must partition all semantic components')
        placed, links, ports = {self.root_part}, set(), set()
        for c in self.connections:
            if c.id in links or c.tab_part == c.slot_part or {c.tab_part,c.slot_part} - set(ids):
                raise ValueError('duplicate connection, self connection or unknown print part')
            if c.slot_part not in placed or c.tab_part in placed:
                raise ValueError('unsupported placement: connections must be an ordered rooted tree, receiver before tab child')
            if (c.slot_part,c.slot_port) in ports:
                raise ValueError('receiver port already occupied')
            ports.add((c.slot_part,c.slot_port))
            links.add(c.id)
            placed.add(c.tab_part)
        if placed != set(ids) or not all(math.isfinite(v) and v > 0 for v in [self.mm_per_unit,*self.final_size_mm]):
            raise ValueError('all print parts must be placed; fixed scale and size must be finite and positive')
        return self


class FixedAssemblyConfig(BaseModel):
    # The request, not the generated program, freezes scale and required size.
    mm_per_unit: float = Field(gt=0, allow_inf_nan=False)
    fit_offset_mm: float = Field(allow_inf_nan=False)
    final_size_mm: list[float] = Field(min_length=3, max_length=3)

    @model_validator(mode='after')
    def valid_size(self):
        import math
        if not all(math.isfinite(v) and v > 0 for v in self.final_size_mm):
            raise ValueError('final_size_mm must contain three finite positive dimensions')
        return self


class EditPlan(BaseModel):
    summary: str
    preserved_features: list[str]
    changes: list[str]
    patch_scope: list[str]


class DebuggerDecision(BaseModel):
    bug_description: str
    suggested_fix: str


class ImageCriticDecision(BaseModel):
    approved: bool
    observations: list[str]
    required_changes: list[str] = Field(default_factory=list)


class CodeCriticDecision(BaseModel):
    approved: bool
    observations: list[str]
    required_changes: list[str] = Field(default_factory=list)
    image_critic_corrections: list[str] = Field(default_factory=list)


CheckerStatus = Literal["PASS", "FAIL", "INDETERMINATE", "ERROR"]
FindingCategory = Literal[
    "geometry_failure",
    "physical_violation",
    "evidence_insufficient",
    "missing_semantics",
    "infrastructure_error",
    "optimization_opportunity",
]
FindingApplicability = Literal["applicable", "not_applicable", "unknown"]
FindingRepairability = Literal[
    "geometry",
    "design_variable",
    "analysis",
    "semantics",
    "unknown",
]
MetricComparator = Literal["lt", "le", "gt", "ge", "eq"]
RegionKind = Literal["point", "aabb", "faces", "layers", "parts", "global", "unknown"]


class MetricEvidence(BaseModel):
    name: str
    value: float | int | bool | None = None
    unit: str | None = None
    threshold: float | int | bool | None = None
    comparator: MetricComparator | None = None
    relative_tolerance: float = Field(default=0.01, ge=0)
    absolute_tolerance: float = Field(default=1e-9, ge=0)


class RegionEvidence(BaseModel):
    kind: RegionKind = "unknown"
    frame: str | None = None
    unit: str | None = None
    point: list[float] | None = None
    bounds: list[list[float]] | None = None
    face_ids: list[int] = Field(default_factory=list)
    layer_range: list[float] | None = None
    part_names: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class RelationEndpoint(BaseModel):
    role: str
    part_names: list[str] = Field(default_factory=list)
    feature_ids: list[str] = Field(default_factory=list)
    point: list[float] | None = None


class RelationEvidence(BaseModel):
    kind: Literal["disconnected", "weak_contact"]
    frame: str = "authored_scene"
    unit: str = "scene_unit"
    endpoints: list[RelationEndpoint] = Field(min_length=2, max_length=2)
    distance: MetricEvidence | None = None
    bridge_feature_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class SourceCandidate(BaseModel):
    feature_id: str
    source_ids: list[str] = Field(default_factory=list)
    source_locations: list[str] = Field(default_factory=list)
    method: Literal["direct", "relation", "geometric", "dependency", "global_heuristic"]
    relation_role: str | None = None
    overlap_score: float | None = Field(default=None, ge=0)
    evidence: list[str] = Field(default_factory=list)
    ambiguous: bool = False


class CheckerFinding(BaseModel):
    finding_id: str
    rule_id: str
    category: FindingCategory
    applicability: FindingApplicability = "applicable"
    applicability_basis: str | None = None
    metric: MetricEvidence | None = None
    region: RegionEvidence | None = None
    relations: list[RelationEvidence] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    source_candidates: list[SourceCandidate] = Field(default_factory=list)
    required: bool = True
    repairability: FindingRepairability = "unknown"
    message: str = ""
    domain: dict[str, Any] = Field(default_factory=dict)


class CheckerAnalysisContext(BaseModel):
    checker: str
    checker_version: int = 1
    source_frame: str = "authored_scene"
    analysis_frame: str | None = None
    source_length_unit: str = "scene_unit"
    analysis_length_unit: str | None = None
    source_to_analysis: list[list[float]] | None = None
    use_pose: str = "authored"
    print_pose: str = "fixed_authored"
    assumptions_source: str = "checker_result"
    details: dict[str, Any] = Field(default_factory=dict)


class AnalysisContext(BaseModel):
    version: int = 1
    source_sha256: str
    geometry_sha256: str
    source_index_sha256: str | None = None
    checker_specs_sha256: str
    required_checkers: list[str] = Field(default_factory=list)
    checker_contexts: list[CheckerAnalysisContext] = Field(default_factory=list)
    immutable_inputs: dict[str, str] = Field(default_factory=dict)
    print_orientation_editable: bool = False


class RepairTarget(BaseModel):
    feature_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    parameters: list[str] = Field(default_factory=list)
    allowed_scopes: list[str] = Field(default_factory=list)


class RepairProposal(BaseModel):
    proposal_id: str
    finding_ids: list[str] = Field(min_length=1)
    hypothesis: str
    evidence: list[str] = Field(default_factory=list)
    target: RepairTarget
    action: Literal[
        "resize",
        "relayout",
        "add_local_structure",
        "reshape",
        "change_print_orientation",
        "request_evidence",
    ]
    parameter_bounds: dict[str, Any] = Field(default_factory=dict)
    expected_improvements: list[str] = Field(default_factory=list)
    possible_regressions: list[str] = Field(default_factory=list)
    preserve: list[str] = Field(default_factory=list)
    rerun_checkers: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)


class RepairPolicy(BaseModel):
    max_candidates_per_round: int = Field(default=1, ge=1, le=10)
    max_total_candidates: int | None = Field(default=5, ge=1, le=50)
    time_budget_seconds: float = Field(default=7200.0, gt=0)
    print_orientation_editable: bool = False
    default_relative_tolerance: float = Field(default=0.01, ge=0)
    default_absolute_tolerance: float = Field(default=1e-9, ge=0)


class CheckerSpec(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    command: list[str] = Field(min_length=1)
    required: bool = True
    timeout_seconds: float = Field(default=900.0, gt=0)
    prepend_environment: dict[str, list[str]] = Field(default_factory=dict)


class CheckerResult(BaseModel):
    checker: str
    version: int = 1
    status: CheckerStatus
    summary: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    violations: list[dict[str, Any]] = Field(default_factory=list)
    assumptions: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)
    analysis_context: CheckerAnalysisContext | None = None
    findings: list[CheckerFinding] = Field(default_factory=list)


class EngineeringCriticDecision(BaseModel):
    approved: bool
    observations: list[str]
    required_changes: list[str] = Field(default_factory=list)
    checker_interpretation: list[str] = Field(default_factory=list)
    repair_proposals: list[RepairProposal] = Field(default_factory=list)
    unresolved_findings: list[str] = Field(default_factory=list)


class PlannedEngineeringCriticDecision(EngineeringCriticDecision):
    stop_category: Literal['no_change_needed', 'insufficient_localization', 'scope_limited', 'no_reasonable_plan'] | None = None


@dataclass(frozen=True)
class ObjectRequest:
    requirement: str
    workspace: Path
    task_id: str
    image_paths: tuple[Path, ...] = ()
    articulation: bool = False
    max_rounds: int = 5
    checker_specs: tuple[CheckerSpec, ...] = ()
    check_first: bool = False
    repair_policy: RepairPolicy = field(default_factory=RepairPolicy)
    # Explicit local-edit experiment only. Empty keeps production behavior.
    overhang_experiment: dict[str, Any] = field(default_factory=dict)
    fixed_assembly: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ObjectRunResult:
    workspace: Path
    source_path: Path
    glb_path: Path
    urdf_path: Path | None
    render_paths: tuple[Path, ...]
    selected_round: int
    approved: bool
    usage: UsageTotals


EditKind = Literal["continue", "extend", "variant"]


__all__ = [
    "CodeCriticDecision",
    "CheckerResult",
    "CheckerAnalysisContext",
    "CheckerFinding",
    "CheckerSpec",
    "CheckerStatus",
    "AnalysisContext",
    "DebuggerDecision",
    "EditKind",
    "EditPlan",
    "EngineeringCriticDecision",
    "MetricEvidence",
    "ImageCriticDecision",
    "ObjectPlan",
    "ObjectComponent",
    "ObjectRequest",
    "ObjectRunResult",
    "RegionEvidence",
    "RelationEndpoint",
    "RelationEvidence",
    "RepairPolicy",
    "RepairProposal",
    "RepairTarget",
    "SourceCandidate",
]
