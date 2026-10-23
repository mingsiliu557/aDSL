"""Call the existing renderer and capture effective settings in the same process."""
import argparse
import importlib.metadata
import json
import os
import subprocess
import resource
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--settings-output', required=True, type=Path)
    parser.add_argument('--cycles-gpu', action='store_true')
    args, renderer_args = parser.parse_known_args(argv)
    if renderer_args[:1] == ['--']:
        renderer_args = renderer_args[1:]
    allocated = os.environ['CUDA_VISIBLE_DEVICES'].split(',')[0]
    monitor_proc = subprocess.Popen([
        'nvidia-smi', 'pmon', '-i', allocated, '-d', '1', '-s', 'm'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        import bpy
        if args.cycles_gpu:
            preferences = bpy.context.preferences.addons['cycles'].preferences
            preferences.compute_device_type = 'CUDA'
            preferences.get_devices()
            cuda_devices = [d for d in preferences.devices if d.type == 'CUDA']
            if len(cuda_devices) != 1:
                raise RuntimeError('Expected exactly one visible CUDA device from Slurm')
            for device in preferences.devices:
                device.use = device in cuda_devices
            bpy.context.scene.cycles.device = 'GPU'
        from adsl.tools import render
        render.main(renderer_args)
    finally:
        monitor_proc.terminate()
        monitor, _ = monitor_proc.communicate(timeout=10)
    import bpy
    import gpu
    scene = bpy.context.scene
    sun = next(x for x in scene.objects if x.type == 'LIGHT' and x.data.type == 'SUN')
    world = scene.world.node_tree.nodes.get('Background')
    graphics = {}
    try:
        graphics = {
            'graphics_renderer': gpu.platform.renderer_get(),
            'graphics_vendor': gpu.platform.vendor_get(),
            'graphics_version': gpu.platform.version_get(),
        }
    except SystemError:
        graphics = dict(graphics_renderer=None, graphics_vendor=None, graphics_version=None,
                        graphics_api_note='GPU platform query unavailable in background mode')
    allocated = os.environ['CUDA_VISIBLE_DEVICES'].split(',')[0]
    device_name = subprocess.check_output([
        'nvidia-smi', '-i', allocated, '--query-gpu=name', '--format=csv,noheader'], text=True).strip()
    own_graphics = any(len(row.split()) > 2 and row.split()[1] == str(os.getpid())
                       and any(t in row.split()[2] for t in ('G', 'C')) for row in monitor.splitlines() if not row.startswith('#'))
    settings = {
        'engine': scene.render.engine,
        'width': scene.render.resolution_x, 'height': scene.render.resolution_y,
        'resolution_percentage': scene.render.resolution_percentage,
        'render_samples': scene.cycles.samples if args.cycles_gpu else scene.eevee.taa_render_samples,
        'render_threads': scene.render.threads,
        'cycles_device': scene.cycles.device if args.cycles_gpu else None,
        'cycles_compute_backend': 'CUDA' if args.cycles_gpu else None,
        'visible_cuda_render_devices': len(cuda_devices) if args.cycles_gpu else None,
        'cycles_denoising': scene.cycles.use_denoising if args.cycles_gpu else None,
        'film_transparent': scene.render.film_transparent,
        'software_opengl_requested': os.environ.get('LIBGL_ALWAYS_SOFTWARE') == '1',
        'gallium_driver': os.environ.get('GALLIUM_DRIVER'),
        'view_transform': scene.view_settings.view_transform,
        'look': scene.view_settings.look,
        'exposure': scene.view_settings.exposure,
        'gamma': scene.view_settings.gamma,
        'sun_location': list(sun.location), 'sun_strength': sun.data.energy,
        'sun_shadows': sun.data.use_shadow,
        'world_strength': world.inputs['Strength'].default_value,
        'ambient_occlusion': scene.eevee.use_gtao,
        'blender_version': bpy.app.version_string,
        'bpyrenderer_version': importlib.metadata.version('bpyrenderer'),
        **graphics, 'allocated_device_name': device_name,
    }
    args.settings_output.write_text(json.dumps({
        'settings': settings,
        'allocated_gpu_process_confirmed': own_graphics,
        'gpu_observation_method': 'nvidia-smi pmon during rendering on allocated device',
        'gpu_process_samples': [row for row in monitor.splitlines()
                                if len(row.split()) > 2 and row.split()[1] == str(os.getpid())],
        'max_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }, indent=2)+'\n')


if __name__ == '__main__':
    main()
