"""Small per-edge CEM. Objective callback returns geometry + rollout evidence."""
import numpy as np

def optimize(theta0, *, D, config, evaluate, baseline_global, seed=42):
    rng=np.random.default_rng(seed);scale=np.array([D,D,1,1,D,D]); x0=np.array(theta0)/scale
    mean=x0.copy();cov=np.diag(np.square(config['sigma']));floor=np.array(config['floor'])
    best=None; history=[];evaluations=0
    for generation in range(config['generations']):
        candidates=[];draws=0;failures=[]
        while len(candidates)<config['population'] and draws<config['max_draws']:
            draw=draws;draws+=1
            if draw==0: x=x0.copy()
            else:
                x=rng.multivariate_normal(mean,cov)
                if generation==0:
                    # Uniform spherical cap: 11/10/10 as-built / normal10 / normal45.
                    family=(draw-1)%31; cap=np.deg2rad(45 if family<11 or family>=21 else 10)
                    polar=np.arccos(rng.uniform(np.cos(cap),1)); az=rng.uniform(0,2*np.pi)
                    target=np.array([np.sin(polar)*np.cos(az),np.sin(polar)*np.sin(az),np.cos(polar)])
                    alpha=-np.arcsin(target[1]); beta=np.arctan2(target[0],target[2])
                    if family<11:
                        from scipy.spatial.transform import Rotation
                        target=(Rotation.from_rotvec([0,x0[3],0])*Rotation.from_rotvec([x0[2],0,0])).apply(target)
                        alpha=-np.arcsin(target[1]);beta=np.arctan2(target[0],target[2])
                    x[2:4]=[alpha,beta]
            result=evaluate(x*scale);evaluations+=1
            if result['status']!='PASS': failures.append(result.get('reason','FAILED'));continue
            if not np.isfinite([result['local_score'],result['global_score']]).all():raise ValueError('NONFINITE_SCORE')
            eligible=result['global_score']<=baseline_global
            candidates.append((x,result,eligible))
            if eligible and (best is None or result['local_score']<best['result']['local_score']):
                best=dict(theta=(x*scale).tolist(),result=result)
        ranked=sorted((p for p in candidates if p[2]),key=lambda p:p[1]['local_score'])
        history.append(dict(generation=generation,draws=draws,valid=len(candidates),eligible=len(ranked),
                            theta0_evaluated=True,failures=failures,best_score=None if not ranked else ranked[0][1]['local_score']))
        if len(ranked)<config['elite']: break
        elites=np.array([p[0] for p in ranked[:config['elite']]])
        mean=elites.mean(axis=0);centered=elites-mean
        cov=centered.T@centered/len(elites)
        cov+=np.diag(np.maximum(0.,floor*floor-np.diag(cov)))
        # PSD jitter is dimensionless numerical conditioning, not geometry clearance.
        cov+=np.eye(6)*1e-16
    return dict(best=best,history=history,evaluations=evaluations)
