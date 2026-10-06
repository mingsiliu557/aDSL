"""Metadata classification, incremental selection, and selective ZIP acquisition."""
import copy
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import collect_candidates as collector
from common import load, rows, sha, write_rows


@pytest.fixture(autouse=True)
def ample_fixture_disk(monkeypatch):
    # Small ZIP fixtures exercise logic without requiring 10 GiB in /tmp.
    monkeypatch.setattr(collector.shutil, 'disk_usage', lambda path: SimpleNamespace(free=100 << 30))


def metadata(kind='HOME', title='', model='one', nodes=()):
    return {'item_id': model, '3dmodel_id': model, 'product_type': [dict(value=kind)],
            'item_name': [dict(language_tag='en_GB', value=title)],
            'node': [dict(node_name=n) for n in nodes], 'model_number': [dict(value='family_'+model)]}


def test_specific_type_precedes_title_and_parent_node():
    assert collector.category(metadata('CABINET', 'File cabinet', nodes=['/Furniture & Lighting/Cabinets'])) == 'cabinet_shelf'
    assert collector.category(metadata('CHAIR', 'Chair with table', nodes=['/Lighting/Chairs'])) == 'chair_stool'
    assert collector.category(metadata('LADDER', '3-Step Stool', nodes=['/Furniture & Lighting/Step Stools'])) is None
    assert collector.category(metadata('HOME_MIRROR', 'Storage Cabinet with Shelves')) is None


def test_generic_types_use_all_english_titles_and_leaf_nodes():
    record = metadata('HOME_FURNITURE_AND_DECOR', 'Console table')
    record['item_name'].insert(0, dict(language_tag='zh_CN', value='lamp stool cabinet'))
    record['item_name'].append(dict(language_tag='en_IN', value='Console desk'))
    assert collector.category(record) == 'table'
    assert collector.category(metadata('HOME', nodes=['/Office Furniture & Lighting/Bookcases'])) == 'cabinet_shelf'
    assert collector.category(metadata('HOME', nodes=['/Office Furniture & Lighting/Furniture'])) is None
    assert collector.category(metadata('HOME', nodes=['/Chairs & Sofas/Tables'])) == 'table'


@pytest.mark.parametrize('title', ['Wall lamp', 'Wall-mounted lamp', 'Pendant lamp', 'Ceiling light', 'Chandelier lamp', 'Sconce'])
def test_lamp_mount_exclusions_override_specific_type(title):
    assert collector.category(metadata('LAMP', title)) is None


def test_reclassification_preserves_identifiers_assets_and_human_records():
    ladder = dict(case_id='ABO_ladder', source='ABO', source_id='ladder', category='lamp', status='candidate',
                  raw_mesh='raw/original.glb', raw_sha256='digest', catalog_image='catalog.jpg', family_key='kept',
                  selection_note='Human note', tags=['reviewed'], metadata=metadata('LADDER', 'Step stool'))
    mirror = dict(case_id='ABO_mirror', source='ABO', source_id='mirror', category='cabinet_shelf', status='candidate',
                  manual_review=dict(note='keep me'), selection_note='Existing note', metadata=metadata('HOME_MIRROR', 'Storage cabinet'))
    cart = dict(case_id='ABO_cart', source='ABO', category='cabinet_shelf', status='candidate', metadata=metadata('HOME', 'Kitchen cart with cabinet'))
    originals = copy.deepcopy([ladder, mirror, cart])
    collector.reclassify([ladder, mirror, cart])
    for key, value in originals[0].items():
        if key not in {'category', 'status'}: assert ladder[key] == value
    assert ladder['category'] is None and ladder['status'] == 'excluded'
    assert ladder['classification_review']['scope'] == 'out_of_scope'
    assert mirror['category'] == 'cabinet_shelf' and mirror['status'] == 'needs_review'
    assert mirror['manual_review'] == originals[1]['manual_review']
    assert mirror['classification_review']['scope'] == 'unresolved'
    assert cart['status'] == 'needs_review' and cart['classification_review']['needs_review']


def test_incremental_append_fills_lamps_then_rotates_without_losing_old_rows(tmp_path, monkeypatch):
    pools = {g: [metadata(t, f'{t} {i}', f'{g}_{i}') for i in range(12)]
             for g, t in [('lamp', 'LAMP'), ('chair_stool', 'CHAIR'), ('table', 'TABLE'), ('cabinet_shelf', 'CABINET')]}
    index = {r['3dmodel_id']: dict(path=r['3dmodel_id']+'.glb') for pool in pools.values() for r in pool}
    monkeypatch.setattr(collector, 'abo_inventory', lambda c: (index, copy.deepcopy(pools), set(), []))
    c = dict(root=str(tmp_path), seed=20261006, abo_quotas=dict(lamp=7, chair_stool=6, table=6, cabinet_shelf=6))
    existing = [dict(case_id=f'old_{i}', source='Toys4K', source_id=str(i), manual_review=dict(note='retained')) for i in range(50)]
    first = copy.deepcopy(existing); second = copy.deepcopy(existing)
    collector.append_candidates(c, first, 10); collector.append_candidates(c, second, 10)
    assert first == second and first[:50] == existing
    assert [r['category'] for r in first[50:]] == ['lamp']*8+['chair_stool', 'table']
    assert len({r['family_key'] for r in first[50:]}) == 10
    assert rows(tmp_path/'manifests/candidates.jsonl') == first
    collector.append_candidates(c, first, 10)
    assert len(first) == 60
    with pytest.raises(ValueError, match='between 0 and 10'): collector.append_candidates(c, first, 11)


def toy_fixture(tmp_path, members=None, expected=None):
    archive = tmp_path/'fixture.zip'
    data = b'fixture blend bytes'
    if members is None:
        members = {'mirror/cat/cat_001/cat_001.blend': data,
                   'mirror/cat/cat_001/textures/color.png': b'color',
                   'mirror/cat/cat_002/cat_002.blend': b'other',
                   'mirror/shared/wood.png': b'resource'}
    with zipfile.ZipFile(archive, 'w') as output:
        for name, value in members.items(): output.writestr(name, value)
    c = dict(root=str(tmp_path/'data'), toys_archive=dict(filename='fixture.zip', path=str(archive),
        url='https://example.invalid/fixture.zip', repo_id='fixture/nonofficial', revision='pinned',
        size_bytes=archive.stat().st_size, sha256=sha(archive),
        resources=['mirror/shared/wood.png'] if 'mirror/shared/wood.png' in members else []))
    row = dict(case_id='Toys4K_cat_001', source='Toys4K', source_id='cat_001', category='cat', status='needs_review',
               file_identifier='cat/cat_001/cat_001.blend', expected_sha256=expected or hashlib.sha256(data).hexdigest(),
               selection_note='Review remains pending', manual_review=dict(note='retain'))
    metadata_path = tmp_path/'Toys4k.csv'
    metadata_path.write_text('file_identifier,sha256\n'+row['file_identifier']+','+row['expected_sha256']+'\n')
    c['toys_metadata'] = str(metadata_path)
    return c, row, archive


def test_verified_zip_extracts_one_instance_and_explicit_resources(tmp_path):
    c, row, path = toy_fixture(tmp_path)
    record = collector.verify_toys_archive(c)
    with zipfile.ZipFile(path) as archive: collector.extract_toy(c, row, archive, record)
    root = Path(c['root']); folder = root/'raw/Toys4K/cat_001'
    assert (root/row['raw_mesh']).read_bytes() == b'fixture blend bytes'
    assert (folder/'mirror/cat/cat_001/textures/color.png').exists()
    assert (folder/'mirror/shared/wood.png').exists()
    assert not (folder/'mirror/cat/cat_002').exists()
    assert row['asset_status'] == 'downloaded' and row['raw_sha256'] == row['expected_sha256']
    assert row['status'] == 'needs_review' and row['manual_review'] == dict(note='retain')
    provenance = load(folder/'provenance.json')
    assert provenance['source_kind'] == 'non_official_mirror'
    assert provenance['metadata_csv'] == dict(path=c['toys_metadata'], sha256=sha(c['toys_metadata']), source_kind='local_cached_csv')
    assert provenance['expected_sha256'] == provenance['actual_sha256'] == row['raw_sha256']
    assert provenance['archive_member'] == 'mirror/cat/cat_001/cat_001.blend'
    assert row['resource_sha256'] == {str(p.relative_to(root)): sha(p) for p in (folder/'mirror/cat/cat_001/textures/color.png', folder/'mirror/shared/wood.png')}


@pytest.mark.parametrize('members, expected, error', [
    ({'cat/cat_002/cat_002.blend': b'other'}, None, 'MEMBER_MISSING'),
    ({'cat/cat_001/cat_001.blend': b'wrong'}, '0'*64, 'MEMBER_SHA256_MISMATCH'),
    ({'cat/cat_001/cat_001.blend': b'fixture blend bytes', 'cat/cat_001/../../escape': b'bad'}, None, 'UNSAFE_PATH'),
    ({'/cat/cat_001/cat_001.blend': b'fixture blend bytes'}, None, 'UNSAFE_PATH'),
    ({'cat\\cat_001\\cat_001.blend': b'fixture blend bytes'}, None, 'UNSAFE_PATH'),
])
def test_zip_missing_mismatch_and_unsafe_paths_never_publish_assets(tmp_path, members, expected, error):
    c, row, path = toy_fixture(tmp_path, members, expected)
    record = collector.verify_toys_archive(c)
    with zipfile.ZipFile(path) as archive:
        with pytest.raises(ValueError, match=error): collector.extract_toy(c, row, archive, record)
    assert not (Path(c['root'])/'raw/Toys4K/cat_001').exists()


def test_archive_size_and_sha_are_required_before_extract(tmp_path):
    c, row, path = toy_fixture(tmp_path)
    c['toys_archive']['size_bytes'] += 1
    with pytest.raises(ValueError, match='SIZE_MISMATCH'): collector.verify_toys_archive(c)
    c['toys_archive']['size_bytes'] -= 1; c['toys_archive']['sha256'] = '0'*64
    with pytest.raises(ValueError, match='SHA256_MISMATCH'): collector.verify_toys_archive(c)


def test_archive_downloader_resumes_part_and_verifies_before_publish(tmp_path, monkeypatch):
    c, row, path = toy_fixture(tmp_path)
    data = path.read_bytes(); path.unlink(); path.with_name(path.name+'.part').write_bytes(b'partial')
    calls = []
    def run(command, check):
        calls.append(command); path.with_name(path.name+'.part').write_bytes(data)
    monkeypatch.setattr(collector.subprocess, 'run', run)
    record = collector.download_toys_archive(c)
    assert '--continue=true' in calls[0] and '--out=fixture.zip.part' in calls[0]
    assert record['sha256'] == sha(path) and not path.with_name(path.name+'.part').exists()
    assert path.with_suffix('.zip.provenance.json').exists()


def test_fetch_subset_persists_complete_manifest(tmp_path, monkeypatch):
    c, selected, path = toy_fixture(tmp_path)
    all_rows = [dict(case_id='ABO_old', source='ABO', source_id='old', category='table', status='candidate'), selected,
                dict(case_id='Toys4K_old', source='Toys4K', source_id='old', category='dog', status='needs_review')]
    monkeypatch.setattr(collector, 'download_toys_archive', lambda c: collector.verify_toys_archive(c))
    write_rows(Path(c['root'])/'manifests/candidates.jsonl', all_rows)
    # Even legacy direct subset callers cannot replace the complete manifest.
    collector.fetch(c, [selected], [selected])
    saved = rows(Path(c['root'])/'manifests/candidates.jsonl')
    assert [r['case_id'] for r in saved] == [r['case_id'] for r in all_rows]
    assert saved[1]['asset_status'] == 'downloaded' and saved[0] == all_rows[0] and saved[2] == all_rows[2]


def test_archive_insufficient_space_does_not_start_download(tmp_path, monkeypatch):
    c, row, path = toy_fixture(tmp_path)
    path.unlink()
    monkeypatch.setattr(collector.shutil, 'disk_usage', lambda path: SimpleNamespace(free=10 << 30))
    monkeypatch.setattr(collector.subprocess, 'run', lambda *a, **k: pytest.fail('download started without reserve'))
    with pytest.raises(OSError, match='INSUFFICIENT_SPACE'): collector.download_toys_archive(c)


def test_zip_symlink_is_rejected_without_publication(tmp_path):
    c, row, path = toy_fixture(tmp_path)
    with zipfile.ZipFile(path, 'a') as output:
        info = zipfile.ZipInfo('mirror/cat/cat_001/textures/link.png')
        info.create_system = 3; info.external_attr = 0o120777 << 16
        output.writestr(info, '/etc/passwd')
    c['toys_archive'].update(size_bytes=path.stat().st_size, sha256=sha(path))
    with zipfile.ZipFile(path) as archive:
        with pytest.raises(ValueError, match='SYMLINK'): collector.extract_toy(c, row, archive, collector.verify_toys_archive(c))
    assert not (Path(c['root'])/'raw/Toys4K/cat_001').exists()


@pytest.mark.parametrize('status', ['excluded', 'user_confirmed', 'user_confirmed_dev'])
@pytest.mark.parametrize('kind,title', [('LADDER', 'Step stool'), ('HOME_MIRROR', 'Storage cabinet')])
def test_reclassification_retains_manual_status_without_review_object(status, kind, title):
    row = dict(case_id='ABO_kept', source='ABO', source_id='kept', category='cabinet_shelf',
               status=status, selection_note='User decision', metadata=metadata(kind, title))
    collector.reclassify([row])
    assert row['status'] == status and row['selection_note'] == 'User decision'
    assert row['classification_review']['needs_review']
    if kind == 'HOME_MIRROR': assert row['category'] == 'cabinet_shelf'


def test_changed_csv_expected_checksum_blocks_extraction(tmp_path):
    c, row, path = toy_fixture(tmp_path)
    Path(c['toys_metadata']).write_text('file_identifier,sha256\n'+row['file_identifier']+','+'0'*64+'\n')
    with zipfile.ZipFile(path) as archive:
        with pytest.raises(ValueError, match='METADATA_CHECKSUM_MISMATCH'):
            collector.extract_toy(c, row, archive, collector.verify_toys_archive(c))
    assert not (Path(c['root'])/'raw/Toys4K/cat_001').exists()
