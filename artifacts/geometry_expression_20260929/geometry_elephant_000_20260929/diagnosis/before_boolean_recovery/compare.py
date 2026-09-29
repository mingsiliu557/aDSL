"""Compose actual reference/A/B views and audit their conditioning inputs."""
from pathlib import Path
import base64,hashlib,json
from PIL import Image,ImageDraw,ImageFont
root=Path(__file__).parent
results={a:json.loads((root/a/'demo_result.json').read_text()) for a in ('A','B')}
ref=json.loads((root/'reference/provenance.json').read_text())
expected=ref['reference_image_sha256'];calls=[]
for arm,r in results.items():
    assert r['reference_images'][0]['sha256']==expected
    for role in ('planner','coder_initial','coder_execution_patch'):
        p=root/arm/role/'input.json'
        if not p.exists():continue
        payload=json.loads(p.read_text());images=[c for c in payload[0]['content'] if c['type']=='input_image']
        assert len(images)==1
        h=hashlib.sha256(base64.b64decode(images[0]['image_url'].split(',',1)[1])).hexdigest()
        assert h==expected
        calls.append(dict(arm=arm,role=role,reference_sha256=h))
assert results['A']['input_bundle_sha256']==results['B']['input_bundle_sha256']
assert results['A']['runner_sha256']==results['B']['runner_sha256']
assert results['A']['render_config']==results['B']['render_config']
(root/'comparison_data.json').write_text(json.dumps(dict(reference=ref,results=results,actual_reference_inputs=calls),indent=2))
try:font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',18)
except OSError:font=ImageFont.load_default()
for name,views in (('comparison',[2,1,3,7]),('all_views',list(range(1,9)))):
    rows_per=len(views)//4
    canvas=Image.new('RGB',(1600,3*rows_per*434),(245,245,245));draw=ImageDraw.Draw(canvas)
    for row,label in enumerate(('Reference','A original','B enhanced')):
        folder=root/'reference/views' if row==0 else root/('A' if row==1 else 'B')/'views'
        for j,view in enumerate(views):
            x=(j%4)*400;y=(row*rows_per+j//4)*434
            p=folder/f'render_{view:04d}.png'
            text=f'{label} - view {view}'
            if row==0:text+=' (input)' if view==2 else ' (not model input)'
            if p.exists():canvas.paste(Image.open(p).convert('RGB').resize((400,400)),(x,y+34))
            else:draw.text((x+8,y+120),results['A' if row==1 else 'B']['status'],fill='black',font=font)
            draw.text((x+8,y+8),text,fill='black',font=font)
    canvas.save(root/f'{name}.jpg',quality=93)
lines=['# elephant_000: one-image A/B display','',
    'Reference: public same-ID PLY from [Yang2001/toys4k_meshes](https://huggingface.co/datasets/Yang2001/toys4k_meshes), pinned in reference/provenance.json. Rendered with the existing CPU renderer. The user explicitly approved this source. The original .blend and original aDSL input/run were not available; this is a fresh comparison, not a claim to reproduce an identified paper sample.',
    '', 'Only reference/views/render_0002.png (copied to reference/input.png) was supplied to the models. Other reference views in the collage are display context only. Original target geometry/code was not provided. Actual Planner/Coder/patch image bytes were decoded and hash-checked identical for both arms.',
    '', 'A: pre-enhancement local master 8943a83. B: enhanced constructive API and prompts. Both use the same gpt-5.6-sol profile, one initial generation, at most one execution-error patch, 300 s execution and 300 s rendering. CPU Cycles, 512², 32 samples, 4 threads, review_eight, neutral/gray. No critics, checkers, FEA, URDF or FixedAssembly. This compares the API + prompt bundle, not either component in isolation.',
    '', '| Arm | Status | Source lines | Triangles | Execution patches | Export seconds | Render seconds | New API call sites |', '|---|---|---:|---:|---:|---:|---:|---|']
for a,r in results.items():
    lines.append(f"| {a} | {r['status']} | {r.get('source_lines')} | {r.get('triangles')} | {r['execution_patches']} | {sum(x.get('seconds',0) for x in r['execution_attempts']):.2f} | {r.get('render_seconds')} | {r.get('new_api_call_sites')} |")
    if r.get('error'):lines.extend(['',f"{a} error: `{r.get('exception')}`. See {a}/demo_result.json and execution logs."])
lines.extend(['','[Selected views: reference / A / B](comparison.jpg), [all eight views](all_views.jpg).',
    '', 'Folders A/ and B/ contain exact prompts, inputs, outputs, tool call messages, source snapshots, GLB, eight images, source_index, analysis_geometry, usage and session snapshots. comparison_data.json records matching input bundles, image hashes and statuses. Geometry API use is not by itself proof of better shape fidelity. No manufacturing/physical conclusions are drawn.'])
(root/'README.md').write_text('\n'.join(lines)+'\n')
print('COMPARISON_COMPLETE', {a:r['status'] for a,r in results.items()}, flush=True)
