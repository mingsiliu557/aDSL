"""Replay only the feedback adapter on copied evidence; never run an Agent.

An optional --adapter-source permits a statistics-only baseline snapshot to be
compared with the new adapter at the same --output/workspace path. Inputs are
explicit descriptions, not automatically reconstructed historical workflows.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
KINDS = {'captured_pre_projection', 'reconstructed_pre_projection', 'captured_post_projection'}


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def referenced_paths(value):
    """Find explicit file references, without treating arbitrary text as a path."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(child, str) and (key in {
                    'path', 'result_ref', 'report_path', 'source_path', 'assigned_source',
                    'report_ref', 'detail_ref', 'geometry_report_ref', 'execution_error_ref',
                    'error_evidence_ref', 'previous_result_ref'}):
                yield child
            elif isinstance(child, (Mapping, list)):
                yield from referenced_paths(child)
    elif isinstance(value, list):
        for child in value:
            yield from referenced_paths(child)


def prepare_copy(original, isolated, payload, description, source):
    marker = isolated / '.feedback_replay_workspace'
    if isolated.is_symlink():
        raise ValueError('isolated workspace must not be a symlink')
    if isolated.exists():
        if not marker.is_file() or marker.read_text() != str(original):
            raise ValueError('refuse to reset an unrecognized output workspace')
        shutil.rmtree(isolated)
    isolated.mkdir(parents=True)
    marker.write_text(str(original))
    queue = [str(source), *referenced_paths(payload), *referenced_paths(description.get('report_ref')),
             *referenced_paths(description.get('evidence_files', []))]
    copied, unavailable = {}, {}
    while queue:
        supplied = queue.pop()
        p = Path(supplied).expanduser()
        candidate = p if p.is_absolute() else original / p
        resolved = candidate.resolve()
        if not resolved.is_relative_to(original):
            unavailable[supplied] = 'outside_workspace'
            continue
        rel = resolved.relative_to(original).as_posix()
        if rel in copied or supplied in unavailable:
            continue
        if candidate.is_symlink() or not resolved.is_file():
            unavailable[supplied] = 'symlink_not_copied' if candidate.is_symlink() else 'file_not_found'
            continue
        target = isolated / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(resolved, target)
        copied[rel] = file_hash(resolved)
        if resolved.suffix == '.json':
            try:
                queue.extend(referenced_paths(load(resolved)))
            except (ValueError, UnicodeError):
                unavailable[supplied + '#document'] = 'not_readable_json'
    return copied, unavailable


def load_adapter(path):
    name = 'adsl.agents._feedback_replay_' + file_hash(path)[:16]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def bind_isolated_refs(adapter, original, isolated):
    """Replay-only path resolver; copied document bytes/hashes stay unchanged.

    Both baseline and new adapter receive the identical resolver. Outside-source
    paths stay outside, missing paths stay missing. Snapshot writes are confined
    by _Projection to the supplied isolated source directory.
    """
    base = adapter._Projection
    class IsolatedProjection(base):
        def ref(self, supplied=None, *, pointer=None):
            if isinstance(supplied, Mapping):
                supplied = dict(supplied)
                raw = supplied.get('path')
            else:
                raw = supplied
            if isinstance(raw, str) and Path(raw).is_absolute():
                resolved = Path(raw).resolve()
                if resolved.is_relative_to(original):
                    mapped = str(isolated / resolved.relative_to(original))
                    if isinstance(supplied, dict):
                        supplied['path'] = mapped
                    else:
                        supplied = mapped
            return super().ref(supplied, pointer=pointer)
    adapter._Projection = IsolatedProjection


def check_refs(value, adapter, original, isolated):
    refs = []
    def walk(obj, pointer=''):
        if isinstance(obj, Mapping):
            if isinstance(obj.get('path'), str) and ('json_pointer' in obj or 'availability' in obj):
                raw = Path(obj['path'])
                path = raw if raw.is_absolute() else isolated / raw
                if path.is_absolute() and path.is_relative_to(original):
                    path = isolated / path.relative_to(original)
                path = path.resolve()
                reason = None
                if not path.is_relative_to(isolated):
                    reason = 'outside_workspace'
                elif not path.is_file():
                    reason = 'file_not_found'
                elif obj.get('file_sha256') and file_hash(path) != obj['file_sha256']:
                    reason = 'file_hash_mismatch'
                elif obj.get('json_pointer'):
                    try:
                        adapter._at(load(path), obj['json_pointer'])
                    except (ValueError, KeyError, IndexError, TypeError, OSError):
                        reason = 'pointer_unreadable'
                refs.append(dict(payload_pointer=pointer, path=obj['path'], json_pointer=obj.get('json_pointer'),
                    source_sha256=obj.get('source_sha256'), source_version=obj.get('source_version'),
                    recorded_availability=obj.get('availability'), availability='AVAILABLE' if reason is None else 'UNAVAILABLE',
                    reason=reason))
            for key, child in obj.items():
                walk(child, adapter._pointer(pointer, key))
        elif isinstance(obj, list):
            for index, child in enumerate(obj):
                walk(child, adapter._pointer(pointer, index))
    walk(value)
    return refs


def finding_rows(value, pointer=''):
    """Same known containers as the adapter; no arbitrary task traversal."""
    if not isinstance(value, Mapping):
        return
    for key in ('typed_findings', 'checker_evidence'):
        if isinstance(value.get(key), list):
            for index, finding in enumerate(value[key]):
                yield pointer + '/' + key + '/' + str(index), finding
    for key in ('feedback', 'assembly_context', 'pending_reviews', 'engineering_feedback', 'checker_evidence'):
        if isinstance(value.get(key), Mapping):
            yield from finding_rows(value[key], pointer + '/' + key)


def candidate_statistics(value, adapter):
    rows = list(finding_rows(value))
    lists = [row.get('source_candidates', []) for _, row in rows]
    return dict(finding_count=len(rows), candidate_count=sum(len(c) for c in lists),
        findings_bytes=sum(len(adapter._encoded(row)) for _, row in rows),
        source_candidates_bytes=sum(len(adapter._encoded(c)) for c in lists),
        note='List/field sizes overlap total feedback; do not add them to the total.')


def check_candidate_refs(value, adapter, arguments):
    """Validate every candidate full_ref, including identity/binding/completeness."""
    if not hasattr(adapter._Projection, 'candidate_ref'):
        return dict(status='NOT_EVALUATED', reason='baseline_has_no_candidate_validator', items=[])
    view = adapter._Projection(arguments['workspace'], arguments['source_path'],
        arguments['source_version'], arguments['report_ref'], arguments['evidence_files'])
    rows = []
    for pointer, finding in finding_rows(value):
        info = (finding.get('preview_info') or {}).get('source_candidates') or {}
        ref = info.get('full_ref')
        if not isinstance(ref, Mapping):
            continue
        checked = view.candidate_ref(finding, finding, ref)
        rows.append(dict(payload_pointer=pointer, finding_id=finding.get('finding_id'),
            full_ref=ref, total=info.get('total'), displayed=len(finding.get('source_candidates', [])),
            status='PASS' if checked is not None and checked[1] == info.get('total') else 'UNAVAILABLE'))
    return dict(status='CHECKED', items=rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--adapter-source', type=Path, default=REPO/'adsl-agents/agent_feedback.py')
    parser.add_argument('--legacy-source-candidates', action='store_true',
        help='Disable only v5 compaction; compare both adapters with a fixed 96 KiB budget.')
    args = parser.parse_args(argv)
    description_path = args.input.resolve()
    description = load(description_path)
    if description.get('input_kind') not in KINDS:
        raise ValueError('explicit input_kind is required')
    output = args.output.resolve()
    original = Path(description['workspace']).resolve()
    if output.is_relative_to(original) or original.is_relative_to(output):
        raise ValueError('output must be outside and not contain the original workspace')
    output.mkdir(parents=True, exist_ok=True)
    if description.get('missing_items'):
        save(output/'reconstruction_incomplete.json', dict(status='RECONSTRUCTION_INCOMPLETE',
            missing_items=description['missing_items']))
        return 2
    payload_path = Path(description['payload_path'])
    payload_path = payload_path if payload_path.is_absolute() else description_path.parent/payload_path
    payload = load(payload_path)
    source = Path(description['source_path']) if description.get('source_path') is not None else None
    if source is not None:
        source = (source if source.is_absolute() else original/source).resolve()
        if not source.is_relative_to(original) or not source.is_file():
            raise ValueError('assigned source must be an existing original workspace file')
        expected = description.get('source_sha256')
        if expected and file_hash(source) != expected:
            raise ValueError('assigned source hash mismatch')
    adapter_source = args.adapter_source.resolve()
    adapter = load_adapter(adapter_source)
    implementation_hash = file_hash(adapter_source)
    loaded_limit = adapter.FEEDBACK_LIMIT_BYTES
    # Replay comparison premise, never a production runtime configuration.
    adapter.FEEDBACK_LIMIT_BYTES = 96 * 1024
    if args.legacy_source_candidates:
        adapter._COMPACT_ASSEMBLY_CANDIDATES = False
    mode = 'legacy_candidates' if args.legacy_source_candidates else 'current_candidates'
    run = output/'runs'/(implementation_hash + '_' + mode)
    if run.exists():
        raise ValueError('result already exists for this implementation; use a fresh output directory')
    run.mkdir(parents=True)
    isolated = output/'workspace'
    copied, unavailable = prepare_copy(original, isolated, payload, description,
                                     source if source else original/'__unavailable_source__')
    bind_isolated_refs(adapter, original, isolated)
    stages = {'input_selected': adapter.feedback_size_report(payload)}
    filtered = deepcopy(payload)
    nsv = (isinstance(filtered.get('support_evidence'), Mapping) or
        isinstance((filtered.get('feedback') or {}).get('support_evidence'), Mapping) or
        (filtered.get('fixed_assembly') or {}).get('physics', {}).get('overhang', {}).get('support_metric')=='nsv')
    if nsv:
        adapter._remove_support_score_fields(filtered)
    stages['filtered_selected'] = adapter.feedback_size_report(filtered)
    events = adapter._deduplicate_support_evidence(filtered) if nsv and hasattr(adapter, '_deduplicate_support_evidence') else []
    stages['deduplicated_selected'] = adapter.feedback_size_report(filtered)
    captured = {}
    select = adapter._feedback_view
    def observe(value):
        view = select(value)
        captured['final_view'] = deepcopy(view)
        return view
    adapter._feedback_view = observe
    candidate_edits = []
    compact = getattr(adapter, '_compact_source_candidates', None)
    if compact is not None:
        def observe_candidates(finding, **kwargs):
            projected = compact(finding, **kwargs)
            if projected is not finding:
                fields = adapter._CANDIDATE_FIELDS
                expected = [{k:c[k] for k in fields if k in c} for c in finding['source_candidates']]
                def other_fields(row):
                    other = deepcopy(row)
                    other.pop('source_candidates', None)
                    preview = other.get('preview_info', {})
                    preview.pop('source_candidates', None)
                    if not preview:
                        other.pop('preview_info', None)
                    return other
                candidate_edits.append(dict(finding_id=finding.get('finding_id'),
                    before_bytes=len(adapter._encoded(finding)), after_bytes=len(adapter._encoded(projected)),
                    before_candidates_bytes=len(adapter._encoded(finding['source_candidates'])),
                    after_candidates_bytes=len(adapter._encoded(projected['source_candidates'])),
                    candidate_count=len(projected['source_candidates']),
                    retained_fields_exact=projected['source_candidates']==expected,
                    other_fields_exact=other_fields(finding)==other_fields(projected),
                    full_ref=projected['preview_info']['source_candidates']['full_ref']))
            return projected
        adapter._compact_source_candidates = observe_candidates
    before = adapter._encoded(payload)
    status, error, result = 'PASS', None, None
    arguments = dict(workspace=isolated,
        source_path=isolated/source.relative_to(original) if source else None,
        source_version=description.get('source_version'), role=description['role'],
        report_ref=description.get('report_ref'), evidence_files=description.get('evidence_files', ()))
    try:
        result = adapter.payload_for_agent(payload, **arguments)
    except adapter.AgentFeedbackError as failure:
        status = failure.code
        error = dict(code=failure.code, details=failure.details)
    finally:
        adapter._feedback_view = select
        if compact is not None:
            adapter._compact_source_candidates = compact
    final_view = captured.get('final_view')
    stages['final_selected'] = adapter.feedback_size_report(final_view) if final_view is not None else None
    repeated = dict(status='NOT_EVALUATED', reason='initial_projection_failed')
    if result is not None:
        diagnostic = (arguments['source_path'].parent if source else isolated)/'diagnostics/agent_feedback'
        initial_snapshots = {p.name:file_hash(p) for p in diagnostic.glob('*.json')}
        try:
            again = adapter.payload_for_agent(result, **arguments)
            same = adapter._encoded(again)==adapter._encoded(result)
            final_snapshots = {p.name:file_hash(p) for p in diagnostic.glob('*.json')}
            repeated = dict(status='PASS' if same and initial_snapshots==final_snapshots else 'MISMATCH',
                payload_identical=same, snapshots_unchanged=initial_snapshots==final_snapshots)
        except adapter.AgentFeedbackError as failure:
            repeated = dict(status=failure.code, details=failure.details)
    refs = check_refs(result if result is not None else final_view, adapter, original, isolated)
    unchanged = all((original/name).is_file() and file_hash(original/name)==digest for name,digest in copied.items())
    materials = description.get('material_hashes', {})
    material_binding = {name: ((original/name).resolve().is_relative_to(original) and
        (original/name).is_file() and file_hash(original/name)==digest) for name,digest in materials.items()}
    report = dict(status=status, input_kind=description['input_kind'], role=description['role'],
        source_version=description.get('source_version'), source_sha256=file_hash(source) if source else None,
        source_path=str(source.relative_to(original)) if source else None,
        baseline_commit=description.get('baseline_commit'), adapter_sha256=implementation_hash,
        adapter_source=str(adapter_source), input_sha256=file_hash(payload_path),
        reconstruction_basis=description.get('reconstruction_basis', []), missing_items=[],
        reconstruction_limitation=description.get('reconstruction_limitation'),
        full_chain_comparison_eligible=description['input_kind']!='captured_post_projection',
        original_recorded_final_bytes=description.get('original_recorded_final_bytes'),
        candidate_projection_mode=mode, loaded_adapter_limit_bytes=loaded_limit,
        comparison_limit_bytes=96*1024,
        candidate_statistics=dict(input=candidate_statistics(payload, adapter),
                                  final=candidate_statistics(final_view, adapter)),
        candidate_compaction=candidate_edits,
        candidate_reference_checks=check_candidate_refs(result or final_view, adapter, arguments),
        configuration_sha256=description.get('configuration_sha256'),
        stages=stages, deduplication_events=events, input_unchanged=adapter._encoded(payload)==before,
        original_files_unchanged=unchanged, error=error,
        repeated_projection=repeated,
        local_dedup_saved_bytes=stages['filtered_selected']['actual_bytes']-stages['deduplicated_selected']['actual_bytes'],
        limit_bytes=adapter.FEEDBACK_LIMIT_BYTES,
        remaining_bytes=adapter.FEEDBACK_LIMIT_BYTES-stages['final_selected']['actual_bytes'] if stages['final_selected'] else None,
        path_mapping=dict(original_workspace=str(original), isolated_workspace=str(isolated),
            policy='replay-only ref resolver; relative layout and copied document bytes preserved; identical in both runs',
            byte_effect='not assumed zero; compare both final_selected values at this same isolated root'),
        dependency_files=copied, unavailable_dependencies=unavailable,
        source_material_bindings=material_binding,
        reference_summary=dict(total=len(refs),available=sum(r['availability']=='AVAILABLE' for r in refs),
            unavailable=sum(r['availability']=='UNAVAILABLE' for r in refs)),
        restrictions=dict(model_calls=0,workflow_calls=0,checker_calls=0,pytest_executed=False))
    save(run/'report.json', report)
    save(run/'reference_checks.json', refs)
    save(run/'final_feedback_view.json', final_view)
    if result is not None:
        save(run/'agent_payload.json', result)
    source_dir = isolated/source.relative_to(original) if source else isolated/'source.py'
    diagnostic = source_dir.parent/'diagnostics/agent_feedback'
    if diagnostic.is_dir():
        shutil.copytree(diagnostic, run/'diagnostics')
    rows = ['# Feedback adapter replay', '', f'- Status: {status}',
        f'- Input kind: {description["input_kind"]}', f'- Role: {description["role"]}',
        f'- Adapter SHA256: {implementation_hash}', '', '| Stage | Bytes |', '| --- | ---: |']
    rows.extend(f'| {name} | {value["actual_bytes"] if value else "unavailable"} |' for name,value in stages.items())
    rows.extend(['', f'- Local deduplication saving: {report["local_dedup_saved_bytes"]} bytes.',
        f'- Remaining budget: {report["remaining_bytes"]} bytes.',
        '- Parent/child field sizes overlap; do not sum them.',
        '- See report.json for bounded field distribution and mapping limitations.',
        '- No model, checker, geometry execution or pytest was run.'])
    (run/'report.md').write_text('\n'.join(rows)+'\n')
    print(json.dumps({key:report[key] for key in ('status','input_kind','role','stages',
        'local_dedup_saved_bytes','remaining_bytes','input_unchanged','original_files_unchanged','reference_summary')},ensure_ascii=False))
    if not unchanged or not report['input_unchanged'] or not all(material_binding.values()):
        return 2
    return 0 if status=='PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
