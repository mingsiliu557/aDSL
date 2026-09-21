"""Small batch-entry checks; no API or geometry execution."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from adsl.agents.utils.io import write_json, read_json
from experiments.fixed_assembly_prompt import run_six as batch


@pytest.mark.parametrize('changed,tool_events,error_type,expected', [
    (False, [], 'InternalServerError', 1),
    (True, [{'tool': 'apply_patch', 'success': True}], 'InternalServerError', 1),
    (False, [{'tool': 'read_file', 'success': True}], 'InternalServerError', 1),
    (False, [], 'BadRequestError', 1),
    (False, [], 'ValueError', 0),
])
def test_api_interruption_is_uncharged_even_after_tools_or_partial_patch(tmp_path,changed,tool_events,error_type,expected):
    from experiments.fixed_assembly_prompt import retry_api_cases as retry
    source=tmp_path/'source.py';source.write_text('saved geometry')
    before=retry.file_hash(source)
    if changed:source.write_text('changed geometry')
    write_json(tmp_path/'assembly_versions.json',dict(versions={'attempt_0001':dict(
        source=str(source),reviews={'edit_outcome':dict(status='TOOL_ERROR',tool_events=tool_events,
            before_sha256=before,after_sha256=retry.file_hash(source),reason='simulated failure')})}))
    (tmp_path/'repair_history.jsonl').write_text('{"attempt_id":"attempt_0001","recorded_at":10}\n')
    write_json(tmp_path/'api_calls/001.json',dict(stage='assembly_repair:2',status='ERROR',
        started_at=11,error_type=error_type,reason='simulated failure'))
    credits=retry.api_interrupted_repairs(tmp_path)
    assert len(credits)==expected
    assert source.read_text()==('changed geometry' if changed else 'saved geometry')
    write_json(tmp_path/'retry_plan.json',dict(jobs=[dict(
        name='new_SF03',case_id='SF03',previous_version='original',used_repairs=2)]))
    # The existing summary must separate reservations from chargeable repair attempts.
    (tmp_path/'continued').mkdir()
    (tmp_path/'continued/new_SF03').symlink_to(tmp_path,target_is_directory=True)
    retry.summarize(tmp_path)
    result=read_json(tmp_path/'RESULTS.json')[0]
    assert result['new_reserved_attempts']==1
    assert result['new_repairs']==1-expected and result['cumulative_repairs']==3-expected


def test_retry_uses_latest_generated_source_and_cumulative_budget(tmp_path,monkeypatch):
    from experiments.fixed_assembly_prompt import retry_api_cases as retry
    previous=tmp_path/'previous';work=previous/'fresh/SF13/generate'
    profile=tmp_path/'profile';profile.write_text('frozen')
    work.mkdir(parents=True)
    (work/'source.py').write_text('latest generated source')
    for filename in ('plan.json','runtime_config.json'): write_json(work/filename,{})
    for folder in ('assembly','render'): (work/folder).mkdir()
    write_json(work/'assembly_versions.json',dict(working='original',versions={
        'original':dict(source=str(work/'source.py'),execution={'output_root':str(work)})}))
    write_json(previous/'retry_plan.json',dict(profile_sha256=retry.file_hash(profile),
        max_total_repairs_per_case=4,previous_batch_tokens=100,previous_batch_unknown_usage=1,
        jobs=[dict(name='new_SF13',original_group='fresh')]))
    write_json(previous/'RESULTS.json',[dict(case='new_SF13',workspace=str(work),
        stop_reason='FLOW_ERROR',cumulative_repairs=1,known_tokens=20,unknown_usage_calls=1)])
    monkeypatch.setattr(retry,'NAMES',('new_SF13',))
    monkeypatch.setattr(retry.prompt,'PROFILE',profile)
    monkeypatch.setattr(retry,'assert_version',lambda version:None)
    root=tmp_path/'next';retry.prepare(previous,root)
    plan=read_json(root/'retry_plan.json');job=plan['jobs'][0]
    assert job['previous_version']=='original'
    assert job['used_repairs']==1 and job['remaining_repairs']==3 and job['max_rounds']==4
    assert (root/'inputs/new_SF13/source.py').read_text()=='latest generated source'
    assert not (root/'fresh').exists()
    assert plan['previous_batch_tokens']==120 and plan['previous_batch_unknown_usage']==2
    assert 'preflight' not in plan

    # Continue only the selected case; keep approved and exhausted siblings untouched.
    before=tmp_path/'old_profile'
    before.write_text('params:\n  max_retries: 0\n  model: frozen\n')
    profile.write_text('params:\n  max_retries: 3\n  model: frozen\n')
    prior=read_json(previous/'retry_plan.json');prior['profile_sha256']=retry.file_hash(before)
    prior['jobs'] += [dict(name='existing_SF13_open',original_group='existing'),dict(name='new_SF03',original_group='fresh')]
    write_json(previous/'retry_plan.json',prior)
    rows=read_json(previous/'RESULTS.json')
    rows += [dict(case='existing_SF13_open',approved=True,stop_reason='passed',cumulative_repairs=1,known_tokens=5,unknown_usage_calls=0),
             dict(case='new_SF03',approved=False,stop_reason='TOOL_ERROR',cumulative_repairs=4,known_tokens=8,unknown_usage_calls=1)]
    write_json(previous/'RESULTS.json',rows)
    retry.prepare(previous,tmp_path/'selected',cases=['new_SF13'],previous_profile=before)
    selected=read_json(tmp_path/'selected/retry_plan.json')
    assert selected['profile_amendment']['after']==3
    assert selected['jobs'][0]['remaining_repairs']==3
    assert selected['omitted_cases']=={'existing_SF13_open':'already_approved','new_SF03':'repair_budget_exhausted'}
    profile.write_text('params:\n  max_retries: 3\n  model: changed\n')
    with pytest.raises(AssertionError,match='only max_retries'):
        retry.prepare(previous,tmp_path/'invalid',cases=['new_SF13'],previous_profile=before)
    assert not (tmp_path/'invalid').exists()


def test_failed_case_continues_once_without_budget_reset(tmp_path, monkeypatch):
    jobs=[dict(name='first'),dict(name='second')]
    def prepare(root): write_json(root/'six_cases.json',{'jobs':jobs})
    monkeypatch.setattr(batch,'prepare',prepare)
    monkeypatch.setattr(batch,'summarize',lambda root:None)
    invoked=[]
    def run(command,**kwargs):
        invoked.append(command[-1])
        return SimpleNamespace(returncode=1 if len(invoked)==1 else 0)
    monkeypatch.setattr(batch.subprocess,'run',run)
    batch.run(tmp_path)
    assert invoked==['first','second']
    assert read_json(tmp_path/'jobs/first/state.json')['status']=='ERROR'
    assert read_json(tmp_path/'jobs/second/state.json')['status']=='COMPLETED'


def test_existing_normal_control_still_requests_review(tmp_path, monkeypatch):
    profile=tmp_path/'profile';profile.write_text('frozen')
    asset=tmp_path/'asset';asset.mkdir()
    (asset/'source.py').write_text('original')
    write_json(asset/'assembly/assembly_manifest.json',{})
    item=dict(name='control',case_id='SF13',group='existing',asset_root=str(asset),
              source_sha256=batch.file_hash(asset/'source.py'),
              manifest_sha256=batch.file_hash(asset/'assembly/assembly_manifest.json'))
    write_json(tmp_path/'six_cases.json',dict(profile=str(profile),profile_sha256=batch.file_hash(profile),
                                            implementation_sha256={},max_evaluation_rounds=5,jobs=[item]))
    measurements=[]
    monkeypatch.setattr(batch.saved,'measure',lambda *a,**kw:measurements.append((a,kw)))
    repair=AsyncMock();monkeypatch.setattr(batch.saved,'repair',repair)
    asyncio.run(batch.job(tmp_path,'control'))
    assert measurements[0][1]['asset_root']==asset
    assert measurements[0][1]['max_rounds']==5
    repair.assert_awaited_once_with(tmp_path/'existing/control',review_all=True)


@pytest.mark.parametrize('max_rounds',[2,5])
def test_normal_control_reaches_native_loop_without_actionable_failure(tmp_path,monkeypatch,max_rounds):
    from test_fixed_assembly import plan_data
    saved=batch.saved
    write_json(tmp_path/'input.json',dict(fixed_assembly={'mm_per_unit':1.,'fit_offset_mm':.2,
        'final_size_mm':[100.,32.,200.],'validation_mode':'visual_only'},requirement='book shelf',
        source_sha256='test',source_origin=str(tmp_path/'source.py'),case_id='SF13',max_rounds=max_rounds))
    write_json(tmp_path/'baseline_result.json',dict(checker='assembly_topology',status='PASS',summary='control'))
    write_json(tmp_path/'plan.json',plan_data())
    (tmp_path/'source.py').write_text('original')
    runtime=SimpleNamespace(usage=SimpleNamespace(totals=lambda:{}))
    monkeypatch.setattr(saved,'PromptWorkflow',lambda profile:SimpleNamespace(_runtime=lambda *a,**kw:runtime))
    loop=AsyncMock(return_value=SimpleNamespace(approved=True))
    monkeypatch.setattr(saved,'iterate_fixed_assembly',loop)
    asyncio.run(saved.repair(tmp_path,review_all=True))
    assert loop.await_count==1
    request=loop.call_args.kwargs['request']
    assert request.max_rounds==max_rounds
    assert f'At most {max_rounds-1} source edits, {max_rounds} evaluations' in request.requirement
    assert [s.name for s in request.checker_specs]==['assembly_topology']
    assert read_json(tmp_path/'run_result.json')['approved']
