"""Stage cache invariants with fixture files and a simulated VLM runner only."""
import asyncio
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import common
import preflight_candidates as runner
from common import cache_key, dump, load, sha
from build_review_pack import review_evidence


@pytest.fixture
def cache_case(tmp_path, monkeypatch):
    root = tmp_path / 'selection_v1'
    raw = root / 'raw' / 'ABO' / 'fixture' / 'original.glb'
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b'original fixture model')
    profile = tmp_path / 'profile.yaml'
    profile.write_text('provider: fixture\napi: responses\nparams:\n  model: fixture-model\ncredential:\n  env: FIXTURE_API_KEY\n')
    c = dict(root=str(root), project_root=str(Path(__file__).parents[2]), longest_extent_mm=150,
             render=dict(width=512, height=512, samples=32, threads=4, timeout_seconds=300),
             physics=dict(material=dict(density_kg_m3=1240), standing=dict(duration_seconds=5)),
             vlm=dict(profile=str(profile), enabled=True, max_calls=50, timeout_seconds=180))
    row = dict(case_id='ABO_fixture', source='ABO', source_id='fixture', category='lamp',
               raw_mesh=str(raw.relative_to(root)), raw_sha256=sha(raw), status='needs_review', tags=[])
    # Library version lookup is external to these deterministic input/file tests.
    monkeypatch.setattr(common, 'native_versions', lambda c, stage: dict(fixture='1'))
    return root, c, row


def _pictures(root, row):
    input_path = root / 'previews' / row['case_id'] / 'input.png'
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_bytes(b'input image fixture')
    row['input_image'] = str(input_path.relative_to(root))
    neutral = input_path.parent / 'neutral'
    neutral.mkdir(exist_ok=True)
    for i in range(1, 9):
        (neutral / f'render_{i:04d}.png').write_bytes(f'neutral image {i}'.encode())
    return input_path


@pytest.fixture
def fake_vlm(monkeypatch):
    state = dict(calls=0, failure=False)

    @dataclass
    class Profile:
        max_retries: int = 1
        timeout: int = 1
        max_tokens: int = 1

        @classmethod
        def load(cls, path):
            return cls()

        def runtime_metadata(self):
            return dict(provider='fixture', model='fixture-model')

        def agent_model(self, workspace=None):
            return 'fixture-model'

        def model_settings(self):
            return {}

    @dataclass
    class Usage:
        requests: int = 1

    class Runner:
        @staticmethod
        async def run(agent, **kwargs):
            state['calls'] += 1
            if state['failure']:
                raise RuntimeError('fixture VLM unavailable')
            output = runner.Labels(tags=['complex_surface'], selection_note='Fixture visual evidence.',
                                   needs_manual_confirmation=False, pose_observation='Upright ground use.',
                                   appearance_input_usable=True, standing_applicable=True)
            return SimpleNamespace(final_output=output, context_wrapper=SimpleNamespace(usage=Usage()), new_items=[])

    agents = ModuleType('agents')
    agents.Agent = lambda **kwargs: SimpleNamespace(**kwargs)
    agents.Runner = Runner
    profile_module = ModuleType('adsl.agents.utils.config')
    profile_module.ModelProfile = Profile
    input_module = ModuleType('adsl.agents.utils.inputs')
    input_module.user_input = lambda text, pictures: dict(text=text, pictures=pictures)
    monkeypatch.setitem(sys.modules, 'agents', agents)
    monkeypatch.setitem(sys.modules, 'adsl.agents.utils.config', profile_module)
    monkeypatch.setitem(sys.modules, 'adsl.agents.utils.inputs', input_module)
    return state


@pytest.mark.parametrize('changed', ['image', 'prompt', 'category'])
def test_label_cache_tracks_actual_image_prompt_and_category(cache_case, fake_vlm, monkeypatch, changed):
    root, c, row = cache_case
    input_path = _pictures(root, row)
    first = asyncio.run(runner.label(c, row))
    assert first['status'] == 'PASS' and fake_vlm['calls'] == 1
    assert runner.current_label(c, row)['cache_current']
    if changed == 'image':
        input_path.write_bytes(b'changed actual input image')
    elif changed == 'prompt':
        monkeypatch.setattr(runner, 'LABEL_PROMPT', runner.LABEL_PROMPT + ' Changed prompt contract.')
    else:
        row['category'] = 'chair_stool'
    assert runner.current_label(c, row) is None
    second = asyncio.run(runner.label(c, row))
    assert second['status'] == 'PASS' and second['cache_key'] != first['cache_key']
    assert fake_vlm['calls'] == 2 and runner.vlm_attempt_count(c) == 2
    attempts = list((root / 'measurements' / row['case_id'] / 'vlm' / 'attempts').glob('*/attempt.json'))
    assert len(attempts) == 2 and load(attempts[0])['input_files_sha256']


def test_failed_label_cache_is_reused_without_repeating_request(cache_case, fake_vlm):
    root, c, row = cache_case
    _pictures(root, row)
    fake_vlm['failure'] = True
    first = asyncio.run(runner.label(c, row))
    second = asyncio.run(runner.label(c, row))
    assert first['status'] == second['status'] == 'UNAVAILABLE'
    assert second['cache_current'] and first['attempt_id'] == second['attempt_id']
    assert fake_vlm['calls'] == runner.vlm_attempt_count(c) == 1
    assert not list((root / 'measurements').rglob('messages.json'))


def test_full_budget_still_serves_current_success_and_failed_cache(cache_case, fake_vlm):
    root, c, row = cache_case
    _pictures(root, row)
    c['vlm']['max_calls'] = 1
    first = asyncio.run(runner.label(c, row))
    assert fake_vlm['calls'] == 1
    cached = asyncio.run(runner.label(c, row))
    assert cached['status'] == 'PASS' and cached['cache_current']
    row['category'] = 'changed'
    unavailable = asyncio.run(runner.label(c, row))
    assert unavailable['status'] == 'NOT_CALLED' and fake_vlm['calls'] == 1
    row['category'] = 'lamp'
    attempt_path = root / 'measurements' / row['case_id'] / 'vlm' / 'attempts' / first['cache_key'] / 'attempt.json'
    dump(attempt_path, dict(first, status='UNAVAILABLE', output=None))
    cached_failure = asyncio.run(runner.label(c, row))
    assert cached_failure['status'] == 'UNAVAILABLE' and cached_failure['cache_current']
    assert fake_vlm['calls'] == 1


def test_label_snapshot_hash_corruption_is_rejected(cache_case, fake_vlm):
    root, c, row = cache_case
    _pictures(root, row)
    attempt = asyncio.run(runner.label(c, row))
    captured = next(iter(attempt['input_files_sha256']))
    (root / captured).write_bytes(b'corrupt saved VLM snapshot')
    assert runner.current_label(c, row) is None
    original=load(root/'measurements'/row['case_id']/'vlm/attempts'/attempt['cache_key']/'attempt.json')
    refused=asyncio.run(runner.label(c,row))
    assert refused['status']=='UNAVAILABLE' and fake_vlm['calls']==1
    assert runner.vlm_attempt_count(c)==1
    assert load(root/'measurements'/row['case_id']/'vlm/attempts'/attempt['cache_key']/'attempt.json')==original


def _fake_process(monkeypatch):
    state = dict(calls=0)

    def process(command, folder, timeout, c):
        state['calls'] += 1
        output = Path(command[command.index('--output') + 1])
        output.mkdir(parents=True, exist_ok=True)
        stage = command[command.index('--stage') + 1]
        if stage == 'import':
            dump(output / 'basic.json', dict(status='PASS', connected_components=1, standing_eligible=True))
            dump(output / 'use_pose.json', dict(source_axis='fixture', normalization_mm=[]))
            dump(output / 'assembly_manifest.json', dict(parts=['fixture']))
            dump(output / 'reference_source.json', dict(source='fixture'))
            for name in ('reference.glb', 'reference_whole.stl', 'reference_whole.body.npz'):
                (output / name).write_bytes(name.encode())
        else:
            for i in range(1, 9):
                (output / f'render_{i:04d}.png').write_bytes(f'fixture render {i}'.encode())
            dump(output / 'meta.json', dict(views=8))
        return dict(status='PASS', elapsed_seconds=0)

    monkeypatch.setattr(runner, 'process', process)
    return state


def test_source_mirror_config_changes_do_not_invalidate_preflight(cache_case, monkeypatch):
    root, c, row = cache_case
    state = _fake_process(monkeypatch)
    first = runner.preflight(c, row, root / 'config.json')
    assert state['calls'] == 3
    changed = dict(c, toys_archive='/new/mirror.zip', toys_archive_sha256='new-index',
                   toys_metadata='/new/source.csv', seed=99, shortlist_count=24, recommend_per_source=10)
    changed['render'] = dict(c['render'], timeout_seconds=999)
    assert runner.validated_preflight(changed, row)['cache_key'] == first['cache_key']
    assert runner.preflight(changed, row, root / 'another-config.json')['cache_key'] == first['cache_key']
    assert state['calls'] == 3


def test_candidate_classification_json_and_row_tags_are_not_geometry_hash_inputs(cache_case, monkeypatch):
    root, c, row = cache_case
    state = _fake_process(monkeypatch)
    first = runner.preflight(c, row, root / 'config.json')
    row.update(category='chair_stool', tags=['standing_sensitive'], status='excluded',
               classification_review=dict(scope='out_of_scope', needs_review=True))
    dump(root / 'derived' / row['case_id'] / 'candidate.json', row)
    assert not any(Path(p).name == 'candidate.json' for p in first['files_sha256'])
    assert runner.validated_preflight(c, row)['cache_key'] == first['cache_key']
    assert runner.preflight(c, row, root / 'config.json')['cache_key'] == first['cache_key']
    assert state['calls'] == 3


def _save_measurement(root, c, row):
    inputs = runner._measurement_inputs(c, row)
    record = dict(cache_key=cache_key(c, row['raw_sha256'], stage='measurement', inputs=inputs),
                  status='COMPLETED', standing=dict(status='PASS'), overhang=dict(status='PASS'),
                  files_sha256={str((root / 'derived' / row['case_id'] / name).relative_to(root)): digest
                                for name, digest in inputs['files'].items()})
    dump(root / 'measurements' / row['case_id'] / 'reference_measurement_v2.json', record)
    return record


def test_physics_changes_invalidate_measurement_without_preview_or_label_invalidation(cache_case, fake_vlm, monkeypatch):
    root, c, row = cache_case
    _fake_process(monkeypatch)
    preview = runner.preflight(c, row, root / 'config.json')
    row.update({k: preview[k] for k in ('input_image', 'reference_mesh', 'use_pose')})
    asyncio.run(runner.label(c, row))
    measurement = _save_measurement(root, c, row)
    assert runner.validated_measurement(c, row)['cache_key'] == measurement['cache_key']
    changed = deepcopy(c)
    changed['physics']['standing']['duration_seconds'] = 9
    assert runner.validated_measurement(changed, row) is None
    assert runner.validated_preflight(changed, row)['cache_key'] == preview['cache_key']
    assert runner.current_label(changed, row)['cache_current']
    assert fake_vlm['calls'] == 1


def test_current_v2_measurement_report_path_precedes_legacy_record(cache_case, fake_vlm, monkeypatch):
    root, c, row = cache_case
    _fake_process(monkeypatch)
    preview = runner.preflight(c, row, root / 'config.json')
    row.update({k: preview[k] for k in ('input_image', 'reference_mesh', 'use_pose')})
    asyncio.run(runner.label(c, row))
    _save_measurement(root, c, row)
    dump(root / 'measurements' / row['case_id'] / 'reference_measurement.json', dict(status='historical'))
    evidence, result = review_evidence(c, row)
    assert evidence['measurement_path'].endswith('/reference_measurement_v2.json')
    assert evidence['measurement_current'] and evidence['measurement']['report_exists']
    assert result['status'] == 'COMPLETED'
    assert all(r['status'] == 'ELIGIBLE' for r in evidence['metric_eligibility'].values())


def test_separate_processes_cannot_exceed_shared_label_budget(cache_case, fake_vlm):
    import multiprocessing
    if 'fork' not in multiprocessing.get_all_start_methods():
        pytest.skip('Remote screening uses POSIX process locking')
    root,c,row=cache_case
    _pictures(root,row);c['vlm']['max_calls']=1
    context=multiprocessing.get_context('fork');queue=context.Queue();start=context.Barrier(2)
    def call(category):
        current=dict(row,category=category)
        start.wait(timeout=5)
        queue.put(asyncio.run(runner.label(c,current))['status'])
    processes=[context.Process(target=call,args=(name,)) for name in ('lamp','chair_stool')]
    for process in processes:process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode==0
    assert sorted([queue.get(timeout=2),queue.get(timeout=2)])==['NOT_CALLED','PASS']
    assert runner.vlm_attempt_count(c)==1
