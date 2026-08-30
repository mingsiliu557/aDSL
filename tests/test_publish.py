from __future__ import annotations

from pathlib import Path

from adsl.agents.service import ObjectWorkflow
from adsl.agents.utils.execution import ExecutionResult


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_publish_preserves_joint_states_and_render_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    render_root = tmp_path / "execution" / "render"
    glb_path = _write(render_root / "scene.glb", "glb")
    urdf_path = _write(render_root / "scene.urdf", "urdf")
    render_path = _write(render_root / "render_0001.png", "png")
    _write(render_root / "scene.joint_states.json", '{"door": 0.4363}')
    _write(render_root / "meta.json", '{"width": 512, "height": 512}')
    _write(render_root / "meshes" / "door.glb", "door")
    execution = ExecutionResult(
        output_root=render_root.parent,
        glb_path=glb_path,
        urdf_path=urdf_path,
        render_paths=(render_path,),
        stdout="",
        stderr="",
    )

    published_glb, published_urdf, published_renders = ObjectWorkflow._publish(
        workspace,
        execution,
    )

    assert published_glb.read_text(encoding="utf-8") == "glb"
    assert published_urdf is not None
    assert published_urdf.read_text(encoding="utf-8") == "urdf"
    assert [path.name for path in published_renders] == ["render_0001.png"]
    assert (workspace / "scene.joint_states.json").read_text(encoding="utf-8") == (
        '{"door": 0.4363}'
    )
    assert (workspace / "render" / "meta.json").read_text(encoding="utf-8") == (
        '{"width": 512, "height": 512}'
    )
    assert (workspace / "meshes" / "door.glb").read_text(encoding="utf-8") == "door"
