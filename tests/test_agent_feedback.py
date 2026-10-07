"""Offline contract tests; no native geometry, simulation or model requests."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from adsl.agents.agent_feedback import (
    AgentFeedbackError, evaluation_details, finding_for_agent, payload_for_agent,
)


def diagnostic(count=10000):
    return {'code': 'TARGET_PRECISION_UNREPRESENTABLE', 'stage': 'target_precision',
        'node_path': 'body/arm/union', 'operation': 'UNION', 'input_count': 3,
        'internal_metrics': {'valid': True, 'zero_area_triangles': 0, 'boundary_edges': 0},
        'displacement_budget_mm': 0.00014, 'local_scale_mm': 10,
        'absolute_scale_floor_mm': 0.001,
        'attempts': [{'method': 'local_precision_edge_repair', 'status': 'REJECTED',
            'diagnostic': {'repair': {'method': 'edge_collapse', 'status': 'REJECTED',
                'remaining_bad_faces_total': 8, 'target_zero_normals': 8,
                'remaining_bad_faces': [{'vertices': [[1, 2, 3]] * 3, 'neighbors': list(range(8))}] * count}}}
            for _ in range(3)],
        'operation_nodes': [{'node_path': f'body/arm/{i}'} for i in range(100)]}


def inputs(tmp_path, count=10000, groups=1):
    source = tmp_path / 'candidate' / 'source.py'
    source.parent.mkdir(exist_ok=True)
    source.write_text('scene = None\n')
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    rows = [{'part_id': f'body_{i}', 'diagnostic': diagnostic(count),
        'failure_kind': 'candidate_evaluation', 'geometry_repair_allowed': True,
        'output_role': 'display', 'file': file}
        for i in range(groups) for file in (f'body_{i}.glb', 'scene.glb', 'exploded.glb')]
    report = tmp_path / 'manifest.json'
    report.write_text(json.dumps({'source_sha256': sha, 'failures': rows}))
    ref = {'path': str(report), 'source_sha256': sha, 'source_version': 'original'}
    payload = {'requirement': 'keep this task', 'source_version': 'original',
        'assembly_context': {'failure_feedback': rows, 'report_ref': ref,
                             'current_assembly': {'parts': [{'id': 'body_0'}]}},
        'evaluation_failures': rows,
        'feedback': {'failures': rows, 'failure_feedback': rows,
            'resolved_visual_feedback': {'approved': False},
            'edit_restriction': 'keep scope', 'partition_guidance': {'current': {'N': 3}}}}
    return source, rows, ref, payload


def project(tmp_path, source, ref, payload, role='engineering'):
    return payload_for_agent(payload, workspace=tmp_path, source_path=source,
        source_version='original', role=role, report_ref=ref)


def test_details_formats_preserve_false_zero_empty_and_complete_nodes():
    raw = diagnostic(10)
    assert evaluation_details(raw) == evaluation_details({'diagnostic': raw})
    details = evaluation_details({'diagnostic': {'metrics': {}, 'input_count': 0,
        'attempts': [], 'operation_nodes': []}, 'metrics': {'valid': False}, 'attempts': [1]})
    assert details['metrics'] == {} and details['input_count'] == 0
    assert details['attempts'] == [] and details['operation_nodes'] == []
    assert evaluation_details({})['attempts'] is None
    assert len(evaluation_details(raw)['operation_nodes']) == 100


@pytest.mark.parametrize('role', ['engineering', 'code_critic', 'image_critic', 'coder'])
def test_huge_raw_payload_deduplicated_and_idempotent(tmp_path, role):
    source, rows, ref, raw = inputs(tmp_path)
    original = deepcopy(raw)
    projected = project(tmp_path, source, ref, raw, role)
    text = json.dumps(projected, ensure_ascii=False, allow_nan=False)
    assert len(text.encode()) < 32768
    assert 'remaining_bad_faces"' not in text and 'neighbors' not in text
    feedback = projected['evaluation_feedback']
    assert feedback['failure_count'] == 3 and feedback['unique_failure_count'] == 1
    group, = feedback['failures']
    assert group['occurrence_count'] == 3 and group['attempt_count'] == 3
    assert group['internal_metrics']['valid'] is True
    assert group['attempts_summary'][0]['repair']['remaining_bad_faces_total'] == 8
    assert group['displacement_budget_mm'] == .00014
    assert raw == original
    assert projected == project(tmp_path, source, ref, projected, role)
    assert projected['feedback']['resolved_visual_feedback'] == raw['feedback']['resolved_visual_feedback']
    for evidence in group['evidence_refs']:
        assert evidence['source_binding'] == 'CURRENT'
        document = json.loads((tmp_path / evidence['path']).read_text())
        assert document['failures'][int(evidence['json_pointer'].split('/')[-1])] in rows


def test_growth_of_detail_arrays_does_not_grow_summary(tmp_path):
    source, rows, ref, payload = inputs(tmp_path, 100)
    first = project(tmp_path, source, ref, payload)
    source, rows, ref, payload = inputs(tmp_path, 1000)
    second = project(tmp_path, source, ref, payload)
    assert abs(len(json.dumps(first)) - len(json.dumps(second))) < 100


def test_distinct_parts_and_canonical_full_index(tmp_path):
    source, _, ref, payload = inputs(tmp_path, 10, groups=8)
    out = project(tmp_path, source, ref, payload)
    feedback = out['evaluation_feedback']
    assert feedback['unique_failure_count'] == 8 and feedback['failure_count'] == 24
    assert len(feedback['failures']) == 6
    full = feedback['preview_info']['failures']['full_ref']
    doc = json.loads((tmp_path / full['path']).read_text())
    assert len(doc['failures']) == 8
    assert out == project(tmp_path, source, ref, out)


def test_finding_contract_and_nonfinite_measurement():
    raw = {'finding_id': 'test', 'rule_id': 'R', 'required': True, 'repairability': 'geometry',
        'category': 'geometry_failure', 'applicability': 'applicable', 'applicability_basis': 'measured',
        'metric': {'name': 'volume', 'value': float('nan'), 'unit': 'mm3', 'threshold': 0},
        'region': {'kind': 'aabb', 'frame': 'assembly', 'unit': 'mm',
            'bounds': [[0, 0, 0], [1, 1, 1]], 'layer_range': [0, 2], 'details': diagnostic(10)},
        'source_candidates': [{'feature_id': 'a', 'source_ids': list('abcdef'),
            'source_locations': [f'x:{i}' for i in range(6)], 'method': 'direct',
            'ambiguous': True, 'relation_role': 'tab', 'overlap_score': .2, 'evidence': ['large'] * 1000}],
        'domain': diagnostic(10)}
    out = finding_for_agent(raw, result_ref='result.json', result_pointer='/findings/2', source_sha256=None)
    assert out['metric']['value'] is None and out['metric']['value_status'] == 'NONFINITE'
    assert out['metric']['name'] == 'volume'
    assert out['region']['layer_range'] == [0, 2]
    assert out['applicability_basis'] == 'measured'
    c, = out['source_candidates']
    assert c['source_ids'] == list('abc') and c['source_locations'] == ['x:0', 'x:1', 'x:2']
    assert c['ambiguous'] and c['relation_role'] == 'tab' and c['overlap_score'] == .2
    assert 'attempts' not in json.dumps(out)
    json.dumps(out, allow_nan=False)


def test_evidence_unavailable_stale_unknown_and_no_source_guess(tmp_path):
    source, _, ref, payload = inputs(tmp_path, 1)
    source.write_text('changed = True\n')
    out = project(tmp_path, source, ref, payload)
    evidence = out['evaluation_feedback']['failures'][0]['evidence_refs'][0]
    assert evidence['source_binding'] == 'STALE' and evidence['matches_current_source'] is False
    payload = {'evidence_files': [{'path': '../outside.json'}, {'path': 'missing.json'}]}
    out = payload_for_agent(payload, workspace=tmp_path, source_path=None,
                            source_version=None, role='image_critic')
    assert out['evidence_files'][0]['reason'] == 'outside_workspace'
    assert out['evidence_files'][1]['source_binding'] == 'UNKNOWN'
    assert out['evidence_files'][1]['matches_current_source'] is None


def test_missing_attempt_count_and_old_book_snapshot(tmp_path):
    source, _, ref, _ = inputs(tmp_path, 1)
    payload = {'feedback': {'failure_feedback': [{'code': 'INPUT_GEOMETRY_INVALID', 'stage': 'input_geometry'}]}}
    out = project(tmp_path, source, None, payload)
    group, = out['evaluation_feedback']['failures']
    assert group['attempt_count'] is None
    evidence, = group['evidence_refs']
    assert evidence['source_binding'] == 'UNKNOWN' and evidence['matches_current_source'] is None
    assert (tmp_path / evidence['path']).is_file()
    files = list(tmp_path.rglob('feedback_snapshot_*'))
    assert out == project(tmp_path, source, None, out)
    assert files == list(tmp_path.rglob('feedback_snapshot_*'))


def test_image_data_excluded_from_feedback_budget(tmp_path):
    source, _, ref, payload = inputs(tmp_path, 1)
    payload['images'] = ['data:image/png;base64,' + 'a' * 1000000] * 8
    assert project(tmp_path, source, ref, payload)['images'] == payload['images']


def test_required_facts_over_budget_fail_without_raw_fallback(tmp_path):
    payload = {'checker_summary': [{'status': 'FAIL', 'required': True, 'rule_id': 'x' * 200}] * 300}
    with pytest.raises(AgentFeedbackError) as caught:
        payload_for_agent(payload, workspace=tmp_path, source_path=None, source_version=None, role='engineering')
    assert caught.value.code == 'AGENT_FEEDBACK_TOO_LARGE'
