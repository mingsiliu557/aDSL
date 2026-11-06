"""Continue one frozen case, with the requested 0.1 s timing probe and 24 h guard."""
import argparse
import json
from pathlib import Path
import time
import yaml

from snap3d.common import write_json,code_identity
from snap3d.export import load_parts
from snap3d.pipeline import evaluate_geometry,rollout_cost
from snap3d.rollout import rollout,select_edges


def continue_case(run,config):
    run=Path(run);started=time.monotonic();completed=[];stage=None
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
        execute('pge');execute('cgr')
        stage='speed_probe';progress('RUNNING');print('START speed_probe',flush=True)
        D=json.loads((run/'case.json').read_text())['D_mm']
        contact=json.loads((run/'cgr/contact.json').read_text())
        parts=load_parts(run/'cgr')
        probe=rollout(parts,D=D,config=dict(config['rollout'],duration=.1),
                      clearance_mm=config['connector']['clearance_ratio']*D,model_path=run/'model.xml')
        write_json(run/'speed_probe/result.json',probe)
        if probe['status']!='PASS':raise RuntimeError('SPEED_PROBE_FAILED: '+str(probe.get('detail',probe.get('reason'))))
        rebuild=contact.get('timing',{}).get('rebuild_seconds',0.)
        preview=rollout_cost(probe,config=config,edge_count=len(contact['edges']),rebuild_seconds=rebuild)
        preview['edge_count_assumption']='all placed edges; baseline selection follows'
        write_json(run/'speed_probe/projection.json',preview)
        completed.append(dict(stage='speed_probe',wall_seconds=probe['wall_seconds']))
        print(json.dumps(preview),flush=True)
        # Even when the preview exceeds 24 h, finish the requested baseline.
        execute('rollout')
        baseline=json.loads((run/'rollout/result.json').read_text())
        selected=[] if baseline['stable'] else select_edges(baseline,contact['edges'],D=D,config=config['rollout'])
        # Use edges selected from the full baseline, not every contact edge.
        count=len(selected)
        cost=rollout_cost(probe,config=config,edge_count=count,rebuild_seconds=rebuild)
        single=max(cost['projected_single_rollout_seconds'],baseline['wall_seconds'])
        total=time.monotonic()-started+(single+rebuild)*cost['nominal_evaluations']+single
        cost.update(baseline_selected_edges=selected,placed_edge_count=len(contact['edges']),
            edge_count_assumption='edges selected for optimization from the full baseline',
            measured_baseline_seconds=baseline['wall_seconds'],conservative_single_rollout_seconds=single,
            projected_case_total_seconds=total,limit_seconds=24*3600,
            excludes='one terminal Boolean volume sweep and report/export overhead',
            stop_after_baseline=total>24*3600)
        write_json(run/'speed_probe/cost_guard.json',cost)
        print(json.dumps(cost),flush=True)
        if cost['stop_after_baseline']:
            progress('STOPPED_COST_LIMIT',cost_guard=cost,stable=baseline['stable'])
            return 'STOPPED_COST_LIMIT'
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
