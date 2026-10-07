"""Manufacturing execution survives an explicitly diagnosed display failure."""
import importlib
import json

import pytest

from adsl.agents.utils.execution import AssetExecutionError, execute_asset_source


@pytest.mark.parametrize('manufacturing,display,fixed,allowed', [
    ('PASS', 'FAIL', True, True),
    ('PASS', 'PASS', True, False),
    ('FAIL', 'FAIL', True, False),
    (None, None, True, False),
    ('PASS', 'FAIL', False, False),
])
def test_missing_display_is_only_allowed_for_explicit_manufacturing_success(
        tmp_path, monkeypatch, manufacturing, display, fixed, allowed):
    source = tmp_path/'source.py'
    source.write_text('scene = None\n')
    output = tmp_path/'asset'
    calls = []

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            calls.append(command)

        def communicate(self, **kwargs):
            (output/'execution.json').write_text(json.dumps({
                'glb_path': str(output/'render/scene.glb'), 'urdf_path': None,
                'manufacturing_status': manufacturing, 'display_status': display,
            }))
            return '', ''

    monkeypatch.setattr('adsl.agents.utils.execution.subprocess.Popen', Process)
    kwargs = dict(render=True, export_urdf=False,
                  fixed_assembly={'mm_per_unit': 1} if fixed else None)
    if not allowed:
        with pytest.raises(AssetExecutionError):
            execute_asset_source(source, output, **kwargs)
        return
    execution = execute_asset_source(source, output, **kwargs)
    assert not execution.glb_path.exists()
    assert execution.render_paths == () and len(calls) == 1
    report = json.loads((output/'execution.json').read_text())
    assert report['manufacturing_status'] == 'PASS'
    assert report['display_status'] == 'FAIL'
    assert report['render_status'] == 'NOT_EVALUATED'


@pytest.mark.parametrize('rejected_file_remains', [False, True])
def test_executor_records_source_and_manufacturing_when_no_display_exists(tmp_path, monkeypatch, rejected_file_remains):
    from adsl.core import Cube, FixedAssembly
    executor = importlib.import_module('adsl.agents.utils.asset_executor')
    exporter = importlib.import_module('adsl.core.export.export_assembly')
    assembly = FixedAssembly(root_id='one', mm_per_unit=1)
    assembly.add_part('one', Cube((1, 1, 1)), components=['body'])
    source = tmp_path/'source.py'
    source.write_text('from adsl.core import *\nscene = Cube((1,1,1))\n')
    output = tmp_path/'asset'
    output.mkdir()
    config = tmp_path/'config.json'
    config.write_text('{}')
    monkeypatch.setattr(executor.runpy, 'run_path',
        lambda *args, **kwargs: {'scene': assembly.scene(), 'assembly': assembly})

    def fake_export(assembly, directory, **kwargs):
        directory.mkdir()
        report = dict(status='PASS', manufacturing_status='PASS', display_status='FAIL', scene_glb=None,
            failures=[], display_failures=[{'code':'TARGET_PRECISION_UNREPRESENTABLE'}],
            diagnostic={'display_available':False, 'glb':None})
        (directory/'assembly_manifest.json').write_text(json.dumps(report))
        if rejected_file_remains:
            (directory/'scene.glb').write_bytes(b'readback rejected these bytes')
        return report

    monkeypatch.setattr(exporter, 'export_assembly', fake_export)
    monkeypatch.setattr(executor, 'render_video',
        lambda **kwargs: pytest.fail('missing display must not be submitted to renderer'))
    assert executor.main(['--source', str(source), '--output', str(output),
        '--fixed-assembly-config', str(config), '--render']) == 0
    actual = json.loads((output/'execution.json').read_text())
    assert actual['manufacturing_status'] == 'PASS' and actual['display_status'] == 'FAIL'
    assert actual['display_available'] is False
    assert actual['assembly_diagnostic']['diagnostic_only']
    assert actual['render_status'] == 'NOT_EVALUATED'
    assert not (output/'render/scene.glb').exists()
    assert (output/'source_index.json').is_file()
    assert executor.main(['--source', str(source), '--output', str(output),
                          '--render-only']) == 0
