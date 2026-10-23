"""Prepare a frozen GPU evaluator without changing the default CPU protocol."""


def gpu_evaluator_source(source):
    """Preserve the assigned device and enter the renderer through its import hooks."""
    source=source.replace('"device": "CPU"','"device": "GPU"')
    old='CUDA_VISIBLE_DEVICES="", HIP_VISIBLE_DEVICES="",'
    assert source.count(old)==1, 'Unexpected evaluator CUDA environment'
    source=source.replace(old,'HIP_VISIBLE_DEVICES="",')
    old='''cpu_cli = ("import bpy, runpy; bpy.context.scene.cycles.device = 'CPU'; "
                   "runpy.run_module('adsl.tools.render', run_name='__main__')")'''
    assert source.count(old)==1, 'Unexpected evaluator renderer entrypoint'
    source=source.replace(old,'cpu_cli = "from adsl.tools.render import main; main()"')
    source=source.replace('# The thin launcher explicitly fixes the device before invoking\n'
        '                # the existing renderer CLI; its scene clear preserves settings.',
        '# Normal import applies the batch GPU and render audit hooks.\n'
        '                # Preserve the device assigned by the case runner.')
    return source
