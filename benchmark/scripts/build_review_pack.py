"""Input and structural-risk selection; optional GT diagnostics stay separate."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import shutil
import zipfile
from PIL import Image, ImageDraw
from common import config, dump, load, rows, sha, valid_files, write_rows
import preflight_candidates

METRICS = ('appearance', 'overhang', 'standing')
TAGS = ('complex_surface', 'standing_sensitive', 'grouping_tradeoff', 'multipart_contact')
FIELDS = ['case_id', 'source', 'source_id', 'category', 'input_image', 'reference_mesh',
          'use_pose', 'tags', 'current_tags', 'historical_tags', 'status', 'recommended_for',
          'measurement_status', 'measurement_current', 'overhang_status', 'standing_status',
          'appearance_eligibility', 'appearance_reason', 'overhang_eligibility',
          'overhang_reason', 'standing_eligibility', 'standing_reason', 'selection_note', 'review_source',
          'ordinary_control', 'needs_manual_confirmation', 'user_confirmed']


# Keep the validators shared with the runner; they never rewrite cached evidence.
def validated_preflight(c, row):
    return preflight_candidates.validated_preflight(c, row)


def validated_measurement(c, row):
    return preflight_candidates.validated_measurement(c, row, diagnostic=True)


def current_label(c, row):
    return preflight_candidates.current_label(c, row)


def _round_robin(values):
    groups = {}
    for row in values:
        groups.setdefault(row.get('category'), []).append(row)
    while any(groups.values()):
        for group in groups.values():
            if group:
                yield group.pop(0)


def _source_current(root, row):
    raw = root / row['raw_mesh'] if row.get('raw_mesh') else None
    return bool(raw and raw.is_file() and row.get('raw_sha256') and sha(raw) == row['raw_sha256'])


def _classification_ready(row):
    classification = row.get('classification_review') or row.get('classification') or {}
    return bool(row.get('category') and classification.get('scope') != 'out_of_scope'
                and not classification.get('needs_review'))


def shortlist(c, values):
    """Physical shortlist: current reliable volumes, source quotas, then redistribution."""
    valid = []
    for row in values:
        if row.get('status') == 'excluded' or not _classification_ready(row):
            continue
        if 'root' in c:
            saved = validated_preflight(c, row)
            reliable = (_source_current(Path(c['root']), row) and saved is not None
                        and saved.get('status') == 'PASS'
                        and (saved.get('basic') or {}).get('status') == 'PASS')
        else:
            reliable = row.get('material_current', row.get('preflight_status') == 'PASS'
                                   and row.get('geometry_status') == 'PASS')
        if reliable:
            valid.append(row)
    sources = list(dict.fromkeys(row.get('source', 'unknown') for row in values))
    count = c.get('shortlist_count', 24)
    quota = c.get('shortlist_per_source', count // max(1, len(sources)))
    groups = {source: list(_round_robin([r for r in valid if r.get('source', 'unknown') == source]))
              for source in sources}
    chosen = []
    for _ in range(quota):
        for group in groups.values():
            if group and len(chosen) < count:
                chosen.append(group.pop(0)['case_id'])
    while len(chosen) < count and any(groups.values()):
        for group in groups.values():
            if group and len(chosen) < count:
                chosen.append(group.pop(0)['case_id'])
    return chosen


def _evidence_value(*reviews, field):
    for review in reviews:
        value = review.get(field)
        if value is not None:
            return value
    return None


def _eligibility(status, reason):
    return dict(status=status, reason=reason)


def review_evidence(c, row):
    """Task applicability depends on visible input/use, never GT physics verdicts."""
    root = Path(c['root'])
    source_current = _source_current(root, row)
    preflight = validated_preflight(c, row) if source_current else None
    preview_current = bool(preflight and preflight.get('status') == 'PASS')
    basic = (preflight or {}).get('basic') or {}
    material_current = preview_current and basic.get('status') == 'PASS'
    attempt = current_label(c, row) if preview_current else None
    automatic = ((attempt or {}).get('output') or {}) if (attempt or {}).get('status') == 'PASS' else {}
    manual = row.get('manual_review') or {}
    selection = row.get('selection_review') or {}
    selection_files = selection.get('input_files_sha256') or {}
    input_path = root / row['input_image'] if row.get('input_image') else None
    input_bound = bool(input_path and any((root / name).resolve() == input_path.resolve() for name in selection_files))
    selection_current = bool(preview_current and input_bound and valid_files(selection_files, root))
    if not selection_current:
        selection = {}
    reviews = (manual, selection, automatic)
    value = lambda field: _evidence_value(*reviews, field=field)
    input_path = root / row['input_image'] if row.get('input_image') else None
    input_current = bool(source_current and preview_current and input_path and input_path.is_file())
    usable = value('appearance_input_usable')
    pose_observation = str(value('pose_observation') or '')
    pose_confirmed = None
    for review_record in reviews:
        confirmed = review_record.get('pose_confirmed')
        if confirmed is None:
            confirmed = review_record.get('use_pose_confirmed')
        if confirmed is not None:
            pose_confirmed = confirmed
            break
    if pose_confirmed is None:
        pose_confirmed = bool(pose_observation.strip() and automatic.get('needs_manual_confirmation') is False)
    tasks_clear = value('task_requirements_clear')
    if tasks_clear is None:
        # Direct human preview confirmation remains a supported screening path.
        tasks_clear = bool(pose_observation.strip() or manual.get('appearance_input_usable') is True)
    if not input_current:
        appearance = _eligibility('INDETERMINATE', 'Source SHA or preview/input evidence is missing or stale.')
    elif manual.get('appearance_applicable') is False:
        appearance = _eligibility('NOT_APPLICABLE', manual.get('reason', 'Appearance use is excluded by manual review.'))
    elif usable is not True:
        appearance = _eligibility('INDETERMINATE', 'Clear, recognizable reference input awaits preview review; missing VLM labels do not invalidate the case.')
    else:
        appearance = _eligibility('ELIGIBLE', 'Current source and preview are usable; GT closure, volume and physics are not required.')
    if not input_current or usable is not True or tasks_clear is not True:
        overhang = _eligibility('INDETERMINATE', 'Clear reference input and object/task interpretation await review; GT measurements are optional.')
    else:
        overhang = _eligibility('ELIGIBLE', 'Reference object/task is clear; evaluate generated outputs at their searched print orientations, independently of GT mesh validity.')
    applicable = value('standing_applicable')
    if not input_current or usable is not True:
        standing = _eligibility('INDETERMINATE', 'Current clear input is required to establish the standing task.')
    elif applicable is False:
        standing = _eligibility('NOT_APPLICABLE', str(value('reason') or pose_observation or 'Natural use requires external support or flight.'))
    elif applicable is not True:
        standing = _eligibility('INDETERMINATE', 'Independent standing use awaits preview review, not a GT physical test.')
    elif pose_confirmed is not True or tasks_clear is not True:
        standing = _eligibility('INDETERMINATE', 'Expected use pose/task needs confirmation; GT standing PASS is not required.')
    else:
        standing = _eligibility('ELIGIBLE', 'Independent standing use and intended pose are clear; test generated models regardless of GT islands or standing verdict.')
    eligibility = dict(appearance=appearance, overhang=overhang, standing=standing)
    current = validated_measurement(c, row) if preview_current else None
    measurement_folder = root / 'measurements' / row['case_id']
    measurement_path = measurement_folder / 'reference_measurement_v2.json'
    if not measurement_path.is_file():
        measurement_path = measurement_folder / 'reference_measurement.json'
    unvalidated_status = 'STALE' if measurement_path.is_file() else 'NOT_RUN'
    measurement = dict(status=(current or {}).get('status', unvalidated_status), current=current is not None,
                       report_exists=measurement_path.is_file(), optional=True,
                       reason=None if current else 'No validated current GT diagnostic; this does not affect task applicability.')
    measured_request = (((current or {}).get('cache_descriptor') or {}).get('inputs') or {}).get('standing_request') or {}
    measured_applicable = measured_request.get('applicable', applicable)
    for name in ('overhang', 'standing'):
        status = ((current or {}).get(name) or {}).get('status', unvalidated_status)
        measurement[name] = dict(status=status, valid=bool(current and status in ('PASS', 'FAIL')),
                                 scope='gt_diagnostic', task_applicable=eligibility[name]['status'] == 'ELIGIBLE',
                                 externally_supported=bool(name == 'standing' and measured_applicable is False))
    tags = value('tags') or []
    tags = list(dict.fromkeys(tag for tag in tags if tag in TAGS))
    note = str(value('selection_note') or '')
    ordinary = value('ordinary_control') is True
    structural_review = bool(note.strip() and (tags or ordinary))
    review_source = ('manual' if manual.get('selection_note') or manual.get('tags') is not None
                     else 'coding_agent' if selection else 'vlm' if automatic else 'pending')
    return dict(source_current=source_current, preflight_current=preview_current, material_current=material_current,
                task_applicability=eligibility, metric_eligibility=eligibility, automatic_review=automatic,
                selection_review_current=selection_current, review_source=review_source,
                structural_review_current=structural_review, ordinary_control=ordinary,
                reviewed_selection_note=note, needs_manual_confirmation=not (row.get('user_confirmed') is True or manual.get('user_confirmed') is True or row.get('status') in ('user_confirmed', 'user_confirmed_dev')),
                measurement=measurement, measurement_current=current is not None,
                measurement_path=str(measurement_path.relative_to(root)) if measurement_path.is_file() else None,
                current_tags=tags, vlm_current=attempt is not None,
                vlm_cache_key=(attempt or {}).get('cache_key'), vlm_called_at=(attempt or {}).get('called_at')), current


def recommendation_candidates_for(row):
    """Concrete reviewed structure or ordinary control, with applicable tasks."""
    if (row.get('status') == 'excluded' or not _classification_ready(row)
            or not row.get('structural_review_current')):
        return []
    return [name for name in METRICS if row['task_applicability'][name]['status'] == 'ELIGIBLE']


def _recommendations(values, count):
    # Risk ordering occurs within each category; category rotation retains coverage.
    ordered = sorted(values, key=lambda row: -sum(tag in row.get('current_tags', []) for tag in ('standing_sensitive', 'grouping_tradeoff')))
    controls = sorted([row for row in ordered if row.get('ordinary_control')],
                      key=lambda row: sum(tag in row.get('current_tags', []) for tag in ('standing_sensitive', 'grouping_tradeoff')))
    risks = [row for row in ordered if not row.get('ordinary_control')]
    reserved = min(1, len(controls), max(0, count - 1))
    controls = list(_round_robin(controls))[:reserved]
    rotated = list(_round_robin(risks))
    chosen, covered = [], {row.get('category') for row in controls}
    for row in rotated:
        if row.get('category') not in covered and len(chosen) < count - reserved:
            chosen.append(row)
            covered.add(row.get('category'))
    selected_ids = {row['case_id'] for row in chosen}
    chosen += [row for row in rotated if row['case_id'] not in selected_ids][:count - reserved - len(chosen)]
    chosen += controls
    if len(chosen) < count:
        chosen_ids = {row['case_id'] for row in chosen}
        chosen += list(_round_robin([row for row in ordered if row['case_id'] not in chosen_ids]))[:count - len(chosen)]
    return chosen


def _vlm_attempts(root, values):
    """Count actual calls, including old attempts, once per call rather than copied file."""
    cases = {r['case_id']: r['source'] for r in values}
    unique = {}
    for path in (root / 'measurements').rglob('*.json'):
        try:
            attempt = load(path)
        except (ValueError, OSError):
            continue
        if not isinstance(attempt, dict) or 'status' not in attempt:
            continue
        if 'called_at' not in attempt and not path.name.startswith('attempt'):
            continue
        if 'output' not in attempt and 'model' not in attempt and 'cache_key' not in attempt:
            continue
        case = attempt.get('case_id') or next((p for p in path.parts if p in cases), path.parent.parent.name)
        identity = (case, attempt.get('attempt_id') or attempt.get('called_at') or sha(path))
        previous = unique.get(identity)
        if previous is None or attempt.get('status') == 'PASS':
            unique[identity] = dict(attempt, case_id=case, source=cases.get(case, 'unknown'))
    return list(unique.values())


def sheet(root, values, source):
    values = [r for r in values if r['source'] == source]
    w, h, columns = 240, 265, 5
    canvas = Image.new('RGB', (w * columns, h * max(1, (len(values) + columns - 1) // columns)), 'white')
    draw = ImageDraw.Draw(canvas)
    for i, row in enumerate(values):
        x, y = (i % columns) * w, (i // columns) * h
        path = root / row['input_image'] if row.get('input_image') else None
        if path and path.is_file():
            with Image.open(path) as original:
                picture = original.convert('RGBA')
                picture.thumbnail((225, 225))
                canvas.paste(picture, (x + (w - picture.width) // 2, y), picture)
        else:
            draw.text((x + 8, y + 85), 'MODEL UNAVAILABLE', fill='gray')
        draw.text((x + 7, y + 228), row['source_id'], fill='black')
        draw.text((x + 7, y + 245), row.get('category') or 'classification pending', fill='black')
    path = root / 'review' / f'{source}_contactsheet.jpg'
    canvas.save(path, quality=90)
    return path


def _source_counts(values, sources, predicate=lambda row: True):
    counts = Counter(row['source'] for row in values if predicate(row))
    return {source: counts[source] for source in sources}


def _statistics(root, values, recommended, attempts):
    sources = list(dict.fromkeys(r['source'] for r in values))
    count = lambda predicate: _source_counts(values, sources, predicate)
    statistics = dict(candidates=count(lambda r: True), assets=count(lambda r: r['source_current']),
                      downloaded=count(lambda r: r.get('asset_status') == 'downloaded'),
                      previews=count(lambda r: r['preflight_current']),
                      reliable_geometry=count(lambda r: r['material_current']),
                      measured=count(lambda r: r['measurement']['report_exists']),
                      recommended=_source_counts(recommended, sources),
                      eligible={name: count(lambda r: r['metric_eligibility'][name]['status'] == 'ELIGIBLE') for name in METRICS},
                      recommended_for={name: _source_counts(recommended, sources, lambda r: name in r['recommended_for']) for name in METRICS},
                      categories=dict(Counter(r.get('category') or 'unclassified' for r in values)),
                      statuses=dict(Counter(r['status'] for r in values)),
                      current_tags={source: dict(Counter(t for r in values if r['source'] == source for t in r['current_tags'])) for source in sources},
                      historical_tags={source: dict(Counter(t for r in values if r['source'] == source for t in r['historical_tags'])) for source in sources},
                      vlm_calls=len(attempts), vlm_successes=sum(a['status'] == 'PASS' for a in attempts),
                      vlm_calls_by_source={s: sum(a['source'] == s for a in attempts) for s in sources},
                      vlm_successes_by_source={s: sum(a['source'] == s and a['status'] == 'PASS' for a in attempts) for s in sources},
                      manual_reviews=sum(bool(r.get('manual_review')) for r in values),
                      manual_confirmations=sum(r['status'] in ('user_confirmed', 'user_confirmed_dev') or r.get('user_confirmed') is True or (r.get('manual_review') or {}).get('user_confirmed') is True for r in values))
    statistics['tags'] = dict(Counter(t for r in values for t in r['current_tags']))
    measurements = dict(reports_exist=statistics['measured'], current_reports=count(lambda r: r['measurement_current']),
                        stale_reports=count(lambda r: r['measurement']['report_exists'] and not r['measurement_current']),
                        current_valid={}, current_statuses={}, diagnostic={}, externally_supported={})
    completed = {}
    for name in ('overhang', 'standing'):
        measurements['current_valid'][name] = {source: {status: sum(r['source'] == source and r['measurement'][name]['valid'] and r['measurement'][name]['status'] == status for r in values) for status in ('PASS', 'FAIL')} for source in sources}
        measurements['diagnostic'][name] = count(lambda r: r['measurement_current'] and r['measurement'][name]['scope'] == 'gt_diagnostic' and r['measurement'][name]['status'] in ('PASS', 'FAIL'))
        measurements['externally_supported'][name] = count(lambda r: r['measurement_current'] and r['measurement'][name]['externally_supported'])
        measurements['current_statuses'][name] = {source: dict(Counter(r['measurement'][name]['status'] for r in values if r['source'] == source)) for source in sources}
        completed[name] = sum(r['measurement'][name]['valid'] for r in values)
    statistics.update(measurements=measurements, completed_reference_measurements=completed)
    statistics['task_applicability'] = statistics['eligible']
    statistics['pending_structural_review'] = count(lambda r: not r['structural_review_current'])
    statistics['ordinary_controls_recommended'] = _source_counts(recommended, sources, lambda r: r['ordinary_control'])
    statistics['risk_reasons_recommended'] = sum(bool(r.get('selection_note', '').strip()) for r in recommended)
    statistics['recommendations_waiting_user_confirmation'] = sum(r['needs_manual_confirmation'] for r in recommended)
    statistics['selection_basis'] = 'source identity, clear reviewed input/use, structural risk and category coverage; GT diagnostics and method outcomes excluded'
    grouped = {r['case_id'] for r in values if any(isinstance(record, dict) and record.get('status') in ('PASS', 'FAIL', 'COMPLETED') for record in (r.get('grouping_measurement'), (r.get('reference_measurement') or {}).get('grouping')))}
    for path in (root / 'measurements').glob('*/grouping*.json'):
        record = load(path)
        if record.get('status') in ('PASS', 'FAIL', 'COMPLETED'):
            grouped.add(path.parent.name)
    statistics['grouping_measurements'] = len(grouped)
    faults = Counter()
    for row in values:
        path = root / 'derived' / row['case_id'] / 'use_pose.json'
        if row['preflight_current'] and path.is_file():
            operations = load(path).get('operations', [])
            for key in ('boundary_edges', 'nonmanifold_edges', 'zero_area_triangles', 'duplicate_faces', 'inconsistent_edges'):
                if any(o.get('after', {}).get(key) for o in operations):
                    faults[key] += 1
    statistics['geometry_defect_case_counts'] = dict(faults)
    return statistics


def _cell(value):
    if value is None:
        return 'N/A'
    return str(value).replace('|', '\\|').replace('\n', ' ')


def _safe_config(value):
    if isinstance(value, dict):
        return {k: _safe_config(v) for k, v in value.items() if not any(word in k.lower() for word in ('credential', 'token', 'password', 'secret', 'api_key'))}
    if isinstance(value, list):
        return [_safe_config(v) for v in value]
    return value


def _package(root):
    allowed = {'.json', '.jsonl', '.csv', '.md', '.txt', '.log', '.png', '.jpg', '.jpeg', '.yaml', '.yml'}
    paths = []
    for folder in ('review', 'manifests', 'previews', 'measurements', 'logs', 'history', 'protocol', 'configs'):
        paths.extend((root / folder).rglob('*'))
    paths.extend((root / 'derived').rglob('*.json'))
    paths.extend((root / 'raw').rglob('provenance.json'))
    paths.extend((root / 'raw').rglob('catalog.jpg'))
    paths.extend(p for p in (root / 'raw' / 'indices').glob('*') if 'readme' in p.name.lower() or 'license' in p.name.lower())
    destination = root / 'selection_v1_review.zip'
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(paths)):
            lowered = path.name.lower()
            if (path.is_file() and path.suffix.lower() in allowed
                    and not any(p in ('.git', 'node_modules', '.aws') for p in path.parts)
                    and not any(word in lowered for word in ('credential', 'secret', '.npmrc', '.env', 'token'))):
                archive.write(path, str(path.relative_to(root)))
    return destination


def build(c):
    root = Path(c['root'])
    path = root / 'manifests' / 'candidates.jsonl'
    values = rows(path)
    review = root / 'review'
    review.mkdir(exist_ok=True)
    results = {}
    old_tags = {r['case_id']: list(r.get('tags', [])) for r in values}
    for row in values:
        row.setdefault('selection_note', '')
        row.setdefault('tags', [])
        for field in ('input_image', 'reference_mesh', 'use_pose'):
            row.setdefault(field, None)
            if row[field] and not (root / row[field]).is_file():
                row.setdefault('missing_input_paths', {})[field] = row[field]
                row[field] = None
        evidence, current = review_evidence(c, row)
        row.update(evidence)
        if row.get('reviewed_selection_note'):
            row['selection_note'] = row['reviewed_selection_note']
        results[row['case_id']] = current or {}
        row['historical_tags'] = list(dict.fromkeys(row.get('historical_tags', []) + (old_tags[row['case_id']] if old_tags[row['case_id']] != row['current_tags'] else [])))
        row['tags'] = list(row['current_tags'])
        purposes = recommendation_candidates_for(row)
        row['eligible_for'] = [name for name in METRICS if row['metric_eligibility'][name]['status'] == 'ELIGIBLE']
        manual = row.get('manual_review') or {}
        requested = manual.get('recommended_for', manual.get('selected_for', row.get('recommended_for', []) if row.get('status') in ('user_confirmed', 'user_confirmed_dev') else []))
        selected_manually = row.get('status') in ('user_confirmed', 'user_confirmed_dev') or manual.get('selected') is True or manual.get('user_confirmed') is True
        if selected_manually:
            row.setdefault('manual_selection', dict(status=row.get('status'), recommended_for=list(requested) if isinstance(requested, list) else [], review=manual))
        conflicts = row.setdefault('selection_conflicts', [])
        for name in requested if isinstance(requested, list) else []:
            if name not in purposes:
                conflict = dict(metric=name, reason=row['metric_eligibility'].get(name, {}).get('reason', 'Unknown metric selection.'))
                if conflict not in conflicts:
                    conflicts.append(conflict)
        if selected_manually and (not purposes or not _classification_ready(row)):
            conflict = dict(metric='selection', reason='Manual selection retained; current eligibility or classification requires review.')
            if conflict not in conflicts:
                conflicts.append(conflict)
        row['recommended_for'] = []
        row['recommendation_candidates_for'] = purposes
        if row.get('status') not in ('excluded', 'user_confirmed', 'user_confirmed_dev'):
            row['status'] = 'needs_review'
    recommended = []
    for source in dict.fromkeys(r['source'] for r in values):
        available = [r for r in values if r['source'] == source and r['recommendation_candidates_for']]
        for row in _recommendations(available, c.get('recommend_per_source', 10)):
            row['recommended_for'] = row['recommendation_candidates_for']
            if row['status'] not in ('user_confirmed', 'user_confirmed_dev'):
                row['status'] = 'recommended_dev'
            recommended.append(row)
    attempts = _vlm_attempts(root, values)
    for row in values:
        historical = [t for a in attempts if a['case_id'] == row['case_id']
                      and not (row['vlm_current'] and (a.get('called_at') == row['vlm_called_at'] if row['vlm_called_at'] else a.get('cache_key') == row['vlm_cache_key']))
                      for t in (a.get('output') or {}).get('tags', [])]
        row['historical_tags'] = list(dict.fromkeys(row['historical_tags'] + historical))
    # Physical diagnostics require the explicit --shortlist-only / --measure path.
    dump(root / 'manifests' / 'shortlist.json', [])
    write_rows(path, values)
    write_rows(root / 'manifests' / 'recommended_dev20.jsonl', recommended)
    with (root / 'manifests' / 'candidates.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator='\n')
        writer.writeheader()
        for row in values:
            record = dict(row, measurement_status=row['measurement']['status'], overhang_status=row['measurement']['overhang']['status'], standing_status=row['measurement']['standing']['status'])
            for name in METRICS:
                record[name + '_eligibility'] = row['metric_eligibility'][name]['status']
                record[name + '_reason'] = row['metric_eligibility'][name]['reason']
            writer.writerow({k: ';'.join(record[k]) if isinstance(record.get(k), list) else record.get(k) for k in FIELDS})
    sources = list(dict.fromkeys(r['source'] for r in values))
    for source in sources:
        sheet(root, values, source)
    statistics = _statistics(root, values, recommended, attempts)
    dump(review / 'summary.json', statistics)
    durations = [load(p).get('elapsed_seconds', 0) for p in (root / 'logs').rglob('process.json')]
    summary = '# Selection v1 actual results\n\n```json\n' + json.dumps(statistics, indent=2) + '\n```\n\n'
    summary += f'Recorded import/render process time: {sum(durations):.1f} s (serial process durations). Checker and VLM timings remain in their reports.\n\n'
    summary += 'Task applicability is based on clear reference inputs, intended use and reviewed structural risk. GT closure, material islands, volume and standing/overhang results never gate recommendations. All GT measurements below are optional diagnostics; missing or invalid values are N/A. Wall-mounted/flying objects may serve appearance/printing tasks while independent standing is not applicable. Recommendations await user confirmation; risk labels do not assert failure or grouping gain. No generation comparison or FEA was run. Common cross-method print scale, voxel settings and frozen Dapper reference quantities remain to be specified separately; existing preview/GT settings do not freeze that evaluation protocol.\n\n'
    summary += 'For later frozen-case evaluations, keep generation failures, invalid meshes and unmeasurable physics in each method’s case denominator. Analyze generated geometry with the existing topology checker; a baseline without interfaces has interface-specific metrics marked not applicable. No GT node-to-connection inference is made.\n\n'
    summary += '| Case | Category | Tasks | Structural risk / ordinary control | Confirmation | GT standing diagnostic | GT G | GT h mm | GT score |\n|---|---|---|---|---|---|---|---|---|\n'
    for row in recommended:
        objective = (((results[row['case_id']].get('overhang') or {}).get('metrics') or {}).get('partition_objective') or {})
        standing = row['measurement']['standing']['status']
        if row['metric_eligibility']['standing']['status'] == 'NOT_APPLICABLE':
            standing = f'N/A (diagnostic {standing})'
        summary += '| ' + ' | '.join(_cell(v) for v in (row['source_id'], row.get('category'), ', '.join(row['recommended_for']), row['selection_note'], 'pending' if row['needs_manual_confirmation'] else 'user confirmed', standing, objective.get('gap_voxels'), objective.get('voxel_pitch_mm'), objective.get('score'))) + ' |\n'
    (review / 'summary.md').write_text(summary)
    readme = '# ABO / Toys4K selection review\n\nOpen the contactsheets and individual case images. Transparent PNGs contain no text. Development recommendations cover task applicability and structural risk, not GT physics qualification, and await user confirmation. Missing VLM labels mean pending review; humans may directly confirm clear previews. GT measurements are optional diagnostics.\n\n'
    for source in sources:
        readme += f'![{source}]({source}_contactsheet.jpg)\n\n'
    for row in values:
        readme += f"- **{row['case_id']}** ({row.get('category') or 'classification pending'}): {row['status']}; recommended for {', '.join(row['recommended_for']) or 'pending'}. {row['selection_note']}"
        if row.get('input_image'):
            readme += f" [input](../{row['input_image']}) · [native eight](../previews/{row['case_id']}/native/meta.json) · [neutral eight](../previews/{row['case_id']}/neutral/meta.json)"
        if row.get('measurement_path'):
            readme += f" · [measurement](../{row['measurement_path']})"
        readme += f" Review: {row['review_source']}; user confirmation {'pending' if row['needs_manual_confirmation'] else 'recorded'}.\n"
        for name in METRICS:
            record = row['metric_eligibility'][name]
            readme += f"  {name}: {record['status']}. {record['reason']}\n"
    readme += '\nSee summary.md and ../manifests/candidates.csv for actual counts. Task applicability is independent of optional GT diagnostics. Dapper G is an empty-voxel proxy; no score or method success/failure enters selection. Risk labels consider print-orientation freedom rather than only use-pose suspension. Original model files are stored outside this package. No new physics, partition optimization or generation comparison was run.\n'
    (review / 'README.md').write_text(readme)
    dump(review / 'config.json', _safe_config(c))
    protocol = Path(__file__).parents[1] / 'selection_protocol.md'
    if protocol.is_file():
        shutil.copyfile(protocol, review / 'selection_protocol.md')
    destination = _package(root)
    print(statistics)
    print(destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--shortlist-only', action='store_true')
    args = parser.parse_args()
    settings = config(args.config)
    if args.shortlist_only:
        selected = shortlist(settings, rows(Path(settings['root']) / 'manifests/candidates.jsonl'))
        dump(Path(settings['root']) / 'manifests/shortlist.json', selected)
        print(selected)
    else:
        build(settings)
