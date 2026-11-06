"""CEM: deterministic generation plans, ordered results, parallel physics."""
import multiprocessing
import os
import time
import numpy as np

FAMILIES=('as_built_45','normal_10','normal_45')


def effective_workers(config):
    requested=config.get('workers',8)
    if isinstance(requested,bool) or not isinstance(requested,int) or requested<1:
        raise ValueError('CEM workers must be a positive integer')
    cores=len(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else (os.cpu_count() or 1)
    return min(requested,cores)


def optimize(theta0, *, D, config, evaluate=None, baseline_global, seed=42,
             prepare=None,simulate=None,on_result=None):
    """Plan a generation in order in both serial and parallel modes.

    Only ordered, consumed results affect selection. On eligible stable output,
    terminate unconsumed parallel work; preparation/RNG history remains identical
    to workers=1. Legacy combined evaluate callbacks remain serial.
    """
    if prepare is None:
        if evaluate is None:raise ValueError('CEM requires evaluate or prepare/simulate')
        workers=1
    else:
        if simulate is None:raise ValueError('CEM prepare requires simulate')
        workers=effective_workers(config)
    pool=None if workers==1 else multiprocessing.get_context('spawn').Pool(workers)
    terminated=False
    rng=np.random.default_rng(seed);scale=np.array([D,D,1,1,D,D]);x0=np.array(theta0)/scale
    mean=x0.copy();cov=np.diag(np.square(config['sigma']));floor=np.array(config['floor'])
    best=None;history=[];evaluations=0;early_stop=None
    try:
        for generation in range(config['generations']):
            started=time.monotonic();candidates=[];draws=0;failures=[];plan=[]
            family_counts=dict.fromkeys(FAMILIES,0);submitted=0;consumed=0
            while len(candidates)<config['population'] and draws<config['max_draws'] and early_stop is None:
                pending=[];feasible=0;capacity=config['population']-len(candidates)
                while feasible<capacity and draws<config['max_draws']:
                    draw=draws;draws+=1;family=None
                    if draw==0:x=x0.copy()
                    else:
                        x=rng.multivariate_normal(mean,cov)
                        if generation==0:
                            family=(draw-1)%3
                            cap=np.deg2rad(10 if family==1 else 45)
                            polar=np.arccos(rng.uniform(np.cos(cap),1));az=rng.uniform(0,2*np.pi)
                            target=np.array([np.sin(polar)*np.cos(az),np.sin(polar)*np.sin(az),np.cos(polar)])
                            alpha=-np.arcsin(target[1]);beta=np.arctan2(target[0],target[2])
                            if family==0:
                                from scipy.spatial.transform import Rotation
                                target=(Rotation.from_rotvec([0,x0[3],0])*Rotation.from_rotvec([x0[2],0,0])).apply(target)
                                alpha=-np.arcsin(target[1]);beta=np.arctan2(target[0],target[2])
                            x[2:4]=[alpha,beta];family_counts[FAMILIES[family]]+=1
                    theta=x*scale;evaluations+=1
                    meta=dict(generation=generation,candidate_index=draw+1,family=None if family is None else FAMILIES[family])
                    if prepare is None:
                        job=dict(status='PASS',job=None)
                    else:job=prepare(theta,**meta)
                    plan.append(dict(**meta,theta=theta.tolist(),feasible=job['status']=='PASS'))
                    if job['status']!='PASS':pending.append((x,theta,job,'ready',meta));continue
                    feasible+=1
                    if prepare is None:pending.append((x,theta,None,'legacy',meta))
                    elif pool is None:pending.append((x,theta,job['job'],'serial',meta))
                    else:
                        pending.append((x,theta,pool.apply_async(simulate,(job['job'],)),'parallel',meta));submitted+=1
                # One generation plan is prepared before consuming results in
                # either mode. Parallel completion order cannot change adoption.
                for x,theta,value,mode,meta in pending:
                    if mode=='ready':result=value
                    else:
                        if mode=='parallel':result=value.get()
                        elif mode=='serial':result=simulate(value);submitted+=1
                        else:result=evaluate(theta);submitted+=1
                        consumed+=1
                    if on_result is not None:on_result(theta,result)
                    if result['status']!='PASS':failures.append(result.get('reason','FAILED'));continue
                    if not np.isfinite([result['local_score'],result['global_score']]).all():raise ValueError('NONFINITE_SCORE')
                    eligible=result['global_score']<=baseline_global
                    candidates.append((x,result,eligible))
                    if eligible and (best is None or result['local_score']<best['result']['local_score']):
                        best=dict(theta=theta.tolist(),result=result)
                    stable=result.get('rollout',{}).get('stable',result.get('stable',False))
                    if eligible and stable:
                        best=dict(theta=theta.tolist(),result=result)
                        early_stop=dict(generation=generation+1,candidate_index=meta['candidate_index'],
                                        consumed_rollouts_in_generation=consumed)
                        if pool is not None:pool.terminate();terminated=True
                        break
            ranked=sorted((p for p in candidates if p[2]),key=lambda p:p[1]['local_score'])
            history.append(dict(generation=generation,draws=draws,valid=len(candidates),eligible=len(ranked),
                theta0_evaluated=True,failures=failures,best_score=None if not ranked else ranked[0][1]['local_score'],
                workers=workers,wall_seconds=time.monotonic()-started,sampling_plan=plan,family_counts=family_counts,
                submitted_rollouts=submitted,consumed_rollouts=consumed,early_stop=early_stop))
            if early_stop is not None or len(ranked)<config['elite']:break
            elites=np.array([p[0] for p in ranked[:config['elite']]])
            mean=elites.mean(axis=0);centered=elites-mean
            cov=centered.T@centered/len(elites)
            cov+=np.diag(np.maximum(0.,floor*floor-np.diag(cov)))
            cov+=np.eye(6)*1e-16
    finally:
        if pool is not None:
            if not terminated:pool.close()
            pool.join()
    return dict(best=best,history=history,evaluations=evaluations,workers=workers,early_stop=early_stop)
