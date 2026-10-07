"""Seeded ABO candidates and checksum-verified Toys4K mirror assets."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import csv
import gzip
import json
from pathlib import Path, PurePosixPath
import random
import re
import shutil
import stat
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from common import config, download, dump, load, now, rows, sha, write_rows

BASE = 'https://amazon-berkeley-objects.s3.amazonaws.com/'
NS = {'s': 'http://s3.amazonaws.com/doc/2006-03-01/'}
TYPE_GROUPS = {
    'LAMP': 'lamp', 'CHAIR': 'chair_stool', 'STOOL': 'chair_stool',
    'TABLE': 'table', 'DESK': 'table', 'CABINET': 'cabinet_shelf',
    'SHELF': 'cabinet_shelf', 'BOOKCASE': 'cabinet_shelf',
    'BOOKSHELF': 'cabinet_shelf', 'SHELVING': 'cabinet_shelf',
}
GENERIC_TYPES = {'HOME', 'HOME_FURNITURE_AND_DECOR', 'FURNITURE', 'HOME_FURNITURE', 'HOME_DECOR'}
TOYS_MIRROR_NOTE = 'Non-official Hugging Face mirror of Toys4K; original dataset attribution and terms still apply.'


def values(field):
    if isinstance(field, str): return [field]
    if isinstance(field, dict): return [str(field.get('value', ''))]
    return [str(x.get('value', '')) if isinstance(x, dict) else str(x) for x in (field or [])]


def product_types(record):
    return [v.upper().strip() for v in values(record.get('product_type')) if v.strip()]


def classification_text(record):
    names = record.get('item_name', []) or []
    if isinstance(names, str): names = [names]
    elif isinstance(names, dict): names = [names]
    titles = [str(x.get('value', '')) if isinstance(x, dict) else str(x) for x in names
              if not isinstance(x, dict) or not x.get('language_tag') or x['language_tag'].lower().startswith('en_')]
    nodes = record.get('node', []) or []
    if isinstance(nodes, (str, dict)): nodes = [nodes]
    leaves = [str(x.get('node_name', '')) if isinstance(x, dict) else str(x) for x in nodes]
    leaves += values(record.get('node_name'))
    return ' '.join(titles + [p.rstrip('/').split('/')[-1] for p in leaves]).lower()


def text_category(text):
    if re.search(r'\b(lamps?|lighting)\b', text) and not excluded_lamp(text): return 'lamp'
    if re.search(r'\b(chairs?|stools?)\b', text) and not re.search(r'\b(sofa|couch|cover)\b|chair mat', text): return 'chair_stool'
    if re.search(r'\b(tables?|desks?)\b', text) and not re.search(r'\b(tablecloth|lamps?|placemat|tableware)\b', text): return 'table'
    if re.search(r'\b(cabinets?|bookcases?|bookshelves|bookshelf|shelving|shelves|shelf)\b', text): return 'cabinet_shelf'


def excluded_lamp(text):
    return bool(re.search(r'\b(ceiling|pendant|sconce|chandelier)\b|wall[ -](?:lamp|light|mount)', text))


def category(record):
    text = classification_text(record)
    specific = [t for t in product_types(record) if t not in GENERIC_TYPES]
    if specific:
        groups = {TYPE_GROUPS.get(t) for t in specific}
        group = next(iter(groups)) if len(groups) == 1 else None
        return None if group == 'lamp' and excluded_lamp(text) else group
    return text_category(text)


def classification_review(record):
    types = product_types(record); text = classification_text(record)
    reasons = []
    scope = 'in_scope' if category(record) else 'out_of_scope'
    if 'HOME_MIRROR' in types and text_category(text) == 'cabinet_shelf':
        reasons.append('HOME_MIRROR product type conflicts with cabinet/shelf title; verify manually.')
        scope = 'unresolved'
    if re.search(r'\bkitchen\b.*\b(cart|trolley)\b|\b(cart|trolley)\b.*\bkitchen\b', text):
        reasons.append('Kitchen cart may be outside cabinet/shelf scope; verify manually.')
    if scope == 'out_of_scope':
        reasons.append('Specific product type or metadata is outside the four ABO target categories.')
    return dict(product_types=types, scope=scope, needs_review=bool(reasons), reasons=reasons)


def reclassify(candidates):
    """Only update classification fields; raw provenance and human records survive."""
    for row in candidates:
        if row['source'] != 'ABO': continue
        review = classification_review(row['metadata'])
        row['classification_review'] = review
        if review['scope'] != 'unresolved': row['category'] = category(row['metadata'])
        if row.get('status') not in ('excluded', 'user_confirmed', 'user_confirmed_dev') and row.get('user_confirmed') is not True:
            if review['scope'] == 'out_of_scope' and not row.get('manual_review'):
                row['status'] = 'excluded'
            elif review['needs_review']:
                row['status'] = 'needs_review'
    return candidates


def family(r):
    # Preserve the original family definition and keys, including existing deduplication.
    models = r.get('model_number', [])
    model = ' '.join(x.get('value', '') for x in models)
    names = [x['value'] for x in r.get('item_name', []) if x.get('language_tag') == 'en_US']
    title = names[0] if names else r['item_id']
    stem = re.split(r',|\b(?:inches|inch|color)\b', title, maxsplit=1, flags=re.I)[0]
    stem = re.sub(r'\d+(?:\.\d+)?', '#', stem.lower())
    return model.split('-')[0] if model and len(model.split('-')[0]) > 4 else stem


def abo_inventory(c):
    root = Path(c['root']); meta = root/'raw'/'indices'
    meta.mkdir(parents=True, exist_ok=True); records = []
    for key in ('README.md', 'LICENSE-CC-BY-4.0.txt', '3dmodels/README.md', 'listings/README.md', 'images/README.md', '3dmodels/metadata/3dmodels.csv.gz'):
        records.append(download(BASE+key, meta/key.replace('/', '_')))
    with gzip.open(meta/'3dmodels_metadata_3dmodels.csv.gz', 'rt') as stream:
        index = {r['3dmodel_id']: r for r in csv.DictReader(stream)}
    listing = download(BASE+'?list-type=2&prefix=listings/metadata/', meta/'listings_index.xml')
    records.append(listing)
    keys = [x.text for x in ET.parse(listing['path']).findall('.//s:Key', NS)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        records += list(pool.map(lambda key: download(BASE+key, meta/Path(key).name), keys))
    excluded = {r['object_id'] for r in load(Path(c['project_root'])/'experiments/standing_fea_30/case_manifest.json')['cases'] if r.get('dataset', '').lower() == 'abo'}
    pools = {k: [] for k in c['abo_quotas']}; seen = set()
    for key in sorted(keys):
        with gzip.open(meta/Path(key).name, 'rt') as stream:
            for line in stream:
                r = json.loads(line); model = r.get('3dmodel_id'); group = category(r)
                if model not in index or model in excluded or group not in pools or model in seen: continue
                seen.add(model); pools[group].append(r)
    return index, pools, excluded, records


def abo_row(record, index, note):
    model = record['3dmodel_id']; entry = index[model]; review = classification_review(record)
    return dict(case_id='ABO_'+model, source='ABO', source_id=model, category=category(record),
                status='needs_review' if review['needs_review'] else 'candidate', tags=[], selection_note=note,
                source_page=BASE+'index.html', model_url=BASE+'3dmodels/original/'+entry['path'],
                metadata=record, model_index=entry, family_key=family(record),
                classification_review=review, asset_status='not_downloaded')


def append_candidates(c, candidates, count):
    if not 0 <= count <= 10: raise ValueError('--append must be between 0 and 10')
    count = min(count, max(0, 60-len(candidates)))
    if not count: return candidates
    index, pools, excluded, records = abo_inventory(c)
    rng = random.Random(c['seed'])
    for pool in pools.values():
        pool.sort(key=lambda r: r['3dmodel_id']); rng.shuffle(pool)
    seen = {r['source_id'] for r in candidates if r['source'] == 'ABO'}
    families = {r.get('family_key') for r in candidates if r.get('family_key')}
    added = []
    def take(group):
        while pools.get(group):
            record = pools[group].pop(0); model = record['3dmodel_id']; key = family(record)
            if model in seen or model in excluded or key in families: continue
            seen.add(model); families.add(key)
            row = abo_row(record, index, 'Seeded incremental candidate; awaiting geometry and review.')
            added.append(row); return True
        return False
    # Corrected genuine lamp count, then deterministic category rotation.
    lamps = sum(r['source'] == 'ABO' and category(r.get('metadata', {})) == 'lamp' for r in candidates)
    for _ in range(min(count, max(0, c['abo_quotas'].get('lamp', 0)-lamps))):
        if not take('lamp'): break
    while len(added) < count:
        progress = False
        for group in c['abo_quotas']:
            if len(added) == count: break
            progress = take(group) or progress
        if not progress: break
    candidates.extend(added)
    write_rows(Path(c['root'])/'manifests/candidates.jsonl', candidates)
    dump(Path(c['root'])/'manifests/append.json', dict(created_at=now(), seed=c['seed'],
         added_ids=[r['case_id'] for r in added], total_count=len(candidates),
         category_inventory={g: len(pool) for g, pool in pools.items()}, excluded_debug_ids=sorted(excluded)))
    return candidates


def collect(c):
    index, pools, excluded, records = abo_inventory(c)
    root = Path(c['root']); meta = root/'raw/indices'
    rng = random.Random(c['seed']); chosen = []; families = set(); duplicate = []
    for group, quota in c['abo_quotas'].items():
        pool = sorted(pools[group], key=lambda r: r['3dmodel_id']); rng.shuffle(pool)
        for record in pool:
            if sum(x['category'] == group for x in chosen) >= quota: break
            key = family(record)
            if key in families: duplicate.append(record['3dmodel_id']); continue
            families.add(key)
            chosen.append(abo_row(record, index, 'Seeded official-index candidate; awaiting geometry and review.'))
    for group in c['abo_quotas']:
        for record in sorted(pools[group], key=lambda r: r['3dmodel_id']):
            if len(chosen) >= sum(c['abo_quotas'].values()): break
            key = family(record)
            if key in families: continue
            families.add(key); chosen.append(abo_row(record, index, 'Quota redistribution; awaiting review.'))
    toyfile = Path(c['toys_metadata'])
    download('https://raw.githubusercontent.com/rehg-lab/lowshot-shapebias/main/toys4k/README.md', meta/'Toys4K_README.md')
    with toyfile.open() as stream: toys = list(csv.DictReader(stream))
    groups = ['cat', 'cow', 'dinosaur', 'dog', 'dragon', 'giraffe', 'horse', 'lion', 'monkey', 'robot', 'bear', 'bunny', 'deer', 'tiger', 'kangaroo']
    pools_t = {g: sorted([r for r in toys if r['file_identifier'].split('/')[0] == g], key=lambda r: r['file_identifier']) for g in groups}
    for pool in pools_t.values(): rng.shuffle(pool)
    selected = []
    while len(selected) < c['toys_count'] and any(pools_t.values()):
        for group in groups:
            if pools_t[group] and len(selected) < c['toys_count']: selected.append(pools_t[group].pop())
    for record in selected:
        ident = record['file_identifier']; name = Path(ident).parent.name
        chosen.append(dict(case_id='Toys4K_'+name, source='Toys4K', source_id=name, category=ident.split('/')[0],
            file_identifier=ident, expected_sha256=record['sha256'], caption=record.get('captions', ''), tags=[], status='needs_review',
            asset_status='not_downloaded', selection_note='Local cached CSV candidate; mirror asset requires SHA256 verification and review.',
            source_page='https://github.com/rehg-lab/lowshot-shapebias/tree/main/toys4k'))
    write_rows(root/'manifests/candidates.jsonl', chosen)
    dump(root/'manifests/collection.json', dict(created_at=now(), seed=c['seed'],
        code_sha=subprocess.check_output(['git', '-C', c['project_root'], 'rev-parse', 'HEAD'], text=True).strip(),
        official_indices=records, toys_metadata=dict(path=str(toyfile), sha256=sha(toyfile), source_kind='local_cached_csv'),
        category_inventory={g: len(p) for g, p in pools.items()}, excluded_debug_ids=sorted(excluded), deduplicated_ids=duplicate,
        actual_abo_quotas=dict(Counter(r['category'] for r in chosen if r['source'] == 'ABO')), toys_asset_source=c.get('toys_archive')))
    return chosen


def archive_spec(c):
    spec = c.get('toys_archive')
    if not isinstance(spec, dict): raise ValueError('Toys4K mirror archive is not configured')
    spec = dict(spec)
    if not spec.get('url'):
        spec['url'] = f"https://huggingface.co/datasets/{spec['repo_id']}/resolve/{spec['revision']}/{spec['filename']}?download=true"
    spec['path'] = str(Path(spec.get('path', Path(c['root'])/'raw/archives'/spec['filename'])))
    return spec


def verify_toys_archive(c, path=None):
    spec = archive_spec(c); path = Path(path or spec['path'])
    if path.stat().st_size != spec['size_bytes']: raise ValueError('TOYS_ARCHIVE_SIZE_MISMATCH')
    digest = sha(path)
    if digest != spec['sha256']: raise ValueError('TOYS_ARCHIVE_SHA256_MISMATCH')
    return dict(path=str(path), sha256=digest, size_bytes=path.stat().st_size,
                repo_id=spec.get('repo_id'), revision=spec.get('revision'), filename=spec['filename'],
                url=spec['url'], source_kind='non_official_mirror', attribution_note=TOYS_MIRROR_NOTE, verified_at=now())


def download_toys_archive(c):
    """Resume into .part, and publish an archive only after full size/hash checks."""
    spec = archive_spec(c); path = Path(spec['path']); path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        part = path.with_name(path.name+'.part')
        allocated = part.stat().st_blocks*512 if part.exists() else 0
        if shutil.disk_usage(path.parent).free < max(0, spec['size_bytes']-allocated) + (10 << 30):
            raise OSError('TOYS_ARCHIVE_INSUFFICIENT_SPACE: archive remainder plus 10 GiB reserve required')
        log = Path(c['root'])/'logs/toys_archive'; log.mkdir(parents=True, exist_ok=True)
        command = ['aria2c', '--continue=true', '--max-connection-per-server=8', '--split=8',
                   '--file-allocation=none', '--auto-file-renaming=false', '--allow-overwrite=true',
                   '--summary-interval=30', '--show-console-readout=false', '--console-log-level=warn', '--log-level=notice',
                   '--log='+str(log/'aria2.log'), '--dir='+str(path.parent), '--out='+part.name, spec['url']]
        dump(log/'command.json', dict(created_at=now(), command=command, mirror=spec, attribution_note=TOYS_MIRROR_NOTE))
        subprocess.run(command, check=True)
        record = verify_toys_archive(c, part)
        part.replace(path); record['path'] = str(path)
    else:
        record = verify_toys_archive(c)
    dump(path.with_suffix(path.suffix+'.provenance.json'), record)
    return record


def safe_zip_path(name):
    path = PurePosixPath(name)
    if not name or not path.parts or '\\' in name or path.is_absolute() or '..' in path.parts or ':' in path.parts[0]:
        raise ValueError('TOYS_ARCHIVE_UNSAFE_PATH: '+name)
    return path


def extract_toy(c, row, archive, archive_record):
    """Extract only this instance plus individually configured resource files."""
    ident = safe_zip_path(row['file_identifier'])
    if ident.suffix.lower() != '.blend' or ident.parent.name != row['source_id']:
        raise ValueError('TOYS_IDENTIFIER_INVALID')
    csv_path = Path(c['toys_metadata'])
    with csv_path.open() as stream:
        metadata_matches = [r for r in csv.DictReader(stream) if r['file_identifier'] == str(ident)]
    if len(metadata_matches) != 1 or metadata_matches[0]['sha256'] != row['expected_sha256']:
        raise ValueError('TOYS_METADATA_CHECKSUM_MISMATCH')
    csv_record = dict(path=str(csv_path), sha256=sha(csv_path), source_kind='local_cached_csv')
    infos = archive.infolist(); names = [info.filename for info in infos]
    if len(names) != len(set(names)): raise ValueError('TOYS_ARCHIVE_DUPLICATE_PATH')
    paths = {info.filename: safe_zip_path(info.filename) for info in infos}
    matches = [info for info in infos if not info.is_dir() and (paths[info.filename] == ident or str(paths[info.filename]).endswith('/'+str(ident)))]
    if len(matches) != 1: raise ValueError('TOYS_MEMBER_MISSING_OR_AMBIGUOUS: '+str(ident))
    model = matches[0]; prefix = paths[model.filename].parent
    resources = {safe_zip_path(p) for p in c['toys_archive'].get('resources', [])}
    if resources-set(paths.values()): raise ValueError('TOYS_RESOURCE_MISSING')
    selected = [info for info in infos if paths[info.filename].is_relative_to(prefix) or paths[info.filename] in resources]
    for info in selected:
        if stat.S_ISLNK(info.external_attr >> 16): raise ValueError('TOYS_ARCHIVE_SYMLINK: '+info.filename)
    root = Path(c['root']); folder = root/'raw/Toys4K'/row['source_id']
    folder.parent.mkdir(parents=True, exist_ok=True)
    if folder.is_symlink(): raise ValueError('TOYS_DESTINATION_SYMLINK')
    if shutil.disk_usage(folder.parent).free < sum(i.file_size for i in selected) + (10 << 30):
        raise OSError('TOYS_EXTRACT_INSUFFICIENT_SPACE: instance plus 10 GiB reserve required')
    records = []
    with tempfile.TemporaryDirectory(prefix=row['source_id']+'.', dir=folder.parent) as temp:
        staged = Path(temp)
        for info in selected:
            if info.is_dir(): continue
            member = paths[info.filename]
            relative = member
            target = staged/str(relative); target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open('wb') as output: shutil.copyfileobj(source, output, 1 << 20)
            records.append(dict(member=info.filename, path=str(relative), size_bytes=target.stat().st_size, sha256=sha(target)))
        original = staged/str(paths[model.filename])
        digest = sha(original)
        if digest != row['expected_sha256']: raise ValueError('TOYS_MEMBER_SHA256_MISMATCH: '+model.filename)
        if folder.exists():
            existing = folder/str(paths[model.filename])
            if existing.exists() and sha(existing) != digest: raise ValueError('RAW_ASSET_CHECKSUM_CHANGED; original provenance retained')
            for record in records:
                target = folder/record['path']
                if target.is_symlink() or any(p.is_symlink() for p in target.parents if p != folder.parent):
                    raise ValueError('TOYS_DESTINATION_SYMLINK')
                if target.exists() and sha(target) != record['sha256']: raise ValueError('TOYS_RESOURCE_CHECKSUM_CHANGED')
        folder.mkdir(parents=True, exist_ok=True)
        for record in records:
            target = folder/record['path']; target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists(): (staged/record['path']).replace(target)
    if not (folder/'provenance.json').exists():
        dump(folder/'provenance.json', dict(downloaded_at=now(), archive=archive_record,
            metadata_identifier=str(ident), expected_sha256=row['expected_sha256'], actual_sha256=digest,
            archive_member=model.filename, metadata_csv=csv_record, files=records,
            source_kind='non_official_mirror', attribution_note=TOYS_MIRROR_NOTE,
            original_source='https://github.com/rehg-lab/lowshot-shapebias/tree/main/toys4k'))
    row.update(asset_status='downloaded', raw_mesh=str((folder/str(paths[model.filename])).relative_to(root)),
               raw_sha256=digest, asset_source='non_official_mirror',
               resource_sha256={str((folder/r['path']).relative_to(root)): r['sha256']
                                for r in records if Path(r['path']).suffix.lower() != '.blend'})


def fetch(c, candidates, selected=None):
    """Keep the entire manifest on every write, even for a single-case download."""
    if selected is None: selected = [r for r in candidates if r['source'] == 'ABO']
    root = Path(c['root']); meta = root/'raw/indices'
    manifest = root/'manifests/candidates.jsonl'
    existing = rows(manifest) if manifest.exists() else candidates
    byid = {r['case_id']: r for r in candidates}
    byid.update({r['case_id']: r for r in selected})
    complete = [byid.pop(r['case_id'], r) for r in existing]
    complete.extend(byid.values())
    abo = [r for r in selected if r['source'] == 'ABO']; toys = [r for r in selected if r['source'] == 'Toys4K']
    images = {}
    if abo:
        image_index_path = meta/'images.csv.gz'; download(BASE+'images/metadata/images.csv.gz', image_index_path)
        wanted = {r['metadata'].get('main_image_id') for r in abo}
        with gzip.open(image_index_path, 'rt') as stream:
            images = {r['image_id']: r for r in csv.DictReader(stream) if r['image_id'] in wanted}
    archive_record = download_toys_archive(c) if toys else None
    archive = zipfile.ZipFile(archive_record['path']) if toys else None
    try:
        for row in selected:
            try:
                if row['source'] == 'Toys4K': extract_toy(c, row, archive, archive_record)
                elif row['source'] == 'ABO':
                    folder = root/'raw/ABO'/row['source_id']; existing = folder/'original.glb'
                    if existing.exists() and row.get('raw_sha256') and sha(existing) != row['raw_sha256']:
                        raise ValueError('RAW_ASSET_CHECKSUM_CHANGED; original provenance retained')
                    record = download(row['model_url'], existing)
                    row.update(asset_status='downloaded', raw_mesh=str(Path(record['path']).relative_to(root)), raw_sha256=record['sha256'])
                    image = images.get(row['metadata'].get('main_image_id'))
                    if image:
                        thumb = download(BASE+'images/small/'+image['path'], folder/'catalog.jpg')
                        row['catalog_image'] = str(Path(thumb['path']).relative_to(root))
                    if not (folder/'provenance.json').exists():
                        dump(folder/'provenance.json', dict(downloaded_at=now(), model=record, listing=row['metadata'], model_index=row['model_index'], attribution='Amazon.com; Guillaumin et al.', license='CC BY 4.0'))
                row.pop('asset_error', None)
            except Exception as error:
                row.update(asset_status='download_failed', status='needs_review', asset_error=f'{type(error).__name__}: {error}')
            write_rows(manifest, complete)
            print(row['case_id'], row['asset_status'], flush=True)
    finally:
        if archive: archive.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True); parser.add_argument('--download', action='store_true')
    parser.add_argument('--limit', type=int); parser.add_argument('--reclassify', action='store_true')
    parser.add_argument('--append', type=int, default=0)
    parser.add_argument('--source', choices=['ABO', 'Toys4K', 'all'], default='ABO')
    parser.add_argument('--case', action='append', default=[])
    args = parser.parse_args()
    if not 0 <= args.append <= 10: parser.error('--append must be between 0 and 10')
    if args.limit is not None and args.limit < 0: parser.error('--limit must be nonnegative')
    c = config(args.config); path = Path(c['root'])/'manifests/candidates.jsonl'
    candidates = rows(path) if path.exists() else collect(c)
    if args.reclassify:
        reclassify(candidates); write_rows(path, candidates)
    if args.append: append_candidates(c, candidates, args.append)
    if args.download:
        selected = [r for r in candidates if (args.source == 'all' or r['source'] == args.source)
                    and (not args.case or r['case_id'] in args.case)]
        if args.case and set(args.case)-{r['case_id'] for r in selected}: parser.error('--case did not match selected source')
        fetch(c, candidates, selected[:args.limit])
    print(dict(Counter(r['source'] for r in candidates)))


if __name__ == '__main__': main()
