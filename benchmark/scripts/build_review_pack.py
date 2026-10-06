"""Portable Markdown, JSONL/CSV and contactsheets. No raw assets or HTML in the review ZIP."""
import argparse
from collections import Counter
import csv
import io
from pathlib import Path
import zipfile
from PIL import Image, ImageDraw
from common import config, dump, load, rows, sha, write_rows

FIELDS=['case_id','source','source_id','category','input_image','reference_mesh','use_pose','tags','status','selection_note']

def shortlist(c, values):
    valid=[r for r in values if r.get('preflight_status')=='PASS' and r.get('geometry_status')=='PASS']
    groups={k:[r for r in valid if r['category']==k] for k in sorted({r['category'] for r in valid})}
    chosen=[]
    # Round robin for coverage, seeded collection order within categories; no score cherry-picking.
    while len(chosen)<c['shortlist_count'] and any(groups.values()):
        for g in groups.values():
            if g and len(chosen)<c['shortlist_count']: chosen.append(g.pop(0)['case_id'])
    return chosen


def sheet(root, values, source):
    values=[r for r in values if r['source']==source]; w=240;h=265;columns=5
    canvas=Image.new('RGB',(w*columns,h*((len(values)+columns-1)//columns)),'white');draw=ImageDraw.Draw(canvas)
    for i,r in enumerate(values):
        x=(i%columns)*w;y=(i//columns)*h
        path=root/r['input_image'] if r.get('input_image') else None
        if path and path.is_file():
            image=Image.open(path).convert('RGBA');image.thumbnail((225,225))
            canvas.paste(image,(x+(w-image.width)//2,y),image)
        else: draw.text((x+8,y+85),'MODEL UNAVAILABLE',fill='gray')
        draw.text((x+7,y+228),r['source_id'],fill='black');draw.text((x+7,y+245),r['category'],fill='black')
    path=root/'review'/f'{source}_contactsheet.jpg';canvas.save(path,quality=90);return path


def build(c):
    root=Path(c['root']);path=root/'manifests/candidates.jsonl';values=rows(path);review=root/'review';review.mkdir(exist_ok=True)
    shortlisted=shortlist(c,values);dump(root/'manifests/shortlist.json',shortlisted)
    recommended=[]
    for r in values:
        for field in ('input_image','reference_mesh','use_pose'): r.setdefault(field,None)
        if r.get('preflight_status')=='PASS' and r.get('geometry_status')!='PASS':
            r['selection_note']=r['selection_note'].split(' Geometry unavailable:')[0]+' Geometry unavailable: original reference is not a verified closed material volume; inspect basic.json/use_pose.json.'
        m=root/'measurements'/r['case_id']/'reference_measurement.json'
        result=load(m) if m.exists() else {}
        r['measurement_path']=str(m.relative_to(root)) if m.exists() else None
        if (result.get('standing') or {}).get('status')=='PASS' and (result.get('overhang') or {}).get('status')=='PASS' and r.get('geometry_status')=='PASS' and r.get('preflight_status')=='PASS':
            if len([x for x in recommended if x['source']==r['source']])<c['recommend_per_source']:
                r['status']='recommended_dev';r['selection_note']=r['selection_note'].split(' Reference whole-body')[0]+' Reference whole-body standing and orientation measurement passed; grouping tradeoff unverified.';recommended.append(r)
        elif r.get('geometry_status')=='PASS': r['status']='needs_review'
        if r['source']=='Toys4K': r['status']='needs_review'
    write_rows(path,values);write_rows(root/'manifests/recommended_dev20.jsonl',recommended)
    with (root/'manifests/candidates.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader()
        for r in values: writer.writerow({k:';'.join(r[k]) if isinstance(r.get(k),list) else r.get(k) for k in FIELDS})
    for s in ('ABO','Toys4K'):sheet(root,values,s)
    statistics=dict(candidates=dict(Counter(r['source'] for r in values)),downloaded=dict(Counter(r['source'] for r in values if r.get('asset_status')=='downloaded')),
        previews=dict(Counter(r['source'] for r in values if r.get('preflight_status')=='PASS')),
        reliable_geometry=dict(Counter(r['source'] for r in values if r.get('geometry_status')=='PASS')),
        measured=dict(Counter(r['source'] for r in values if (root/'measurements'/r['case_id']/'reference_measurement.json').exists())),
        recommended=dict(Counter(r['source'] for r in recommended)),categories=dict(Counter(r['category'] for r in values)),
        tags=dict(Counter(t for r in values for t in r['tags'])),statuses=dict(Counter(r['status'] for r in values)),
        vlm_calls=len(list((root/'measurements').glob('*/vlm/attempt.json'))),
        vlm_successes=sum(load(p)['status']=='PASS' for p in (root/'measurements').glob('*/vlm/attempt.json')),
        grouping_measurements=0,manual_confirmations=0,toys_limit='Authorized model archive unavailable; metadata only.')
    for key in ('candidates','downloaded','previews','reliable_geometry','measured','recommended'):
        for source in ('ABO','Toys4K'): statistics[key].setdefault(source,0)
    complete={name:0 for name in ('overhang','standing')}
    faults=Counter()
    for r in values:
        file=root/'measurements'/r['case_id']/'reference_measurement.json'
        if file.exists():
            measured=load(file)
            for name in complete: complete[name]+=int((measured.get(name) or {}).get('status') in ('PASS','FAIL'))
        file=root/'derived'/r['case_id']/'use_pose.json'
        if file.exists():
            operations=load(file).get('operations',[])
            for key in ('boundary_edges','nonmanifold_edges','zero_area_triangles','duplicate_faces','inconsistent_edges'):
                if any(o.get('after',{}).get(key) for o in operations): faults[key]+=1
    statistics.update(completed_reference_measurements=complete,geometry_defect_case_counts=dict(faults))
    dump(review/'summary.json',statistics)
    # Total stage times are serial durations, not wall-clock time.
    durations=[load(p).get('elapsed_seconds',0) for p in (root/'logs').rglob('process.json')]
    summary='# Selection v1 actual results\n\n'+'```json\n'+__import__('json').dumps(statistics,indent=2)+'\n```\n\n'+f'Recorded import/render process time: {sum(durations):.1f} s. Checker and VLM timings are in individual reports.\n\n'
    summary+='Toys4K has no authorized models, previews or recommendations. No missing measurement is interpreted as zero or a physical failure. No arbitrary hole filling or mesh deformation was used. All recommendations require user review. No Planner/Coder, generation comparison or FEA was run.\n\n'
    summary+='| Case | Category | Tags | Standing | G | h mm | Score | Note |\n|---|---|---|---|---|---|---|---|\n'
    for r in recommended:
        m=load(root/r['measurement_path']);o=m['overhang']['metrics']['partition_objective']
        summary+=f"| {r['source_id']} | {r['category']} | {', '.join(r['tags'])} | PASS | {o['gap_voxels']} | {o['voxel_pitch_mm']:.4g} | {o['score']:.4g} | {r['selection_note']} |\n"
    (review/'summary.md').write_text(summary)
    readme='# ABO / Toys4K selection review\n\nOpen the two contactsheets, then individual case images. Transparent PNGs contain no text. Recommendations are not a frozen test set.\n\n'
    for source in ('ABO','Toys4K'):readme+=f'![{source}]({source}_contactsheet.jpg)\n\n'
    for r in values:
        readme+=f"- **{r['case_id']}** ({r['category']}): {r['status']}. {r['selection_note']}"
        if r.get('input_image'):readme+=f" [input](../{r['input_image']}) · [native eight](../previews/{r['case_id']}/native/meta.json) · [neutral eight](../previews/{r['case_id']}/neutral/meta.json)"
        if r.get('measurement_path'):readme+=f" · [measurement](../{r['measurement_path']})"
        readme+='\n'
    readme+='\nSee summary.md, ../manifests/candidates.csv and recommended_dev20.jsonl for actual counts. `grouping_tradeoff` is only a visual hypothesis; no split/merge was measured. Standing uses one valid connected reference volume, free under gravity, with no pinned support or connector-retention claim. Dapper G is a vertical empty-voxel proxy, not slicer support cost.\n'
    (review/'README.md').write_text(readme)
    # Package all previews, lightweight measurements and provenance; omit NPZ/STL/GLB/raw models.
    paths=list(review.glob('*'))+list((root/'manifests').glob('*'))
    paths+=list((root/'previews').rglob('*.png'))+list((root/'previews').rglob('meta.json'))
    paths+=list((root/'derived').glob('*/basic.json'))+list((root/'derived').glob('*/use_pose.json'))
    paths+=list((root/'measurements').rglob('*.json'))+list((root/'measurements').rglob('*.txt'))
    paths+=list((root/'logs').rglob('process.json'))+list((root/'logs').glob('smoke.json'))+list((root/'logs').glob('run.json'))+list((root/'logs').glob('raw_geometry_inventory.json'))
    paths+=[p for p in (root/'raw/indices').glob('*README*')]
    paths+=list((root/'raw/ABO').glob('*/catalog.jpg'))+list((root/'raw/ABO').glob('*/provenance.json'))
    destination=root/'selection_v1_review.zip'
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(set(paths)):
            if p.is_file():z.write(p,str(p.relative_to(root)))
    # All declared input paths must resolve; missing assets stay null.
    for r in values:
        for key in ('input_image','reference_mesh','use_pose'):
            if r.get(key) and not (root/r[key]).is_file(): raise ValueError(f'missing {key}: {r["case_id"]}')
    print(statistics);print(destination)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--shortlist-only',action='store_true');a=p.parse_args();c=config(a.config)
    if a.shortlist_only:
        selected=shortlist(c,rows(Path(c['root'])/'manifests/candidates.jsonl'));dump(Path(c['root'])/'manifests/shortlist.json',selected);print(selected)
    else:build(c)
