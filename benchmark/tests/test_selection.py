"""Targeted review invariants; native adapter checks remain opt-in below."""
import sys
from pathlib import Path
import zipfile
sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from collect_candidates import category, family
import build_review_pack as pack
from build_review_pack import shortlist, build, review_evidence
from common import dump, load, rows, sha, write_rows


def test_seed_metadata_categories_do_not_infer_mesh_parts():
    assert category({'product_type': [{'value': 'CHAIR'}]}) == 'chair_stool'
    assert category({'item_name': [{'value': 'pendant ceiling lamp'}]}) is None
    assert category({'item_name': [{'value': 'Table lamp'}]}) == 'lamp'
    assert category({'item_name': [{'value': 'Dining table'}]}) == 'table'
    a = dict(item_id='one', item_name=[dict(language_tag='en_US', value='Rivet chair, Blue')])
    b = dict(item_id='two', item_name=[dict(language_tag='en_US', value='Rivet chair, Red')])
    assert family(a) == family(b)


def test_shortlist_round_robin_uses_manifest_category_order_and_reliable_references():
    values = [dict(case_id=str(i), source='ABO', category=cat, preflight_status='PASS', geometry_status='PASS')
              for i, cat in enumerate(['lamp', 'lamp', 'table', 'chair'])]
    values.append(dict(case_id='bad', source='ABO', category='cabinet', preflight_status='PASS', geometry_status='INDETERMINATE'))
    assert shortlist(dict(shortlist_count=3), values) == ['0', '2', '3']


def test_shortlist_balances_sources_and_redistributes_only_reliable_geometry():
    values = [dict(case_id=f'A{i}', source='ABO', category=('lamp' if i % 2 else 'chair'), material_current=True)
              for i in range(30)]
    values += [dict(case_id=f'T{i}', source='Toys4K', category='dog', material_current=True) for i in range(2)]
    values.append(dict(case_id='open', source='Toys4K', category='cat', material_current=False))
    selected = shortlist(dict(shortlist_count=24, shortlist_per_source=12), values)
    assert len(selected) == 24 and len([i for i in selected if i.startswith('A')]) == 22
    assert selected[:4] == ['A0', 'T0', 'A1', 'T1'] and 'open' not in selected



def test_shortlist_revalidates_cached_preflight_after_stale_build_material_flag(tmp_path, monkeypatch):
    row = dict(case_id='Toys4K_downloaded', source='Toys4K', category='dog', status='needs_review',
               material_current=False, preflight_status='INDETERMINATE', geometry_status='INDETERMINATE')
    monkeypatch.setattr(pack, '_source_current', lambda root, row: True)
    monkeypatch.setattr(pack, 'validated_preflight', lambda c, row: dict(status='PASS', basic=dict(status='PASS')))
    assert shortlist(dict(root=str(tmp_path), shortlist_count=24), [row]) == ['Toys4K_downloaded']
    row['material_current'] = True
    monkeypatch.setattr(pack, 'validated_preflight', lambda c, row: None)
    assert shortlist(dict(root=str(tmp_path), shortlist_count=24), [row]) == []


def _case(tmp_path, monkeypatch, *, case='ABO_fixture', source='ABO', material=True,
          standing='PASS', overhang='PASS', appearance=True, applicable=True, pose=True, current=True):
    raw = tmp_path / 'raw' / source / case / 'original.glb'
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(case.encode())
    image = tmp_path / 'previews' / case / 'input.png'
    image.parent.mkdir(parents=True, exist_ok=True)
    __import__('PIL.Image', fromlist=['Image']).new('RGBA', (8, 8), (1, 2, 3, 255)).save(image)
    row = dict(case_id=case, source=source, source_id=case, category='lamp', status='needs_review', tags=[],
               selection_note='fixture', raw_mesh=str(raw.relative_to(tmp_path)), raw_sha256=sha(raw),
               input_image=str(image.relative_to(tmp_path)), preflight_status='PASS',
               geometry_status='PASS' if material else 'INDETERMINATE')
    preflight = dict(status='PASS', basic=dict(status='PASS' if material else 'INDETERMINATE', connected_components=1))
    measurement = dict(status='COMPLETED', cache_key='measurement', standing=dict(status=standing), overhang=dict(status=overhang))
    label = dict(status='PASS', cache_key='label', called_at='2026-10-06T01:00:00Z', output=dict(
        tags=['complex_surface'], appearance_input_usable=appearance, standing_applicable=applicable,
        pose_observation='Upright use pose.' if pose else '', pose_confirmed=pose,
        task_requirements_clear=True, selection_note='Curved upper body projects beyond its narrow support; print-orientation and grouping choices need comparison.',
        needs_manual_confirmation=True))
    monkeypatch.setattr(pack, 'validated_preflight', lambda c, r: preflight)
    monkeypatch.setattr(pack, 'validated_measurement', lambda c, r: measurement if current else None)
    monkeypatch.setattr(pack, 'current_label', lambda c, r: label)
    dump(tmp_path / 'measurements' / case / 'reference_measurement.json', measurement)
    return row, dict(root=str(tmp_path), shortlist_count=24, shortlist_per_source=12, recommend_per_source=10), preflight, measurement, label


def _build_one(tmp_path, c, row):
    write_rows(tmp_path / 'manifests' / 'candidates.jsonl', [row])
    build(c)
    return rows(tmp_path / 'manifests' / 'candidates.jsonl')[0], load(tmp_path / 'review' / 'summary.json')


def test_missing_source_and_preview_are_not_recommendations_even_with_report(tmp_path):
    values = [dict(case_id='ABO_bad', source='ABO', source_id='bad', category='lamp', status='needs_review', tags=[], selection_note='invalid'),
              dict(case_id='Toys4K_missing', source='Toys4K', source_id='missing', category='dog', status='needs_review', tags=[], selection_note='archive absent')]
    write_rows(tmp_path / 'manifests' / 'candidates.jsonl', values)
    dump(tmp_path / 'measurements' / 'ABO_bad' / 'reference_measurement.json', dict(standing=None, overhang=None))
    build(dict(root=str(tmp_path), shortlist_count=24, recommend_per_source=10))
    assert rows(tmp_path / 'manifests' / 'recommended_dev20.jsonl') == []
    assert all(r['status'] == 'needs_review' for r in rows(tmp_path / 'manifests' / 'candidates.jsonl'))
    stats = load(tmp_path / 'review' / 'summary.json')
    assert stats['completed_reference_measurements'] == dict(overhang=0, standing=0)
    assert stats['measurements']['stale_reports']['ABO'] == 1
    assert (tmp_path / 'selection_v1_review.zip').is_file()


def test_appearance_can_recommend_open_toys_with_current_visual_evidence(tmp_path, monkeypatch):
    row, c, preflight, _, _ = _case(tmp_path, monkeypatch, source='Toys4K', case='Toys4K_open', material=False)
    result, stats = _build_one(tmp_path, c, row)
    assert result['metric_eligibility']['appearance']['status'] == 'ELIGIBLE'
    assert result['metric_eligibility']['overhang']['status'] == 'ELIGIBLE'
    assert result['recommended_for'] == ['appearance', 'overhang', 'standing'] and result['status'] == 'recommended_dev'
    assert result['task_applicability'] == result['metric_eligibility']
    assert not result['material_current']
    assert stats['recommended']['Toys4K'] == 1
    assert load(tmp_path / 'manifests' / 'shortlist.json') == []
    assert 'N/A' in (tmp_path / 'review' / 'summary.md').read_text()


def test_current_visual_evidence_and_actual_source_sha_are_required(tmp_path, monkeypatch):
    row, c, _, _, _ = _case(tmp_path, monkeypatch)
    monkeypatch.setattr(pack, 'current_label', lambda c, r: None)
    result, _ = review_evidence(c, row)
    assert result['metric_eligibility']['appearance']['status'] == 'INDETERMINATE'
    row['manual_review'] = dict(appearance_input_usable=True)
    assert review_evidence(c, row)[0]['metric_eligibility']['appearance']['status'] == 'ELIGIBLE'
    (tmp_path / row['raw_mesh']).write_bytes(b'changed source')
    result, _ = review_evidence(c, row)
    assert all(x['status'] == 'INDETERMINATE' for x in result['metric_eligibility'].values())
    assert not result['measurement_current']


def test_wall_ground_pass_is_diagnostic_but_overhang_can_recommend(tmp_path, monkeypatch):
    row, c, _, _, _ = _case(tmp_path, monkeypatch)
    row['manual_review'] = dict(standing_applicable=False, reason='Wall mounted.', user_confirmed=False)
    result, stats = _build_one(tmp_path, c, row)
    assert result['metric_eligibility']['standing']['status'] == 'NOT_APPLICABLE'
    assert result['measurement']['standing'] == dict(status='PASS', valid=True, scope='gt_diagnostic', task_applicable=False, externally_supported=True)
    assert result['recommended_for'] == ['appearance', 'overhang']
    assert stats['measurements']['current_valid']['standing']['ABO'] == dict(PASS=1, FAIL=0)
    assert stats['measurements']['externally_supported']['standing']['ABO'] == 1
    assert stats['measurements']['diagnostic']['standing']['ABO'] == 1
    assert stats['manual_confirmations'] == 0


def test_gt_standing_fail_and_unknown_overhang_do_not_gate_recommendation(tmp_path, monkeypatch):
    row, c, _, _, _ = _case(tmp_path, monkeypatch, standing='FAIL', overhang='INDETERMINATE')
    result, stats = _build_one(tmp_path, c, row)
    assert result['metric_eligibility']['standing']['status'] == 'ELIGIBLE'
    assert result['measurement']['standing']['valid']
    assert result['recommended_for'] == ['appearance', 'overhang', 'standing'] and result['status'] == 'recommended_dev'
    assert stats['measurements']['current_statuses']['overhang']['ABO'] == dict(INDETERMINATE=1)
    assert stats['measurements']['current_valid']['standing']['ABO'] == dict(PASS=0, FAIL=1)
    row['manual_review'] = dict(standing_applicable=True, pose_confirmed=False)
    assert review_evidence(c, row)[0]['metric_eligibility']['standing']['status'] == 'INDETERMINATE'


def test_manual_pose_alias_and_partial_applicability_review_keep_their_priority(tmp_path, monkeypatch):
    row, c, _, _, label = _case(tmp_path, monkeypatch)
    row['selection_review'] = dict(label['output'], pose_confirmed=True, reviewer='coding_agent',
                                  input_files_sha256={row['input_image']: sha(tmp_path / row['input_image'])})
    row['manual_review'] = dict(use_pose_confirmed=False, standing_applicable=True)
    result, _ = review_evidence(c, row)
    assert result['task_applicability']['standing']['status'] == 'INDETERMINATE'
    assert result['review_source'] == 'coding_agent'


def test_risk_selection_covers_distinct_categories_and_a_low_risk_control():
    def item(name, category, tags, ordinary=False):
        return dict(case_id=name, category=category, current_tags=tags, ordinary_control=ordinary)
    values = [item('cat_risk', 'cat', ['standing_sensitive', 'grouping_tradeoff']),
              item('bunny_risk', 'bunny', ['standing_sensitive', 'grouping_tradeoff']),
              item('robot_risk', 'robot', ['standing_sensitive', 'grouping_tradeoff']),
              item('dragon_print', 'dragon', ['grouping_tradeoff']),
              item('round_cat_control', 'cat', ['standing_sensitive'], True),
              item('simple_bunny_control', 'bunny', [], True)]
    selected = pack._recommendations(values, 4)
    assert [row['case_id'] for row in selected] == ['cat_risk', 'robot_risk', 'dragon_print', 'simple_bunny_control']
    assert len({row['category'] for row in selected}) == 4


def test_stale_measurement_report_is_separate_from_valid_completed_counts(tmp_path, monkeypatch):
    row, c, _, _, _ = _case(tmp_path, monkeypatch, current=False)
    result, stats = _build_one(tmp_path, c, row)
    assert result['measurement']['status'] == 'STALE'
    assert result['measurement']['report_exists'] and not result['measurement_current']
    assert result['recommended_for'] == ['appearance', 'overhang', 'standing']
    assert stats['measured']['ABO'] == 1 and stats['completed_reference_measurements'] == dict(overhang=0, standing=0)


def test_manual_decisions_and_classification_conflicts_survive_rebuild(tmp_path, monkeypatch):
    row, c, _, _, _ = _case(tmp_path, monkeypatch, current=False, appearance=False)
    row.update(status='user_confirmed', recommended_for=['standing'], manual_review=dict(user_confirmed=True))
    result, stats = _build_one(tmp_path, c, row)
    assert result['status'] == 'user_confirmed'
    assert result['manual_selection']['recommended_for'] == ['standing']
    assert result['selection_conflicts'] and stats['manual_confirmations'] == 1
    rebuilt, _ = _build_one(tmp_path, c, result)
    assert rebuilt['selection_conflicts'] == result['selection_conflicts']
    row.update(status='excluded', category=None, classification=dict(scope='out_of_scope'))
    result, _ = _build_one(tmp_path, c, row)
    assert result['status'] == 'excluded' and not result['recommended_for']
    row.update(status='needs_review', category='lamp', classification_review=dict(needs_review=True))
    assert _build_one(tmp_path, c, row)[0]['recommended_for'] == []


def test_recommendation_source_quota_and_category_order_ignore_scores(tmp_path, monkeypatch):
    row, c, _, measurement, _ = _case(tmp_path, monkeypatch)
    values = []
    for source in ('ABO', 'Toys4K'):
        for i in range(12):
            values.append(dict(row, case_id=f'{source}_{i}', source=source, source_id=str(i),
                               category='z_first' if i < 6 else 'a_second'))
    measurement['overhang']['metrics'] = dict(partition_objective=dict(score=-100, gap_voxels=999999))
    write_rows(tmp_path / 'manifests' / 'candidates.jsonl', values)
    build(c)
    recommended = rows(tmp_path / 'manifests' / 'recommended_dev20.jsonl')
    assert len(recommended) == 20
    assert [r['case_id'] for r in recommended[:4]] == ['ABO_0', 'ABO_6', 'ABO_1', 'ABO_7']
    assert all(r['recommended_for'] == ['appearance', 'overhang', 'standing'] for r in recommended)


def test_vlm_history_counts_independent_calls_without_migration_duplicates_and_zip_is_lightweight(tmp_path, monkeypatch):
    row, c, _, _, label = _case(tmp_path, monkeypatch, source='Toys4K', case='Toys4K_fixture')
    label['case_id'] = row['case_id']
    folder = tmp_path / 'measurements' / row['case_id'] / 'vlm'
    dump(folder / 'attempt.json', label)
    dump(folder / 'history' / 'migrated_attempt.json', dict(label, cache_key='old-key'))
    old = dict(label, called_at='2026-10-05T01:00:00Z', status='UNAVAILABLE', output=dict(tags=['standing_sensitive']))
    dump(folder / 'history' / 'attempt_old.json', old)
    dump(tmp_path / 'raw' / 'Toys4K' / row['case_id'] / 'provenance.json', dict(source='authorized archive'))
    dump(tmp_path / 'logs' / 'case' / 'command.json', ['python', 'worker.py'])
    (tmp_path / 'logs' / 'case' / 'stdout.log').write_text('raw process output')
    (tmp_path / 'review').mkdir()
    (tmp_path / 'review' / '.npmrc').write_text('credential fixture')
    (tmp_path / 'review' / 'credentials.json').write_text('{}')
    (tmp_path / 'review' / 'original.blend').write_bytes(b'heavy model')
    (tmp_path / 'review' / 'old.zip').write_bytes(b'archive')
    result, stats = _build_one(tmp_path, c, row)
    assert stats['vlm_calls'] == 2 and stats['vlm_successes'] == 1
    assert stats['vlm_calls_by_source']['Toys4K'] == 2
    assert result['current_tags'] == ['complex_surface'] and result['historical_tags'] == ['standing_sensitive']
    with zipfile.ZipFile(tmp_path / 'selection_v1_review.zip') as archive:
        paths = archive.namelist()
    assert 'raw/Toys4K/Toys4K_fixture/provenance.json' in paths
    assert 'logs/case/command.json' in paths and 'logs/case/stdout.log' in paths
    assert 'review/config.json' in paths and 'review/selection_protocol.md' in paths
    assert any('attempt_old.json' in p for p in paths)
    assert not any(p.endswith(('.glb', '.blend', '.zip', '.npmrc', 'credentials.json')) for p in paths)



def test_clear_preview_recommends_without_any_gt_measurement_or_connected_volume(tmp_path, monkeypatch):
    row, c, preflight, _, _ = _case(tmp_path, monkeypatch, material=False, current=False)
    preflight['basic']['connected_components'] = 9
    (tmp_path / 'measurements' / row['case_id'] / 'reference_measurement.json').unlink()
    result, stats = _build_one(tmp_path, c, row)
    assert result['recommended_for'] == ['appearance', 'overhang', 'standing']
    assert result['measurement']['status'] == 'NOT_RUN'
    assert stats['completed_reference_measurements'] == dict(overhang=0, standing=0)
    assert stats['measurements']['current_statuses']['standing']['ABO'] == dict(NOT_RUN=1)
    assert load(tmp_path / 'manifests' / 'shortlist.json') == []


def test_flying_use_does_not_prevent_printing_and_appearance_recommendation(tmp_path, monkeypatch):
    row, c, _, _, label = _case(tmp_path, monkeypatch, source='Toys4K', case='dragon', current=False)
    label['output'].update(standing_applicable=False, pose_observation='Flying pose with spread wings.',
                           tags=['complex_surface', 'grouping_tradeoff'],
                           selection_note='Wings and neck project in different directions; a print rotation may help but splitting changes support and part count.')
    result, stats = _build_one(tmp_path, c, row)
    assert result['recommended_for'] == ['appearance', 'overhang']
    assert result['task_applicability']['standing']['status'] == 'NOT_APPLICABLE'
    assert stats['risk_reasons_recommended'] == 1
    assert 'Wings and neck' in (tmp_path / 'review' / 'summary.md').read_text()


def test_direct_human_preview_review_without_vlm_is_accepted_without_user_confirmation_invention(tmp_path, monkeypatch):
    row, c, _, _, _ = _case(tmp_path, monkeypatch, material=False, current=False)
    monkeypatch.setattr(pack, 'current_label', lambda c, r: None)
    row['manual_review'] = dict(appearance_input_usable=True, standing_applicable=True,
                              pose_confirmed=True, task_requirements_clear=True,
                              tags=['standing_sensitive'], selection_note='Tall body on narrow legs gives a small support footprint.',
                              user_confirmed=False)
    result, stats = _build_one(tmp_path, c, row)
    assert result['recommended_for'] == ['appearance', 'overhang', 'standing']
    assert result['review_source'] == 'manual' and not result['vlm_current']
    assert result['needs_manual_confirmation'] and stats['manual_confirmations'] == 0


def test_coding_agent_preview_review_is_hash_bound_and_human_fields_take_priority(tmp_path, monkeypatch):
    row, c, _, _, _ = _case(tmp_path, monkeypatch, current=False)
    monkeypatch.setattr(pack, 'current_label', lambda c, r: None)
    row['selection_review'] = dict(reviewer='coding_agent', input_files_sha256={row['input_image']: sha(tmp_path / row['input_image'])},
                                   appearance_input_usable=True, standing_applicable=True, pose_confirmed=True,
                                   task_requirements_clear=True, tags=['grouping_tradeoff'],
                                   selection_note='Several overhanging arms face different directions; compare whole-body orientation and grouping.',
                                   needs_manual_confirmation=True)
    result, _ = _build_one(tmp_path, c, row)
    assert result['selection_review_current'] and result['status'] == 'recommended_dev'
    assert result['needs_manual_confirmation'] and result['review_source'] == 'coding_agent'
    unrelated = tmp_path / 'unrelated.png'
    unrelated.write_bytes(b'not the input preview')
    recorded = row['selection_review']['input_files_sha256']
    row['selection_review']['input_files_sha256'] = {'unrelated.png': sha(unrelated)}
    assert not review_evidence(c, row)[0]['selection_review_current']
    row['selection_review']['input_files_sha256'] = recorded
    row['manual_review'] = dict(standing_applicable=False, reason='Wall mounted.')
    assert review_evidence(c, row)[0]['task_applicability']['standing']['status'] == 'NOT_APPLICABLE'
    (tmp_path / row['input_image']).write_bytes(b'changed preview')
    evidence, _ = review_evidence(c, row)
    assert not evidence['selection_review_current']
    assert evidence['task_applicability']['appearance']['status'] == 'INDETERMINATE'


def test_missing_labels_leave_candidate_pending_not_excluded(tmp_path, monkeypatch):
    row, c, _, _, _ = _case(tmp_path, monkeypatch, current=False)
    monkeypatch.setattr(pack, 'current_label', lambda c, r: None)
    result, stats = _build_one(tmp_path, c, row)
    assert result['source_current'] and result['preflight_current']
    assert result['status'] == 'needs_review' and result['review_source'] == 'pending'
    assert stats['pending_structural_review']['ABO'] == 1
    assert 'missing VLM labels do not invalidate' in result['task_applicability']['appearance']['reason']


def test_structural_risk_priority_and_an_ordinary_control_ignore_gt_scores(tmp_path, monkeypatch):
    row, c, _, measurement, label = _case(tmp_path, monkeypatch)
    values = []
    for i in range(12):
        review = dict(appearance_input_usable=True, standing_applicable=True, pose_confirmed=True,
                      task_requirements_clear=True, tags=['complex_surface'],
                      selection_note=f'Curved body {i} has local protrusions requiring print-orientation review.')
        if i == 0:
            review.update(tags=[], ordinary_control=True, selection_note='Compact body and broad base provide an ordinary structural control.')
        if i == 11:
            review.update(tags=['standing_sensitive', 'grouping_tradeoff'],
                          selection_note='Large offset upper body on a slim base combines standing risk and print-grouping tradeoffs.')
        values.append(dict(row, case_id=f'ABO_{i}', source_id=str(i), manual_review=review))
    measurement['overhang']['metrics'] = dict(partition_objective=dict(score=999999, gap_voxels=0))
    write_rows(tmp_path / 'manifests' / 'candidates.jsonl', values)
    build(c)
    recommended = rows(tmp_path / 'manifests' / 'recommended_dev20.jsonl')
    assert len(recommended) == 10
    assert recommended[0]['case_id'] == 'ABO_11'
    assert recommended[-1]['case_id'] == 'ABO_0'
    stats = load(tmp_path / 'review' / 'summary.json')
    assert stats['ordinary_controls_recommended'] == dict(ABO=1)
    assert stats['risk_reasons_recommended'] == 10
    assert all(r['selection_note'].strip() and r['status'] == 'recommended_dev' for r in recommended)
    assert stats['recommendations_waiting_user_confirmation'] == 10

def test_native_import_mm_and_glb_m_roundtrip(tmp_path):
    import os
    if os.environ.get('ADSL_TEST_BENCHMARK_REAL')!='1':
        __import__('pytest').skip('explicit native import smoke')
    import numpy as np
    import trimesh
    from common import process, sha, load
    root=tmp_path/'data/selection_v1';raw=root/'raw/ABO/fixture/original.glb';raw.parent.mkdir(parents=True)
    trimesh.creation.box((.02,.04,.06)).export(raw)
    original=sha(raw);output=root/'derived/fixture';output.mkdir(parents=True)
    row=tmp_path/'row.json';dump(row,dict(raw_mesh=str(raw.relative_to(root))))
    c=dict(root=str(root),data_root=str(tmp_path/'data'),longest_extent_mm=150,
           render=dict(width=512,height=512,samples=32,threads=4,timeout_seconds=300))
    cfg=tmp_path/'config.json';dump(cfg,c)
    worker=Path(__file__).parents[1]/'scripts/reference_worker.py'
    result=process([sys.executable,str(worker),'--config',str(cfg),'--row',str(row),'--output',str(output),'--stage','import'],tmp_path/'logs',300,c)
    assert result['status']=='PASS'
    basic=load(output/'basic.json');assert basic['status']=='PASS' and basic['standing_eligible']
    assert basic['volume_mm3']==__import__('pytest').approx(750000,rel=1e-6)
    with np.load(output/'reference_whole.body.npz') as data:
        extents=np.ptp(data['vertices'],axis=0)
    np.testing.assert_allclose(sorted(extents),[50,100,150],atol=1e-4)
    scene=trimesh.load(output/'reference.glb',force='scene')
    np.testing.assert_allclose(sorted(scene.extents),[.05,.1,.15],atol=1e-7)
    assert sha(raw)==original


def test_native_overlapping_shells_union_and_cavity_is_one_material_island(tmp_path):
    import os
    if os.environ.get('ADSL_TEST_BENCHMARK_REAL')!='1': __import__('pytest').skip('explicit native import smoke')
    import numpy as np
    import trimesh
    import manifold3d as mf
    from adsl.core.assembly_topology import solid_mesh
    from common import process, load
    shapes=[('overlap',trimesh.util.concatenate([trimesh.creation.box((2,2,2)),trimesh.creation.box((2,2,2),transform=trimesh.transformations.translation_matrix([1,0,0]))]),12.,3.),
            ('cavity',solid_mesh(mf.Manifold.cube((3,3,3))-mf.Manifold.cube((1,1,1)).translate((1,1,1))),26.,3.)]
    for name,mesh,volume,extent in shapes:
        root=tmp_path/name/'selection_v1';raw=root/'raw/model.glb';raw.parent.mkdir(parents=True);mesh.export(raw)
        row=tmp_path/(name+'.json');dump(row,dict(raw_mesh=str(raw.relative_to(root))))
        cfg=tmp_path/(name+'.config.json');c=dict(data_root=str(root.parent),longest_extent_mm=150,render=dict(timeout_seconds=300));dump(cfg,c)
        output=root/'derived';worker=Path(__file__).parents[1]/'scripts/reference_worker.py'
        result=process([sys.executable,str(worker),'--config',str(cfg),'--row',str(row),'--output',str(output),'--stage','import'],root/'logs',300,c)
        assert result['status']=='PASS'
        basic=load(output/'basic.json')
        assert basic['status']=='PASS' and basic['connected_components']==1 and basic['standing_eligible']
        assert basic['volume_mm3']==__import__('pytest').approx(volume*(150/extent)**3,rel=1e-6)
