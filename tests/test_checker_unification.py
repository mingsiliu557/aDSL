from __future__ import annotations

from pathlib import Path

from agents import AgentOutputSchema
import runpy

from adsl.agents.feedback_schema import canonicalize_result
from adsl.agents.analysis_geometry import build_analysis_geometry
from adsl.agents.localization import localize_findings
from adsl.agents.models import (
    CheckerAnalysisContext,
    CheckerFinding,
    CheckerResult,
    EngineeringCriticDecision,
    MetricEvidence,
    RegionEvidence,
    RepairPolicy,
    RepairProposal,
    RepairTarget,
    RelationEndpoint,
    RelationEvidence,
    SourceCandidate,
)
from adsl.agents.repair_controller import RepairController
from adsl.agents.repair_policy import assess_candidate, validate_patch_scope
from adsl.agents.source_index import RuntimeFeature, SourceIndex, build_source_index
from experiments.workflow_checkers.run import enrich_result, standing_result, topology_result


def test_engineering_decision_can_use_non_strict_sdk_schema() -> None:
    schema = AgentOutputSchema(
        EngineeringCriticDecision,
        strict_json_schema=False,
    )

    value = schema.validate_json(
        '{"approved":false,"observations":[],"required_changes":[],'
        '"checker_interpretation":[],"repair_proposals":[],'
        '"unresolved_findings":[]}'
    )

    assert isinstance(value, EngineeringCriticDecision)


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "source.py"
    path.write_text(
        "from adsl.core import *\n\n"
        "class Stand(Asset):\n"
        "    def __init__(self):\n"
        "        super().__init__(label='Stand')\n"
        "        self.attach_part('base', Cube((1, 1, 0.1), center=(0, 0, 0.05)))\n"
        "        for index in range(2):\n"
        "            self.attach_part(f'leg_{index}', Cube((0.1, 0.1, 1), center=(index, 0, 0.5)))\n\n"
        "scene = Stand()\n",
        encoding="utf-8",
    )
    return path


def test_v1_result_is_upgraded_without_fake_metric_values() -> None:
    legacy = CheckerResult(
        checker="legacy",
        status="INDETERMINATE",
        summary="mesh evidence missing",
        violations=[{"code": "MESH_NOT_CONVERGED", "message": "not converged"}],
    )
    upgraded = canonicalize_result(legacy, required=True)
    assert upgraded.version == 2
    assert upgraded.violations == legacy.violations
    assert upgraded.findings[0].category == "evidence_insufficient"
    assert upgraded.findings[0].metric is None
    assert upgraded.findings[0].repairability == "analysis"

    infrastructure = canonicalize_result(
        CheckerResult(
            checker="legacy",
            status="ERROR",
            summary="solver unavailable",
            violations=[{"code": "CHECKER_INFRASTRUCTURE_ERROR"}],
        ),
        required=True,
    )
    assert infrastructure.findings[0].category == "infrastructure_error"
    assert infrastructure.findings[0].repairability == "analysis"


def test_standing_adapter_emits_global_typed_finding(tmp_path: Path) -> None:
    raw = {
        "states": [{
            "state": "initial",
            "geometric_verdict": "UNSTABLE",
            "mujoco": {
                "available": True,
                "settle": {"tipped": True, "peak_tilt_deg": 31.0, "final_tilt_deg": 30.0},
                "force_probe": {},
            },
        }]
    }
    result = enrich_result(standing_result(raw, tmp_path / "raw.json"))
    finding = result.findings[0]
    assert result.version == 2
    assert finding.metric == MetricEvidence(
        name="peak_tilt_deg", value=31.0, unit="deg", threshold=25.0, comparator="le"
    )
    assert finding.region is not None and finding.region.kind == "global"
    assert result.analysis_context is not None
    assert result.analysis_context.source_to_analysis is not None


def test_other_adapters_emit_domain_metric_and_region_evidence() -> None:
    progressive = enrich_result(
        CheckerResult(
            checker="progressive",
            status="FAIL",
            summary="partial tipped",
            violations=[{
                "code": "FIRST_PARTIAL_TILT_GT_25_DEG",
                "height_fraction": 0.4,
                "peak_tilt_deg": 33.0,
                "active_geometry_names": ["stem", "shelf"],
            }],
        )
    )
    assert progressive.findings[0].region is not None
    assert progressive.findings[0].region.kind == "layers"
    assert progressive.findings[0].region.part_names == ["stem", "shelf"]

    fea = enrich_result(
        CheckerResult(
            checker="fea",
            status="FAIL",
            summary="displacement exceeded",
            metrics={
                "max_displacement_over_characteristic_length": 0.04,
                "thresholds": {"max_displacement_ratio": 0.01},
            },
            violations=[{
                "code": "DISPLACEMENT_GT_1PCT_CHARACTERISTIC_LENGTH",
                "hotspot_centroid_m": [0.2, 0.1, 0.5],
            }],
            assumptions={"scale": {"factor_m_per_scene_unit": 2.0}},
        )
    )
    assert fea.findings[0].metric is not None
    assert fea.findings[0].metric.threshold == 0.01
    assert fea.findings[0].region is not None
    assert fea.findings[0].region.frame == "fea_m"
    assert fea.analysis_context is not None
    assert fea.analysis_context.source_to_analysis[0][0] == 2.0

    overhang = enrich_result(
        CheckerResult(
            checker="overhang",
            status="FAIL",
            summary="contact reduction missed",
            violations=[{
                "code": "SUPPORT_CONTACT_AREA_REDUCTION_LT_TARGET",
                "observed_reduction_fraction": 0.0,
                "required_reduction_fraction": 0.01,
                "supported_regions": [{"collision": "mug/handle#collision_0"}],
            }],
        )
    )
    assert overhang.findings[0].region is not None
    assert overhang.findings[0].region.part_names == ["mug/handle#collision_0"]

    support = enrich_result(
        CheckerResult(
            checker="support",
            status="FAIL",
            summary="critical contact",
            violations=[{
                "code": "SUPPORT_TOUCHES_CRITICAL_SURFACE",
                "contact_overlap_area_mm2": 2.5,
                "overlap_regions": [{"semantic_path": "chair/seat"}],
            }],
        ),
        {"profile": {"critical_overlap_epsilon_mm2": 0.01}},
    )
    assert support.findings[0].metric is not None
    assert support.findings[0].metric.threshold == 0.01
    assert support.findings[0].region is not None
    assert support.findings[0].region.part_names == ["chair/seat"]


def test_source_index_links_literal_parts_and_marks_dynamic_names(tmp_path: Path) -> None:
    path = _source(tmp_path)
    scene = runpy.run_path(str(path))["scene"]
    index = build_source_index(path, scene)
    base = next(feature for feature in index.features if feature.name == "base")
    leg = next(feature for feature in index.features if feature.name == "leg_0")
    assert base.source_ids and base.resolution == "complete"
    assert base.bounds == [[-0.5, -0.5, 0.0], [0.5, 0.5, 0.1]]
    assert leg.source_ids and leg.resolution == "partial"
    dynamic_node = next(
        node for node in index.source_nodes
        if node.kind == "attach_part" and node.semantic_name is None
    )
    assert dynamic_node.resolution == "partial"


def test_source_index_expands_asset_feature_to_constructor_class(tmp_path: Path) -> None:
    path = tmp_path / "nested.py"
    path.write_text(
        "from adsl.core import *\n\n"
        "class Base(Asset):\n"
        "    def __init__(self):\n"
        "        super().__init__(label='Base')\n"
        "        self.attach_part('body', Sphere(radius=1))\n\n"
        "class Lamp(Asset):\n"
        "    def __init__(self):\n"
        "        super().__init__(label='Lamp')\n"
        "        self.attach_part('base', Base())\n\n"
        "scene = Lamp()\n",
        encoding="utf-8",
    )
    scene = runpy.run_path(str(path))["scene"]
    index = build_source_index(path, scene)
    feature = next(value for value in index.features if value.name == "base")
    nodes = {
        node.source_id: node
        for node in index.source_nodes
        if node.source_id in feature.source_ids
    }

    assert {node.kind for node in nodes.values()} == {"attach_part", "class"}
    assert any(node.kind == "class" and node.name == "Base" for node in nodes.values())

    finding = CheckerFinding(
        finding_id="standing:TILT:initial",
        rule_id="TILT",
        category="physical_violation",
        repairability="geometry",
        source_candidates=[
            SourceCandidate(
                feature_id=feature.feature_id,
                source_ids=feature.source_ids,
                source_locations=feature.source_locations,
                method="global_heuristic",
            )
        ],
    )
    controller = RepairController(
        workspace=tmp_path,
        round_root=tmp_path / "round",
        baseline_source=path,
        source_index=index,
        findings=[finding],
        checker_specs_sha256="spec",
        policy=RepairPolicy(),
    )
    direct_attach = next(
        source_id
        for source_id, node in nodes.items()
        if node.kind == "attach_part"
    )
    proposal = _proposal(finding.finding_id, feature.feature_id, direct_attach)
    normalized, errors = controller.normalize_proposal(proposal)

    assert not errors and normalized is not None
    assert normalized.target.allowed_scopes == ["Base", "Lamp"]
    assert set(normalized.target.source_ids) == set(feature.source_ids)


def test_localization_applies_explicit_coordinate_transform() -> None:
    feature = RuntimeFeature(
        feature_id="feature:root/leg",
        semantic_path="root/leg",
        name="leg",
        asset_class="Cube",
        parent_feature_id="feature:root",
        bounds=[[0, 0, 0], [1, 1, 1]],
        source_ids=["source:leg"],
        source_locations=["L4-L4"],
    )
    index = SourceIndex(
        source_path="source.py",
        source_sha256="a",
        index_sha256="b",
        root_feature_id="feature:root",
        features=[feature],
    )
    finding = CheckerFinding(
        finding_id="fea:HOTSPOT:1",
        rule_id="HOTSPOT",
        category="physical_violation",
        repairability="geometry",
        region=RegionEvidence(kind="point", frame="fea_m", unit="m", point=[2, 2, 2]),
    )
    context = CheckerAnalysisContext(
        checker="fea",
        analysis_frame="fea_m",
        analysis_length_unit="m",
        source_to_analysis=[
            [2, 0, 0, 0],
            [0, 2, 0, 0],
            [0, 0, 2, 0],
            [0, 0, 0, 1],
        ],
    )
    localized, report = localize_findings([finding], index, checker_contexts=[context])
    assert localized[0].source_candidates[0].feature_id == feature.feature_id
    assert localized[0].source_candidates[0].method == "geometric"
    assert report.findings[0].unresolved_reason is None


def test_localization_refuses_cross_frame_guess_without_transform() -> None:
    index = SourceIndex(
        source_path="source.py",
        source_sha256="a",
        index_sha256="b",
        root_feature_id="feature:root",
    )
    finding = CheckerFinding(
        finding_id="support:CONTACT:1",
        rule_id="CONTACT",
        category="physical_violation",
        repairability="geometry",
        region=RegionEvidence(kind="point", frame="print_mm", point=[1, 2, 3]),
    )
    localized, report = localize_findings([finding], index)
    assert localized[0].source_candidates == []
    assert "transform" in str(report.findings[0].unresolved_reason)


def _proposal(finding_id: str, feature_id: str, source_id: str) -> RepairProposal:
    return RepairProposal(
        proposal_id="widen-base",
        finding_ids=[finding_id],
        hypothesis="A wider base may increase the support margin.",
        target=RepairTarget(feature_ids=[feature_id], source_ids=[source_id]),
        action="resize",
        parameter_bounds={"size_x": [1.0, 1.5]},
        rerun_checkers=["standing"],
    )


def test_scope_validation_accepts_target_class_and_rejects_module_changes(tmp_path: Path) -> None:
    baseline = _source(tmp_path)
    scene = runpy.run_path(str(baseline))["scene"]
    index = build_source_index(baseline, scene)
    feature = next(value for value in index.features if value.name == "base")
    proposal = _proposal("standing:TILT:1", feature.feature_id, feature.source_ids[0])

    candidate = tmp_path / "candidate.py"
    candidate.write_text(
        baseline.read_text(encoding="utf-8").replace("Cube((1, 1, 0.1)", "Cube((1.4, 1, 0.1)"),
        encoding="utf-8",
    )
    accepted = validate_patch_scope(
        baseline, candidate, proposal=proposal, source_index=index
    )
    assert accepted.valid
    candidate.write_text("import os\n" + candidate.read_text(encoding="utf-8"), encoding="utf-8")
    rejected = validate_patch_scope(
        baseline, candidate, proposal=proposal, source_index=index
    )
    assert not rejected.valid
    assert any("outside" in error for error in rejected.violations)


def _result(checker: str, status: str, finding: CheckerFinding | None = None) -> CheckerResult:
    return CheckerResult(
        checker=checker,
        version=2,
        status=status,
        summary=status,
        findings=[] if finding is None else [finding],
    )


def test_candidate_acceptance_requires_target_progress_and_no_pass_regression() -> None:
    before_finding = CheckerFinding(
        finding_id="standing:TILT:initial",
        rule_id="TILT",
        category="physical_violation",
        repairability="geometry",
        metric=MetricEvidence(name="tilt", value=35.0, threshold=25.0, comparator="le"),
    )
    after_finding = before_finding.model_copy(
        update={"metric": MetricEvidence(name="tilt", value=30.0, threshold=25.0, comparator="le")}
    )
    decision = assess_candidate(
        [_result("standing", "FAIL", before_finding), _result("support", "PASS")],
        [_result("standing", "FAIL", after_finding), _result("support", "PASS")],
        target_finding_ids=[before_finding.finding_id],
        appearance_approved=True,
        policy=RepairPolicy(),
    )
    assert decision.accepted
    regressed = assess_candidate(
        [_result("standing", "FAIL", before_finding), _result("support", "PASS")],
        [_result("standing", "PASS"), _result("support", "FAIL")],
        target_finding_ids=[before_finding.finding_id],
        appearance_approved=True,
        policy=RepairPolicy(),
    )
    assert not regressed.accepted
    assert regressed.regressions == ["support: PASS -> FAIL"]

    fea_before = CheckerFinding(
        finding_id="fea:DISPLACEMENT:1",
        rule_id="DISPLACEMENT",
        category="physical_violation",
        repairability="geometry",
        metric=MetricEvidence(
            name="displacement_ratio",
            value=0.02,
            threshold=0.01,
            comparator="le",
        ),
    )
    fea_after = fea_before.model_copy(
        update={
            "metric": MetricEvidence(
                name="displacement_ratio",
                value=0.04,
                threshold=0.01,
                comparator="le",
            )
        }
    )
    cross_regressed = assess_candidate(
        [_result("standing", "FAIL", before_finding), _result("fea", "FAIL", fea_before)],
        [_result("standing", "FAIL", after_finding), _result("fea", "FAIL", fea_after)],
        target_finding_ids=[before_finding.finding_id],
        appearance_approved=True,
        policy=RepairPolicy(),
    )
    assert not cross_regressed.accepted
    assert any("fea/DISPLACEMENT" in row for row in cross_regressed.regressions)


def test_repair_controller_rejects_unlocalized_feature_and_duplicate(tmp_path: Path) -> None:
    source = _source(tmp_path)
    scene = runpy.run_path(str(source))["scene"]
    index = build_source_index(source, scene)
    feature = next(value for value in index.features if value.name == "base")
    finding = CheckerFinding(
        finding_id="standing:TILT:initial",
        rule_id="TILT",
        category="physical_violation",
        repairability="geometry",
        region=RegionEvidence(kind="global"),
        source_candidates=[
            SourceCandidate(
                feature_id=feature.feature_id,
                source_ids=feature.source_ids,
                source_locations=feature.source_locations,
                method="global_heuristic",
            )
        ],
    )
    controller = RepairController(
        workspace=tmp_path,
        round_root=tmp_path / "round",
        baseline_source=source,
        source_index=index,
        findings=[finding],
        checker_specs_sha256="spec",
        policy=RepairPolicy(),
    )
    bad = _proposal(finding.finding_id, "feature:not-allowed", feature.source_ids[0])
    assert controller.normalize_proposal(bad)[0] is None
    good = _proposal(finding.finding_id, feature.feature_id, feature.source_ids[0])
    normalized, errors = controller.normalize_proposal(good)
    assert normalized is not None and not errors
    _, _, fingerprint = controller.prepare_candidate(normalized, 1)
    controller.record({"fingerprint": fingerprint, "accepted": False})
    assert controller.normalize_proposal(good)[0] is None
    limited = RepairController(
        workspace=tmp_path,
        round_root=tmp_path / "round_limited",
        baseline_source=source,
        source_index=index,
        findings=[finding],
        checker_specs_sha256="spec",
        policy=RepairPolicy(max_total_candidates=1),
    )
    assert limited.budget_error() == "maximum total candidate budget reached"

def test_source_ids_are_unique_for_repeated_dsl_calls(tmp_path: Path) -> None:
    source = _source(tmp_path)
    scene = runpy.run_path(str(source))["scene"]
    index = build_source_index(source, scene)
    ids = [node.source_id for node in index.source_nodes]
    assert len(ids) == len(set(ids))


def test_analysis_geometry_preserves_source_linked_hierarchy(tmp_path: Path) -> None:
    source = _source(tmp_path)
    scene = runpy.run_path(str(source))["scene"]
    index = build_source_index(source, scene)
    manifest = build_analysis_geometry(source, scene, index)
    assert manifest["source_sha256"] == index.source_sha256
    assert manifest["source_index_sha256"] == index.index_sha256
    assert manifest["geometry_sha256"]
    children = manifest["root"]["children"]
    assert {child["name"] for child in children} == {"base", "leg_0", "leg_1"}
    assert all(child["feature_id"] for child in children)
    assert children[0]["primitives"]


def _topology_raw() -> dict[str, object]:
    return {
        "status": "FAIL",
        "mode": "load_path",
        "part_count": 3,
        "component_count": 2,
        "components": [["Stand/base"], ["Stand/leg_0", "Stand/leg_1"]],
        "active_part_paths": [],
        "weak_contacts": [],
        "numerical_tolerance_m": 1e-8,
        "scale": {"factor_m_per_scene_unit": 1.0},
        "violations": [{
            "code": "LOAD_PATH_DISCONNECTED",
            "message": "load has no bonded path to support",
            "load_part_paths": ["Stand/leg_0"],
            "disconnected_component_paths": ["Stand/leg_0", "Stand/leg_1"],
            "support_part_paths": ["Stand/base"],
            "relation": {
                "left_path": "Stand/leg_0",
                "right_path": "Stand/base",
                "left_feature_id": "feature:Stand/leg_0",
                "right_feature_id": "feature:Stand/base",
                "distance_m": 0.1,
                "closest_points_m": [[0, 0, 0.1], [0, 0, 0]],
                "contact_kind": "gap",
            },
        }],
    }


def test_topology_relation_localizes_both_endpoints_and_bridge(tmp_path: Path) -> None:
    source = _source(tmp_path)
    scene = runpy.run_path(str(source))["scene"]
    index = build_source_index(source, scene)
    result = enrich_result(topology_result(_topology_raw(), tmp_path / "raw.json"))
    finding = result.findings[0]
    assert finding.category == "physical_violation"
    assert finding.repairability == "geometry"
    assert len(finding.relations) == 1
    localized, report = localize_findings(
        result.findings,
        index,
        checker_contexts=[result.analysis_context],
    )
    candidates = localized[0].source_candidates
    roles = {candidate.relation_role for candidate in candidates}
    assert {"load", "support", "bridge_parent"}.issubset(roles)
    assert {candidate.feature_id for candidate in candidates} == {
        "feature:Stand/leg_0",
        "feature:Stand/base",
        "feature:Stand",
    }
    assert report.findings[0].unresolved_reason is None


def test_repair_controller_enforces_relation_bridge_scope(tmp_path: Path) -> None:
    source = _source(tmp_path)
    scene = runpy.run_path(str(source))["scene"]
    index = build_source_index(source, scene)
    result = enrich_result(topology_result(_topology_raw(), tmp_path / "raw.json"))
    localized, _ = localize_findings(
        result.findings,
        index,
        checker_contexts=[result.analysis_context],
    )
    finding = localized[0]
    controller = RepairController(
        workspace=tmp_path,
        round_root=tmp_path / "round_topology",
        baseline_source=source,
        source_index=index,
        findings=[finding],
        checker_specs_sha256="topology-spec",
        policy=RepairPolicy(),
    )
    bridge = next(
        candidate
        for candidate in finding.source_candidates
        if candidate.relation_role == "bridge_parent"
    )
    endpoint = next(
        candidate
        for candidate in finding.source_candidates
        if candidate.relation_role == "load"
    )
    wrong = RepairProposal(
        proposal_id="wrong-bridge-resize",
        finding_ids=[finding.finding_id],
        hypothesis="incorrectly resize the bridge scope",
        target=RepairTarget(
            feature_ids=[bridge.feature_id],
            source_ids=bridge.source_ids,
        ),
        action="resize",
        rerun_checkers=["topology", "fea", "standing"],
    )
    assert controller.normalize_proposal(wrong)[0] is None
    connector = RepairProposal(
        proposal_id="add-local-connector",
        finding_ids=[finding.finding_id],
        hypothesis="a bounded connector can bond the measured gap",
        target=RepairTarget(
            feature_ids=[bridge.feature_id],
            source_ids=bridge.source_ids,
        ),
        action="add_local_structure",
        parameter_bounds={"maximum_connector_extent_scene_units": 0.15},
        rerun_checkers=["topology", "fea", "standing"],
    )
    normalized, errors = controller.normalize_proposal(connector)
    assert normalized is not None
    assert not errors
    assert normalized.target.feature_ids == [bridge.feature_id]

    wrong_connector = connector.model_copy(
        update={
            "proposal_id": "wrong-endpoint-connector",
            "target": RepairTarget(
                feature_ids=[endpoint.feature_id],
                source_ids=endpoint.source_ids,
            ),
        }
    )
    assert controller.normalize_proposal(wrong_connector)[0] is None


def test_topology_indeterminate_is_not_source_editable(tmp_path: Path) -> None:
    raw = {
        "status": "INDETERMINATE",
        "mode": "load_path",
        "violations": [{
            "code": "ANALYTIC_RECONSTRUCTION_UNAVAILABLE",
            "message": "unsupported analytic primitive",
        }],
    }
    result = enrich_result(topology_result(raw, tmp_path / "raw.json"))
    finding = result.findings[0]
    assert finding.category == "evidence_insufficient"
    assert finding.repairability == "analysis"
