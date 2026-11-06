"""Hash-bound continuation from frozen CGR and a completed full baseline."""
import argparse
import json
from pathlib import Path
import time
import yaml

from snap3d.common import write_json,code_identity,sha
from snap3d.export import load_parts
from snap3d.pipeline import evaluate_geometry
from snap3d.rollout import rollout


def verify_reuse(run,config):
    binding=json.loads((run/'reuse_bindings.json').read_text())
    for path,expected in binding['files'].items():
        if sha(run/path)!=expected:raise ValueError('REUSE_HASH_MISMATCH: '+path)
    old=binding['original_config'];allowed={'workers','population','elite','generations'}
    if {k:v for k,v in old.items() if k!='cem'}!={k:v for k,v in config.items() if k!='cem'}:
        raise ValueError('REUSE_PHYSICS_CONFIG_CHANGED')
    if {k:v for k,v in old['cem'].items() if k not in allowed}!={k:v for k,v in config['cem'].items() if k not in allowed}:
        raise ValueError('REUSE_CEM_PARAMETERS_CHANGED')
    baseline=json.loads((run/'rollout/result.json').read_text())
    if baseline['status']!='PASS' or baseline['step_count']!=round(config['rollout']['duration']/config['rollout']['dt']):
        raise ValueError('REUSE_BASELINE_INCOMPLETE')
    return binding,baseline


def continue_case(run,config):
    run=Path(run);started=time.monotonic();completed=[];stage='verify_reuse'
    write_json(run/'continuation_code_identity.json',code_identity())
    def progress(status,**fields):
        write_json(run/'continuation.json',dict(status=status,stage=stage,completed=completed,
            elapsed_seconds=time.monotonic()-started,**fields))
    def execute(name):
        nonlocal stage
        stage=name;progress('RUNNING');print('START',stage,flush=True)
        t=time.monotonic();evaluate_geometry(run,config,stage=stage)
        completed.append(dict(stage=stage,wall_seconds=time.monotonic()-t))
        print(json.dumps(completed[-1]),flush=True)
    try:
        binding,baseline=verify_reuse(run,config)
        stage='initialization_comparison';progress('RUNNING')
        D=json.loads((run/'case.json').read_text())['D_mm']
        after=rollout(load_parts(run/'cgr'),D=D,config=config['rollout'],fixed_ids=baseline['fixed_ids'],
            clearance_mm=config['connector']['clearance_ratio']*D,initial_only=True,model_path=run/'model.xml')
        write_json(run/'initialization_after.json',after)
        if after['status']!='PASS':raise RuntimeError('INITIALIZATION_FAILED: '+str(after.get('reason')))
        if baseline['initial_contact']!=after['initial_contact'] or baseline['flex_element_count']!=after['flex_element_count']:
            raise ValueError('DISPLAY_REMOVAL_INITIAL_STATE_CHANGED')
        write_json(run/'initialization_comparison.json',dict(
            before_seconds=baseline['initialization_wall_seconds'],after_seconds=after['wall_seconds'],
            before_source='hash-verified completed baseline (includes displays)',after_source='initialization only, no displays',
            before_xml=binding['baseline_xml'],after_xml=after['xml_ref'],
            flex_element_count=after['flex_element_count'],initial_contact=after['initial_contact'],unchanged=True))
        print('INITIALIZATION',json.dumps({'before':baseline['initialization_wall_seconds'],'after':after['wall_seconds']}),flush=True)
        execute('cem');execute('export')
        result=json.loads((run/'export/result.json').read_text())
        progress('COMPLETE',stable=result['stable'],zero_gravity_validity=result['zero_gravity_validity'])
        return 'COMPLETE'
    except Exception as error:
        progress('FAILED',error_type=type(error).__name__,reason=str(error));raise


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--config',default='configs/default.yaml')
    a=p.parse_args();continue_case(a.run,yaml.safe_load(Path(a.config).read_text()))


if __name__=='__main__':main()
