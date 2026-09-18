"""Small helpers for the opt-in local-edit experiment, not a checker framework."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import time
import hashlib
import ast
from dataclasses import asdict
from functools import lru_cache
from typing import Any

from .models import CheckerFinding, MetricEvidence, RegionEvidence


MODEL_LOCATION_INSTRUCTION = (
    "Overhang experiment exception to the source-candidate prerequisite: when index candidates "
    "are absent or ambiguous, you may infer a location from the complete current source, renders, "
    "region coordinates and protection checklist. Read the assigned source (all pages). "
    "For model-inferred proposals leave feature_ids and source_ids empty; put existing qualified "
    "class/function names in target.allowed_scopes (e.g. Frame or Frame.__init__). "
    "Explain the target, geometric evidence and uncertainty briefly in evidence/hypothesis. "
    "This is model inference, not tool-confirmed attribution. Reliable indexed targets still use "
    "the existing IDs. No proposal remains valid when you cannot judge safely. No extra budget "
    "or wider protection scope is granted."
)

PLANNED_LOCATION_INSTRUCTION = (
    "Joint checker repair: source_index is localization evidence, not edit permission. "
    "Read the complete current source and use renders and checker evidence to select the "
    "smallest relevant existing classes, assembly code or helper functions in target.allowed_scopes. "
    "Each allowed_scopes entry must be ONLY an exact source symbol, e.g. ChairLegs.__init__; "
    "put explanatory text and local restrictions in evidence, not after the symbol. "
    "Explain each target's relationship to the issue in hypothesis/evidence. Missing or ambiguous "
    "index candidates do not prohibit a proposal. Leave source_ids/feature_ids empty for inferred "
    "locations; never fabricate IDs. No bridge_parent is required. Do not change checker code, "
    "analysis conditions, thresholds, configuration, imports or unrelated source. Preserve explicit "
    "task dimensions, interfaces, function and appearance, but do not assume a class whitelist, "
    "exact triangles or unchanged overall bounds unless explicitly configured. You may stop without "
    "a proposal; set stop_category to no_change_needed, insufficient_localization, scope_limited, "
    "or no_reasonable_plan and explain. Modification never implies acceptance."
)


def reliable_location(finding):
    return any(c.source_ids and not c.ambiguous for c in finding.source_candidates)


def inferred_proposal(proposal, source, options, findings, source_index=None):
    """Validate model-named source locations, not geometry provenance or synthetic IDs."""
    known = {f.finding_id: f for f in findings}
    if any(i not in known for i in proposal.finding_ids):
        return None, ["unknown finding ids"]
    if any(known[i].repairability not in {"geometry", "design_variable"} for i in proposal.finding_ids):
        return None, ["finding is not source-editable"]
    planned = options.get('mode') == 'planned_checks'
    if planned and (proposal.target.feature_ids or proposal.target.source_ids):
        source_ids = {n.source_id for n in source_index.source_nodes} if source_index else set()
        feature_ids = {f.feature_id for f in source_index.features} if source_index else set()
        if not set(proposal.target.source_ids) <= source_ids or not set(proposal.target.feature_ids) <= feature_ids:
            return None, ['unverified index IDs; use real source symbols without fabricated IDs']
    elif proposal.target.feature_ids or proposal.target.source_ids:
        return None, ["model-inferred targets must not claim index IDs"]
    if not proposal.evidence or proposal.action in {"change_print_orientation", "request_evidence"}:
        return None, ["inferred edit requires evidence and an executable fixed-orientation action"]
    nodes = {}
    def visit(body, prefix=""):
        for n in body:
            if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + n.name
                nodes[name] = n
                visit(n.body, name + ".")
    visit(ast.parse(source.read_text()).body)
    scopes = proposal.target.allowed_scopes
    scope_notes = []
    if planned:
        normalized = []
        for entry in scopes:
            symbol, separator, note = entry.partition(':')
            if separator and symbol.strip() in nodes and note.strip():
                normalized.append(symbol.strip())
                scope_notes.append(entry)
            else:
                normalized.append(entry)
        scopes = normalized
    allowed = options.get("protection", {}).get("allowed_classes", [])
    if not scopes or any(s not in nodes or (not planned and s.split('.')[0] not in allowed) for s in scopes):
        return None, (["missing or unknown exact source symbols: " + repr(scopes)] if planned
                      else ["inferred source location missing or outside configured protected edit scope"])
    # The existing experiment authorizes classes; qualified methods remain within them.
    if not planned and any(not isinstance(nodes[s.split('.')[0]], ast.ClassDef) for s in scopes):
        return None, ["top-level function edits are not authorized by the configured class-only protection"]
    location = {"method": "model_inferred", "tool_confirmed": False,
                "source_sha256": file_hash(source),
                "targets": [{"symbol": s, "start_line": nodes[s].lineno,
                             "end_line": nodes[s].end_lineno} for s in scopes],
                "evidence": proposal.evidence[:3], "scope_notes": scope_notes}
    return proposal.model_copy(update={
        "target": proposal.target.model_copy(update={"allowed_scopes": sorted({s.split('.')[0] for s in scopes})}),
        "evidence": proposal.evidence + ["Preserve local restriction: " + note for note in scope_notes],
        "parameter_bounds": {**proposal.parameter_bounds, "_localization": location}}), []


def experiment_protection(original, candidate, options, *, exact_check):
    if options.get('mode') != 'planned_checks':
        return exact_check(original, candidate, options)
    if options.get('protection_policy') != 'explicit_task_constraints':
        return {'status':'UNCONFIRMED','reason':'joint mode requires explicit protection policy; legacy constraints not silently removed'}
    protection = options.get('protection', {})
    if protection.get('explicit_constraints'):
        return {'status':'UNCONFIRMED','reason':'explicit constraints require an executable evaluator; not silently ignored'}
    if protection.get('surfaces') or protection.get('preserve_bounds'):
        checked = dict(options)
        if not protection.get('preserve_bounds'):
            checked.pop('scale_mm_per_source_unit', None)
        return exact_check(original, candidate, checked)
    return {'status':'PASS','policy':'explicit_task_constraints',
            'scope':'no numerical/surface constraints configured; appearance/function still require critics',
            'exact_dimensions_certified':False}


def inferred_scope_unchanged(before, after, location):
    """Mask authorized AST nodes and require the rest of the program to be unchanged."""
    targets = {t['symbol'] for t in location['targets']}
    def masked(path):
        tree = ast.parse(path.read_text())
        def walk(body, prefix=""):
            result = []
            for node in body:
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = prefix + node.name
                    if name in targets:
                        result.append(ast.Pass())
                        continue
                    node.body = walk(node.body, name + '.')
                result.append(node)
            return result
        tree.body = walk(tree.body)
        return ast.dump(tree, include_attributes=False)
    return masked(before) == masked(after)


OVERHANG_OPTIMIZATION_INSTRUCTION = (
    "Optional local optimization. Preserve shape/function/appearance first. Read the source. "
    "The measurement counts exposed overhanging exterior surfaces after Boolean union; "
    "internal overlapping faces are excluded. Reducing overlap, rearranging rails, or reducing "
    "primitive count does not imply less overhang area. In each proposal, briefly explain which "
    "exposed surfaces should shrink or disappear and whether new exposed undersides will be created. "
    "Distinguish geometric evidence from a hypothesis. If evidence is insufficient, return no "
    "proposals with a reason; no further edit is required."
)


def attempt_feedback(attempts):
    """Short existing-record feedback; never infer correspondence from region rank/ID."""
    return [{
        "status": a.get("status"), "reason": str(a.get("reason") or "")[:600],
        "modification_summary": a.get("modification_summary", "Unavailable in this saved attempt."),
        "overhang_comparison": a.get("overhang_comparison"),
        "total_area": a.get("total_area"),
        "local_area_change": {
            "status": "UNCERTAIN",
            "reason": "No verified cross-version exterior-region correspondence is recorded; "
                      "region IDs/ranks and source AABB overlaps are not sufficient attribution."
        },
        "candidate_report": a.get("candidate"),
    } for a in attempts]


def budget_remaining(workspace: Path, options: dict[str, Any]) -> int | str:
    if options.get('mode') == 'planned_checks' and options.get('max_candidates', 2) is None:
        return 'round_limited'  # No independent edit cap; outer rounds/time/API budgets still apply.
    path = workspace / "edit_attempts.jsonl"
    count = len(path.read_text().splitlines()) if path.exists() else 0
    return max(0, int(options.get("max_candidates", 2)) - count)


def reserve_attempt(workspace: Path, options: dict[str, Any], stage: str,
                    *, attempt_id: str | None = None, parent: str | None = None) -> bool:
    if not options:
        return True
    ledger = workspace / "edit_attempts.jsonl"
    if attempt_id and ledger.exists() and any(json.loads(line).get("attempt_id") == attempt_id
                                              for line in ledger.read_text().splitlines()):
        return False  # An existing reservation is never permission to call again.
    if not budget_remaining(workspace, options):
        return False
    with (workspace / "edit_attempts.jsonl").open("a") as handle:
        handle.write(json.dumps({"stage": stage, "origin": stage, "started_at": time.time(),
                                 "attempt_id": attempt_id, "parent_version": parent}) + "\n")
        handle.flush()
    return True


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def review_images(reference, baseline, candidate):
    """Send byte-identical images once, preserving every group/view assignment."""
    images, by_hash = [], {}
    mapping = {"index_base": 1, "deduplication": "sha256_of_file_bytes"}
    for group, paths in (("reference", reference), ("baseline", baseline), ("candidate", candidate)):
        indices = []
        for path in paths:
            digest = file_hash(Path(path))
            if digest not in by_hash:
                images.append(path)
                by_hash[digest] = len(images)
            indices.append(by_hash[digest])
        mapping[f"{group}_indices"] = indices
    mapping["unique_image_count"] = len(images)
    return tuple(images), mapping


def version_record(version_id, source, execution, runs=(), reviews=None, extra_files=()):
    """Bind immutable asset files and their exact measurement/review payloads."""
    paths = [source, *extra_files]
    if not source.is_file():
        raise ValueError("version source is missing")
    if execution is not None:
        if not execution.glb_path.is_file() or (execution.urdf_path and not execution.urdf_path.is_file()):
            raise ValueError("version generated asset is missing")
        if any(not p.is_file() for p in execution.render_paths):
            raise ValueError("version render is missing")
        paths += [execution.glb_path, *execution.render_paths]
        paths += [execution.glb_path.parent / "meta.json",
                  execution.glb_path.with_name(f"{execution.glb_path.stem}.joint_states.json")]
        if execution.urdf_path:
            paths += [execution.urdf_path, *sorted((execution.urdf_path.parent / "meshes").rglob("*"))]
        if execution.source_index_path:
            paths.append(execution.source_index_path)
        if execution.analysis_geometry_path:
            paths.append(execution.analysis_geometry_path)
    payload = {"id": version_id, "source": str(source),
               "execution": json.loads(json.dumps(asdict(execution), default=str)) if execution else None,
               "checkers": [{"spec": r.spec.model_dump(), "result": r.result.model_dump(),
                             "output_dir": str(r.output_dir), "command": list(r.command)} for r in runs],
               "reviews": reviews or {"appearance_approved": None, "protection": {"status": "UNCONFIRMED"}},
               "files": {str(p): file_hash(p) for p in paths if p.is_file()}}
    payload["record_hash"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return payload


def assert_version(record):
    payload = {k: v for k, v in record.items() if k != "record_hash"}
    if hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest() != record["record_hash"]:
        raise ValueError("version checker/review record changed")
    for name, expected in record["files"].items():
        if file_hash(Path(name)) != expected:
            raise ValueError(f"version asset hash changed: {name}")


def version_assets(record):
    from .utils.execution import ExecutionResult
    from .checkers import CheckerRun
    from .models import CheckerSpec, CheckerResult
    assert_version(record)
    raw = record["execution"]
    execution = ExecutionResult(**{k: (tuple(Path(p) for p in v) if k == "render_paths" else
                                      Path(v) if v is not None and (k.endswith("_path") or k == "output_root") else v)
                                   for k, v in raw.items()})
    runs = [CheckerRun(CheckerSpec.model_validate(r["spec"]), CheckerResult.model_validate(r["result"]),
                       Path(r["output_dir"]), tuple(r["command"])) for r in record["checkers"]]
    return Path(record["source"]), execution, runs


def edit_outcome(output, events, before_hash, after_hash, error=None):
    from .tools.context import patch_failure
    evidence = {"tool_events": [asdict(e) for e in events], "before_sha256": before_hash,
                "after_sha256": after_hash}
    if error is not None:
        return {**evidence, "status": "TOOL_ERROR", "reason": str(error)[:300]}
    if patch_failure(events):
        return {**evidence, "status": "TOOL_ERROR", "reason": patch_failure(events)}
    if before_hash != after_hash:
        return {**evidence, "status": "CHANGED", "reason": "candidate source changed; evaluation required"}
    declaration = output if isinstance(output, dict) else None
    if isinstance(output, str):
        try:
            declaration = json.loads(output)
        except (ValueError, TypeError):
            match = re.match(r"^\s*NO_CHANGE\s*:\s*(\S.+)", output, re.S)
            declaration = {"edit_action": "NO_CHANGE", "reason": match[1]} if match else None
    if any(e.tool == "apply_patch" for e in events):
        return {**evidence, "status": "NO_EFFECT", "reason": "patch tool completed but source hash is unchanged"}
    if isinstance(declaration, dict) and declaration.get("edit_action") == "NO_CHANGE" and str(declaration.get("reason", "")).strip():
        return {**evidence, "status": "NO_CHANGE", "reason": str(declaration["reason"])[:500]}
    return {**evidence, "status": "NO_PATCH_UNEXPLAINED", "reason": "no changed source and no explicit NO_CHANGE declaration"}


def original_execution(options: dict[str, Any]):
    from .utils.execution import ExecutionResult
    root = Path(options["original_urdf"]).parent
    return ExecutionResult(root, root / "scene.glb", root / "scene.urdf",
                           tuple(sorted((root / "render").glob("*.png"))), "", "",
                           root / "source_index.json" if (root / "source_index.json").is_file() else None,
                           root / "analysis_geometry.json" if (root / "analysis_geometry.json").is_file() else None)


def allowed_edit(original: Path, candidate: Path, options: dict[str, Any]) -> bool:
    from .repair_policy import _module_symbols
    before, after = _module_symbols(original), _module_symbols(candidate)
    allowed = {"class:" + name for name in options["protection"].get("allowed_classes", [])}
    return all(key in allowed for key in set(before) | set(after) if before.get(key) != after.get(key))


def opportunities(result: Any) -> list[CheckerFinding]:
    if result.checker != "overhang" or result.status != "PASS":
        return []
    if not result.metrics.get("measurement"):
        return []
    regions = sorted(result.metrics.get("overhang_regions", []),
                     key=lambda row: row.get("area_mm2", 0), reverse=True)[:3]
    return [CheckerFinding(
        finding_id=f"overhang:optimization:{i}", rule_id="OVERHANG_AREA_OPTIMIZATION",
        category="optimization_opportunity", required=False, repairability="geometry",
        message="Optional local reduction; preserve protected shape/function/appearance first. May decline.",
        metric=MetricEvidence(name="overhang_area_mm2", value=row["area_mm2"], unit="mm^2", relative_tolerance=0),
        # Existing AABB localization uses volume overlap; a planar face has zero
        # volume. Use a measured surface point instead, retaining region bounds.
        region=RegionEvidence(kind="point", frame="print_mm", unit="mm",
                              point=row.get("representative_point_mm", row.get("centroid_mm")),
                              details={"bounds_mm": row["bounds_mm"],
                                       "provenance": "largest-face center on exterior; semantic source uncertain"}),
        evidence_refs=list(result.artifacts.values()),
    ) for i, row in enumerate(regions) if row.get("area_mm2", 0) > 0]


def compare_measurements(before: Any, after: Any) -> dict[str, Any]:
    out = {"conclusion": "unevaluated", "reason": "incomplete or inconsistent measurement"}
    if before.status != "PASS" or after.status != "PASS":
        return out
    a, b = before.metrics, after.metrics
    if not a.get("measurement") or a["measurement"] != b.get("measurement"):
        return out
    values = [a.get("overhang_area_mm2"), b.get("overhang_area_mm2"),
              a.get("area_uncertainty_mm2"), b.get("area_uncertainty_mm2")]
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0 for x in values):
        return out
    delta, tolerance = values[0] - values[1], values[2] + values[3]
    return {"conclusion": "improved" if delta > tolerance else "worsened" if delta < -tolerance else "unchanged",
            "reason": "area difference compared to combined absolute numerical uncertainty",
            "reduction_mm2": delta, "tolerance_mm2": tolerance,
            "support_contact_increased": b.get("nominal_contact_area_mm2", 0) > a.get("nominal_contact_area_mm2", 0)}


@lru_cache(maxsize=1)
def _support_analysis():
    """Console scripts install adsl.agents, not the repository experiments namespace."""
    try:
        from experiments.support_requirement_critical_surfaces import analyze
        return analyze
    except ModuleNotFoundError as error:
        if error.name != "experiments":
            raise  # Do not mask a missing dependency of the analyzer itself.
    import importlib.util
    import sys
    path = Path(__file__).resolve().parents[1] / "experiments/support_requirement_critical_surfaces/analyze.py"
    spec = importlib.util.spec_from_file_location("_adsl_overhang_support_analysis", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load overhang protection analyzer: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def protection_check(original_urdf: Path | None, candidate_urdf: Path | None,
                     options: dict[str, Any]) -> dict[str, Any]:
    """Compare configured protected triangles in world coordinates, not just AABBs.

    Exact part/surface correspondence required. No inferred dimensions or interfaces.
    """
    import numpy as np
    rules = options.get("protection", {}).get("surfaces", [])
    if not rules or original_urdf is None or candidate_urdf is None:
        return {"status": "UNCONFIRMED", "reason": "missing measurable protection surfaces or URDF"}
    def meshes(path):
        parsed = FINAL.parse_urdf(path)
        return {g.name: g.transformed_mesh() for g in FINAL.collision_geometries(parsed, FINAL.state_values(parsed)["initial"])}
    def triangles(mesh, selector, boxes=None):
        tri = np.asarray(mesh.triangles)[face_selector(mesh, selector)]
        if boxes is not None:
            # Conservative region correspondence: include whole triangles whose
            # AABBs intersect a protected original feature. Never clip surfaces.
            mask = np.zeros(len(tri), dtype=bool)
            for lower, upper in boxes:
                lower, upper = np.asarray(lower), np.asarray(upper)
                # Enlarge the selection only to cover float32 export rounding;
                # the selected triangles still require exact equality.
                padding = 8 * np.finfo(np.float32).eps * max(1.0, float(np.abs([lower, upper]).max()))
                lower, upper = lower - padding, upper + padding
                mask |= np.all(tri.max(axis=1) >= lower, axis=1) & np.all(tri.min(axis=1) <= upper, axis=1)
            tri = tri[mask]
        # Canonicalize vertex and triangle order without rescaling or translating.
        tri = np.array([t[np.lexsort(t.T[::-1])] for t in tri]).reshape(-1, 9)
        return tri[np.lexsort(tri.T[::-1])]
    try:
        analyzer = _support_analysis()
        FINAL, face_selector = analyzer.FINAL, analyzer.face_selector
        before, after = meshes(original_urdf), meshes(candidate_urdf)
        errors = []
        if options.get("scale_mm_per_source_unit") is not None:
            a = np.vstack([m.vertices for m in before.values()])
            b = np.vstack([m.vertices for m in after.values()])
            delta = np.abs(np.ptp(a, axis=0) - np.ptp(b, axis=0)) * float(options["scale_mm_per_source_unit"])
            if np.any(delta > 0.01):
                errors.append("initial print-space AABB extent changed beyond 0.01 mm")
        for rule in rules:
            if rule.get("source_index_regex"):
                root = original_urdf.parent
                index = json.loads((root / "source_index.json").read_text())
                if index["source_sha256"] != hashlib.sha256((root / "source.py").read_bytes()).hexdigest():
                    return {"status": "UNCONFIRMED", "reason": "protected-region source index does not match original source"}
                features = [f for f in index["features"] if re.search(rule["source_index_regex"], f["semantic_path"])
                            and f.get("resolution") == "complete" and f.get("frame") == "authored_scene" and f.get("bounds")]
                if not features:
                    return {"status": "UNCONFIRMED", "reason": "no reliable source-index protection regions"}
                boxes = [f["bounds"] for f in features]
                a = np.concatenate([triangles(m, rule.get("face", "all"), boxes) for m in before.values()])
                b = np.concatenate([triangles(m, rule.get("face", "all"), boxes) for m in after.values()])
                a, b = a[np.lexsort(a.T[::-1])], b[np.lexsort(b.T[::-1])]
                if not len(a) or a.shape != b.shape or not np.array_equal(a, b):
                    errors.append(f"{rule['label']}: fixed source-index protected-region surfaces changed or unmatched")
                continue
            names = sorted(n for n in before if re.search(rule["collision_regex"], n))
            candidate_names = sorted(n for n in after if re.search(rule["collision_regex"], n))
            if not names or not candidate_names or (rule["collision_regex"] != ".*" and names != candidate_names):
                errors.append(f"{rule['label']}: protected part missing or correspondence changed")
                continue
            if rule["collision_regex"] == ".*":
                # Legacy assets have one merged collision; new exports may have
                # semantic parts. Compare the same world-space surfaces, not names.
                a = np.concatenate([triangles(before[n], rule.get("face", "all")) for n in names])
                b = np.concatenate([triangles(after[n], rule.get("face", "all")) for n in candidate_names])
                a, b = a[np.lexsort(a.T[::-1])], b[np.lexsort(b.T[::-1])]
                if not len(a) or a.shape != b.shape or not np.array_equal(a, b):
                    errors.append(f"{rule['label']}: protected surface changed or correspondence unconfirmed")
                continue
            for name in names:
                a, b = triangles(before[name], rule.get("face", "all")), triangles(after[name], rule.get("face", "all"))
                if not len(a) or a.shape != b.shape or not np.allclose(a, b, rtol=0, atol=0):
                    errors.append(f"{rule['label']}: world-space protected triangles changed or could not be matched")
        # This checks specified surface geometry including dimensions; no claim
        # about other dimensions/interfaces unsupported by these constraints.
        return {"status": "FAIL" if errors else "PASS", "errors": errors,
                "scope": "exact configured world-space surfaces only; other dimensions unconfirmed"}
    except Exception as exc:
        return {"status": "UNCONFIRMED", "reason": f"{type(exc).__name__}: {exc}"[:240]}
