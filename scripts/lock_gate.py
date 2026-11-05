"""D=150 artificial proxy acceptance followed by the three mandatory lock gates."""
from pathlib import Path
import argparse
import json
import time

import manifold3d as mf
import numpy as np
import yaml

from snap3d.connector import shapes
from snap3d.vendor import solid_mesh, checked
from snap3d.proxies import build_zone_proxies, verify_zone_proxies
from snap3d.rollout import rollout
from snap3d.common import write_json, digest

LABELS = [('zero_gravity', True, 0.), ('no_head', False, 9.81), ('headed', True, 9.81)]


def fixture(headed):
    D = 150.
    scale = D / float(np.linalg.norm([18., 18., 24.]))
    r = .006 * D
    length = 5 * D / 256
    receiver = mf.Manifold.cube([18, 18, 12]).translate([-9, -9, 12])
    for x in [-8, 6]:
        receiver = receiver + mf.Manifold.cube([2, 18, 12]).translate([x, -9, 0])
    donor = mf.Manifold.cube([5, 5, 4]).translate([-2.5, -2.5, 8])
    receiver = checked(receiver.scale([scale]*3))
    donor = checked(donor.scale([scale]*3))
    transform = np.eye(4)
    transform[2, 3] = 12 * scale
    peg, slot = shapes(radius=r, length=length, D=D, headed=headed)
    peg = checked(peg.transform(transform[:3]))
    slot = checked(slot.transform(transform[:3]))
    base = {'receiver': solid_mesh(receiver), 'peg': solid_mesh(donor)}
    parts = {'receiver': solid_mesh(checked(receiver-slot)), 'peg': solid_mesh(checked(donor+peg))}
    record = dict(edge='lock', donor='peg', receiver='receiver', T=transform.tolist(),
                  radius=r, length=length, clearance=.25*D/256, overhang=.75*D/256,
                  head_thickness=D/256, headed=headed)
    return parts, base, record, D, length


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    config = yaml.safe_load(Path('configs/default.yaml').read_text())
    accepted = []
    tests = []
    prepared = {}

    def finish():
        passed = len(accepted) == 3 and all(r['pass_'] for r in accepted) and len(tests) == 3 and all(r['pass_'] for r in tests)
        report = dict(status='PASS' if passed else 'FAIL', proxy_acceptance=accepted, tests=tests,
                      config_sha256=digest(config), next_stage_allowed=bool(passed),
                      not_run=[label for label, _, _ in LABELS if label not in {r['test'] for r in tests}],
                      fixture_diagonal_mm=150., coacd_timeout_s_per_part=300,
                      far_threshold_ratio=config['coacd']['far_threshold_ratio'],
                      entrance_approximation='No proxy in local z [-c,c]; no lock-head or shoulder geometry changed.')
        write_json(out/'gate.json', report)
        return report

    # Accept all three representations before starting any time integration.
    for label, headed, gravity in LABELS:
        parts, base, record, D, length = fixture(headed)
        folder = out/label
        folder.mkdir()
        for part, mesh in parts.items():
            mesh.export(folder/f'{part}.stl')
            base[part].export(folder/f'{part}_base.stl')
        write_json(folder/'connector.json', record)
        cfg = dict(config['rollout'], friction=[0., 0., 0.], gravity=gravity)
        started = time.monotonic()
        try:
            proxies, build = build_zone_proxies(base, [record], D=D, config=config['coacd'], cache_dir=out/'proxy_cache')
            for part, values in proxies.items():
                for index, mesh in enumerate(values):
                    mesh.export(folder/f'{part}_proxy_{index:04d}.stl')
            verification = verify_zone_proxies(parts, proxies, [record], D=D, roles=build['blocks'],
                rollout_config=cfg, clearance_mm=record['clearance'], fixed_ids=['receiver'])
            write_json(folder/'proxy_build.json', build)
            write_json(folder/'proxy_verification.json', verification)
            ok = verification['status'] == 'PASS'
            accepted.append(dict(test=label, pass_=ok, status=verification['status'],
                elapsed_s=time.monotonic()-started, proxy_counts=build['proxy_counts'],
                zones=build['zones'], verification=verification, far=build['far']))
            prepared[label] = (parts, proxies, cfg, D, length, record)
        except (ValueError, RuntimeError) as error:
            accepted.append(dict(test=label, pass_=False, status='UNAVAILABLE',
                elapsed_s=time.monotonic()-started, reason=str(error).split(':', 1)[0], detail=str(error)))
        print(json.dumps(dict(phase='proxy_acceptance', **accepted[-1])), flush=True)
        if not accepted[-1]['pass_']:
            finish()
            raise SystemExit(2)

    for label, _, _ in LABELS:
        parts, proxies, cfg, D, length, record = prepared[label]
        folder = out/label
        started = time.monotonic()
        result = rollout(parts, D=D, config=cfg, coacd_config=config['coacd'],
                         clearance_mm=record['clearance'], fixed_ids=['receiver'], proxy_cache=proxies)
        if 'xml' in result:
            (folder/'model.xml').write_text(result.pop('xml'))
        measured = result.get('metrics', {})
        fixed = measured.get('receiver', {}).get('max_displacement_mm')
        motion = measured.get('peg', {}).get('max_displacement_mm')
        frames = result.get('frames', [])
        drop = None if not frames else frames[-1]['peg']['position_mm'][2]-frames[0]['peg']['position_mm'][2]
        ok = result['status'] == 'PASS' and fixed is not None and fixed <= 1e-4*D
        if label == 'zero_gravity':
            ok = ok and motion is not None and motion < 1e-4*D
        elif label == 'no_head':
            ok = ok and drop is not None and drop < -length
        else:
            ok = ok and drop is not None and drop > -2*record['clearance'] and motion < length
        result['gate_pass'] = bool(ok)
        write_json(folder/'result.json', result)
        tests.append(dict(test=label, pass_=bool(ok), elapsed_s=time.monotonic()-started,
                          status=result['status'], reason=result.get('reason'),
                          fixed_displacement_mm=fixed, peg_displacement_mm=motion,
                          peg_final_delta_z_mm=drop, length_mm=length, clearance_mm=record['clearance']))
        print(json.dumps(dict(phase='dynamic_gate', **tests[-1])), flush=True)
        if not ok:
            break
    report = finish()
    raise SystemExit(0 if report['next_stage_allowed'] else 2)


if __name__ == '__main__':
    main()
