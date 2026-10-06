"""Official indexed ABO assets; Toys4K metadata until an authorized archive is provided."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import csv
import gzip
from pathlib import Path
import random
import re
import xml.etree.ElementTree as ET
from common import config, download, dump, load, now, sha, write_rows

BASE = 'https://amazon-berkeley-objects.s3.amazonaws.com/'
NS = {'s': 'http://s3.amazonaws.com/doc/2006-03-01/'}

def category(r):
    text = ' '.join(str(r.get(k, '')) for k in ('item_name', 'product_type', 'node')).lower()
    if ('lamp' in text or 'lighting' in text) and not any(s in text for s in ('ceiling', 'pendant', 'wall lamp', 'sconce', 'chandelier')): return 'lamp'
    if any(s in text for s in ('chair', 'stool')) and not any(s in text for s in ('sofa', 'couch', 'cover', 'chair mat')): return 'chair_stool'
    if any(s in text for s in ('table', 'desk')) and not any(s in text for s in ('tablecloth', 'lamp', 'placemat', 'tableware')): return 'table'
    if any(s in text for s in ('cabinet', 'bookcase', 'bookshelf', 'shelving', 'shelf')): return 'cabinet_shelf'

def family(r):
    # Conservative exact manufacturer model prefix plus title stem, before color variants.
    models = r.get('model_number', [])
    model = ' '.join(x.get('value','') for x in models)
    names = [x['value'] for x in r.get('item_name',[]) if x.get('language_tag') == 'en_US']
    title = names[0] if names else r['item_id']
    stem = re.split(r',|\b(?:inches|inch|color)\b', title, maxsplit=1, flags=re.I)[0]
    stem = re.sub(r'\d+(?:\.\d+)?', '#', stem.lower())
    return model.split('-')[0] if model and len(model.split('-')[0]) > 4 else stem

def collect(c):
    root = Path(c['root']); meta = root/'raw'/'indices'
    meta.mkdir(parents=True, exist_ok=True)
    records = []
    for key in ('README.md','LICENSE-CC-BY-4.0.txt','3dmodels/README.md','listings/README.md','images/README.md','3dmodels/metadata/3dmodels.csv.gz'):
        records.append(download(BASE+key, meta/key.replace('/','_')))
    index = {r['3dmodel_id']: r for r in csv.DictReader(gzip.open(meta/'3dmodels_metadata_3dmodels.csv.gz', 'rt'))}
    listing = download(BASE+'?list-type=2&prefix=listings/metadata/', meta/'listings_index.xml')
    records.append(listing)
    keys = [x.text for x in ET.parse(listing['path']).findall('.//s:Key', NS)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        records += list(pool.map(lambda key: download(BASE+key, meta/Path(key).name), keys))
    excluded = {r['object_id'] for r in load(Path(c['project_root'])/'experiments/standing_fea_30/case_manifest.json')['cases'] if r.get('dataset','').lower() == 'abo'}
    pools = {k: [] for k in c['abo_quotas']}; seen = set(); duplicate = []
    for key in sorted(keys):
        with gzip.open(meta/Path(key).name, 'rt') as stream:
            for line in stream:
                r = __import__('json').loads(line)
                model = r.get('3dmodel_id'); group = category(r)
                if model not in index or model in excluded or group not in pools or model in seen: continue
                seen.add(model); pools[group].append(r)
    rng = random.Random(c['seed']); chosen = []; families = set()
    for group, quota in c['abo_quotas'].items():
        pool = sorted(pools[group], key=lambda r:r['3dmodel_id']); rng.shuffle(pool)
        for r in pool:
            if len([x for x in chosen if x['category'] == group]) >= quota: break
            f = family(r)
            if f in families: duplicate.append(r['3dmodel_id']); continue
            families.add(f); model=r['3dmodel_id']; i=index[model]
            chosen.append(dict(case_id='ABO_'+model, source='ABO', source_id=model, category=group,
                status='candidate', tags=[], selection_note='Seeded official-index candidate; awaiting geometry and review.',
                source_page=BASE+'index.html', model_url=BASE+'3dmodels/original/'+i['path'],
                metadata=r, model_index=i, family_key=f, asset_status='not_downloaded'))
    # Explicit redistribution, never invent a category or asset.
    for group in c['abo_quotas']:
        for r in sorted(pools[group],key=lambda r:r['3dmodel_id']):
            if len(chosen) >= sum(c['abo_quotas'].values()): break
            f=family(r); model=r['3dmodel_id']
            if f in families: continue
            families.add(f); i=index[model]
            chosen.append(dict(case_id='ABO_'+model,source='ABO',source_id=model,category=group,status='candidate',tags=[],selection_note='Quota redistribution; awaiting review.',metadata=r,model_index=i,family_key=f,model_url=BASE+'3dmodels/original/'+i['path'],asset_status='not_downloaded'))
    toyfile = Path(c['toys_metadata'])
    download('https://raw.githubusercontent.com/rehg-lab/lowshot-shapebias/main/toys4k/README.md',meta/'Toys4K_README.md')
    toys = list(csv.DictReader(toyfile.open()))
    groups = ['cat','cow','dinosaur','dog','dragon','giraffe','horse','lion','monkey','robot','bear','bunny','deer','tiger','kangaroo']
    pools_t = {g: sorted([r for r in toys if r['file_identifier'].split('/')[0] == g],key=lambda r:r['file_identifier']) for g in groups}
    for pool in pools_t.values(): rng.shuffle(pool)
    selected=[]
    while len(selected) < c['toys_count'] and any(pools_t.values()):
        for g in groups:
            if pools_t[g] and len(selected)<c['toys_count']: selected.append(pools_t[g].pop())
    for r in selected:
        ident=r['file_identifier']; name=Path(ident).parent.name
        chosen.append(dict(case_id='Toys4K_'+name,source='Toys4K',source_id=name,category=ident.split('/')[0],
            file_identifier=ident,expected_sha256=r['sha256'],caption=r.get('captions',''),tags=[],status='needs_review',
            asset_status='authorized_archive_missing',selection_note='Official metadata candidate; no authorized model archive available.',
            source_page='https://github.com/rehg-lab/lowshot-shapebias/tree/main/toys4k'))
    write_rows(root/'manifests/candidates.jsonl',chosen)
    dump(root/'manifests/collection.json',dict(created_at=now(),seed=c['seed'],code_sha=__import__('subprocess').check_output(['git','-C',c['project_root'],'rev-parse','HEAD'],text=True).strip(),
        official_indices=records,toys_metadata=dict(path=str(toyfile),sha256=sha(toyfile)),
        category_inventory={g:len(p) for g,p in pools.items()},excluded_debug_ids=sorted(excluded),deduplicated_ids=duplicate,
        actual_abo_quotas=dict(Counter(r['category'] for r in chosen if r['source']=='ABO')),toys_asset_limit='Authorized archive unavailable'))
    return chosen

def fetch(c, candidates):
    root=Path(c['root']); meta=root/'raw/indices'
    image_index_path=meta/'images.csv.gz'
    download(BASE+'images/metadata/images.csv.gz',image_index_path)
    wanted={r['metadata'].get('main_image_id') for r in candidates if r['source']=='ABO'}
    images={r['image_id']:r for r in csv.DictReader(gzip.open(image_index_path,'rt')) if r['image_id'] in wanted}
    for r in candidates:
        if r['source']!='ABO': continue
        folder=root/'raw/ABO'/r['source_id']
        try:
            existing=folder/'original.glb'
            if existing.exists() and r.get('raw_sha256') and sha(existing)!=r['raw_sha256']:
                raise ValueError('RAW_ASSET_CHECKSUM_CHANGED; original provenance retained')
            record=download(r['model_url'],existing)
            r.update(asset_status='downloaded',raw_mesh=str(Path(record['path']).relative_to(root)),raw_sha256=record['sha256'])
            image=images.get(r['metadata'].get('main_image_id'))
            if image:
                thumb=download(BASE+'images/small/'+image['path'],folder/'catalog.jpg')
                r['catalog_image']=str(Path(thumb['path']).relative_to(root))
            if not (folder/'provenance.json').exists():
                dump(folder/'provenance.json',dict(downloaded_at=now(),model=record,listing=r['metadata'],model_index=r['model_index'],attribution='Amazon.com; Guillaumin et al.',license='CC BY 4.0'))
        except Exception as error:
            r.update(asset_status='download_failed',status='needs_review',selection_note=f'{type(error).__name__}: {error}')
        write_rows(root/'manifests/candidates.jsonl',candidates)
        print(r['case_id'],r['asset_status'],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--download',action='store_true');p.add_argument('--limit',type=int)
    args=p.parse_args(); c=config(args.config)
    path=Path(c['root'])/'manifests/candidates.jsonl'
    candidates=__import__('common').rows(path) if path.exists() else collect(c)
    if args.download:
        subset=[r for r in candidates if r['source']=='ABO'][:args.limit]
        fetch(c,subset)
        byid={r['case_id']:r for r in subset}
        write_rows(path,[byid.get(r['case_id'],r) for r in candidates])
    print(dict(Counter(r['source'] for r in candidates)))
