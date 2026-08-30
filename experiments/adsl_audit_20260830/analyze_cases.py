from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET


RELATIONAL_CALLS = {
    "align_anchors", "align_centers", "attach_joint", "attach_part",
    "distribute_along_axis", "fixed", "grid_shapes", "offset_from",
    "place_on_axis", "prismatic", "radial_shapes", "revolute",
    "shape_anchor", "stack_shapes",
}
BOOLEAN_CALLS = {
    "boolean_difference", "boolean_intersection", "boolean_union", "boolean_xor"
}
MANUAL_TRANSFORM_CALLS = {
    "rotate", "rotate_shape", "scale", "scale_shape", "translate", "translate_shape"
}
SEMANTIC_TERMS = (
    "armrest", "book", "box", "cabinet", "door", "drawer", "handle", "hub",
    "joint", "lamp", "leg", "lever", "motorcycle", "mug", "rim", "rug",
    "seat", "shelf", "slat", "sofa", "spoke", "table", "television",
    "wall", "wheel",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_json(path: Path) -> dict[str, Any] | None:
    try:
        return _read_json(path) if path.is_file() else None
    except (OSError, json.JSONDecodeError):
        return None


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return "<dynamic>"


def _ast_metrics(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"available": False}
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return {
            "available": False,
            "syntax_error": f"{error.msg} at line {error.lineno}",
            "sha256": _sha256(path),
        }
    calls = Counter(
        _call_name(node) for node in ast.walk(tree) if isinstance(node, ast.Call)
    )
    classes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(base.id)
            elif isinstance(base, ast.Attribute):
                bases.append(base.attr)
            else:
                bases.append(type(base).__name__)
        classes.append({"name": node.name, "bases": bases})
    numeric_literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    ]
    lower_source = source.lower()
    return {
        "available": True,
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "line_count": len(source.splitlines()),
        "class_definitions": classes,
        "call_counts": dict(sorted(calls.items())),
        "relational_call_count": sum(calls[name] for name in RELATIONAL_CALLS),
        "relational_calls": {
            name: calls[name] for name in sorted(RELATIONAL_CALLS) if calls[name]
        },
        "boolean_call_count": sum(calls[name] for name in BOOLEAN_CALLS),
        "boolean_calls": {
            name: calls[name] for name in sorted(BOOLEAN_CALLS) if calls[name]
        },
        "manual_transform_call_count": sum(
            calls[name] for name in MANUAL_TRANSFORM_CALLS
        ),
        "manual_transform_calls": {
            name: calls[name]
            for name in sorted(MANUAL_TRANSFORM_CALLS)
            if calls[name]
        },
        "numeric_literal_count": len(numeric_literals),
        "semantic_term_occurrences": {
            term: len(re.findall(rf"\b{re.escape(term)}\w*\b", lower_source))
            for term in SEMANTIC_TERMS
            if re.search(rf"\b{re.escape(term)}\w*\b", lower_source)
        },
    }


def _glb_metrics(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"available": False}
    base: dict[str, Any] = {
        "available": True,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    try:
        import numpy as np
        import trimesh
    except ImportError as error:
        return {**base, "analysis_error": f"missing dependency: {error.name}"}
    try:
        loaded = trimesh.load(path, force="scene")
        scene = loaded if isinstance(loaded, trimesh.Scene) else trimesh.Scene(loaded)
        geometries = []
        connected_components = 0
        for name, geometry in scene.geometry.items():
            # glTF commonly stores one vertex per face corner so that normals and
            # UVs can differ across a hard edge. Topology checks on that raw
            # representation incorrectly describe an ordinary exported cube as
            # six disconnected, non-watertight faces. Preserve the raw count,
            # then weld coincident vertices on a copy before measuring topology.
            analyzed = geometry.copy()
            raw_vertex_count = int(len(analyzed.vertices))
            analyzed.merge_vertices(merge_tex=True, merge_norm=True)
            row: dict[str, Any] = {
                "name": str(name),
                "raw_vertices": raw_vertex_count,
                "vertices_after_weld": int(len(analyzed.vertices)),
                "faces": int(len(analyzed.faces)),
                "watertight_after_weld": bool(analyzed.is_watertight),
                "euler_number_after_weld": int(analyzed.euler_number),
                "visual_type": type(geometry.visual).__name__,
            }
            try:
                row["degenerate_face_count"] = int(
                    np.count_nonzero(np.asarray(analyzed.area_faces) <= 1e-12)
                )
            except Exception as error:
                row["degenerate_error"] = type(error).__name__
            try:
                count = len(analyzed.split(only_watertight=False, engine=None))
                row["connected_components_after_weld"] = int(count)
                connected_components += int(count)
            except Exception as error:
                row["connected_components_after_weld_error"] = type(error).__name__
            material = getattr(geometry.visual, "material", None)
            if material is not None:
                row["material_type"] = type(material).__name__
                row["material_name"] = getattr(material, "name", None)
            geometries.append(row)
        bounds = scene.bounds
        return {
            **base,
            "geometry_count": len(scene.geometry),
            "connected_component_count": connected_components,
            "bounds": None if bounds is None else np.asarray(bounds).round(6).tolist(),
            "extents": None
            if bounds is None
            else (np.asarray(bounds[1]) - np.asarray(bounds[0])).round(6).tolist(),
            "geometries": geometries,
        }
    except Exception as error:
        return {**base, "analysis_error": f"{type(error).__name__}: {error}"}


def _urdf_metrics(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"available": False}
    base: dict[str, Any] = {
        "available": True,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    try:
        root = ET.parse(path).getroot()
        joints = []
        for joint in root.findall("joint"):
            axis = joint.find("axis")
            limit = joint.find("limit")
            parent = joint.find("parent")
            child = joint.find("child")
            joints.append(
                {
                    "name": joint.get("name"),
                    "type": joint.get("type"),
                    "parent": None if parent is None else parent.get("link"),
                    "child": None if child is None else child.get("link"),
                    "axis": None if axis is None else axis.get("xyz"),
                    "limit": None if limit is None else dict(limit.attrib),
                }
            )
        return {
            **base,
            "robot_name": root.get("name"),
            "link_count": len(root.findall("link")),
            "joint_count": len(joints),
            "movable_joint_count": sum(
                joint["type"] in {"continuous", "prismatic", "revolute"}
                for joint in joints
            ),
            "joint_type_counts": dict(
                Counter(str(joint["type"]) for joint in joints)
            ),
            "mesh_reference_count": len(root.findall(".//mesh")),
            "joints": joints,
        }
    except Exception as error:
        return {**base, "analysis_error": f"{type(error).__name__}: {error}"}


def _session_metrics(workspace: Path) -> dict[str, Any]:
    candidates = sorted(
        {
            path
            for pattern in ("*.sqlite", "*.sqlite3", "*.db")
            for path in workspace.rglob(pattern)
            if path.is_file()
        }
    )
    files = []
    total_images = 0
    for path in candidates:
        data = path.read_bytes()
        image_count = data.count(b"data:image")
        total_images += image_count
        files.append(
            {
                "path": str(path.relative_to(workspace)),
                "bytes": len(data),
                "data_image_occurrences": image_count,
            }
        )
    return {
        "database_count": len(files),
        "total_bytes": sum(item["bytes"] for item in files),
        "data_image_occurrences": total_images,
        "files": files,
    }


def _critic_metrics(workspace: Path) -> dict[str, Any]:
    images: list[dict[str, Any]] = []
    codes: list[dict[str, Any]] = []
    debuggers: list[dict[str, Any]] = []
    rounds = workspace / "rounds"
    if not rounds.is_dir():
        return {
            "image_decisions": images,
            "code_decisions": codes,
            "debugger_decisions": debuggers,
        }
    for path in sorted(rounds.glob("round_*/*.json")):
        payload = _safe_json(path)
        if payload is None:
            continue
        row = {"path": str(path.relative_to(workspace)), **payload}
        if path.name == "image_critique.json":
            images.append(row)
        elif path.name == "code_critique.json":
            codes.append(row)
        elif path.name == "debugger.json":
            debuggers.append(row)
    return {
        "image_decisions": images,
        "code_decisions": codes,
        "debugger_decisions": debuggers,
    }


def _source_diff(source_path: Path, parent: Path | None) -> dict[str, Any] | None:
    if parent is None or not source_path.is_file() or not parent.is_file():
        return None
    before = parent.read_text(encoding="utf-8").splitlines()
    after = source_path.read_text(encoding="utf-8").splitlines()
    diff = list(difflib.unified_diff(before, after, lineterm=""))
    return {
        "parent_source": str(parent),
        "parent_sha256": _sha256(parent),
        "source_sha256": _sha256(source_path),
        "changed": before != after,
        "added_lines": sum(
            line.startswith("+") and not line.startswith("+++") for line in diff
        ),
        "removed_lines": sum(
            line.startswith("-") and not line.startswith("---") for line in diff
        ),
        "diff_line_count": len(diff),
    }


def _analyze_invocation(
    invocation: dict[str, Any],
    output_root: Path,
    launcher: dict[str, Any],
) -> dict[str, Any]:
    workspace = output_root / str(invocation["output"])
    source_path = workspace / "source.py"
    parent = None
    if invocation.get("source_from"):
        parent = output_root / str(invocation["source_from"]) / "source.py"
    renders = sorted((workspace / "render").glob("*.png"))
    return {
        "id": invocation["id"],
        "logical_case": invocation["logical_case"],
        "action": invocation["action"],
        "workspace": str(workspace),
        "workspace_exists": workspace.is_dir(),
        "launcher": launcher.get("invocations", {}).get(invocation["id"]),
        "run": _safe_json(workspace / "run.json"),
        "runtime_config": _safe_json(workspace / "runtime_config.json"),
        "plan": _safe_json(workspace / "plan.json"),
        "source": _ast_metrics(source_path),
        "source_diff": _source_diff(source_path, parent),
        "glb": _glb_metrics(workspace / "scene.glb"),
        "urdf": _urdf_metrics(workspace / "scene.urdf"),
        "render_count": len(renders),
        "render_bytes": sum(path.stat().st_size for path in renders),
        "render_meta": _safe_json(workspace / "render" / "meta.json"),
        "joint_states": _safe_json(workspace / "scene.joint_states.json"),
        "critics": _critic_metrics(workspace),
        "sessions": _session_metrics(workspace) if workspace.is_dir() else {},
    }


def _markdown(manifest: dict[str, Any], results: list[dict[str, Any]]) -> str:
    lines = [
        "# aDSL 诊断 case 结果",
        "",
        f"- 生成时间：{_utc_now()}",
        "- Verification Status：ANALYZED",
        "- 说明：这是环境相关的定向小样本诊断，不是论文完整 benchmark，也不支持统计性 SOTA 结论。",
        "",
        "| Invocation | Logical case | Launcher | Run | Round | Approved | Finalization | Tokens | Seconds |",
        "|---|---|---|---|---:|---|---|---:|---:|",
    ]
    for item in results:
        launcher = item.get("launcher") or {}
        run = item.get("run") or {}
        lines.append(
            "| {id} | {logical} | {launcher_status} | {run_status} | {round} | "
            "{approved} | {reason} | {tokens} | {seconds} |".format(
                id=item["id"],
                logical=item["logical_case"],
                launcher_status=launcher.get("status", "-"),
                run_status=run.get("status", "-"),
                round=run.get("selected_round", "-"),
                approved=run.get("approved", "-"),
                reason=run.get("finalization_reason", "-"),
                tokens=(run.get("usage") or {}).get("total_tokens", "-"),
                seconds=launcher.get("elapsed_seconds", "-"),
            )
        )
    lines.extend(["", "## 逻辑 case 预期", ""])
    for case in manifest["logical_cases"]:
        lines.extend(
            [
                f"### {case['id']} — {case['category']}",
                "",
                case["claim"],
                "",
            ]
        )
        lines.extend(f"- {expectation}" for expectation in case["expectations"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).with_name("case_manifest.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/adsl_audit_20260830/case_results.json"),
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    manifest = _read_json(args.manifest.expanduser().resolve())
    output_root = (repo_root / manifest["runtime"]["output_root"]).resolve()
    launcher = _safe_json(output_root / "launcher_results.json") or {
        "invocations": {}
    }
    results = [
        _analyze_invocation(invocation, output_root, launcher)
        for invocation in manifest["invocations"]
    ]
    payload = {
        "schema_version": 1,
        "study_id": manifest["study_id"],
        "generated_at": _utc_now(),
        "verification_status": "ANALYZED",
        "scope": manifest["scope"],
        "paper": manifest["paper"],
        "runtime": manifest["runtime"],
        "logical_cases": manifest["logical_cases"],
        "invocations": results,
    }
    output = args.output
    if not output.is_absolute():
        output = repo_root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    output.with_suffix(".md").write_text(
        _markdown(manifest, results),
        encoding="utf-8",
    )
    print(output)
    print(output.with_suffix(".md"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
