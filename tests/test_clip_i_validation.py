import csv
import hashlib
import io
import json
import math
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from experiments.clip_i_validation import run


def test_cosine_and_invalid_features():
    assert run.cosine_score([3, 4], [6, 8]) == pytest.approx(100)
    assert run.cosine_score([1, 0], [0, 2]) == pytest.approx(0)
    for x in [[0, 0], [float('nan'), 1], [float('inf'), 0]]:
        with pytest.raises(ValueError):
            run.normalize(x)


def test_alpha_composite_before_processing():
    image = Image.new('RGBA', (2, 1), (0, 0, 0, 0))
    image.putpixel((1, 0), (255, 0, 0, 128))
    out = run.white_rgb(image)
    assert out.mode == 'RGB'
    assert out.getpixel((0, 0)) == (255, 255, 255)
    assert out.getpixel((1, 0)) == (255, 127, 127)


def records_fixture(tmp_path, case_ids=('one', 'two')):
    records = []
    for cid in case_ids:
        ref = cid+'_reference.png'
        Image.new('RGB', (2, 2), 'white').save(tmp_path/ref)
        for method in ['adsl', 'ours']:
            paths = []
            for i in range(8):
                path = f'{cid}_{method}_{i}.png'
                Image.new('RGB', (2, 2), 'red').save(tmp_path/path)
                paths.append(path)
            records.append({'case_id': cid, 'method': method, 'reference_image': ref, 'renders': paths})
    return records


def test_fake_encoder_end_to_end_mean_and_common_sample(tmp_path):
    records = records_fixture(tmp_path)
    calls = []
    def encode(path):
        calls.append(path.name)
        if 'reference' in path.name:
            return [1, 0]
        if 'ours' in path.name:
            return [1, 0] if path.stem.endswith('_0') else [0, 1]
        return [1, 1]
    views, cases, pairs, summary = run.evaluate(records, tmp_path, encode)
    assert len(views) == 32 and len(cases) == 4 and len(pairs) == 2
    assert summary['n'] == 2
    assert summary['method_means']['ours'] == pytest.approx(12.5)
    assert summary['method_means']['adsl'] == pytest.approx(100/math.sqrt(2))
    for row in cases:
        scores = [v['score'] for v in views if v['case_id'] == row['case_id'] and v['method'] == row['method']]
        assert row['case_score'] == pytest.approx(sum(scores)/8)
    assert sum('reference' in name for name in calls) == 2
    assert summary['self_score_check'] is True


def test_missing_image_not_zero_or_partial_mean(tmp_path):
    records = records_fixture(tmp_path)
    (tmp_path/records[3]['renders'][4]).unlink()
    views, cases, pairs, summary = run.evaluate(records, tmp_path, lambda p: [1, 0])
    failed = next(x for x in cases if x['case_id'] == 'two' and x['method'] == 'ours')
    assert failed['status'] == 'UNAVAILABLE' and failed['case_score'] is None
    assert failed['reason'] == 'MISSING_RENDER'
    assert summary['case_ids'] == ['one']
    assert summary['n'] == 1
    assert len(views) == 32
    assert pairs[1]['delta'] is None


def test_zero_norm_encoding_unavailable(tmp_path):
    records = records_fixture(tmp_path, ('one',))
    def encode(path):
        return [0, 0] if 'ours' in path.name else [1, 0]
    _, cases, _, summary = run.evaluate(records, tmp_path, encode)
    assert cases[1]['case_score'] is None
    assert summary['n'] == 0 and summary['method_means']['adsl'] is None


def test_render_command_uses_supported_parameters_and_fresh_paths(tmp_path):
    record = {'scene_glb': 'inputs/model.glb', 'renders': ['renders/one/ours/render_0001.png']}
    cmd = run.render_command(record, tmp_path)
    assert cmd[cmd.index('--')+1:] == [
        '--glb-path', str(tmp_path/'inputs/model.glb'), '--output-dir', str(tmp_path/'renders/one/ours'),
        '--view-layout', 'orbit', '--num-camera-per-layer', '8', '--elevations', '15',
        '--width', '1024', '--height', '1024', '--render-samples', '256',
        '--background', 'transparent', '--material-mode', 'native', '--render-threads', '2',
    ]
    assert '--radius' not in cmd and '--sun-strength' not in cmd


def make_archive(tmp_path, malicious=False):
    blobs = {'reference.png': b'reference', 'adsl.glb': b'adsl', 'ours.glb': b'ours'}
    digest = lambda b: hashlib.sha256(b).hexdigest()
    records = [dict(case_id='one', method=m, reference_image='reference.png',
                    reference_sha256=digest(blobs['reference.png']), scene_glb=m+'.glb',
                    scene_glb_sha256=digest(blobs[m+'.glb']), source_version='original',
                    source_sha256='a'*64, physical_asset_sha256='b'*64) for m in ['adsl', 'ours']]
    blobs['manifest.json'] = json.dumps({'records': records}).encode()
    s = io.StringIO()
    w = csv.DictWriter(s, ['case_id','reference_image','reference_sha256'])
    w.writeheader();w.writerow(dict(case_id='one', reference_image='reference.png', reference_sha256=digest(blobs['reference.png'])))
    blobs['cases.csv'] = s.getvalue().encode()
    sums = ''.join(f'{digest(b)}  {p}\n' for p, b in blobs.items()).encode()
    path = tmp_path/'input.zip'
    with zipfile.ZipFile(path, 'w') as z:
        for name, value in blobs.items(): z.writestr('root/'+name, value)
        z.writestr('root/SHA256SUMS', sums)
        z.writestr('root/source.py', 'raise RuntimeError("must never run")')
        if malicious: z.writestr('root/../../escape', 'unsafe')
    return path


def test_prepare_identity_and_does_not_extract_sources(tmp_path):
    z = make_archive(tmp_path)
    output = tmp_path/'run'
    run.prepare(z, output)
    cfg = run.load(output/'run_config.json')
    selected = run.load(output/'selected_inputs.json')
    assert cfg['case_ids'] == ['one'] and len(selected) == 2
    assert not list(output.rglob('source.py'))
    assert (output/'inputs/adsl.glb').read_bytes() == b'adsl'
    assert all(p.startswith('renders/') for r in selected for p in r['renders'])


def test_unsafe_archive_rejected_before_writing(tmp_path):
    z = make_archive(tmp_path, malicious=True)
    output = tmp_path/'run'
    with pytest.raises(ValueError, match='Unsafe archive'):
        run.prepare(z, output)
    assert not output.exists()


def test_cycles_requires_explicit_protocol_and_worker_gpu_option(tmp_path):
    z = make_archive(tmp_path)
    output = tmp_path/'cycles'
    run.prepare(z, output, engine='CYCLES')
    cfg = run.load(output/'run_config.json')
    assert cfg['protocol_id'] == 'clip_i_adsl_render_orbit8_cycles_v1'
    assert cfg['render_engine'] == 'CYCLES'
    records = run.load(output/'selected_inputs.json')
    cmd = run.render_command(records[0], output)
    assert '--cycles-gpu' in cmd[:cmd.index('--')]
    assert '--engine' not in cmd[cmd.index('--')+1:]


def test_software_eevee_retains_protocol_and_does_not_enable_cycles(tmp_path):
    z = make_archive(tmp_path)
    output = tmp_path/'software'
    run.prepare(z, output, software_opengl=True)
    cfg = run.load(output/'run_config.json')
    assert cfg['protocol_id'] == run.PROTOCOL
    assert cfg['render_engine'] == 'BLENDER_EEVEE'
    assert cfg['software_opengl'] is True
    cmd = run.render_command(run.load(output/'selected_inputs.json')[0],output)
    assert '--cycles-gpu' not in cmd
