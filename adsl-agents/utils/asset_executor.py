from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import runpy
import hashlib
import shutil
import traceback
from typing import Sequence

# Blender must initialize before trimesh-backed modules on Windows.
from adsl.core import Asset, export_glb, export_urdf
from adsl.agents.analysis_geometry import build_analysis_geometry
from adsl.agents.source_index import build_source_index
from adsl.tools.gpu_render_queue import submit_render_job
from adsl.tools.render import render_video


def _positive_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--render-view-count", type=int, default=8)
    parser.add_argument("--render-elevation", type=float, default=15.0)
    parser.add_argument("--view-layout", choices=("orbit", "review_eight"), default="orbit")
    parser.add_argument("--urdf", action="store_true")
    parser.add_argument('--fixed-assembly-config', type=Path)
    parser.add_argument('--render-only', action='store_true')
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    source = args.source.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if args.render_only:
        manifest = json.loads((output/'execution.json').read_text())
        if (manifest.get('manufacturing_status') == 'PASS'
                and manifest.get('display_status') == 'FAIL'
                and not Path(manifest['glb_path']).is_file()):
            manifest.update(render_status='NOT_EVALUATED',
                render_reason='Display export unavailable; manufacturing files are independent')
            (output/'execution.json').write_text(json.dumps(manifest, indent=2))
            return 0
        render_video(output_dir=output/'render', glb_path=Path(manifest['glb_path']),
            elevations=(args.render_elevation,), num_camera_per_layer=args.render_view_count,
            view_layout=args.view_layout)
        if (output/'assembly'/'exploded.glb').is_file():
            render_video(output_dir=output/'assembly'/'exploded_render', glb_path=output/'assembly'/'exploded.glb',
                elevations=(-30.0,30.0), num_camera_per_layer=1)
        diagnostic = manifest.get('assembly_diagnostic', {})
        if diagnostic.get('diagnostic_only'):
            from PIL import Image, ImageDraw
            import textwrap
            label = 'DIAGNOSTIC DISPLAY ONLY - SHAPE AND ASSEMBLY NOT VERIFIED.'
            if diagnostic.get('missing_parts'):
                label += f" Missing display parts: {', '.join(diagnostic['missing_parts'])}."
            if diagnostic.get('omitted_mesh_nodes'):
                label += f" Omitted mesh nodes: {len(diagnostic['omitted_mesh_nodes'])}."
            lines = textwrap.wrap(label.encode('ascii', 'backslashreplace').decode(), 65)
            for path in (output/'render').glob('*.png'):
                with Image.open(path) as raw:
                    picture = raw.convert('RGB')
                marked = Image.new('RGB', (picture.width, picture.height+16*len(lines)+8), 'white')
                marked.paste(picture, (0,16*len(lines)+8))
                draw = ImageDraw.Draw(marked)
                for i, line in enumerate(lines):
                    draw.text((4,4+16*i), line, fill='darkred')
                marked.save(path)
        return 0
    namespace = runpy.run_path(str(source), run_name="__adsl_generated__")
    scene = namespace.get("scene")
    if not isinstance(scene, Asset):
        raise TypeError("Generated source must assign an adsl Asset to `scene`")
    diagnostic = {}
    assembly_report = None
    if args.fixed_assembly_config:
        from adsl.core import FixedAssembly
        from adsl.core.serialize import asset_to_dict
        from adsl.core.export.export_assembly import export_assembly
        assembly = namespace.get('assembly')
        if not isinstance(assembly, FixedAssembly):
            raise TypeError('Fixed assembly source must define global `assembly: FixedAssembly`')
        def scene_signature(value):
            return json.dumps(asset_to_dict(value), sort_keys=True, default=lambda v:v.tolist())
        if scene_signature(scene) != scene_signature(assembly.scene()):
            raise ValueError('scene must equal assembly.scene(); unselected material or post-assembly transform is not permitted')
        try:
            report = export_assembly(assembly, output/'assembly',
                source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                expected=json.loads(args.fixed_assembly_config.read_text()))
        except Exception as error:
            from adsl.agents.utils.io import write_json
            target = output/'assembly'/'assembly_manifest.json'
            report = json.loads(target.read_text()) if target.is_file() else {'failures':[]}
            report['status'] = 'ERROR'
            report['failures'].append({'code':'ASSEMBLY_EXECUTION_ERROR',
                'stage':report.get('stage'), 'reason':f'{type(error).__name__}: {error}'[:300]})
            write_json(target, report)
            traceback.print_exc()
        assembly_report = report
        diagnostic = dict(report.get('diagnostic', {}))
        diagnostic.update(diagnostic_only=report['status'] != 'PASS'
                          or report.get('display_status') == 'FAIL')
        # A failed readback may leave bytes on disk. Only declared display
        # assets can be rendered; existence must not override their verdict.
        declared_scene = report.get('scene_glb', 'scene.glb')
        scene_path = output/'assembly'/(declared_scene or 'display_unavailable.glb')
        if not declared_scene or not scene_path.is_file():
            scene_path = output/'assembly'/(diagnostic.get('glb') or 'display_unavailable.glb')
        manufacturing_without_display = (report.get('manufacturing_status') == 'PASS'
                                         and report.get('display_status') == 'FAIL')
        if not scene_path.is_file() and not manufacturing_without_display:
            raise ValueError('No renderable candidate geometry; Code Critic can read source and assembly_manifest.json')
        (output/'render').mkdir(exist_ok=True)
        if scene_path.is_file():
            shutil.copy2(scene_path, output/'render'/'scene.glb')
        else:
            diagnostic.update(display_available=False,
                reason='Display export unavailable; manufacturing files are independent')
    source_index_path = output / "source_index.json"
    source_index_path.parent.mkdir(parents=True, exist_ok=True)
    analysis_geometry_path = output / "analysis_geometry.json"
    try:
        source_index = build_source_index(source, scene)
        source_index_path.write_text(source_index.model_dump_json(indent=2), encoding="utf-8")
        analysis_geometry_path.write_text(json.dumps(build_analysis_geometry(source, scene, source_index),
            indent=2, ensure_ascii=False), encoding='utf-8')
    except (ValueError, TypeError, KeyError) as error:
        if not args.fixed_assembly_config:
            raise
        (output/'source_index_unavailable.json').write_text(json.dumps({'status':'UNAVAILABLE',
            'reason':str(error)[:300], 'fallback':'read current source and interface/print-part IDs'}))
        source_index_path, analysis_geometry_path = None, None
    render_root = output / "render"
    render_root.mkdir(parents=True, exist_ok=True)
    glb_path = render_root / "scene.glb"
    if not args.fixed_assembly_config:
        export_glb(
            scene,
            filepath=glb_path,
            clear_scene=True,
            apply_modifiers=True,
        )

    urdf_path = None
    if args.urdf:
        urdf_path = render_root / "scene.urdf"
        export_urdf(
            scene,
            filepath=urdf_path,
            mesh_backend="blender",
            visual_mesh_format="glb",
        )

    render_backend = "none"
    render_job: dict[str, object] | None = None
    if args.render and glb_path.is_file():
        queue_value = os.environ.get("ADSL_GPU_RENDER_QUEUE", "").strip()
        if queue_value:
            render_backend = "gpu_queue"
            render_job = submit_render_job(
                queue_root=queue_value,
                glb_path=glb_path,
                output_dir=render_root,
                width=_positive_env_int("ADSL_RENDER_WIDTH", 1024),
                height=_positive_env_int("ADSL_RENDER_HEIGHT", 1024),
                render_samples=_positive_env_int("ADSL_RENDER_SAMPLES", 256),
                elevations=(args.render_elevation,),
                num_camera_per_layer=args.render_view_count,
                view_layout=args.view_layout,
                wait_timeout=_positive_env_float(
                    "ADSL_GPU_RENDER_WAIT_TIMEOUT_SECONDS",
                    3600.0,
                ),
            )
        else:
            render_backend = "local"
            render_video(
                output_dir=render_root,
                glb_path=glb_path,
                elevations=(args.render_elevation,),
                num_camera_per_layer=args.render_view_count,
                view_layout=args.view_layout,
            )

    manifest = {
        "result_path": str(output),
        "render_root": str(render_root),
        "glb_path": str(glb_path),
        "urdf_path": None if urdf_path is None else str(urdf_path),
        "render_backend": render_backend,
        "render_job_id": None if render_job is None else render_job["job_id"],
        "source_index_path": str(source_index_path) if source_index_path else None,
        "analysis_geometry_path": str(analysis_geometry_path) if analysis_geometry_path else None,
    }
    if args.fixed_assembly_config:
        manifest['assembly_diagnostic'] = diagnostic
        manifest['manufacturing_status'] = assembly_report.get('manufacturing_status')
        manifest['display_status'] = assembly_report.get('display_status')
        manifest['display_available'] = glb_path.is_file()
        if args.render and not glb_path.is_file():
            manifest.update(render_status='NOT_EVALUATED',
                render_reason='Display export unavailable; manufacturing files are independent')
    (output / "execution.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
