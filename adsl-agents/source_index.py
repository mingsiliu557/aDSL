from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, Field

from adsl.core import Asset, shape_aabb

from .feedback_schema import sha256_file, stable_hash


_DSL_CALLS = {
    "Cube",
    "Cylinder",
    "RoundedCube",
    "Sphere",
    "align_anchors",
    "align_centers",
    "boolean_difference",
    "boolean_intersection",
    "boolean_union",
    "boolean_xor",
    "distribute_along_axis",
    "grid_shapes",
    "offset_from",
    "place_on_axis",
    "radial_shapes",
    "rotate_shape",
    "scale_shape",
    "stack_shapes",
    "transform_shape",
    "translate_shape",
}


class SourceSpan(BaseModel):
    start_line: int
    end_line: int
    start_col: int = 0
    end_col: int = 0

    @property
    def display(self) -> str:
        return f"L{self.start_line}-L{self.end_line}"


class SourceNode(BaseModel):
    source_id: str
    kind: str
    name: str
    scope: str
    owner_class: str | None = None
    semantic_name: str | None = None
    span: SourceSpan
    parameters: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    expression: str | None = None
    resolution: str = "complete"


class RuntimeFeature(BaseModel):
    feature_id: str
    semantic_path: str
    name: str
    asset_class: str
    parent_feature_id: str | None = None
    attach_mode: str = "part"
    bounds: list[list[float]] | None = None
    frame: str = "authored_scene"
    unit: str = "scene_unit"
    source_ids: list[str] = Field(default_factory=list)
    source_locations: list[str] = Field(default_factory=list)
    resolution: str = "complete"


class SourceIndex(BaseModel):
    version: int = 1
    source_path: str
    source_sha256: str
    index_sha256: str
    root_feature_id: str
    source_nodes: list[SourceNode] = Field(default_factory=list)
    features: list[RuntimeFeature] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return "dynamic_call"


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _source_id(*parts: object) -> str:
    return "source:" + stable_hash(parts)[:20]


class _SourceVisitor(ast.NodeVisitor):
    def __init__(self, text: str) -> None:
        self.text = text
        self.scope: list[str] = []
        self.class_stack: list[str] = []
        self.nodes: list[SourceNode] = []

    def _span(self, node: ast.AST) -> SourceSpan:
        return SourceSpan(
            start_line=int(getattr(node, "lineno", 1)),
            end_line=int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
            start_col=int(getattr(node, "col_offset", 0)),
            end_col=int(getattr(node, "end_col_offset", 0)),
        )

    def _scope_name(self) -> str:
        return "/".join(self.scope) or "<module>"

    def _record(
        self,
        node: ast.AST,
        *,
        kind: str,
        name: str,
        semantic_name: str | None = None,
        resolution: str = "complete",
    ) -> None:
        span = self._span(node)
        expression = ast.get_source_segment(self.text, node)
        names = sorted(
            {
                child.id
                for child in ast.walk(node)
                if isinstance(child, ast.Name)
            }
        )
        parameters = sorted(
            {
                keyword.arg
                for child in ast.walk(node)
                if isinstance(child, ast.Call)
                for keyword in child.keywords
                if keyword.arg is not None
            }
        )
        identity_name = semantic_name or name
        self.nodes.append(
            SourceNode(
                source_id=_source_id(kind, self._scope_name(), identity_name),
                kind=kind,
                name=name,
                scope=self._scope_name(),
                owner_class=self.class_stack[-1] if self.class_stack else None,
                semantic_name=semantic_name,
                span=span,
                parameters=parameters,
                dependencies=names,
                expression=expression,
                resolution=resolution,
            )
        )

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        parent_scope = self._scope_name()
        self._record(node, kind="class", name=node.name)
        self.scope.append(node.name)
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()
        self.scope.pop()
        _ = parent_scope

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self.scope.append(node.name)
        self._record(node, kind="function", name=node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> Any:
        name = _call_name(node)
        if name == "attach_part":
            semantic_name = _literal_string(node.args[0] if node.args else None)
            self._record(
                node,
                kind="attach_part",
                name=name,
                semantic_name=semantic_name,
                resolution="complete" if semantic_name is not None else "partial",
            )
        elif name in _DSL_CALLS:
            self._record(node, kind="dsl_call", name=name)
        self.generic_visit(node)


def parse_source_nodes(source_path: str | Path) -> list[SourceNode]:
    path = Path(source_path)
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    visitor = _SourceVisitor(text)
    visitor.visit(tree)
    return visitor.nodes


def _matching_source_nodes(
    nodes: Iterable[SourceNode], *, owner_class: str, part_name: str
) -> list[SourceNode]:
    exact = [
        node
        for node in nodes
        if node.kind == "attach_part"
        and node.owner_class == owner_class
        and node.semantic_name == part_name
    ]
    if exact:
        return exact
    return [
        node
        for node in nodes
        if node.kind == "attach_part"
        and node.owner_class == owner_class
        and node.semantic_name is None
    ]


def build_source_index(source_path: str | Path, scene: Asset) -> SourceIndex:
    path = Path(source_path).resolve()
    nodes = parse_source_nodes(path)
    features: list[RuntimeFeature] = []
    unresolved: list[str] = []
    root_name = str(scene.label or type(scene).__name__)

    def walk(
        asset: Asset,
        *,
        semantic_path: str,
        parent_feature_id: str | None,
        attach_mode: str,
        parent_class: str | None,
        part_name: str,
    ) -> None:
        feature_id = f"feature:{semantic_path}"
        direct: list[SourceNode]
        if parent_class is None:
            direct = [
                node for node in nodes
                if node.kind == "class" and node.name == type(asset).__name__
            ]
        else:
            direct = _matching_source_nodes(
                nodes, owner_class=parent_class, part_name=part_name
            )
        constructor_dependencies = (
            []
            if parent_class is None
            else [
                node
                for node in nodes
                if node.kind == "class" and node.name == type(asset).__name__
            ]
        )
        matched = list(
            {
                node.source_id: node
                for node in [*direct, *constructor_dependencies]
            }.values()
        )
        resolution = "complete"
        if len(direct) != 1:
            resolution = "unresolved" if not direct else "ambiguous"
            unresolved.append(
                f"{semantic_path}: {len(direct)} direct source candidates for "
                f"{parent_class or type(asset).__name__}.{part_name}"
            )
        elif direct[0].resolution != "complete":
            resolution = "partial"
            unresolved.append(
                f"{semantic_path}: runtime name maps to a dynamic source expression"
            )
        bounds = None
        try:
            lower, upper = shape_aabb(asset)
            bounds = [
                [float(value) for value in lower],
                [float(value) for value in upper],
            ]
        except (TypeError, ValueError):
            resolution = "partial"
            unresolved.append(f"{semantic_path}: geometry bounds unavailable")
        features.append(
            RuntimeFeature(
                feature_id=feature_id,
                semantic_path=semantic_path,
                name=part_name,
                asset_class=type(asset).__name__,
                parent_feature_id=parent_feature_id,
                attach_mode=attach_mode,
                bounds=bounds,
                source_ids=[node.source_id for node in matched],
                source_locations=[node.span.display for node in matched],
                resolution=resolution,
            )
        )
        for child_name, child in asset._parts.items():
            walk(
                child,
                semantic_path=f"{semantic_path}/{child_name}",
                parent_feature_id=feature_id,
                attach_mode="part",
                parent_class=type(asset).__name__,
                part_name=child_name,
            )
        for joint_name, child in asset.joint_children.items():
            walk(
                child,
                semantic_path=f"{semantic_path}/{joint_name}",
                parent_feature_id=feature_id,
                attach_mode="joint",
                parent_class=type(asset).__name__,
                part_name=joint_name,
            )

    walk(
        scene,
        semantic_path=root_name,
        parent_feature_id=None,
        attach_mode="root",
        parent_class=None,
        part_name=root_name,
    )
    payload = {
        "source_sha256": sha256_file(path),
        "source_nodes": [node.model_dump() for node in nodes],
        "features": [feature.model_dump() for feature in features],
    }
    return SourceIndex(
        source_path=str(path),
        source_sha256=payload["source_sha256"],
        index_sha256=stable_hash(payload),
        root_feature_id=f"feature:{root_name}",
        source_nodes=nodes,
        features=features,
        unresolved=unresolved,
    )


def load_source_index(path: str | Path) -> SourceIndex:
    return SourceIndex.model_validate_json(Path(path).read_text(encoding="utf-8"))


__all__ = [
    "RuntimeFeature",
    "SourceIndex",
    "SourceNode",
    "SourceSpan",
    "build_source_index",
    "load_source_index",
    "parse_source_nodes",
]
