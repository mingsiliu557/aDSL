from adsl.agents.models import CheckerResult, CheckerSpec
from adsl.agents.service import _checker_evidence
from experiments.planned_checks.run_edit_smoke import reuse_calibration


def test_cached_report_and_artifacts_are_in_agent_workspace(tmp_path):
    calibration=tmp_path/'outside_agent'/'calibration'
    source=calibration/'checkers'/'overhang'
    source.mkdir(parents=True)
    (source/'surface.json').write_text('{}')
    workspace=tmp_path/'agent'
    spec=CheckerSpec(name='overhang',command=['unused'])
    result=CheckerResult(checker='overhang',status='PASS',summary='measurement completed',
        metrics={'overhang_area_mm2':12.5},artifacts={'surface':str(source/'surface.json')})
    cached=reuse_calibration(spec,result,calibration,workspace/'rounds/round_01')
    feedback=_checker_evidence([cached],workspace=workspace)
    assert feedback
    assert (cached.output_dir/'result.json').is_file()
    assert cached.output_dir.is_relative_to(workspace)
    assert cached.result.artifacts['surface']==str(cached.output_dir/'surface.json')
    assert cached.result.metrics==result.metrics
    assert (source/'surface.json').is_file()
