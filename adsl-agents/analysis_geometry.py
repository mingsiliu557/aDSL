from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import numpy as np

from adsl.core import Asset

from .feedback_schema import sha256_file, stable_hash
from .source_index import RuntimeFeature, SourceIndex


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _feature_payload(feature: RuntimeFeature | None) -> dict[str, Any]:
    if feature is None:
        return {
            "feature_id": None,
            "source_ids": [],
            "source_locations": [],
            "bounds": None,
            "resolution": "unresolved",
        }
    return {
        "feature_id": feature.feature_id,
        "source_ids": list(feature.source_ids),
        "source_locations": list(feature.source_locations),
        "bounds": feature.bounds,
        "resolution": feature.resolution,
    }


def build_analysis_geometry(
    source_path: str | Path,
    scene: Asset,
    source_index: SourceIndex,
) -> dict[str, Any]:
    """Serialize source-linked analytic geometry before GLB/URDF flattening.

    The manifest is evidence, not a solver result.  It retains the aDSL
    hierarchy and boolean markers so topology/FEA tools do not need to infer
    design intent from a rendered triangle mesh.
    """

    source = Path(source_path).expanduser().resolve()
    by_path = {feature.semantic_path: feature for feature in source_index.features}

    def walk(
        asset: Asset,
        *,
        semantic_path: str,
        attach_mode: str,
        edge_name: str,
    ) -> dict[str, Any]:
        feature = by_path.get(semantic_path)
        primitives = [_jsonable(dict(value)) for value in asset.iter_local_primitives()]
        boolean_modes = [
            str(value.get("params", {}).get("mode", "UNION")).upper()
            for value in primitives
            if value.get("type") == "boolean"
        ]
        children = [
            walk(
                child,
                semantic_path=f"{semantic_path}/{name}",
                attach_mode="part",
                edge_name=name,
            )
            for name, child in asset._parts.items()
        ]
        joints = []
        for name, child in asset.joint_children.items():
            joint = asset._joints[name]
            joints.append(
                {
                    "name": name,
                    "spec": _jsonable(joint.to_dict()),
                    "child": walk(
                        child,
                        semantic_path=f"{semantic_path}/{name}",
                        attach_mode="joint",
                        edge_name=name,
                    ),
                }
            )
        return {
            "semantic_path": semantic_path,
            "name": edge_name,
            "label": str(asset.label),
            "asset_class": type(asset).__name__,
            "attach_mode": attach_mode,
            "boolean_mode": boolean_modes[0] if boolean_modes else None,
            "primitives": primitives,
            "children": children,
            "joints": joints,
            **_feature_payload(feature),
        }

    root_name = str(scene.label or type(scene).__name__)
    root = walk(
        scene,
        semantic_path=root_name,
        attach_mode="root",
        edge_name=root_name,
    )
    payload = {
        "version": 1,
        "source_path": str(source),
        "source_sha256": sha256_file(source),
        "source_index_sha256": source_index.index_sha256,
        "frame": "authored_scene",
        "length_unit": "scene_unit",
        "root": root,
        "unresolved": list(source_index.unresolved),
    }
    payload["geometry_sha256"] = stable_hash(root)
    return payload


__all__ = ["build_analysis_geometry"]
