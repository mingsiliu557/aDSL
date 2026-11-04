from pathlib import Path
import numpy as np
import yaml
from snap3d.cem import optimize
from snap3d.rollout import select_edges

CFG=yaml.safe_load(Path('configs/default.yaml').read_text())
def test_cem_theta0_each_generation_and_full_covariance():
    calls=[];theta=[0,0,0,0,1,2]
    def evaluate(x):
        calls.append(x.copy());return dict(status='PASS',local_score=float(np.sum(x*x)),global_score=.5)
    result=optimize(theta,D=150,config=CFG['cem'],evaluate=evaluate,baseline_global=1)
    assert result['evaluations']==96 and len(result['history'])==3
    for k in [0,32,64]:np.testing.assert_allclose(calls[k],theta)
    assert result['best']['result']['global_score']<=1

def test_cem_redraw_cap_no_fabricated_success():
    result=optimize([0,0,0,0,1,2],D=150,config=CFG['cem'],evaluate=lambda _:dict(status='FAIL',reason='GEOMETRY'),baseline_global=1)
    assert result['evaluations']==320 and result['best'] is None

def test_global_regression_never_adopted():
    result=optimize([0,0,0,0,1,2],D=150,config=CFG['cem'],evaluate=lambda _:dict(status='PASS',local_score=0.,global_score=2.),baseline_global=1)
    assert result['best'] is None
