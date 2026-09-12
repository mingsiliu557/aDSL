#!/usr/bin/env python3
"""Adapters from the validated experiment analyzers to the object-agent checker protocol."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import traceback
from typing import Any

from adsl.agents.models import (
    CheckerAnalysisContext,
    CheckerFinding,
    CheckerResult,
    MetricEvidence,
    RegionEvidence,
    RelationEndpoint,
    RelationEvidence,
)


REPO = Path(__file__).resolve().parents[2]
DEFAULT_CCX = Path(
    os.environ.get(
        "ADSL_CCX_BIN",
        "/vepfs_default/chanxueyan/lhp/lms/fea_runtime/calculix-2.23/ccx_2.23",
    )
)
DEFAULT_SLICER = Path(
    os.environ.get(
        "ADSL_PRUSASLICER_BIN",
        "/vepfs_default/chanxueyan/lhp/lms/tools/prusaslicer/2.4.0/sysroot/usr/bin/prusa-slicer",
    )
)


def _region_from_rows(
    rows: list[dict[str, Any]],
    *,
    frame: str,
    unit: str,
) -> RegionEvidence:
    part_names: list[str] = []
    bounds: list[list[float]] | None = None
    lowers: list[list[float]] = []
    uppers: list[list[float]] = []
    for row in rows:
        for key in ("object", "name", "part", "geometry", "collision", "semantic_path"):
            if row.get(key):
                part_names.append(str(row[key]))
        for value in row.get("matched_geometries", []):
            part_names.append(str(value))
        candidate = row.get("bounds_mm") or row.get("bounds") or row.get("aabb")
        if isinstance(candidate, list) and len(candidate) == 2:
            lowers.append([float(value) for value in candidate[0]])
            uppers.append([float(value) for value in candidate[1]])
    if lowers and uppers:
        bounds = [
            [min(row[index] for row in lowers) for index in range(3)],
            [max(row[index] for row in uppers) for index in range(3)],
        ]
    if part_names:
        return RegionEvidence(
            kind="parts",
            frame=frame,
            unit=unit,
            part_names=sorted(set(part_names)),
            bounds=bounds,
            details={"raw_region_count": len(rows)},
        )
    if bounds is not None:
        return RegionEvidence(kind="aabb", frame=frame, unit=unit, bounds=bounds)
    return RegionEvidence(
        kind="unknown",
        frame=frame,
        unit=unit,
        details={"raw_region_count": len(rows)},
    )


def enrich_result(
    result: CheckerResult,
    config: dict[str, Any] | None = None,
) -> CheckerResult:
    """Add v2 findings while preserving every v1 field for compatibility."""

    config = config or {}
    checker = result.checker
    context = CheckerAnalysisContext(checker=checker, checker_version=2)
    if checker in {"standing", "progressive"}:
        context = context.model_copy(
            update={
                "analysis_frame": "urdf_z_up",
                "analysis_length_unit": "scene_unit",
                "source_to_analysis": [
                    [1.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0],
                ],
                "details": {"assumptions": result.assumptions},
            }
        )
    elif checker in {"fea", "topology"}:
        scale = result.assumptions.get("scale") or {}
        factor = scale.get("factor_m_per_scene_unit")
        matrix = None
        if isinstance(factor, (int, float)) and float(factor) > 0:
            value = float(factor)
            matrix = [
                [value, 0.0, 0.0, 0.0],
                [0.0, value, 0.0, 0.0],
                [0.0, 0.0, value, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        context = context.model_copy(
            update={
                "analysis_frame": "fea_m",
                "analysis_length_unit": "m",
                "source_to_analysis": matrix,
                "details": {"assumptions": result.assumptions},
            }
        )
    elif checker in {"overhang", "support"}:
        context = context.model_copy(
            update={
                "analysis_frame": "print_mm",
                "analysis_length_unit": "mm",
                "source_to_analysis": None,
                "details": {
                    "assumptions": result.assumptions,
                    "transform_status": "not recorded by current slicer analyzer",
                },
            }
        )

    findings: list[CheckerFinding] = []
    for index, violation in enumerate(result.violations, 1):
        code = str(violation.get("code") or f"{checker.upper()}_{result.status}")
        category = "physical_violation"
        repairability = "geometry"
        applicability = "applicable"
        if result.status == "ERROR":
            category, repairability, applicability = "infrastructure_error", "analysis", "unknown"
        elif checker == "fea" and code == "MESH_INVALID" and result.status == "INDETERMINATE":
            category, repairability, applicability = "geometry_failure", "geometry", "unknown"
        elif result.status == "INDETERMINATE" or code in {
            "MESH_NOT_CONVERGED",
            "PARTIAL_CLIP_FAILURE",
        }:
            category, repairability, applicability = "evidence_insufficient", "analysis", "unknown"
        if "SEMANTIC" in code or code == "CRITICAL_SURFACE_INDETERMINATE":
            category, repairability, applicability = "missing_semantics", "semantics", "unknown"

        metric = None
        region = None
        relations: list[RelationEvidence] = []
        if checker == "standing" and "TILT" in code:
            metric = MetricEvidence(
                name="peak_tilt_deg",
                value=violation.get("peak_tilt_deg"),
                unit="deg",
                threshold=25.0,
                comparator="le",
            )
            region = RegionEvidence(
                kind="global",
                frame="authored_scene",
                details={"state": violation.get("state")},
            )
        elif checker == "progressive" and "TILT" in code:
            metric = MetricEvidence(
                name="peak_tilt_deg",
                value=violation.get("peak_tilt_deg"),
                unit="deg",
                threshold=25.0,
                comparator="le",
            )
            fraction = violation.get("height_fraction")
            region = RegionEvidence(
                kind="layers",
                frame="authored_scene",
                unit="fraction",
                layer_range=[float(fraction), float(fraction)] if fraction is not None else None,
                part_names=[str(value) for value in violation.get("active_geometry_names", [])],
                details={"events": violation.get("events", [])},
            )
        elif checker == "topology":
            paths = [
                str(value)
                for key in (
                    "part_paths",
                    "load_part_paths",
                    "disconnected_component_paths",
                    "support_part_paths",
                )
                for value in violation.get(key, [])
            ]
            if paths:
                region = RegionEvidence(
                    kind="parts",
                    frame="authored_scene",
                    unit="scene_unit",
                    part_names=sorted(set(paths)),
                )
            component_count = violation.get("component_count")
            if component_count is not None:
                metric = MetricEvidence(
                    name="component_count",
                    value=component_count,
                    unit="count",
                    threshold=1,
                    comparator="le",
                )
            raw_relation = violation.get("relation")
            if isinstance(raw_relation, dict):
                distance = raw_relation.get("distance_m")
                left_path = str(raw_relation.get("left_path", ""))
                right_path = str(raw_relation.get("right_path", ""))
                load_paths = set(map(str, violation.get("load_part_paths", [])))
                support_paths = set(map(str, violation.get("support_part_paths", [])))
                points = raw_relation.get("closest_points_m") or [None, None]
                left_role = (
                    "load" if left_path in load_paths
                    else "support" if left_path in support_paths
                    else "left"
                )
                right_role = (
                    "load" if right_path in load_paths
                    else "support" if right_path in support_paths
                    else "right"
                )
                relations = [
                    RelationEvidence(
                        kind=(
                            "weak_contact"
                            if raw_relation.get("contact_kind") == "point_or_edge"
                            else "disconnected"
                        ),
                        frame="occ_m",
                        unit="m",
                        endpoints=[
                            RelationEndpoint(
                                role=left_role,
                                part_names=[left_path],
                                feature_ids=[str(raw_relation["left_feature_id"])]
                                if raw_relation.get("left_feature_id") else [],
                                point=points[0],
                            ),
                            RelationEndpoint(
                                role=right_role,
                                part_names=[right_path],
                                feature_ids=[str(raw_relation["right_feature_id"])]
                                if raw_relation.get("right_feature_id") else [],
                                point=points[1],
                            ),
                        ],
                        distance=MetricEvidence(
                            name="endpoint_separation",
                            value=distance,
                            unit="m",
                            threshold=0.0,
                            comparator="le",
                            absolute_tolerance=float(
                                result.assumptions.get("numerical_tolerance_m", 1e-8)
                            ),
                        ),
                        details={"contact_kind": raw_relation.get("contact_kind")},
                    )
                ]
        elif checker == "fea":
            if code == "MESH_INVALID" and violation.get("bounds_m") is not None:
                region = RegionEvidence(kind="aabb", frame="fea_m", unit="m",
                                        bounds=violation["bounds_m"])
            threshold_map = result.metrics.get("thresholds", {})
            if code == "DISPLACEMENT_GT_1PCT_CHARACTERISTIC_LENGTH":
                metric = MetricEvidence(
                    name="max_displacement_over_characteristic_length",
                    value=result.metrics.get("max_displacement_over_characteristic_length"),
                    unit="ratio",
                    threshold=threshold_map.get("max_displacement_ratio"),
                    comparator="le",
                )
            elif code == "NOMINAL_YIELD_FOS_LT_2":
                metric = MetricEvidence(
                    name="nominal_safety_factor",
                    value=result.metrics.get("nominal_safety_factor"),
                    unit="ratio",
                    threshold=threshold_map.get("minimum_nominal_yield_fos"),
                    comparator="ge",
                )
            elif code == "LINEAR_BUCKLING_FACTOR_LT_2":
                metric = MetricEvidence(
                    name="first_positive_buckling_factor",
                    value=result.metrics.get("first_positive_buckling_factor"),
                    unit="ratio",
                    threshold=threshold_map.get("minimum_linear_buckling_factor"),
                    comparator="ge",
                )
            hotspot = violation.get("hotspot_centroid_m")
            if hotspot is not None:
                region = RegionEvidence(
                    kind="point",
                    frame="fea_m",
                    unit="m",
                    point=[float(value) for value in hotspot],
                )
        elif checker == "overhang":
            if code == "SUPPORT_CONTACT_AREA_REDUCTION_LT_TARGET":
                metric = MetricEvidence(
                    name="contact_area_reduction_fraction",
                    value=violation.get("observed_reduction_fraction"),
                    unit="fraction",
                    threshold=violation.get("required_reduction_fraction"),
                    comparator="ge",
                )
                region = _region_from_rows(
                    list(violation.get("supported_regions", [])), frame="print_mm", unit="mm"
                )
            elif code == "GEOMETRIC_OVERHANG_AREA_INCREASED":
                metric = MetricEvidence(
                    name="overhang_area_change_fraction",
                    value=violation.get("observed_increase_fraction"),
                    unit="fraction",
                    threshold=violation.get("maximum_increase_fraction"),
                    comparator="le",
                )
            elif code == "PRINT_EXTENT_CHANGED":
                baseline = violation.get("baseline_print_extent_mm") or []
                current = violation.get("current_print_extent_mm") or []
                deltas = [abs(float(a) - float(b)) for a, b in zip(current, baseline)]
                metric = MetricEvidence(
                    name="maximum_print_extent_delta_mm",
                    value=max(deltas) if deltas else None,
                    unit="mm",
                    threshold=violation.get("maximum_delta_mm"),
                    comparator="le",
                )
        elif checker == "support":
            if code == "SUPPORT_TOUCHES_CRITICAL_SURFACE":
                epsilon = config.get("profile", {}).get("critical_overlap_epsilon_mm2", 0.01)
                metric = MetricEvidence(
                    name="critical_contact_overlap_area_mm2",
                    value=violation.get("contact_overlap_area_mm2"),
                    unit="mm^2",
                    threshold=epsilon,
                    comparator="le",
                )
                region = _region_from_rows(
                    list(violation.get("overlap_regions", [])), frame="print_mm", unit="mm"
                )
            elif code == "SUPPORT_REQUIRED":
                metric = MetricEvidence(
                    name="support_required",
                    value=True,
                    unit="boolean",
                    threshold=False,
                    comparator="eq",
                )

        identity = violation.get("state") or violation.get("height_fraction") or index
        findings.append(
            CheckerFinding(
                finding_id=f"{checker}:{code}:{identity}",
                rule_id=code,
                category=category,
                applicability=applicability,
                applicability_basis="reported by the configured checker under its recorded assumptions",
                metric=metric,
                region=region,
                evidence_refs=list(result.artifacts.values()),
                relations=relations,
                repairability=repairability,
                message=str(violation.get("message") or result.summary),
                domain=dict(violation),
            )
        )
    return result.model_copy(
        update={"version": 2, "analysis_context": context, "findings": findings}
    )


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import analyzer {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def stage_case(asset_dir: Path, source: Path, output_dir: Path) -> Path:
    case_dir = output_dir / "input_case"
    case_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(source, case_dir / "source.py")
    for name in (
        "scene.glb",
        "scene.urdf",
        "scene.joint_states.json",
        "source_index.json",
        "analysis_geometry.json",
    ):
        candidates = (asset_dir / name, asset_dir.parent / name)
        candidate = next((path for path in candidates if path.is_file()), None)
        if candidate is not None:
            shutil.copy2(candidate, case_dir / name)
    meshes = asset_dir / "meshes"
    if meshes.is_dir():
        shutil.copytree(meshes, case_dir / "meshes")
    for required in ("scene.glb", "scene.urdf"):
        if not (case_dir / required).is_file():
            raise FileNotFoundError(f"checker requires {required}")
    return case_dir


def _manifest_scale(
    manifest: dict[str, Any],
    rule: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    bounds = manifest.get("root", {}).get("bounds")
    if not isinstance(bounds, list) or len(bounds) != 2:
        raise ValueError("analysis geometry root bounds are unavailable")
    extents = [
        float(bounds[1][index]) - float(bounds[0][index])
        for index in range(3)
    ]
    measure = str(rule.get("measure", "height"))
    if measure == "height":
        source = extents[2]
    elif measure == "length":
        source = max(extents[0], extents[1])
    elif measure == "diameter":
        source = max(extents)
    else:
        raise ValueError(f"unknown scale measure {measure}")
    if source <= 0:
        raise ValueError("analysis geometry has a non-positive characteristic extent")
    factor = float(rule["target_m"]) / source
    return factor, {
        "measure": measure,
        "source_scene_units": source,
        "target_m": float(rule["target_m"]),
        "factor_m_per_scene_unit": factor,
        "source_bounds": bounds,
    }


def topology_result(raw: dict[str, Any], raw_path: Path) -> CheckerResult:
    status = str(raw.get("status", "INDETERMINATE"))
    mode = str(raw.get("mode", "load_path"))
    violations = list(raw.get("violations", []))
    assumptions = {
        **dict(raw.get("assumptions", {})),
        "mode": mode,
        "numerical_tolerance_m": raw.get("numerical_tolerance_m"),
        "scale": raw.get("scale"),
    }
    if status == "PASS":
        summary = (
            f"Verified one bonded component across {raw.get('part_count', 0)} parts"
            if mode == "one_piece"
            else "Every configured load region has a bonded OCC path to a support part"
        )
    elif status == "FAIL":
        summary = (
            f"Topology validation found {len(violations)} disconnected load-path or one-piece violation(s)"
        )
    else:
        summary = "Topology could not be determined from the available analytic geometry or semantics"
    return CheckerResult(
        checker="topology",
        status=status if status in {"PASS", "FAIL", "INDETERMINATE"} else "ERROR",
        summary=summary,
        metrics={
            "mode": mode,
            "part_count": raw.get("part_count"),
            "component_count": raw.get("component_count"),
            "components": raw.get("components", []),
            "active_part_paths": raw.get("active_part_paths", []),
            "weak_contact_count": len(raw.get("weak_contacts", [])),
        },
        violations=violations,
        assumptions=assumptions,
        artifacts={
            "raw_result": str(raw_path),
            "analysis_geometry": str(raw_path.parent.parent / "input_case" / "analysis_geometry.json"),
        },
    )


def standing_result(raw: dict[str, Any], raw_path: Path) -> CheckerResult:
    states = [
        state
        for state in raw.get("states", [])
        if state.get("geometric_verdict") != "EXCLUDED"
    ]
    unavailable = [
        state.get("state")
        for state in states
        if not state.get("mujoco", {}).get("available", False)
    ]
    if unavailable:
        return CheckerResult(
            checker="standing",
            status="ERROR",
            summary="MuJoCo result is unavailable for one or more active states",
            violations=[
                {
                    "code": "MUJOCO_UNAVAILABLE",
                    "states": unavailable,
                    "message": "The required natural-settle simulation did not run.",
                }
            ],
            artifacts={"raw_result": str(raw_path)},
        )
    tipped = [
        state
        for state in states
        if state["mujoco"]["settle"].get("tipped", False)
    ]
    state_metrics = {
        str(state["state"]): {
            "tipped": bool(state["mujoco"]["settle"].get("tipped", False)),
            "topple_threshold_deg": state["mujoco"].get("topple_threshold_deg", 25.0),
            "peak_tilt_deg": state["mujoco"]["settle"].get("peak_tilt_deg"),
            "final_tilt_deg": state["mujoco"]["settle"].get("final_tilt_deg"),
            "minimum_force_over_weight": state["mujoco"]
            .get("force_probe", {})
            .get("minimum_observed_force_over_weight"),
        }
        for state in states
    }
    violations = [
        {
            "code": "NATURAL_SETTLE_TILT_GT_25_DEG",
            "state": state["state"],
            "peak_tilt_deg": state["mujoco"]["settle"].get("peak_tilt_deg"),
            "final_tilt_deg": state["mujoco"]["settle"].get("final_tilt_deg"),
            "message": "MuJoCo natural settling exceeded the fixed 25 degree topple threshold.",
        }
        for state in tipped
    ]
    return CheckerResult(
        checker="standing",
        status="FAIL" if tipped else "PASS",
        summary=(
            f"{len(tipped)} of {len(states)} active states tipped during natural settling"
            if tipped
            else f"All {len(states)} active states stayed within 25 degrees during natural settling"
        ),
        metrics={"states": state_metrics},
        violations=violations,
        assumptions={
            "criterion": "natural-settle tilt strictly greater than 25 degrees is a fall",
            "physics": "MuJoCo rigid-body convex collision proxy",
        },
        artifacts={"raw_result": str(raw_path)},
    )


def progressive_result(raw: dict[str, Any], raw_path: Path) -> CheckerResult:
    samples = list(raw.get("samples", []))
    summary = dict(raw.get("summary", {}))
    unavailable = [
        row.get("height_fraction")
        for row in samples
        if row.get("support") and not row.get("mujoco", {}).get("available", False)
    ]
    clip_failures = [
        {
            "height_fraction": row.get("height_fraction"),
            "failures": row.get("clip_failures", []),
        }
        for row in samples
        if row.get("clip_failures")
    ]
    if unavailable:
        return CheckerResult(
            checker="progressive",
            status="ERROR",
            summary="MuJoCo result is unavailable for one or more non-empty partial builds",
            violations=[{
                "code": "MUJOCO_PARTIAL_UNAVAILABLE",
                "height_fractions": unavailable,
                "message": "The required natural-settle simulation did not run.",
            }],
            artifacts={"raw_result": str(raw_path)},
        )
    if clip_failures:
        return CheckerResult(
            checker="progressive",
            status="INDETERMINATE",
            summary="One or more partial geometries could not be clipped reliably",
            violations=[{
                "code": "PARTIAL_CLIP_FAILURE",
                "details": clip_failures,
                "message": "The build-height scan is incomplete.",
            }],
            artifacts={"raw_result": str(raw_path)},
        )

    tipped = [
        row for row in samples
        if row.get("mujoco", {}).get("available", False)
        and row["mujoco"].get("tipped", False)
    ]
    worst = max(
        (
            row for row in samples
            if row.get("mujoco", {}).get("available", False)
        ),
        key=lambda row: float(row["mujoco"].get("peak_tilt_deg", 0.0)),
        default=None,
    )

    def evidence(row: dict[str, Any]) -> dict[str, Any]:
        mass_name, mass = select_progressive_mass_model(row.get("mass_models", {}))
        return {
            "height_fraction": row.get("height_fraction"),
            "height_scene_units": row.get("height"),
            "peak_tilt_deg": row.get("mujoco", {}).get("peak_tilt_deg"),
            "final_tilt_deg": row.get("mujoco", {}).get("final_tilt_deg"),
            "support_area_scene_units2": row.get("support", {}).get("area"),
            "mass_model": mass_name,
            "com_scene_units": None if mass is None else mass.get("com"),
            "signed_margin_scene_units": None if mass is None else mass.get("signed_margin"),
            "normalized_margin": None if mass is None else mass.get("normalized_margin"),
            "active_geometry_names": row.get("active_geometry_names", []),
            "events": row.get("events", []),
        }

    violations: list[dict[str, Any]] = []
    if tipped:
        violations.append({
            "code": "FIRST_PARTIAL_TILT_GT_25_DEG",
            "message": "The earliest failing partial build exceeded the fixed 25 degree threshold.",
            **evidence(tipped[0]),
        })
        if worst is not None and worst is not tipped[0]:
            violations.append({
                "code": "WORST_PARTIAL_TILT_GT_25_DEG",
                "message": "This build height produced the largest measured tilt.",
                **evidence(worst),
            })

    return CheckerResult(
        checker="progressive",
        status="FAIL" if tipped else "PASS",
        summary=(
            f"{len(tipped)} of {len(samples)} sampled partial builds exceeded 25 degrees"
            if tipped else
            f"All {len(samples)} sampled partial builds stayed within 25 degrees"
        ),
        metrics={
            "sample_count": len(samples),
            "dynamic_failure_count": len(tipped),
            "first_dynamic_failure_fraction": summary.get("first_dynamic_failure_fraction"),
            "peak_dynamic_tilt_deg": summary.get("peak_dynamic_tilt_deg"),
            "peak_dynamic_tilt_fraction": summary.get("peak_dynamic_tilt_fraction"),
            "geometric_unstable_sample_count": summary.get("geometric_unstable_sample_count"),
            "uniform_delta_h_complete": summary.get("uniform_delta_h_complete"),
            "worst_partial": None if worst is None else evidence(worst),
        },
        violations=violations,
        assumptions={
            "criterion": "partial-build natural-settle peak tilt strictly greater than 25 degrees is a fall",
            "height_step_fraction": raw.get("checker_assumptions", {}).get("height_step_fraction"),
            "event_height_policy": "geometry birth and completion heights are added to uniform delta-h samples",
            "build_orientation": "authored URDF Z-up",
            "physics": "unbonded MuJoCo rigid-body convex collision proxy; 5 second settling",
            "scope_exclusions": ["bending", "buckling", "thermal warping", "nozzle drag", "layer adhesion"],
        },
        artifacts={"raw_result": str(raw_path)},
    )


def select_progressive_mass_model(
    models: dict[str, dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None]:
    for name in ("uniform_solid", "uniform_shell"):
        model = models.get(name)
        if model and model.get("available"):
            return name, model
    return None, None


def fea_result(raw: dict[str, Any], raw_path: Path) -> CheckerResult:
    status = str(raw.get("status", "SOLVER_FAILED"))
    assumptions = {
        "material": raw.get("material"),
        "scale": raw.get("scale"),
        "boundary_condition": "all translational DOFs fixed on global minimum-z nodes",
        "analysis": "linear static plus linear eigenvalue buckling",
    }
    invalid_levels = [level for level in raw.get("mesh_levels", [])
                      if level.get("status") == "MESH_INVALID"]
    if status == "MESH_INVALID" or invalid_levels:
        return CheckerResult(
            checker="fea", status="INDETERMINATE",
            summary="MESH_INVALID: geometric cause is undetermined; structural performance is unverified",
            violations=[{
                "code": "MESH_INVALID", "stage": "volume_mesh_validation",
                "mesh_level": level.get("mesh_level"), **level.get("mesh_invalid", {}),
            } for level in invalid_levels] or [{
                "code": "MESH_INVALID", "stage": "volume_mesh_validation",
                "message": "Invalid mesh; no local evidence available. Structural performance unverified.",
            }],
            assumptions=assumptions, artifacts={"raw_result": str(raw_path)},
        )
    mesh_errors = [
        str(level.get("error", ""))
        for level in raw.get("mesh_levels", [])
        if level.get("error")
    ]
    infrastructure_markers = (
        "ModuleNotFoundError",
        "ImportError",
        "cannot open shared object file",
        "No such file or directory",
    )
    if any(
        marker in error
        for error in mesh_errors
        for marker in infrastructure_markers
    ):
        return CheckerResult(
            checker="fea",
            status="ERROR",
            summary="Gmsh/FEA runtime dependency is unavailable",
            violations=[
                {
                    "code": "FEA_RUNTIME_UNAVAILABLE",
                    "message": error,
                }
                for error in mesh_errors
            ],
            assumptions=assumptions,
            artifacts={"raw_result": str(raw_path)},
        )
    if status in {"SOLVER_FAILED", "NOT_CONVERGED"}:
        return CheckerResult(
            checker="fea",
            status="ERROR",
            summary=f"CalculiX analysis did not complete: {status}",
            violations=[{"code": status, "message": raw.get("error", status)}],
            assumptions=assumptions,
            artifacts={"raw_result": str(raw_path)},
        )
    if status != "SOLVED":
        return CheckerResult(
            checker="fea",
            status="INDETERMINATE",
            summary=f"No valid continuous solid load path was solved: {status}",
            violations=[
                {
                    "code": status,
                    "message": raw.get("geometry_gate", {}).get(
                        "reason", "Geometry or semantic load regions are not FEA-ready."
                    ),
                    "geometry_gate": raw.get("geometry_gate", {}),
                }
            ],
            assumptions=assumptions,
            artifacts={"raw_result": str(raw_path)},
        )

    convergence = raw.get("mesh_convergence", {})
    fine = next(
        (row for row in raw.get("mesh_levels", []) if row.get("mesh_level") == "fine"),
        {},
    )
    functional = fine.get("analyses", {}).get("functional", {})
    screening = functional.get("screening_assessment", {})
    violations: list[dict[str, Any]] = []
    if convergence.get("status") != "PASSED":
        violations.append(
            {
                "code": "MESH_NOT_CONVERGED",
                "message": "Medium-to-fine convergence thresholds were not all met.",
                "details": convergence,
            }
        )
    for concern in screening.get("concerns", []):
        violations.append(
            {
                "code": concern,
                "message": "Fine-mesh functional-load screening threshold was violated.",
                "hotspot_centroid_m": functional.get("stress_hotspot_centroid_m"),
            }
        )
    passed = not violations and screening.get("status") == "SCREEN_PASS"
    metrics = {
        "mesh_convergence": convergence,
        "max_displacement_m": functional.get("max_displacement_m"),
        "max_displacement_over_characteristic_length": screening.get(
            "max_displacement_over_characteristic_length"
        ),
        "max_von_mises_pa": functional.get("max_von_mises_pa"),
        "nominal_safety_factor": functional.get("nominal_safety_factor"),
        "first_positive_buckling_factor": functional.get(
            "first_positive_buckling_factor"
        ),
        "stress_hotspot_centroid_m": functional.get("stress_hotspot_centroid_m"),
        "load_regions": functional.get("load_regions", []),
        "thresholds": screening.get("thresholds", {}),
    }
    return CheckerResult(
        checker="fea",
        status="PASS" if passed else "FAIL",
        summary=(
            "Converged fine-mesh functional analysis passed displacement, yield, and buckling gates"
            if passed
            else "Converged FEA did not satisfy every required screening gate"
        ),
        metrics=metrics,
        violations=violations,
        assumptions=assumptions,
        artifacts={"raw_result": str(raw_path)},
    )


def overhang_result(
    raw: dict[str, Any], raw_path: Path, config: dict[str, Any]
) -> CheckerResult:
    status = str(raw.get("status", "ANALYSIS_FAILED"))
    if status in {"SLICER_FAILED", "SLICER_TIMEOUT"}:
        return CheckerResult(
            checker="overhang",
            status="ERROR",
            summary=f"Support slicer did not complete: {status}",
            violations=[{"code": status, "message": raw.get("error", status)}],
            artifacts={"raw_result": str(raw_path)},
        )
    if status != "ANALYZED":
        return CheckerResult(
            checker="overhang",
            status="INDETERMINATE",
            summary=f"Overhang/support analysis is unavailable: {status}",
            violations=[{"code": status, "message": raw.get("error", status)}],
            artifacts={"raw_result": str(raw_path)},
        )

    optimization = config["optimization"]
    baseline_contact = float(optimization["baseline_nominal_contact_area_mm2"])
    baseline_overhang = float(optimization["baseline_overhang_area_mm2"])
    current_contact = float(raw.get("nominal_contact", {}).get("area_mm2", 0.0))
    current_overhang = float(raw.get("overhang", {}).get("area_mm2", 0.0))
    contact_reduction = (
        (baseline_contact - current_contact) / baseline_contact
        if baseline_contact > 0 else 0.0
    )
    overhang_change = (
        (current_overhang - baseline_overhang) / baseline_overhang
        if baseline_overhang > 0 else 0.0
    )
    target_reduction = float(
        optimization.get("minimum_contact_area_reduction_fraction", 0.01)
    )
    max_overhang_increase = float(
        optimization.get("maximum_overhang_area_increase_fraction", 0.0)
    )
    baseline_extent = [
        float(value) for value in optimization["baseline_print_extent_mm"]
    ]
    current_extent = [
        float(value) for value in raw.get("scale", {}).get("print_extent_mm", [])
    ]
    extent_tolerance = float(
        optimization.get("maximum_print_extent_delta_mm", 0.01)
    )
    extent_deltas = (
        [abs(current - baseline) for current, baseline in zip(current_extent, baseline_extent)]
        if len(current_extent) == len(baseline_extent) else []
    )

    violations: list[dict[str, Any]] = []
    if contact_reduction + 1e-12 < target_reduction:
        violations.append({
            "code": "SUPPORT_CONTACT_AREA_REDUCTION_LT_TARGET",
            "message": (
                "Reduce actual slicer-supported model contact area while preserving "
                "the frozen print extent and slicer profile."
            ),
            "baseline_area_mm2": baseline_contact,
            "current_area_mm2": current_contact,
            "required_reduction_fraction": target_reduction,
            "observed_reduction_fraction": contact_reduction,
            "supported_regions": raw.get("unsupported_region", {}).get("regions", []),
        })
    if overhang_change > max_overhang_increase + 1e-12:
        violations.append({
            "code": "GEOMETRIC_OVERHANG_AREA_INCREASED",
            "message": "Do not trade less support contact for more geometric overhang.",
            "baseline_area_mm2": baseline_overhang,
            "current_area_mm2": current_overhang,
            "maximum_increase_fraction": max_overhang_increase,
            "observed_increase_fraction": overhang_change,
        })
    if not extent_deltas or max(extent_deltas) > extent_tolerance:
        violations.append({
            "code": "PRINT_EXTENT_CHANGED",
            "message": "Preserve the frozen print-space AABB to avoid scale or truncation gaming.",
            "baseline_print_extent_mm": baseline_extent,
            "current_print_extent_mm": current_extent,
            "maximum_delta_mm": extent_tolerance,
        })

    passed = not violations
    return CheckerResult(
        checker="overhang",
        status="PASS" if passed else "FAIL",
        summary=(
            f"Nominal support contact fell by {contact_reduction:.2%} without "
            "increasing geometric overhang or changing print extent"
            if passed else
            "The fixed support-contact improvement gate was not satisfied"
        ),
        metrics={
            "support_required": bool(raw.get("slicer_support", {}).get("required", False)),
            "baseline_nominal_contact_area_mm2": baseline_contact,
            "nominal_contact_area_mm2": current_contact,
            "contact_area_reduction_fraction": contact_reduction,
            "baseline_overhang_area_mm2": baseline_overhang,
            "overhang_area_mm2": current_overhang,
            "overhang_area_change_fraction": overhang_change,
            "support_base_path_length_mm": raw.get("slicer_support", {}).get(
                "base_path_length_mm"
            ),
            "support_interface_path_length_mm": raw.get("slicer_support", {}).get(
                "interface_path_length_mm"
            ),
            "bridge_path_length_mm": raw.get("bridge", {}).get("path_length_mm"),
            "max_single_bridge_span_mm": raw.get("bridge", {}).get(
                "max_single_extrusion_span_mm"
            ),
            "supported_regions": raw.get("unsupported_region", {}).get("regions", []),
            "print_extent_mm": current_extent,
        },
        violations=violations,
        assumptions={
            "criterion": (
                f"nominal support contact reduction >= {target_reduction:.2%}; "
                f"geometric overhang increase <= {max_overhang_increase:.2%}"
            ),
            "scale_control": "print-space AABB preserved within configured tolerance",
            "slicer_profile": config.get("profile"),
            "scope": (
                "PrusaSlicer toolpath/contact burden; not scar, surface roughness, "
                "thermal distortion, or structural certification"
            ),
        },
        artifacts={
            "raw_result": str(raw_path),
            "surface_overlay": str(
                raw_path.parent / "cases" / "workflow_case" / "surface_classes.ply"
            ),
        },
    )


def support_result(
    raw: dict[str, Any], raw_path: Path, *, forbid_support: bool
) -> CheckerResult:
    status = str(raw.get("status", "ANALYSIS_FAILED"))
    if status in {"SLICER_FAILED", "SLICER_TIMEOUT"}:
        return CheckerResult(
            checker="support",
            status="ERROR",
            summary=f"Support slicer did not complete: {status}",
            violations=[{"code": status, "message": raw.get("error", status)}],
            artifacts={"raw_result": str(raw_path)},
        )
    if status != "ANALYZED":
        return CheckerResult(
            checker="support",
            status="INDETERMINATE",
            summary=f"Support/contact analysis is unavailable: {status}",
            violations=[{"code": status, "message": raw.get("error", status)}],
            artifacts={"raw_result": str(raw_path)},
        )
    support_required = bool(raw.get("slicer_support", {}).get("required", False))
    critical = raw.get("critical_surfaces", {})
    verdict = str(critical.get("verdict", "INDETERMINATE"))
    violations: list[dict[str, Any]] = []
    if verdict == "VIOLATION":
        violations.append(
            {
                "code": "SUPPORT_TOUCHES_CRITICAL_SURFACE",
                "message": "Nominal support contact overlaps a declared critical surface.",
                "contact_overlap_area_mm2": critical.get("contact_overlap_area_mm2"),
                "overlap_regions": critical.get("overlap_regions", []),
            }
        )
    if forbid_support and support_required:
        violations.append(
            {
                "code": "SUPPORT_REQUIRED",
                "message": "This checker configuration forbids generated support.",
            }
        )
    if verdict == "INDETERMINATE":
        checker_status = "INDETERMINATE"
    else:
        checker_status = "FAIL" if violations else "PASS"
    return CheckerResult(
        checker="support",
        status=checker_status,
        summary=(
            "Critical-surface support policy passed"
            if checker_status == "PASS"
            else "Support policy requires revision or better surface semantics"
        ),
        metrics={
            "support_required": support_required,
            "overhang_area_mm2": raw.get("overhang", {}).get("area_mm2"),
            "bridge_path_length_mm": raw.get("bridge", {}).get("path_length_mm"),
            "nominal_contact_area_mm2": raw.get("nominal_contact", {}).get("area_mm2"),
            "critical_contact_overlap_area_mm2": critical.get(
                "contact_overlap_area_mm2"
            ),
            "critical_verdict": verdict,
        },
        violations=violations,
        assumptions={
            "forbid_support": forbid_support,
            "critical_surface_reliable": critical.get("reliable"),
        },
        artifacts={
            "raw_result": str(raw_path),
            "surface_overlay": str(
                raw_path.parent / "cases" / "workflow_case" / "surface_classes.ply"
            ),
        },
    )


def run_standing(case_dir: Path, output_dir: Path, config: dict[str, Any]) -> CheckerResult:
    extra = config.get("mujoco_pythonpath")
    if extra:
        sys.path.insert(0, str(Path(extra).expanduser().resolve()))
    analyzer = load_module(
        "adsl_workflow_standing",
        REPO / "experiments" / "final_standing_stability" / "analyze.py",
    )
    raw_root = output_dir / "raw"
    raw = analyzer.analyze_case(
        case_dir,
        raw_root,
        True,
        float(config.get("density_kg_m3", 1000.0)),
        float(config.get("friction", 2.0)),
    )
    raw_path = raw_root / "result.json"
    raw_root.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return standing_result(raw, raw_path)


def run_progressive(
    case_dir: Path, output_dir: Path, config: dict[str, Any]
) -> CheckerResult:
    extra = config.get("mujoco_pythonpath")
    if extra:
        sys.path.insert(0, str(Path(extra).expanduser().resolve()))
    analyzer = load_module(
        "adsl_workflow_progressive",
        REPO / "experiments" / "progressive_build_stability" / "analyze.py",
    )
    raw_root = output_dir / "raw"
    scratch = output_dir / "scratch"
    raw_root.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    raw = analyzer.analyze_case(
        case_dir,
        float(config.get("height_step_fraction", 0.01)),
        True,
        scratch,
        float(config.get("density_kg_m3", 1000.0)),
        float(config.get("friction", 2.0)),
    )
    raw["checker_assumptions"] = {
        "height_step_fraction": float(config.get("height_step_fraction", 0.01)),
        "density_kg_m3": float(config.get("density_kg_m3", 1000.0)),
        "friction": float(config.get("friction", 2.0)),
    }
    raw_path = raw_root / "result.json"
    raw_path.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return progressive_result(raw, raw_path)



def run_topology(
    case_dir: Path, output_dir: Path, config: dict[str, Any]
) -> CheckerResult:
    analyzer = load_module(
        "adsl_workflow_topology",
        REPO / "experiments" / "topology_connectivity" / "analyze.py",
    )
    manifest_path = case_dir / "analysis_geometry.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(
            "topology checker requires source-linked analysis_geometry.json"
        )
    manifest = analyzer.load_manifest(manifest_path)
    scale, scale_info = _manifest_scale(manifest, config["scale"])
    profile = dict(config.get("profile", {}))
    if "loads" not in profile and config.get("functional_loads"):
        profile["loads"] = config["functional_loads"]
    raw = analyzer.analyze_manifest(manifest, profile, scale=scale)
    raw["scale"] = scale_info
    raw_root = output_dir / "raw"
    raw_root.mkdir(parents=True, exist_ok=True)
    raw_path = raw_root / "result.json"
    raw_path.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return topology_result(raw, raw_path)

def run_fea(case_dir: Path, output_dir: Path, config: dict[str, Any]) -> CheckerResult:
    analyzer = load_module(
        "adsl_workflow_fea",
        REPO / "experiments" / "load_bearing_structural_performance" / "analyze.py",
    )
    ccx = Path(config.get("ccx", DEFAULT_CCX)).expanduser().resolve()
    if not ccx.is_file():
        raise FileNotFoundError(ccx)
    raw_root = output_dir / "raw"
    case_cfg = {
        "scale": config["scale"],
        "functional_loads": config["functional_loads"],
    }
    if config.get("functional_moments"):
        case_cfg["functional_moments"] = config["functional_moments"]
    raw = analyzer.analyze_case(
        "workflow_case",
        case_dir,
        case_cfg,
        config["material"],
        ccx,
        raw_root,
        int(config.get("solver_timeout_seconds", 900)),
    )
    raw["material"] = config["material"]
    raw_path = raw_root / "result.json"
    raw_root.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return fea_result(raw, raw_path)


def run_support(
    case_dir: Path,
    output_dir: Path,
    config: dict[str, Any],
    *,
    result_kind: str = "support",
) -> CheckerResult:
    analyzer = load_module(
        "adsl_workflow_support",
        REPO / "experiments" / "support_requirement_critical_surfaces" / "analyze.py",
    )
    slicer = Path(config.get("slicer", DEFAULT_SLICER)).expanduser().resolve()
    profile_path = Path(
        config.get(
            "profile_path",
            REPO
            / "experiments"
            / "support_requirement_critical_surfaces"
            / "fff_profile.ini",
        )
    ).expanduser().resolve()
    if not slicer.is_file():
        raise FileNotFoundError(slicer)
    raw_root = output_dir / "raw"
    analyzer_config = {
        "profile": config["profile"],
        "cases": {"workflow_case": config["case"]},
    }
    raw = analyzer.analyze_case(
        "workflow_case",
        case_dir,
        raw_root,
        analyzer_config,
        slicer,
        profile_path,
        int(config.get("slicer_timeout_seconds", 900)),
    )
    raw_path = raw_root / "result.json"
    raw_root.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if result_kind == "overhang":
        return overhang_result(raw, raw_path, config)
    return support_result(
        raw, raw_path, forbid_support=bool(config.get("forbid_support", False))
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument(
        "checker",
        choices=("standing", "progressive", "topology", "fea", "support", "overhang"),
    )
    result.add_argument("--asset-dir", type=Path, required=True)
    result.add_argument("--source", type=Path, required=True)
    result.add_argument("--output-dir", type=Path, required=True)
    result.add_argument("--config", type=Path, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    config = json.loads(args.config.expanduser().resolve().read_text(encoding="utf-8"))
    try:
        case_dir = stage_case(
            args.asset_dir.expanduser().resolve(),
            args.source.expanduser().resolve(),
            output_dir,
        )
        if args.checker == "standing":
            result = run_standing(case_dir, output_dir, config)
        elif args.checker == "progressive":
            result = run_progressive(case_dir, output_dir, config)
        elif args.checker == "topology":
            result = run_topology(case_dir, output_dir, config)
        elif args.checker == "fea":
            result = run_fea(case_dir, output_dir, config)
        elif args.checker == "overhang":
            result = run_support(case_dir, output_dir, config, result_kind="overhang")
        else:
            result = run_support(case_dir, output_dir, config)
    except Exception as error:
        error_path = output_dir / "adapter_error.log"
        error_path.write_text(traceback.format_exc(), encoding="utf-8")
        result = CheckerResult(
            checker=args.checker,
            status="ERROR",
            summary=f"{type(error).__name__}: {error}",
            violations=[
                {
                    "code": "ADAPTER_OR_TOOL_ERROR",
                    "message": f"{type(error).__name__}: {error}",
                }
            ],
            artifacts={"traceback": str(error_path)},
        )
    result = enrich_result(result, config)
    (output_dir / "result.json").write_text(
        result.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
