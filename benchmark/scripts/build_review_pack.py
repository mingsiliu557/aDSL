"""Portable review evidence, metric eligibility and deterministic source coverage."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import shutil
import zipfile
from PIL import Image, ImageDraw
from common import config, dump, load, rows, sha, write_rows
import preflight_candidates

METRICS = ('appearance', 'overhang', 'standing')
FIELDS = ['case_id', 'source', 'source_id', 'category', 'input_image', 'reference_mesh',
          'use_pose', 'tags', 'current_tags', 'historical_tags', 'status', 'recommended_for',
          'measurement_status', 'measurement_current', 'overhang_status', 'standing_status',
          'appearance_eligibility', 'appearance_reason', 'overhang_eligibility',
          'overhang_reason', 'standing_eligibility', 'standing_reason', 'selection_note']


# Keep the validators shared with the runner; they never rewrite cached evidence.
def validated_preflight(c, row):
    return preflight_candidates.validated_preflight(c, row)


def validated_measurement(c, row):
    return preflight_candidates.validated_measurement(c, row)


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


def _evidence_value(manual, automatic, field):
    value = manual.get(field)
    return value if isinstance(value, bool) else automatic.get(field)


def _eligibility(status, reason):
    return dict(status=status, reason=reason)


def review_evidence(c, row):
    """Separate observable inputs, physical eligibility and computational verdicts."""
    root = Path(c['root'])
    source_current = _source_current(root, row)
    preflight = validated_preflight(c, row) if source_current else None
    preview_current = bool(preflight and preflight.get('status') == 'PASS')
    basic = (preflight or {}).get('basic') or {}
    material_current = preview_current and basic.get('status') == 'PASS'
    attempt = current_label(c, row) if preview_current else None
    automatic = ((attempt or {}).get('output') or {}) if (attempt or {}).get('status') == 'PASS' else {}
    manual = row.get('manual_review') or {}
    current = validated_measurement(c, row) if material_current else None
    measurement_folder = root / 'measurements' / row['case_id']
    measurement_path = measurement_folder / 'reference_measurement_v2.json'
    if not measurement_path.is_file():
        measurement_path = measurement_folder / 'reference_measurement.json'
    usable = _evidence_value(manual, automatic, 'appearance_input_usable')
    input_path = root / row['input_image'] if row.get('input_image') else None
    complete = _evidence_value(manual, automatic, 'appearance_materials_complete')
    if complete is None:
        complete = _evidence_value(manual, automatic, 'materials_complete')
    if manual.get('appearance_applicable') is False:
        appearance = _eligibility('NOT_APPLICABLE', manual.get('reason', 'Appearance use is excluded by manual review.'))
    elif not source_current or not preview_current or not input_path or not input_path.is_file():
        appearance = _eligibility('INDETERMINATE', 'Source SHA or preview/preflight evidence is missing or stale.')
    elif usable is not True or complete is False:
        appearance = _eligibility('INDETERMINATE', 'Current VLM or manual evidence has not confirmed complete, usable appearance input.')
    else:
        appearance = _eligibility('ELIGIBLE', 'Validated source and previews; current evidence confirms usable appearance input.')
    overhang_status = ((current or {}).get('overhang') or {}).get('status', 'INDETERMINATE')
    if material_current and current and overhang_status == 'PASS':
        overhang = _eligibility('ELIGIBLE', 'Reliable material volume and a valid current overhang PASS.')
    else:
        overhang = _eligibility('INDETERMINATE', 'Reliable material volume and a valid current overhang PASS are required.')
    applicable = _evidence_value(manual, automatic, 'standing_applicable')
    pose_confirmed = manual.get('pose_confirmed', manual.get('use_pose_confirmed'))
    if pose_confirmed is None:
        pose_confirmed = bool(automatic.get('pose_observation', '').strip()
                              and automatic.get('needs_manual_confirmation') is False)
    standing_status = ((current or {}).get('standing') or {}).get('status', 'INDETERMINATE')
    if applicable is False:
        standing = _eligibility('NOT_APPLICABLE', manual.get('reason', 'Natural use requires external support; ground simulation is diagnostic.'))
    elif applicable is not True:
        standing = _eligibility('INDETERMINATE', 'Natural free-standing applicability has not been confirmed.')
    elif not material_current or basic.get('connected_components') != 1:
        standing = _eligibility('INDETERMINATE', 'One validated connected material island is required.')
    elif pose_confirmed is not True:
        standing = _eligibility('INDETERMINATE', 'Natural use pose has not been explicitly confirmed.')
    elif not current or standing_status not in ('PASS', 'FAIL'):
        standing = _eligibility('INDETERMINATE', 'A valid current standing PASS or FAIL measurement is required.')
    else:
        standing = _eligibility('ELIGIBLE', 'Natural free-standing use, confirmed pose, one material island and a valid current measurement.')
    eligibility = dict(appearance=appearance, overhang=overhang, standing=standing)
    measurement = dict(status=(current or {}).get('status', 'INDETERMINATE'), current=current is not None,
                       report_exists=measurement_path.is_file(),
                       reason=None if current else 'Current descriptor and input/output hashes could not be validated.')
    for name, status in (('overhang', overhang_status), ('standing', standing_status)):
        physical = name == 'overhang' or eligibility[name]['status'] == 'ELIGIBLE'
        measurement[name] = dict(status=status, valid=bool(current and status in ('PASS', 'FAIL') and physical),
                                 scope='reference_evaluation' if physical else 'diagnostic')
    return dict(source_current=source_current, preflight_current=preview_current, material_current=material_current,
                metric_eligibility=eligibility, automatic_review=automatic, measurement=measurement, measurement_current=current is not None,
                measurement_path=str(measurement_path.relative_to(root)) if measurement_path.is_file() else None,
                current_tags=manual.get('tags', automatic.get('tags', [])),
                vlm_current=attempt is not None, vlm_cache_key=(attempt or {}).get('cache_key'),
                vlm_called_at=(attempt or {}).get('called_at')), current


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
                        current_valid={}, diagnostic={})
    completed = {}
    for name in ('overhang', 'standing'):
        measurements['current_valid'][name] = {source: {status: sum(r['source'] == source and r['measurement'][name]['valid'] and r['measurement'][name]['status'] == status for r in values) for status in ('PASS', 'FAIL')} for source in sources}
        measurements['diagnostic'][name] = count(lambda r: r['measurement_current'] and r['measurement'][name]['scope'] == 'diagnostic' and r['measurement'][name]['status'] in ('PASS', 'FAIL'))
        completed[name] = sum(r['measurement'][name]['valid'] for r in values)
    statistics.update(measurements=measurements, completed_reference_measurements=completed)
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
        if row.get('automatic_review') and not row.get('manual_review') and row.get('status') not in ('user_confirmed','user_confirmed_dev'):
            row['selection_note']=row['automatic_review'].get('selection_note',row['selection_note'])
        results[row['case_id']] = current or {}
        row['historical_tags'] = list(dict.fromkeys(row.get('historical_tags', []) + (old_tags[row['case_id']] if old_tags[row['case_id']] != row['current_tags'] else [])))
        row['tags'] = list(row['current_tags'])
        purposes = [name for name in METRICS if row['metric_eligibility'][name]['status'] == 'ELIGIBLE'
                    and (name != 'standing' or row['measurement']['standing']['status'] == 'PASS')]
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
        row['recommendation_candidates_for'] = purposes if row.get('status') != 'excluded' and _classification_ready(row) else []
        if row.get('status') not in ('excluded', 'user_confirmed', 'user_confirmed_dev'):
            row['status'] = 'needs_review'
    recommended = []
    for source in dict.fromkeys(r['source'] for r in values):
        available = [r for r in values if r['source'] == source and r['recommendation_candidates_for']]
        for row in list(_round_robin(available))[:c.get('recommend_per_source', 10)]:
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
    dump(root / 'manifests' / 'shortlist.json', shortlist(c, values))
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
    summary += 'Eligibility is specific to appearance, overhang or standing. Measurement status is recorded separately; missing values are N/A. Standing FAIL can be an eligible reference measurement and awaits review before standing recommendation. Externally supported objects retain ground simulations as diagnostics. Recommendations require user review. Grouping tags are hypotheses; measured grouping and manual confirmation counts come from records. No generation comparison or FEA was run.\n\n'
    summary += '| Case | Category | Recommended for | Standing | G | h mm | Score | Note |\n|---|---|---|---|---|---|---|---|\n'
    for row in recommended:
        objective = (((results[row['case_id']].get('overhang') or {}).get('metrics') or {}).get('partition_objective') or {})
        standing = row['measurement']['standing']['status']
        if row['metric_eligibility']['standing']['status'] == 'NOT_APPLICABLE':
            standing = f'N/A (diagnostic {standing})'
        summary += '| ' + ' | '.join(_cell(v) for v in (row['source_id'], row.get('category'), ', '.join(row['recommended_for']), standing, objective.get('gap_voxels'), objective.get('voxel_pitch_mm'), objective.get('score'), row['selection_note'])) + ' |\n'
    (review / 'summary.md').write_text(summary)
    readme = '# ABO / Toys4K selection review\n\nOpen the contactsheets and individual case images. Transparent PNGs contain no text. Recommendations are a union of eligible uses, not a frozen test set.\n\n'
    for source in sources:
        readme += f'![{source}]({source}_contactsheet.jpg)\n\n'
    for row in values:
        readme += f"- **{row['case_id']}** ({row.get('category') or 'classification pending'}): {row['status']}; recommended for {', '.join(row['recommended_for']) or 'pending'}. {row['selection_note']}"
        if row.get('input_image'):
            readme += f" [input](../{row['input_image']}) · [native eight](../previews/{row['case_id']}/native/meta.json) · [neutral eight](../previews/{row['case_id']}/neutral/meta.json)"
        if row.get('measurement_path'):
            readme += f" · [measurement](../{row['measurement_path']})"
        readme += '\n'
        for name in METRICS:
            record = row['metric_eligibility'][name]
            readme += f"  {name}: {record['status']}. {record['reason']}\n"
    readme += '\nSee summary.md and ../manifests/candidates.csv for actual counts. Standing uses one validated material island under gravity and does not measure connector retention. Dapper G is an empty-voxel proxy; categories and manifest order determine coverage, without score ranking. Original model files are stored outside this package.\n'
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
