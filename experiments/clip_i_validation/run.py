"""V7 CLIP-I evaluator for an existing paired output ZIP. Never executes sources."""
import argparse
import csv
import hashlib
import io
import json
import math
import os
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath
from statistics import mean

PROTOCOL = 'clip_i_adsl_render_orbit8_v1'
MODEL = 'openai/clip-vit-large-patch14'
CYCLES_PROTOCOL = 'clip_i_adsl_render_orbit8_cycles_v1'
RENDER_OPTIONS = [
    '--view-layout', 'orbit', '--num-camera-per-layer', '8', '--elevations', '15',
    '--width', '1024', '--height', '1024', '--render-samples', '256',
    '--background', 'transparent', '--material-mode', 'native', '--render-threads', '2',
]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4*1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def load(path):
    return json.loads(Path(path).read_text())


def csv_write(path, rows, fields):
    with Path(path).open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def prepare(zip_path, output, limit=3, engine='BLENDER_EEVEE', software_opengl=False):
    output = Path(output)
    if output.exists():
        raise ValueError('Use a new output directory')
    if limit < 1:
        raise ValueError('limit must be positive')
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            path = PurePosixPath(info.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in info.filename or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('Unsafe archive member')
        roots = {PurePosixPath(i.filename).parts[0] for i in z.infolist()}
        if len(roots) != 1:
            raise ValueError('Archive must have one root')
        root = next(iter(roots))
        sums = dict((p, h) for h, p in (line.split('  ', 1) for line in z.read(root+'/SHA256SUMS').decode().splitlines()))
        def checked(rel):
            b = z.read(root+'/'+rel)
            if hashlib.sha256(b).hexdigest() != sums[rel]:
                raise ValueError('Archive hash mismatch: '+rel)
            return b
        manifest = json.loads(checked('manifest.json'))
        case_rows = list(csv.DictReader(io.StringIO(checked('cases.csv').decode())))[:limit]
        output.mkdir(parents=True)
        records = []
        for case in case_rows:
            cid = case['case_id']
            for method in ['adsl', 'ours']:
                matches = [r for r in manifest['records'] if r['case_id'] == cid and r['method'] == method]
                if len(matches) != 1:
                    raise ValueError('Expected exactly one final asset per case/method')
                old = matches[0]
                ref, glb = old['reference_image'], old['scene_glb']
                if ref != case['reference_image'] or old['reference_sha256'] != case['reference_sha256']:
                    raise ValueError('Reference association mismatch')
                for rel, expected in [(ref, old['reference_sha256']), (glb, old['scene_glb_sha256'])]:
                    path = PurePosixPath(rel)
                    if path.is_absolute() or '..' in path.parts:
                        raise ValueError('Unsafe manifest path')
                    b = checked(rel)
                    if hashlib.sha256(b).hexdigest() != expected:
                        raise ValueError('Manifest hash mismatch')
                    dst = output/'inputs'/rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if not dst.exists():
                        dst.write_bytes(b)
                records.append({
                    'case_id': cid, 'method': method,
                    'reference_image': 'inputs/'+ref, 'reference_sha256': old['reference_sha256'],
                    'scene_glb': 'inputs/'+glb, 'scene_glb_sha256': old['scene_glb_sha256'],
                    'source_version': old['source_version'], 'source_sha256': old['source_sha256'],
                    'physical_asset_sha256': old['physical_asset_sha256'],
                    'renders': [f'renders/{cid}/{method}/render_{i:04d}.png' for i in range(1, 9)],
                })
    write(output/'selected_inputs.json', records)
    write(output/'run_config.json', {
        'protocol_id': CYCLES_PROTOCOL if engine == 'CYCLES' else PROTOCOL, 'render_engine': engine, 'software_opengl': software_opengl, 'zip_name': Path(zip_path).name, 'zip_sha256': sha(zip_path),
        'case_ids': [c['case_id'] for c in case_rows], 'limit': limit,
        'model_id': MODEL, 'model_revision': None, 'checkpoint_files_sha256': None,
        'dtype': 'float32', 'feature': 'get_image_features', 'view_reduction': 'mean_of_eight',
        'render_cli_options': RENDER_OPTIONS,
        'renderer_internal_settings': {'radius': 1.5, 'azimuth_offset': -90, 'normalize_longest_edge': 0.8},
        'renderer_source_sha256': sha(Path(__file__).resolve().parents[2]/'adsl-core/tools/render.py'),
        'actual_render_settings': {}, 'review_status': 'PENDING', 'timings_seconds': {},
        'code_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    })


def render_command(record, output):
    folder = output/Path(record['renders'][0]).parent
    cfg_path = output/'run_config.json'
    extra = ['--cycles-gpu'] if cfg_path.exists() and load(cfg_path).get('render_engine') == 'CYCLES' else []
    return [sys.executable, '-m', 'experiments.clip_i_validation.render_worker',
            '--settings-output', str(folder/'effective_settings.json'), *extra, '--',
            '--glb-path', str(output/record['scene_glb']), '--output-dir', str(folder), *RENDER_OPTIONS]


def sanitized(text, output):
    for path, label in [(str(output.resolve()), '<RUN>'),
                        (str(Path(__file__).resolve().parents[2]), '<REPO>'),
                        (str(Path(sys.executable).resolve().parents[1]), '<ENV>'),
                        (str(Path.home()), '<HOME>')]:
        text = text.replace(path, label)
    return text


def render(output, first_only=False):
    if not os.environ.get('SLURM_JOB_ID'):
        raise RuntimeError('Rendering requires an allocated Slurm job')
    output = Path(output).resolve()
    cfg = load(output/'run_config.json')
    records = load(output/'selected_inputs.json')
    if not first_only and cfg['review_status'] != 'PASS':
        raise RuntimeError('First-case visual review must pass before continuing')
    start = time.monotonic()
    env = {k: v for k, v in os.environ.items() if not k.startswith('ADSL_RENDER_')}
    env.update(ADSL_RENDER_ENGINE=cfg.get('render_engine', 'BLENDER_EEVEE'), PYTHONNOUSERSITE='1', OMP_NUM_THREADS='2')
    if cfg.get('software_opengl'):
        env.update(LIBGL_ALWAYS_SOFTWARE='1', GALLIUM_DRIVER='llvmpipe', MESA_LOADER_DRIVER_OVERRIDE='llvmpipe')
        cfg['software_graphics_environment'] = {k: env[k] for k in ['LIBGL_ALWAYS_SOFTWARE','GALLIUM_DRIVER','MESA_LOADER_DRIVER_OVERRIDE']}
        cfg['egl_vendor_config_name'] = Path(env.get('__EGL_VENDOR_LIBRARY_FILENAMES', '')).name
    for record in records:
        if first_only and record['case_id'] != cfg['case_ids'][0]:
            continue
        folder = output/Path(record['renders'][0]).parent
        settings_path = folder/'effective_settings.json'
        if settings_path.exists() and all((output/p).is_file() for p in record['renders']):
            continue
        if folder.exists() and any(folder.iterdir()):
            raise RuntimeError('Incomplete existing render; preserve it and use a new run')
        folder.mkdir(parents=True, exist_ok=True)
        if sha(output/record['scene_glb']) != record['scene_glb_sha256']:
            raise ValueError('Final GLB changed after input freeze')
        t0 = time.monotonic()
        result = subprocess.run(render_command(record, output), env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (folder/'renderer.log').write_text(sanitized(result.stdout, output))
        if result.returncode or not settings_path.exists() or not all((output/p).is_file() for p in record['renders']):
            raise RuntimeError(cfg.get('render_engine', 'BLENDER_EEVEE')+'_RENDER_FAILED: '+record['case_id']+'/'+record['method'])
        effective = load(settings_path)
        settings = effective['settings']
        if settings['engine'] != cfg.get('render_engine', 'BLENDER_EEVEE') or (settings['width'], settings['height'], settings['render_samples']) != (1024, 1024, 256):
            raise RuntimeError('Effective render settings differ from protocol')
        if cfg.get('render_engine') == 'CYCLES' and not effective['allocated_gpu_process_confirmed']:
            raise RuntimeError('Allocated NVIDIA graphics backend not confirmed')
        previous = list(cfg['actual_render_settings'].values())
        if previous and settings != previous[0]['settings']:
            raise RuntimeError('Methods must use identical effective render settings')
        key = record['case_id']+'/'+record['method']
        cfg['actual_render_settings'][key] = effective
        cfg['timings_seconds'][key] = time.monotonic()-t0
        write(output/'run_config.json', cfg)
    cfg['slurm_job_id'] = os.environ['SLURM_JOB_ID']
    cfg['timings_seconds']['render_stage_last_invocation'] = time.monotonic()-start
    write(output/'run_config.json', cfg)


def white_rgb(image):
    from PIL import Image
    rgba = image.convert('RGBA')
    bg = Image.new('RGBA', rgba.size, (255, 255, 255, 255))
    bg.alpha_composite(rgba)
    return bg.convert('RGB')


def preview(output):
    from PIL import Image, ImageDraw
    output = Path(output)
    cfg = load(output/'run_config.json')
    records = load(output/'selected_inputs.json')
    dst = output/'previews'
    dst.mkdir(exist_ok=True)
    for r in records:
        if r['case_id'] != cfg['case_ids'][0]:
            continue
        canvas = Image.new('RGB', (1152, 1152), 'white')
        for i, p in enumerate([r['reference_image'], *r['renders']]):
            with Image.open(output/p) as im:
                im = white_rgb(im)
                im.thumbnail((376, 350))
                x, y = i % 3*384, i//3*384
                canvas.paste(im, (x+(384-im.width)//2, y+26+(350-im.height)//2))
                ImageDraw.Draw(canvas).text((x+8, y+7), 'reference' if not i else f'view {i}', fill='black')
        canvas.save(dst/(r['method']+'.png'))


def normalize(values):
    values = [float(x) for x in values]
    if not values or not all(math.isfinite(x) for x in values):
        raise ValueError('NONFINITE_FEATURE')
    length = math.sqrt(sum(x*x for x in values))
    if not math.isfinite(length) or length == 0:
        raise ValueError('ZERO_OR_INVALID_FEATURE_NORM')
    return [x/length for x in values]


def cosine_score(a, b):
    a, b = normalize(a), normalize(b)
    if len(a) != len(b):
        raise ValueError('FEATURE_DIMENSION_MISMATCH')
    return 100*sum(x*y for x, y in zip(a, b))


def evaluate(records, output, encode, protocol_id=PROTOCOL):
    views, cases, references, self_scores = [], [], {}, []
    for r in records:
        base = {k: r[k] for k in ['case_id', 'method']}
        try:
            key = r['reference_image']
            if key not in references:
                references[key] = normalize(encode(output/key))
                self_scores.append(cosine_score(references[key], references[key]))
            reference = references[key]
            scores = []
            for i, rel in enumerate(r['renders'], 1):
                p = output/rel
                if not p.is_file():
                    raise ValueError('MISSING_RENDER')
                score = cosine_score(reference, encode(p))
                views.append({**base, 'view_id': i, 'status': 'PASS', 'score': score,
                              'image_sha256': sha(p), 'image_path': rel, 'reason': ''})
                scores.append(score)
            if len(scores) != 8:
                raise ValueError('EXPECTED_EIGHT_VIEWS')
            cases.append({**base, 'status': 'PASS', 'case_score': mean(scores), 'reason': ''})
        except (ValueError, OSError) as e:
            reason = str(e) if isinstance(e, ValueError) else type(e).__name__
            done = len([v for v in views if v['case_id'] == r['case_id'] and v['method'] == r['method']])
            for i in range(done+1, 9):
                views.append({**base, 'view_id': i, 'status': 'UNAVAILABLE', 'score': None,
                              'image_sha256': None, 'image_path': r['renders'][i-1] if i <= len(r['renders']) else '', 'reason': reason})
            cases.append({**base, 'status': 'UNAVAILABLE', 'case_score': None, 'reason': reason})
    ordered = list(dict.fromkeys(r['case_id'] for r in records))
    comparisons = []
    for cid in ordered:
        pair = {r['method']: r for r in cases if r['case_id'] == cid}
        complete = all(pair[m]['status'] == 'PASS' for m in ['adsl', 'ours'])
        comparisons.append({'case_id': cid, 'status': 'PASS' if complete else 'UNAVAILABLE',
                            'adsl': pair['adsl']['case_score'], 'ours': pair['ours']['case_score'],
                            'delta': pair['ours']['case_score']-pair['adsl']['case_score'] if complete else None})
    common = [r for r in comparisons if r['status'] == 'PASS']
    summary = {'protocol_id': protocol_id, 'n': len(common), 'case_ids': [r['case_id'] for r in common],
               'planned_case_ids': ordered, 'method_means': {m: mean(r[m] for r in common) if common else None for m in ['adsl', 'ours']},
               'mean_delta_ours_minus_adsl': mean(r['delta'] for r in common) if common else None,
               'reference_self_scores': self_scores,
               'self_score_check': bool(self_scores) and all(abs(s-100) < 1e-4 for s in self_scores),
               'unavailable_case_ids': [r['case_id'] for r in comparisons if r['status'] != 'PASS']}
    return views, cases, comparisons, summary


def score(output, model_path):
    output, model_path = Path(output), Path(model_path)
    if not os.environ.get('SLURM_JOB_ID'):
        raise RuntimeError('CLIP inference requires an allocated Slurm job')
    cfg = load(output/'run_config.json')
    if cfg['review_status'] != 'PASS' or len(cfg['actual_render_settings']) != 2*len(cfg['case_ids']):
        raise RuntimeError('Visual review and every fresh render must be complete')
    import importlib.metadata
    import torch
    from PIL import Image
    from transformers import CLIPModel, CLIPProcessor
    start = time.monotonic()
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    processor = CLIPProcessor.from_pretrained(model_path, local_files_only=True)
    model = CLIPModel.from_pretrained(model_path, local_files_only=True, torch_dtype=torch.float32,
                                      use_safetensors=True, low_cpu_mem_usage=True).eval().to('cuda')
    def encode(path):
        with Image.open(path) as im:
            batch = processor(images=white_rgb(im), return_tensors='pt')
        batch = {k: v.to('cuda', dtype=torch.float32) for k, v in batch.items()}
        with torch.inference_mode():
            try:
                return model.get_image_features(**batch)[0].float().cpu().tolist()
            except RuntimeError as error:
                raise ValueError('IMAGE_ENCODING_RUNTIME_ERROR') from error
    results = evaluate(load(output/'selected_inputs.json'), output, encode, cfg['protocol_id'])
    views, cases, comparisons, summary = results
    csv_write(output/'clip_view_scores.csv', views, ['case_id','method','view_id','status','score','image_sha256','image_path','reason'])
    csv_write(output/'clip_case_scores.csv', cases, ['case_id','method','status','case_score','reason'])
    csv_write(output/'clip_comparison.csv', comparisons, ['case_id','status','adsl','ours','delta'])
    write(output/'clip_summary.json', summary)
    cfg['model_revision'] = (model_path/'revision.txt').read_text().strip()
    cfg['checkpoint_files_sha256'] = {p.name: sha(p) for p in model_path.iterdir() if p.is_file()}
    cfg['dependency_versions'] = {p: importlib.metadata.version(p) for p in ['torch','transformers','Pillow','accelerate']}
    cfg['device'] = torch.cuda.get_device_name(0)
    cfg['timings_seconds']['clip_inference_and_load'] = time.monotonic()-start
    write(output/'run_config.json', cfg)
    report = ['# Independent CLIP-I validation', '',
              'Protocol: `'+cfg['protocol_id']+'`. Native GLB materials; fresh '+cfg['render_engine']+' orbit views.', '',
              '| Method | CLIP-I | Paired n |', '|---|---:|---:|']
    for m in ['adsl','ours']:
        val = summary['method_means'][m]
        report.append(f'| {m} | {val:.6f} | {summary["n"]} |' if val is not None else f'| {m} | unavailable | 0 |')
    report += ['', 'Mean delta (ours - adsl): '+str(summary['mean_delta_ours_minus_adsl']),
               '', 'This is an independent validation, not a reproduction of official CLIP results.',
               'Render engine: '+cfg['render_engine']+'. '+('Cycles GPU is a separately authorized fallback; it is not EEVEE-aligned evidence.' if cfg['render_engine']=='CYCLES' else 'EEVEE software OpenGL was authorized; rendering acceleration is not an acceptance condition.' if cfg.get('software_opengl') else 'Rendering acceleration was not asserted.'),
               'Native materials differ from the paper\'s stated neutral-material setting; that discrepancy remains unverified.',
               'The fixed orbit may not reproduce the reference camera. No score-based case selection or physical claims.',
               'Visual review: '+cfg.get('review_note',''), '',
               'Actual timings in seconds: '+json.dumps(cfg['timings_seconds'],sort_keys=True),
               '', 'Paper: https://arxiv.org/html/2608.17975#A1',
               'Renderer: https://github.com/sig-pku/aDSL/blob/0c10f36a459bf1033e89e2e5dfcf451bc30043b0/adsl-core/tools/render.py']
    (output/'report.md').write_text('\n'.join(report)+'\n')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase', choices=['prepare','render-first','preview','review','render','score'], required=True)
    p.add_argument('--zip', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--limit', type=int, default=3)
    p.add_argument('--engine', choices=['eevee', 'cycles'], default='eevee')
    p.add_argument('--software-opengl', action='store_true')
    p.add_argument('--model-path', type=Path)
    p.add_argument('--review-note')
    a = p.parse_args(argv)
    if a.phase == 'prepare':
        if not a.zip: p.error('--zip required')
        prepare(a.zip,a.output,a.limit,'CYCLES' if a.engine=='cycles' else 'BLENDER_EEVEE',a.software_opengl)
    elif a.phase in ['render-first','render']:
        render(a.output,a.phase=='render-first')
    elif a.phase == 'preview': preview(a.output)
    elif a.phase == 'review':
        if not a.review_note: p.error('--review-note required')
        cfg=load(a.output/'run_config.json');cfg.update(review_status='PASS',review_note=a.review_note)
        write(a.output/'run_config.json',cfg)
    else:
        if not a.model_path: p.error('--model-path required')
        score(a.output,a.model_path)


if __name__ == '__main__':
    main()
