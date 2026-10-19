"""Bounded model views of evidence. Raw controller/report data stays unchanged.

This module is an input adapter, not a checker or a repair permission authority.
Detailed arrays live in the original reports and are addressed by JSON pointer.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

FEEDBACK_LIMIT_BYTES = 32 * 1024
_TEXT_LIMIT = 240
_EXPLANATIONS = {'message', 'reason', 'rejection_reason', 'summary', 'problem',
                 'suggested_fix', 'modification_summary', 'applicability_basis',
                 'malformed_arrays', 'self_intersection_reason', 'hypothesis'}
_FAILURE_FIELDS = ('failures', 'display_failures', 'failure_feedback', 'evaluation_failures')
_HISTORY_FIELDS = ('repair_history', 'attempt_history', 'previous_image_decisions',
                   'previous_code_decisions', 'code_critic_corrections', 'previous_attempts')
_SCALARS = (str, int, float, bool, type(None))
_ID = re.compile(r'^eval:[0-9a-f]{64}$')


class AgentFeedbackError(ValueError):
    def __init__(self, code: str, details: Mapping[str, Any]):
        self.code, self.details = code, dict(details)
        super().__init__(f'{code}: {json.dumps(self.details, allow_nan=False)}')


def _first(*values):
    return next((value for value in values if value is not None), None)


def evaluation_details(raw_failure: Mapping[str, Any]) -> dict[str, Any]:
    diagnostic = raw_failure.get('diagnostic')
    diagnostic = diagnostic if isinstance(diagnostic, Mapping) else {}
    result = {key: _first(diagnostic.get(key), raw_failure.get(key)) for key in (
        'code', 'stage', 'node_path', 'operation', 'input_count', 'operation_nodes',
        'displacement_budget_mm', 'local_scale_mm', 'absolute_scale_floor_mm')}
    result['metrics'] = _first(diagnostic.get('metrics'), diagnostic.get('internal_metrics'),
                              raw_failure.get('metrics'), raw_failure.get('internal_metrics'))
    result['attempts'] = _first(diagnostic.get('attempted_actions'), diagnostic.get('attempts'),
                               raw_failure.get('attempted_actions'), raw_failure.get('attempts'))
    return result


def _json(value, *, fingerprint=False):
    """Strict data conversion; never stringify arbitrary objects/datablocks."""
    if isinstance(value, float) and not math.isfinite(value):
        return {'nonfinite': repr(value)} if fingerprint else None
    if isinstance(value, _SCALARS):
        return value
    if isinstance(value, Mapping):
        return {str(k): _json(v, fingerprint=fingerprint) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v, fingerprint=fingerprint) for v in value]
    raise AgentFeedbackError('AGENT_FEEDBACK_INVALID', {'reason': 'non_json_feedback_value',
                                                     'type': type(value).__name__})


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _hash(value):
    return hashlib.sha256(_encoded(_json(value, fingerprint=True))).hexdigest()


def _short(value):
    return value[:_TEXT_LIMIT] if isinstance(value, str) else value


def _fields(value, names):
    if not isinstance(value, Mapping):
        return {}
    result = {key: (_short(_json(value[key])) if key in _EXPLANATIONS else _json(value[key]))
              for key in names if key in value and isinstance(value[key], _SCALARS)}
    if any(isinstance(value.get(key), float) and not math.isfinite(value[key]) for key in names):
        result['value_status'] = 'NONFINITE'
    return result


def _vector(value, length=3):
    if isinstance(value, (list, tuple)) and len(value) == length and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
        return _json(value)
    return None


def _pointer(base, *tokens):
    return (base or '') + ''.join('/' + str(token).replace('~', '~0').replace('/', '~1')
                                 for token in tokens)


def _ref_fields(raw):
    if isinstance(raw, str) or raw is None:
        return {'path':raw, 'json_pointer':None}
    return {key: raw.get(key) for key in ('path', 'json_pointer', 'source_sha256',
        'source_version', 'file_sha256', 'availability', 'source_binding',
        'matches_current_source', 'reason') if key in raw and isinstance(raw[key], _SCALARS)}


def _preview_metadata(raw):
    if not isinstance(raw, Mapping):
        return {}
    allowed = {'relations', 'part_names', 'face_ids', 'feature_ids', 'source_candidates',
               'source_ids', 'source_locations', 'finding_ids', 'occurrences',
               'attempts_summary', 'evidence_refs', 'evidence_files', 'failures', 'issues', 'errors',
               'regressions', 'target_improvements', 'unavailable_checks', 'observations',
               'required_changes', 'addressed_image_issues', 'actual_changed_symbols', *_HISTORY_FIELDS}
    return {key: {**_fields(info, ('total', 'omitted_count')),
                  'full_ref': _ref_fields(info.get('full_ref'))}
            for key, info in raw.items() if key in allowed and isinstance(info, Mapping)}


def _at(document, pointer):
    for token in (pointer or '').split('/')[1:]:
        token = token.replace('~1', '/').replace('~0', '~')
        document = document[int(token)] if isinstance(document, list) else document[token]
    return document


def _preview(owner, field, full, limit, full_ref=None):
    owner[field] = full[:limit]
    info = owner.setdefault('preview_info', {})
    # Existing summaries retain original totals and references on re-projection.
    old = info.get(field, {})
    total = max(len(full), old.get('total', 0))
    if total > len(owner[field]):
        info[field] = {'total': total, 'omitted_count': total - len(owner[field]),
                       'full_ref': old.get('full_ref') or full_ref}
    elif field in info:
        del info[field]
    if not info:
        owner.pop('preview_info', None)


_METRIC_FIELDS = ('name', 'value', 'unit', 'threshold', 'comparator',
                  'relative_tolerance', 'absolute_tolerance', 'value_status')
_DETAIL_FIELDS = ('node_path', 'operation', 'input_count', 'stage', 'code',
    'source_sha256', 'physical_verdict', 'output_role', 'manufacturing_status',
    'boundary_edge_count', 'component_count', 'raw_intersection_mm3',
    'undeclared_interference_mm3', 'length_tolerance_mm', 'volume_tolerance_mm3')
_DOMAIN_FIELDS = ('failure_kind', 'geometry_repair_allowed', 'physical_verdict',
    'output_role', 'manufacturing_status', 'evaluation_code', 'evaluation_stage',
    'length_tolerance_mm', 'volume_tolerance_mm3', 'undeclared_interference_mm3',
    'raw_intersection_mm3', 'boundary_edge_count', 'component_count', 'kind',
    'part_id', 'pair_id', 'connection_id', 'part_local', 'overhang_only',
    'optimization_opportunity', 'partition_optimization', 'print_partition_editable')


def finding_for_agent(finding, *, result_ref, result_pointer, source_sha256):
    raw = finding.model_dump() if hasattr(finding, 'model_dump') else finding
    if not isinstance(raw, Mapping):
        raise AgentFeedbackError('AGENT_FEEDBACK_INVALID', {'reason': 'finding_not_mapping'})
    out = _fields(raw, ('finding_id', 'rule_id', 'category', 'applicability',
        'applicability_basis', 'required', 'repairability', 'message', 'legacy_code',
        'geometry_repair_allowed', 'failure_id', 'diagnostic_sha256', 'association_status'))
    out.update(result_ref=result_ref, result_pointer=result_pointer)
    # The caller may supply an original binding; this pure view makes no claim
    # that it is available or matches the currently assigned source.
    if source_sha256 is not None:
        out['source_sha256'] = source_sha256
    if raw.get('metric') is not None:
        out['metric'] = _fields(raw['metric'], _METRIC_FIELDS)
    region = raw.get('region')
    if isinstance(region, Mapping):
        r = _fields(region, ('kind', 'frame', 'unit'))
        for key, length in (('point', 3), ('layer_range', 2)):
            if key in region:
                r[key] = _vector(region[key], length)
        if 'bounds' in region:
            bounds = region['bounds']
            r['bounds'] = [_vector(v) for v in bounds] if isinstance(bounds, (list, tuple)) and len(bounds) == 2 else None
        r['details'] = _fields(region.get('details'), _DETAIL_FIELDS)
        r['detail_ref'] = _ref_fields(region['detail_ref']) if region.get('detail_ref') else {
            'path':result_ref, 'json_pointer':_pointer(result_pointer, 'region', 'details')}
        r['preview_info'] = _preview_metadata(region.get('preview_info'))
        for field in ('part_names', 'face_ids'):
            values = [v for v in region.get(field, []) if isinstance(v, (str, int))]
            _preview(r, field, values, 6, {'path': result_ref,
                'json_pointer': _pointer(result_pointer, 'region', field)})
        out['region'] = r
    relations = []
    for index, relation in enumerate(raw.get('relations') or []):
        r = _fields(relation, ('kind', 'frame', 'unit', 'bridge_feature_id'))
        r['distance'] = _fields(relation.get('distance'), _METRIC_FIELDS) if relation.get('distance') else None
        r['details'] = _fields(relation.get('details'), _DETAIL_FIELDS)
        r['detail_ref'] = _ref_fields(relation['detail_ref']) if relation.get('detail_ref') else {
            'path':result_ref, 'json_pointer':_pointer(result_pointer, 'relations', index, 'details')}
        r['endpoints'] = []
        for endpoint in (relation.get('endpoints') or [])[:2]:
            e = _fields(endpoint, ('role',))
            e['point'] = _vector(endpoint.get('point'))
            for key in ('part_names', 'feature_ids'):
                _preview(e, key, list(endpoint.get(key) or []), 6, {'path': result_ref,
                    'json_pointer': _pointer(result_pointer, 'relations', index, 'endpoints', len(r['endpoints']), key)})
            r['endpoints'].append(e)
        relations.append(r)
    out['preview_info'] = _preview_metadata(raw.get('preview_info'))
    _preview(out, 'relations', relations, 6, {'path': result_ref, 'json_pointer': _pointer(result_pointer, 'relations')})
    candidates = []
    for index, candidate in enumerate(raw.get('source_candidates') or []):
        c = _fields(candidate, ('feature_id', 'method', 'ambiguous', 'relation_role', 'overlap_score'))
        c['preview_info'] = _preview_metadata(candidate.get('preview_info'))
        # Pair source IDs/locations; don't independently select unrelated halves.
        for key in ('source_ids', 'source_locations'):
            _preview(c, key, list(candidate.get(key) or []), 3, {'path': result_ref,
                'json_pointer': _pointer(result_pointer, 'source_candidates', index, key)})
        c['evidence_ref'] = _ref_fields(candidate['evidence_ref']) if candidate.get('evidence_ref') else {'path': result_ref,
            'json_pointer': _pointer(result_pointer, 'source_candidates', index, 'evidence')}
        c['evidence_count'] = (len(candidate['evidence']) if isinstance(candidate.get('evidence'), list)
                               else candidate.get('evidence_count'))
        candidates.append(c)
    _preview(out, 'source_candidates', candidates, 6, {'path': result_ref,
        'json_pointer': _pointer(result_pointer, 'source_candidates')})
    domain = raw.get('domain') or raw.get('key_values') or {}
    out['key_values'] = _fields(domain, _DOMAIN_FIELDS)
    if raw.get('key_values'):
        out['key_values'].update(_fields(raw['key_values'], _DOMAIN_FIELDS))
    out['detail_ref'] = _ref_fields(raw['detail_ref']) if raw.get('detail_ref') else {'path': result_ref,
        'json_pointer': _pointer(result_pointer, 'domain')}
    _preview(out, 'evidence_refs', [_ref_fields(r) for r in raw.get('evidence_refs') or []], 6,
             {'path':result_ref, 'json_pointer':_pointer(result_pointer, 'evidence_refs')})
    return _json(out)


def _metrics(raw):
    # Only bounded scalars; geometry arrays and arbitrary nested mappings stay on disk.
    if not isinstance(raw, Mapping):
        return None
    allowed = ('valid', 'status', 'value_status', 'vertex_count', 'face_count', 'triangle_count',
        'zero_area_faces', 'zero_area_triangles', 'boundary_edges', 'open_edges',
        'nonmanifold_edges', 'nonmanifold_vertices', 'non_manifold_edges',
        'non_manifold_vertices', 'duplicate_faces', 'duplicate_triangles',
        'component_count', 'signed_volume', 'volume', 'volume_mm3', 'area',
        'orientation_consistent', 'self_intersection', 'self_intersections',
        'target_zero_normals', 'flipped_faces', 'remaining_bad_faces_total',
        'minimum_normal_dot', 'displacement_max_mm', 'max_displacement_mm')
    allowed += ('nonfinite_coordinates', 'invalid_indices', 'nonfinite_face_geometry',
        'nonfinite_geometry_measurements', 'inconsistent_edges', 'shell_components',
        'finite', 'empty', 'positive_volume', 'manifold_status', 'flipped_normals',
        'source_zero_normals', 'uncomparable_faces', 'compared_faces', 'bad_faces_total',
        'target_topology_valid', 'maximum_displacement_mm', 'surface_area', 'bad_faces',
        'orthogonal_faces', 'nonfinite_source_normals', 'nonfinite_target_normals',
        'zero_or_collapsed_faces', 'reversed_faces', 'nonfinite_faces')
    return _fields(raw, allowed)


def _attempt(raw):
    if isinstance(raw, str):
        return {'action': _short(raw)}
    if not isinstance(raw, Mapping):
        return {'status': 'UNKNOWN'}
    out = _fields(raw, ('method', 'status', 'code', 'stage', 'reason', 'rejection_reason',
        'error_bound_mm', 'displacement_budget_mm', 'max_displacement_mm', 'tolerance',
        'simplify_tolerance', 'ulp', 'remaining_bad_faces_total', 'target_zero_normals',
        'flipped_faces', 'minimum_normal_dot', 'collapse_count', 'flip_count',
        'simplify_tolerance_scene_units'))
    diagnostic = raw.get('diagnostic')
    diagnostic = diagnostic if isinstance(diagnostic, Mapping) else {}
    repair = _first(diagnostic.get('repair'), raw.get('repair'))
    out.update({k:v for k,v in _fields(diagnostic, ('code', 'stage', 'reason')).items() if k not in out})
    if isinstance(raw.get('diagnostic'), str):
        out['reason'] = _short(raw['diagnostic'])
    if isinstance(repair, Mapping):
        out['repair'] = _fields(repair, ('method', 'status', 'reason', 'rejection_reason',
            'displacement_budget_mm', 'max_displacement_mm', 'error_bound_mm',
            'remaining_bad_faces_total', 'target_zero_normals', 'flipped_faces',
            'minimum_normal_dot', 'collapse_count', 'flip_count', 'bad_face_count_before',
            'bad_face_count_after', 'maximum_displacement_scene_units',
            'surface_displacement_upper_bound_mm', 'maximum_vertex_cast_displacement_mm'))
        for key in ('method', 'status'):
            if key not in out and key in out['repair']:
                out[key] = out['repair'][key]
        if isinstance(repair.get('operations'), list):
            out['repair']['operation_count'] = len(repair['operations'])
        elif 'operation_count' in repair:
            out['repair']['operation_count'] = repair['operation_count']
        for key in ('metrics_before', 'metrics_after', 'orientation', 'orientation_after',
                    'defect_counts_before', 'defect_counts_after'):
            if key in repair:
                out['repair'][key] = _metrics(repair[key])
    for key in ('metrics', 'internal_metrics'):
        value = _first(diagnostic.get(key), raw.get(key))
        if value is not None:
            out[key] = _metrics(value)
    return out


class _Projection:
    def __init__(self, workspace, source_path, version, report_ref, evidence_files):
        self.workspace = Path(workspace).resolve()
        self.current_hash = hashlib.sha256(Path(source_path).read_bytes()).hexdigest() if source_path else None
        self.version = version
        self.directory = (Path(source_path).resolve().parent if source_path else self.workspace) / 'diagnostics' / 'agent_feedback'
        if not self.directory.is_relative_to(self.workspace):
            raise AgentFeedbackError('AGENT_FEEDBACK_INVALID', {'reason': 'source_outside_workspace'})
        self.report_ref = report_ref
        self.evidence_files = evidence_files
        self.documents = {}
        self.file_hashes = {}
        self.diagnostic_hashes = {}
        self.container_origins = {}
        self.groups = {}
        self.classifications = {}
        self.seen_rows = set()
        self.failure_count = 0

    def ref(self, supplied=None, *, pointer=None):
        supplied = supplied if isinstance(supplied, Mapping) else {'path': supplied}
        out = {key: supplied.get(key) for key in ('path', 'json_pointer', 'source_sha256',
                                               'source_version', 'file_sha256')}
        if pointer is not None:
            out['json_pointer'] = pointer
        out.update(availability='UNAVAILABLE', source_binding='UNKNOWN',
                   matches_current_source=None, reason=None)
        path = out['path']
        if path:
            target = Path(path).expanduser()
            target = (target if target.is_absolute() else self.workspace / target).resolve()
            if not target.is_relative_to(self.workspace):
                out.update(path=None, reason='outside_workspace')
            else:
                out['path'] = target.relative_to(self.workspace).as_posix()
                if target.is_file():
                    out['availability'] = 'AVAILABLE'
                    if out['file_sha256'] is None:
                        token = str(target)
                        if token not in self.file_hashes:
                            self.file_hashes[token] = hashlib.sha256(target.read_bytes()).hexdigest()
                        out['file_sha256'] = self.file_hashes[token]
                else:
                    out['reason'] = 'file_not_found'
        else:
            out['reason'] = 'outside_workspace' if supplied.get('reason') == 'outside_workspace' else 'file_not_found'
        if out['source_sha256'] and self.current_hash:
            current = out['source_sha256'] == self.current_hash
            out.update(source_binding='CURRENT' if current else 'STALE', matches_current_source=current)
        elif out['reason'] is None:
            out['reason'] = 'source_binding_unknown'
        return out

    def document(self, ref):
        if not ref or ref.get('availability') != 'AVAILABLE':
            return None
        path = ref['path']
        if path not in self.documents:
            try:
                self.documents[path] = json.loads((self.workspace / path).read_text())
            except (OSError, ValueError):
                self.documents[path] = None
        return self.documents[path]

    def diagnostic_hash(self, raw):
        token = id(raw)
        if token not in self.diagnostic_hashes:
            self.diagnostic_hashes[token] = (raw, _hash(evaluation_details(raw)))
        return self.diagnostic_hashes[token][1]

    def snapshot(self, content, prefix):
        # Raw legacy evidence may contain nonfinite diagnostic measurements.
        # Keep their original JSON values on disk; only model views replace
        # them by null/value_status. No default=str or object coercion.
        data = json.dumps(content, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=True).encode('utf-8')
        path = self.directory / f'{prefix}_{hashlib.sha256(data).hexdigest()}.json'
        self.directory.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != data:
                raise AgentFeedbackError('AGENT_FEEDBACK_INVALID', {'reason': 'snapshot_hash_mismatch'})
        else:
            path.write_bytes(data)
        return self.ref({'path': str(path)})

    def source_ref(self, raw, container, field=None, ordinal=None, container_identity=''):
        supplied = container.get('geometry_report_ref') or container.get('report_ref') or self.report_ref
        if not supplied and container.get('report_path'):
            supplied = next((r for r in self.evidence_files if r.get('path') == container['report_path']),
                            {'path': container['report_path']})
        supplied = dict(supplied or {})
        supplied_hash = supplied.get('source_sha256')
        if raw.get('source_sha256') is not None:
            supplied['source_sha256'] = raw['source_sha256']
            if raw['source_sha256'] != supplied_hash:
                supplied['source_version'] = raw.get('source_version')
        if raw.get('source_version') is not None:
            supplied['source_version'] = raw['source_version']
        base = self.ref(supplied)
        document = self.document(base)
        # Source provenance can be read from the original report, never assumed
        # from the currently assigned source or an existing file's mere presence.
        if isinstance(document, Mapping):
            document_hash = _first(document.get('source_sha256'),
                                  (document.get('assumptions') or {}).get('source_sha256'))
            base = self.ref({**base,
                'source_sha256': _first(supplied.get('source_sha256'), document.get('source_sha256'),
                    (document.get('assumptions') or {}).get('source_sha256')),
                'source_version': _first(supplied.get('source_version'), document.get('source_version'))})
            keys = [field] if field in document else []
            keys += [key for key in _FAILURE_FIELDS if key not in keys]
            for key in keys:
                if raw.get('source_sha256') and raw['source_sha256'] != document_hash:
                    continue
                originals = list(enumerate(document.get(key) or []))
                if ordinal is not None:
                    originals.sort(key=lambda item: item[0] != ordinal)
                for index, original in originals:
                    if isinstance(original, Mapping) and self.diagnostic_hash(original) == self.diagnostic_hash(raw) and original.get('part_id') == raw.get('part_id') and original.get('file') == raw.get('file'):
                        return self.ref(base, pointer=_pointer('', key, index))
        # Old in-memory/book evidence without a matching report gets a fixed
        # content-hash snapshot. Missing provenance remains UNKNOWN.
        rows = container.get(field) if field else None
        content = {'failures': rows} if isinstance(rows, list) else {'failure': raw}
        origin = supplied.get('path') or container_identity
        content['container_identity'] = origin
        snapshot = self.snapshot(content, 'feedback_snapshot')
        reference = self.ref({**snapshot,
            'source_sha256': _first(raw.get('source_sha256'), supplied.get('source_sha256')),
            'source_version': _first(raw.get('source_version'), supplied.get('source_version'))},
            pointer=_pointer('/failures', ordinal) if isinstance(rows, list) else '/failure')
        self.container_origins[(reference['path'], reference['json_pointer'])] = origin
        return reference

    def add(self, raw, ref, *, finding_id=None, classification_origin='diagnostic'):
        details = evaluation_details(raw)
        is_source = raw.get('code') == 'GENERATED_SOURCE_EXECUTION_FAILED'
        diagnostic_hash = self.diagnostic_hash(raw)
        file = _first(raw.get('file'), raw.get('glb'), raw.get('path'))
        representation = _first(raw.get('target_representation'), raw.get('representation'),
                                Path(file).suffix if isinstance(file, str) else None)
        key = [ref.get('source_sha256'), ref.get('source_version'), raw.get('part_id'),
               details['node_path'], details['operation'], details['code'], details['stage'],
               representation, diagnostic_hash]
        if not ref.get('source_sha256'):
            key.append(self.container_origins.get((ref.get('path'), ref.get('json_pointer')), ref.get('path')))
        if is_source:
            key = [ref.get('source_sha256'), details['stage'], raw.get('exception_type'),
                   raw.get('message'), raw.get('source_locations', [])]
        failure_id = 'eval:' + _hash(key)
        group = self.groups.get(failure_id)
        if group is None:
            attempts = details['attempts']
            group = {'failure_id': failure_id, 'diagnostic_sha256': diagnostic_hash,
                **_fields(raw, ('part_id', 'failure_kind', 'geometry_repair_allowed')),
                **_fields(details, ('code', 'stage', 'node_path', 'operation', 'input_count',
                    'displacement_budget_mm', 'local_scale_mm', 'absolute_scale_floor_mm')),
                'target_representation': representation, 'physical_verdict': 'NOT_EVALUATED',
                'internal_metrics': _metrics(details['metrics']),
                'attempt_count': len(attempts) if attempts is not None else None,
                'attempts_summary': [_attempt(a) for a in attempts] if attempts is not None else [],
                'finding_ids': [], 'occurrences': [], 'evidence_refs': []}
            self.groups[failure_id] = group
        if is_source:
            group.update(exception_type=raw.get('exception_type'), message=str(raw.get('message',''))[:512],
                repair_target=raw.get('repair_target'), source_locations=[{
                    **_fields(f, ('line','function')), 'path':self.ref({'path':f.get('path')})['path']}
                    for f in raw.get('source_locations', [])[:3]])
        # Classification is controller evidence, not a consequence of seeing a
        # diagnostic first. Never OR permissions or infer them from error codes.
        priority = 1 if classification_origin == 'controller' else 0
        classified = self.classifications.setdefault(failure_id, {})
        for field in ('failure_kind', 'geometry_repair_allowed'):
            value = raw.get(field)
            if value is None:
                continue
            previous = classified.get(field)
            if previous is not None and previous[0] == priority and previous[1] != value:
                raise AgentFeedbackError('AGENT_FEEDBACK_INVALID', {
                    'reason':'conflicting_failure_classification', 'failure_id':failure_id,
                    'field':field, 'classification_origin':classification_origin})
            if previous is None or priority > previous[0]:
                classified[field] = (priority, value)
                group[field] = value
        if finding_id and finding_id not in group['finding_ids']:
            group['finding_ids'].append(finding_id)
        occurrence = _fields(raw, ('output_role', 'file', 'glb', 'path', 'status', 'cached_conversion',
                                  'manufacturing_status', 'display_status'))
        if 'output_role' not in occurrence:
            pointer = ref.get('json_pointer') or ''
            if pointer.startswith('/display_failures/'):
                occurrence['output_role'] = 'display'
            elif pointer.startswith('/failures/'):
                occurrence['output_role'] = 'manufacturing'
        if occurrence not in group['occurrences']:
            group['occurrences'].append(occurrence)
        if ref not in group['evidence_refs']:
            group['evidence_refs'].append(ref)
        row_identity = [ref.get('source_sha256'), ref.get('source_version'), failure_id, occurrence]
        if not ref.get('source_sha256'):
            row_identity.extend((ref.get('path'), ref.get('json_pointer')))
        row_hash = _hash(row_identity)
        if row_hash not in self.seen_rows:
            self.seen_rows.add(row_hash)
            self.failure_count += 1
        group['occurrence_count'] = len(group['occurrences'])
        return failure_id

    def findings(self, raw_findings):
        result = []
        for raw in raw_findings or []:
            row = finding_for_agent(raw, result_ref=raw.get('result_ref'),
                result_pointer=raw.get('result_pointer'), source_sha256=raw.get('source_sha256'))
            ref = self.ref({'path': row.get('result_ref'), 'json_pointer': row.get('result_pointer'),
                'source_sha256': row.get('source_sha256')})
            document = self.document(ref)
            original = None
            if document is not None:
                try:
                    original = _at(document, ref['json_pointer'])
                except (KeyError, IndexError, TypeError, ValueError):
                    pass
            if isinstance(document, Mapping):
                binding_hash = _first(ref.get('source_sha256'),
                    (document.get('assumptions') or {}).get('source_sha256'), document.get('source_sha256'))
                ref = self.ref({**ref, 'source_sha256': _first(ref.get('source_sha256'),
                    (document.get('assumptions') or {}).get('source_sha256'), document.get('source_sha256')),
                    'source_version': _first(ref.get('source_version'), document.get('source_version'),
                        (self.report_ref or {}).get('source_version') if binding_hash and
                        binding_hash == (self.report_ref or {}).get('source_sha256') else None)})
            original_ref = ref
            if not isinstance(original, Mapping) and isinstance(raw.get('domain'), Mapping) and raw['domain']:
                # This in-memory finding may be the only complete evidence left
                # after recovery. Save it before projection; retain the missing
                # original reference separately rather than claiming it exists.
                snapshot = self.snapshot({'finding':raw}, 'finding_snapshot')
                ref = self.ref({**snapshot, 'source_sha256':original_ref.get('source_sha256'),
                    'source_version':original_ref.get('source_version')}, pointer='/finding')
                row = finding_for_agent(raw, result_ref=ref['path'], result_pointer='/finding',
                    source_sha256=ref.get('source_sha256'))
                row.update(result_ref=original_ref.get('path'), result_pointer=original_ref.get('json_pointer'))
                row['evidence_refs'] = [*row.get('evidence_refs', []), original_ref, ref]
            domain = original.get('domain') if isinstance(original, Mapping) else raw.get('domain')
            evaluation = (row.get('finding_id', '').startswith('assembly_mesh_evaluation:') or
                          isinstance(domain, Mapping) and domain.get('failure_kind') == 'candidate_evaluation')
            if evaluation:
                if row.get('failure_id') in self.groups:
                    row['association_status'] = 'RESOLVED'
                elif isinstance(domain, Mapping) and domain:
                    row['failure_id'] = self.add(domain, self.ref(ref,
                        pointer=_pointer(ref.get('json_pointer'), 'domain')), finding_id=row.get('finding_id'),
                        classification_origin='controller')
                    row['association_status'] = 'RESOLVED'
                else:
                    row['association_status'] = 'UNRESOLVED'
            row['evidence_refs'] = [self.ref(r) for r in row.get('evidence_refs', [])]
            def bind_refs(container):
                for key, value in list(container.items()):
                    if key in {'detail_ref', 'evidence_ref', 'full_ref'} and isinstance(value, Mapping):
                        container[key] = self.ref({**ref, **value,
                            'source_sha256':_first(value.get('source_sha256'), ref.get('source_sha256')),
                            'source_version':_first(value.get('source_version'), ref.get('source_version'))})
                    elif isinstance(value, dict):
                        bind_refs(value)
                    elif isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict):
                                bind_refs(item)
            bind_refs(row)
            result.append(row)
        return result


def _history(rows):
    result = []
    for row in rows or []:
        if isinstance(row, str):
            result.append(_short(row))
            continue
        if not isinstance(row, Mapping):
            continue
        out = _fields(row, ('round', 'proposal_id', 'candidate', 'accepted', 'status', 'reason',
            'approved', 'severity', 'modification_summary', 'image_critic_corrections',
            'parent_version', 'action', 'hypothesis',
            'addressed_image_issues', 'edit_purpose', 'partition_adopted', 'source_sha256', 'source_version'))
        if 'source_version' not in out and isinstance(row.get('version'), str):
            out['source_version'] = row['version']
        if isinstance(row.get('tool_statuses'), Mapping):
            out['tool_statuses'] = {k:v for k,v in row['tool_statuses'].items() if isinstance(v,str) or v is None}
        if 'failure_codes' in row:
            out['failure_codes'] = [v for v in row.get('failure_codes',[])[:6] if isinstance(v,str)]
        if isinstance(row.get('execution_error'), Mapping):
            out['execution_error'] = _fields(row['execution_error'], ('exception_type','message'))
        if isinstance(row.get('_history_meta'), Mapping):
            out['_history_meta'] = _fields(row['_history_meta'], ('round', 'source_sha256', 'source_version'))
        if row.get('preview_info'):
            out['preview_info'] = _preview_metadata(row['preview_info'])
        for key, names in (
                ('overhang_comparison', ('conclusion', 'baseline_mm2', 'candidate_mm2',
                    'reduction_mm2', 'tolerance_mm2', 'comparison_valid')),
                ('total_area', ('before_mm2', 'candidate_mm2', 'comparison_valid')),
                ('local_area_change', ('status', 'reason'))):
            if key in row:
                out[key] = _fields(row[key], names) if isinstance(row[key], Mapping) else None
        if isinstance(row.get('modification_summary'), Mapping):
            out['modification_summary'] = _fields(row['modification_summary'],
                ('proposed_action', 'summary', 'scope_validation'))
            _preview(out['modification_summary'], 'actual_changed_symbols',
                list(row['modification_summary'].get('actual_changed_symbols') or []), 6,
                row.get('candidate_report'))
        if row.get('candidate_report') is not None:
            out['candidate_report'] = row['candidate_report']
        for key in ('observations', 'required_changes', 'image_critic_corrections', 'addressed_image_issues'):
            if isinstance(row.get(key), list):
                out[key] = [_short(v) for v in row[key][:3] if isinstance(v, str)]
        for key in ('target_improvements', 'regressions', 'errors', 'unavailable_checks', 'issues'):
            values = row.get(key)
            if values:
                out[key] = [_fields(v, ('finding_id', 'code', 'status', 'message', 'severity', 'aspect',
                    'summary', 'target', 'problem', 'suggested_fix', 'geometry_repair_allowed')) if isinstance(v, Mapping) else _short(v)
                    for v in (values if isinstance(values, list) else [])[:3]]
        result.append(out)
    return result


def _support_for_agent(value):
    """Finite explicit support view. Detailed regions remain in the item files."""
    if not isinstance(value, Mapping):
        return None
    support_fields = ('status','reason','support_volume_mm3','mesh_volume_mm3','nsv',
        'risky_projected_area_mm2','risky_face_count','ray_backend','total_support_volume_mm3',
        'total_volume_mm3','nsv_total','total_risky_projected_area_mm2','value_status')
    out = _fields(value, ('source_version','source_sha256','measurement_config_sha256','part_count',
        'tolerance_mm3','interface_count','topology_status','standing_status','acceptance_rule','scale_note','length_unit','value_status'))
    out['assembled_size_mm'] = _vector(value.get('assembled_size_mm'))
    out['support'] = _fields(value.get('support'),support_fields)
    if isinstance(value.get('comparison_baseline'),Mapping):
        base=value['comparison_baseline']
        out['comparison_baseline'] = _fields(base,('source_version','source_sha256',
            'measurement_config_sha256','part_count','tolerance_mm3'))
        out['comparison_baseline']['support'] = _fields(base.get('support'),support_fields)
    out['items'] = []
    for row in value.get('items',[]):
        item = _fields(row,('part_id',))
        item['support'] = _fields(row.get('support'),support_fields)
        bounds = row.get('bounds_mm')
        item['bounds_mm'] = ([_vector(v) for v in bounds]
            if isinstance(bounds,list) and len(bounds)==2 else None)
        matrix = row.get('print_transform_mm')
        if isinstance(matrix,list) and len(matrix)==4 and all(isinstance(r,list) and len(r)==4 for r in matrix):
            item['print_transform_mm'] = [[_json(x) if isinstance(x,(int,float)) else None for x in r] for r in matrix]
        else:
            item['print_transform_mm'] = None
        item['largest_regions'] = []
        for region in row.get('largest_regions',[])[:3]:
            clean = _fields(region,('area_mm2',))
            for key, shape in (('centroid_mm',(3,)),('bounds_mm',(2,3))):
                v = region.get(key)
                if isinstance(v,list) and len(v)==shape[0]:
                    if len(shape)==1 and all(isinstance(x,(int,float)) for x in v):
                        clean[key] = _json(v)
                    elif len(shape)==2 and all(isinstance(r,list) and len(r)==shape[1] and all(isinstance(x,(int,float)) for x in r) for r in v):
                        clean[key] = _json(v)
            item['largest_regions'].append(clean)
        item['evidence_ref'] = _fields(row.get('evidence_ref'),('path','json_pointer','source_sha256','source_version'))
        out['items'].append(item)
    return out


def _remove_support_score_fields(value):
    # Model view only. Raw reports and controller scoring remain intact.
    forbidden = {'partition_objective','partition_guidance','partition_measurement',
        'partition_change_vs_baseline','gap_voxels','reference_voxels','alpha','score',
        'gap_volume_mm3','candidate_score','score_delta'}
    if isinstance(value,dict):
        for key in list(value):
            if key in forbidden:
                value.pop(key)
            elif isinstance(value[key],(dict,list)):
                _remove_support_score_fields(value[key])
    elif isinstance(value,list):
        for row in value:
            _remove_support_score_fields(row)


def payload_for_agent(payload, *, workspace, source_path, source_version, role,
                      report_ref=None, evidence_files=()):
    if role not in {'engineering', 'code_critic', 'image_critic', 'coder', 'debugger'}:
        raise AgentFeedbackError('AGENT_FEEDBACK_INVALID', {'reason': 'invalid_role'})
    try:
        view = _Projection(workspace, source_path, source_version, report_ref, evidence_files)
        out = deepcopy(dict(payload))
        nsv = (isinstance(out.get('support_evidence'),Mapping) or
            isinstance((out.get('feedback') or {}).get('support_evidence'),Mapping) or
            (out.get('fixed_assembly') or {}).get('physics',{}).get('overhang',{}).get('support_metric')=='nsv')
        if nsv:
            _remove_support_score_fields(out)
        full_histories = {}
        existing = out.get('evaluation_feedback')
        existing_index_ref = None
        if isinstance(existing, Mapping):
            existing_groups = existing.get('failures') or []
            full_ref = existing.get('preview_info',{}).get('failures',{}).get('full_ref')
            if isinstance(full_ref, Mapping):
                existing_index_ref = view.ref(full_ref)
                try:
                    full = _at(view.document(existing_index_ref), existing_index_ref.get('json_pointer'))
                except (KeyError,IndexError,TypeError,ValueError):
                    full = None
                if isinstance(full,list):
                    existing_groups = full
            for group in existing_groups:
                if _ID.fullmatch(str(group.get('failure_id', ''))):
                    # A projected input has no raw rows to re-fingerprint.
                    clean = _fields(group, ('failure_id', 'diagnostic_sha256', 'part_id', 'failure_kind',
                        'geometry_repair_allowed', 'code', 'stage', 'node_path', 'operation', 'input_count',
                        'displacement_budget_mm', 'local_scale_mm', 'absolute_scale_floor_mm',
                        'target_representation', 'physical_verdict', 'attempt_count', 'occurrence_count',
                        'exception_type', 'repair_target'))
                    if group.get('exception_type'):
                        clean.update(message=str(group.get('message',''))[:512],
                            source_locations=[_fields(f, ('path','line','function')) for f in group.get('source_locations',[])[:3]])
                    clean.update(internal_metrics=_metrics(group.get('internal_metrics')),
                        attempts_summary=[_attempt(a) for a in group.get('attempts_summary') or []],
                        finding_ids=[v for v in group.get('finding_ids') or [] if isinstance(v, str)],
                        occurrences=[_fields(o, ('output_role', 'file', 'glb', 'path', 'status', 'cached_conversion',
                            'manufacturing_status', 'display_status')) for o in group.get('occurrences') or []],
                        evidence_refs=[view.ref(r) for r in group.get('evidence_refs') or []],
                        preview_info=_preview_metadata(group.get('preview_info')))
                    view.groups[clean['failure_id']] = clean
            view.failure_count = existing.get('failure_count', 0)

        def process(container, container_identity=''):
            if not isinstance(container, dict):
                return
            if nsv and 'support_evidence' in container:
                container['support_evidence'] = _support_for_agent(container['support_evidence'])
            failure_ids = list(container.get('evaluation_failure_ids') or [])
            execution_failure = container.pop('execution_failure', None)
            if isinstance(execution_failure, Mapping):
                ref = view.ref(execution_failure.get('evidence_ref') or container.get('execution_error_ref'))
                identity = view.add(execution_failure, ref)
                if identity not in failure_ids:
                    failure_ids.append(identity)
            for field in _FAILURE_FIELDS:
                rows = container.get(field)
                if isinstance(rows, list):
                    for ordinal, row in enumerate(rows):
                        if not isinstance(row, Mapping):
                            continue
                        ref = view.source_ref(row, container, field, ordinal, container_identity)
                        identity = view.add(row, ref, classification_origin=(
                            'controller' if field in {'failure_feedback', 'evaluation_failures'} else 'diagnostic'))
                        if identity not in failure_ids:
                            failure_ids.append(identity)
                container.pop(field, None)
            if failure_ids:
                container['evaluation_failure_ids'] = failure_ids
            for key in ('typed_findings', 'checker_evidence'):
                if isinstance(container.get(key), list):
                    container[key] = view.findings(container[key])
            for key in _HISTORY_FIELDS:
                if isinstance(container.get(key), list):
                    original = container[key]
                    full_histories[(id(container), key)] = original
                    projected = _history(original)
                    ref = container.get(key + '_ref')
                    nested_lists = ('issues', 'errors', 'regressions', 'target_improvements',
                        'unavailable_checks', 'observations', 'required_changes',
                        'image_critic_corrections', 'addressed_image_issues')
                    nested_cut = any(isinstance(row, Mapping) and any(
                        isinstance(row.get(field), list) and len(row[field]) > 3
                        for field in nested_lists) for row in original)
                    if (len(projected) > 3 or nested_cut) and not isinstance(ref, Mapping):
                        snap = view.snapshot({'history': original}, 'history_snapshot')
                        ref = {**snap, 'json_pointer': '/history'}
                    if nested_cut:
                        for index, (raw_row, row) in enumerate(zip(original, projected)):
                            if not isinstance(raw_row, Mapping) or not isinstance(row, dict):
                                continue
                            for field in nested_lists:
                                if isinstance(raw_row.get(field), list) and len(raw_row[field]) > len(row.get(field) or []):
                                    row.setdefault('preview_info', {})[field] = {
                                        'total':len(raw_row[field]),
                                        'omitted_count':len(raw_row[field]) - len(row.get(field) or []),
                                        'full_ref':{**ref, 'json_pointer':_pointer(ref.get('json_pointer'), index, field)}}
                    container.setdefault('preview_info', {}).update(
                        deepcopy(container.get('preview_info', {})))
                    # Histories preview the latest decisions, with original provenance.
                    total = max(len(projected), container.get('preview_info', {}).get(key, {}).get('total', 0))
                    container[key] = projected[-3:]
                    if total > len(container[key]):
                        container['preview_info'][key] = {'total': total,
                            'omitted_count': total - len(container[key]), 'full_ref': ref or container['preview_info'][key].get('full_ref')}
            if isinstance(container.get('duplicate_source'), Mapping):
                raw = container['duplicate_source']
                container['duplicate_source'] = _fields(raw, ('status','duplicate_of_version','source_sha256','executed'))
                container['duplicate_source']['previous_result_ref'] = view.ref(raw.get('previous_result_ref'))
                if isinstance(raw.get('previous_failure_summary'),Mapping):
                    container['duplicate_source']['previous_failure_summary'] = _fields(raw['previous_failure_summary'],(
                        'code','stage','exception_type','evaluation_code','evaluation_stage','node_path',
                        'source_sha256','source_version','message'))
            for key in ('render_issue', 'execution_error', 'error'):
                if isinstance(container.get(key), Mapping):
                    raw = container[key]
                    container[key] = _fields(raw, ('code', 'stage', 'type', 'status', 'reason', 'message',
                        'geometry_repair_allowed', 'report_path', 'path', 'source_sha256', 'source_version'))
                    if _json(raw) != container[key]:
                        snap = view.snapshot({key:raw}, 'error_snapshot')
                        container[key + '_evidence_ref'] = {**snap, 'json_pointer':_pointer('', key)}
                elif isinstance(container.get(key), str):
                    raw = container[key]
                    container[key] = _short(raw)
                    if len(raw) > _TEXT_LIMIT and key + '_evidence_ref' not in container:
                        container[key + '_evidence_ref'] = view.snapshot({key: raw}, 'error_snapshot')
            if isinstance(container.get('evidence_files'), list):
                container['evidence_files'] = [view.ref(r) for r in container['evidence_files']]
            for key in ('feedback', 'assembly_context', 'pending_reviews', 'engineering_feedback'):
                process(container.get(key), _pointer(container_identity, key))
            if isinstance(container.get('checker_evidence'), dict):
                process(container['checker_evidence'], _pointer(container_identity, 'checker_evidence'))
            if not container.get('preview_info'):
                container.pop('preview_info', None)

        process(out)
        if view.groups or existing:
            groups = list(view.groups.values())
            # Keep the pre-preview mapping for any later budget reduction. A
            # snapshot of an already shortened list cannot become its full_ref.
            full_groups = deepcopy(groups)
            feedback = {'schema_version': 1, 'source_sha256': view.current_hash,
                'source_version': source_version, 'failure_count': view.failure_count,
                'unique_failure_count': max(len(groups), (existing or {}).get('unique_failure_count', 0)),
                'failures': groups, 'preview_info': _preview_metadata((existing or {}).get('preview_info'))}
            # Canonical index is needed only for aggregate lists without a single
            # original array. It contains summaries/refs, never per-face arrays.
            needs_index = len(groups) > 6 or any(len(g.get(k) or []) > limit for g in groups
                for k, limit in (('finding_ids', 6), ('occurrences', 6), ('attempts_summary', 3), ('evidence_refs', 6)))
            index_ref = existing_index_ref or (view.snapshot({'failures': full_groups}, 'canonical_feedback') if needs_index else None)
            for index, group in enumerate(groups):
                for key, limit in (('finding_ids', 6), ('occurrences', 6), ('attempts_summary', 3), ('evidence_refs', 6)):
                    _preview(group, key, list(group.get(key) or []), limit,
                        {**index_ref, 'json_pointer': _pointer('', 'failures', index, key)} if index_ref else None)
            _preview(feedback, 'failures', groups, 6,
                     {**index_ref, 'json_pointer': '/failures'} if index_ref else None)
            out['evaluation_feedback'] = feedback

        def feedback_only(container):
            if not isinstance(container, Mapping):
                return {}
            names = ('evaluation_feedback', 'evaluation_failure_ids', 'typed_findings', 'checker_evidence',
                'checker_summary', 'preview_info', 'evidence_files', 'render_issue', 'execution_error',
                'support_evidence', 'support_baseline_version', 'support_round', 'mode_a_pose_check',
                'error', 'duplicate_source', 'error_evidence_ref', 'execution_error_evidence_ref', 'render_issue_evidence_ref', *_HISTORY_FIELDS)
            selected = {k: container[k] for k in names if k in container}
            for key in ('feedback', 'assembly_context', 'pending_reviews', 'engineering_feedback'):
                if key in container:
                    selected[key] = feedback_only(container[key])
            return selected

        def measure():
            return len(_encoded(feedback_only(out)))

        # Reduce explanations, never a verdict/permission or current metric.
        for history_limit, candidate_limit, attempt_limit, occurrence_limit, group_limit in (
                (1, 6, 3, 6, 6), (0, 6, 3, 6, 6), (0, 3, 3, 6, 6),
                (0, 1, 3, 6, 6), (0, 1, 1, 6, 6), (0, 1, 1, 3, 6),
                (0, 1, 1, 1, 6), (0, 1, 1, 1, 3), (0, 1, 1, 1, 1)):
            if measure() <= FEEDBACK_LIMIT_BYTES:
                break
            def reduce(container):
                support = container.get('support_evidence')
                if isinstance(support,dict):
                    for item in support.get('items',[]):
                        item['largest_regions'] = item.get('largest_regions',[])[:candidate_limit]
                for key in _HISTORY_FIELDS:
                    limit = max(1,history_limit) if key == 'repair_history' else history_limit
                    if isinstance(container.get(key), list) and len(container[key]) > limit:
                        info = container.setdefault('preview_info', {}).setdefault(key, {
                            'total': len(container[key]), 'full_ref': container.get(key + '_ref')})
                        if not info.get('full_ref'):
                            snap = view.snapshot({'history':full_histories[(id(container), key)]}, 'history_snapshot')
                            info['full_ref'] = {**snap, 'json_pointer':'/history'}
                        container[key] = container[key][-limit:] if limit else []
                        info['omitted_count'] = info['total'] - len(container[key])
                # Evidence directory entries can be numerous and carry long
                # paths/hashes. Keep the full directory addressable, while the
                # current findings retain their own exact detail references.
                refs = container.get('evidence_files')
                if candidate_limit == 1 and isinstance(refs, list) and len(refs) > 3:
                    ref = container.get('preview_info', {}).get('evidence_files', {}).get('full_ref')
                    if not ref:
                        snap = view.snapshot({'evidence_files': refs}, 'evidence_directory')
                        ref = {**snap, 'json_pointer': '/evidence_files'}
                    _preview(container, 'evidence_files', refs, 3, ref)
                for f in container.get('typed_findings') or []:
                    candidates = f.get('source_candidates', [])
                    ref = None
                    if len(candidates) > candidate_limit and not f.get('preview_info', {}).get('source_candidates', {}).get('full_ref'):
                        binding = f.get('detail_ref') or {}
                        ref = view.ref({'path':f.get('result_ref'),
                            'json_pointer':_pointer(f.get('result_pointer'), 'source_candidates'),
                            'source_sha256':_first(f.get('source_sha256'), binding.get('source_sha256')),
                            'source_version':binding.get('source_version')})
                        document = view.document(ref)
                        try:
                            full = _at(document, ref['json_pointer'])
                        except (KeyError, IndexError, TypeError, ValueError):
                            full = None
                        if not isinstance(full, list) and str(binding.get('json_pointer', '')).endswith('/domain'):
                            # A recovery snapshot retains the original finding,
                            # while result_ref still names the missing report.
                            saved = view.ref(binding, pointer=_pointer(
                                binding['json_pointer'][:-len('/domain')], 'source_candidates'))
                            try:
                                full = _at(view.document(saved), saved['json_pointer'])
                            except (KeyError, IndexError, TypeError, ValueError):
                                full = None
                            if isinstance(full, list):
                                ref = saved
                        if not isinstance(full, list) or len(full) != len(candidates):
                            # No report exists; these candidates have not yet
                            # been shortened and can be preserved completely.
                            snap = view.snapshot({'source_candidates':candidates}, 'candidates_snapshot')
                            ref = view.ref({**snap, 'source_sha256':ref.get('source_sha256'),
                                'source_version':ref.get('source_version')}, pointer='/source_candidates')
                    _preview(f, 'source_candidates', candidates, candidate_limit, ref)
                for key in ('feedback', 'assembly_context', 'pending_reviews', 'engineering_feedback'):
                    if isinstance(container.get(key), dict):
                        reduce(container[key])
                if isinstance(container.get('checker_evidence'), dict):
                    reduce(container['checker_evidence'])
            reduce(out)
            feedback = out.get('evaluation_feedback')
            if feedback:
                # Retain full canonical mapping before any budget-driven reduction.
                if not feedback.get('preview_info', {}).get('failures', {}).get('full_ref'):
                    ref = index_ref or view.snapshot({'failures': full_groups}, 'canonical_feedback')
                    feedback.setdefault('preview_info', {})['failures'] = {
                        'total': feedback['unique_failure_count'], 'omitted_count': 0,
                        'full_ref': {**ref, 'json_pointer': '/failures'}}
                ref = feedback['preview_info']['failures']['full_ref']
                for index, group in enumerate(feedback['failures']):
                    for key, limit in (('attempts_summary', attempt_limit), ('finding_ids', occurrence_limit), ('occurrences', occurrence_limit)):
                        _preview(group, key, group.get(key, []), limit,
                            {**ref, 'json_pointer': _pointer('/failures', index, key)})
                _preview(feedback, 'failures', feedback['failures'], group_limit, ref)
        if measure() > FEEDBACK_LIMIT_BYTES:
            def minimal_history(container):
                if not isinstance(container,dict):
                    return
                rows = container.get('repair_history')
                if rows:
                    row = rows[-1]
                    container['repair_history'] = [{k:row[k] for k in (
                        'source_sha256','source_version','action','reason','failure_codes','execution_error') if k in row}]
                    info = container.setdefault('preview_info',{}).setdefault('repair_history',{})
                    if not info.get('full_ref'):
                        snap = view.snapshot({'history':full_histories[(id(container),'repair_history')]},'history_snapshot')
                        info['full_ref'] = {**snap,'json_pointer':'/history'}
                    info.update(total=info.get('total',len(rows)),omitted_count=info.get('total',len(rows))-1)
                for key in ('feedback','assembly_context','pending_reviews','engineering_feedback','checker_evidence'):
                    minimal_history(container.get(key))
            minimal_history(out)
        actual = measure()
        if actual > FEEDBACK_LIMIT_BYTES:
            raise AgentFeedbackError('AGENT_FEEDBACK_TOO_LARGE', {'actual_bytes': actual,
                                                               'limit_bytes': FEEDBACK_LIMIT_BYTES})
        # Feedback only is strict JSON. Images and task/source fields remain as
        # delivered by the existing workflow, outside this engineering budget.
        _encoded(feedback_only(out))
        return out
    except AgentFeedbackError:
        raise
    except (TypeError, ValueError, KeyError, OSError) as error:
        raise AgentFeedbackError('AGENT_FEEDBACK_INVALID', {'reason': type(error).__name__}) from error


def feedback_observation(payload):
    """Small recorder metadata, kept outside the idempotent payload."""
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    evaluation = payload.get('evaluation_feedback') or {}
    return {'text_chars': len(text), 'text_utf8_bytes': len(text.encode('utf-8')),
            'evaluation_feedback_bytes': len(_encoded(evaluation)) if evaluation else 0,
            'unique_failure_count': evaluation.get('unique_failure_count', 0)}
