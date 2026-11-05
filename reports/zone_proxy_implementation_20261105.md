# Partitioned collision proxies — implementation and artificial gate

Base: `snap3d@1117e3f`. Scope: code, geometry/stub tests, and CPU-only artificial
proxy/lock validation. No weight downloads, model calls, upstream generation,
X-Part, real cases, actual CEM optimization or benchmark evaluation were run.

## Design and code

- `snap3d/connector.py:30`: `rebuild` retains its material construction and returns
  donor/receiver, rigid 4x4 T, radius, length, clearance, overhang and head thickness.
- `snap3d/proxies.py:31`: the per-part 300 s subprocess watchdog was moved from the
  gate and is shared by the far-field adapter. Timeout is `COACD_TIMEOUT`, with no
  precision fallback.
- `snap3d/proxies.py:68`: one cached CoACD decomposition per immutable PGE base
  geometry/config/threshold identity. Cache hits recheck manifest and STL hashes;
  first use and hits use identical STL-quantized proxies. Native CoACD receives
  `threshold_mm=0.01*D` through the unchanged vendor unit conversion.
- `snap3d/proxies.py:124`: derive the actual 64-gon vertex-angle convention from
  Manifold. Choose the largest divisor k of 64 satisfying the c/4 sagitta bound.
- `snap3d/proxies.py:164`: analytic stem/head, shoulder layer L1, pocket layer L2,
  cap L3. The unheaded control retains its original plain throat, not a newly
  enlarged pocket; its L2 inner radius therefore remains r+c.
- `snap3d/proxies.py:205`: exact convex half-space decomposition of H-Zc, applied
  to every part and every connector. Pieces below 1e-9 D^3 are omitted and counted.
- `snap3d/proxies.py:274`: verify inset-cavity occupancy, shoulder probe coverage
  and the initialized MuJoCo inter-body penetration. Own analytic pins are the
  sole cavity-check exception. Initial contact includes distinct body IDs,
  including ground contacts if any; same-body geometry pairs are excluded.
- `snap3d/rollout.py:12`: no implicit CoACD fallback; `PROXY_MISSING` on absent
  proxies. `initial_only=True` performs `mj_forward` but no time integration.
  Every actual rollout also checks initial penetration against c/4 before steps.
- `snap3d/pipeline.py:22`: CGR writes connector records in contact.json; rollout
  and CEM callbacks build from PGE bases, validate, then integrate. Per-attempt
  proxy checks are recorded. Rejected representations never enter dynamics.
- `scripts/lock_gate.py:20`: scale the old fixture uniformly by
  150/sqrt(18^2+18^2+24^2), freezing D=150 mm. All three proxy representations
  must pass before the original ordered dynamic gates begin.
- `configs/default.yaml:20`: the only added parameter is
  `coacd.far_threshold_ratio=0.01`. Its new config hash invalidates old gates.

The far ratio follows the selected coarse relative scale; it is not a claim of
a certified mesh-distance error bound from finite CoACD sampling. Lock geometry
does not depend on that sampling. Connector material, friction, MCTS parameters,
density, step size and motion thresholds are unchanged. Vendor content hashes
and all old v1/v2 run files were checked unchanged.

The entrance approximation deliberately removes surrounding far/sleeve collision
geometry in local z in [-c,c]. On the receiving side this creates a depth-c shallow
entrance recess, and around the donor root it removes a depth-c layer. The
analytic shaft still occupies its intended central region. No exported material
mesh or its inertia is altered. This is a collision approximation, not a claimed
lossless replacement of the material boundary.

## Stage A and regression

CPU-only commands, existing compatible geometry environment:

```text
OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 PYTHONPATH=. python -m pytest -q tests -k 'not real_mujoco'
42 passed, 2 deselected in 9.04s

OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 PYTHONPATH=. python -m pytest -q tests
44 passed in 8.98s
```

The two exclusions in stage A are the real fixed-ground/free-fall unit test and
the real mj_forward penetration negative test, not failed tests. Both pass in
the full regression. All original 21 tests remain passing. The final fixture
scaling regression caught and corrected a scalar-versus-vector Manifold API
call before native gate execution; geometric parameters were not changed.

`tests/test_proxies.py` covers maximum k/sagitta, actual cylinder angle agreement,
convexity, exact k=1 sleeve material for headed/plain controls, transformed and
multiple-zone clipping, volume accounting, fragment counters, hash-validated
cache/recompute, mock-CEM cache reuse, a blocked-child timeout, positive static
checks, hull/unclipped/missing-shoulder negatives, absent proxies, fake and real
initial penetration, unchanged rebuild geometry/records, CGR/pipeline rejection
plumbing, and exact original-fixture uniform scaling. Native CoACD is mocked in
these unit tests; the next section separately records actual backend use.

## Stage B: actual proxy acceptance

CPU only, eight threads, CUDA hidden. Artifacts: `runs/lock_gate_v3`; old runs are
retained. D=150 mm, c=0.146484375 mm, c/4=0.03662109375 mm,
volume limit=0.003375 mm^3. Far threshold=1.5 mm. All three static checks passed.

| Fixture | Receiver / donor proxies | k1 / k2 | (a) cavity intersection mm^3 | (b) missing shoulder mm^3 | (c) maximum initial depth mm |
|---|---:|---:|---:|---:|---:|
| zero_gravity | 100 / 67 | 4 / 4 | 0 | -1.2490009027e-16 | 0.0039462273 |
| no_head | 100 / 66 | 4 / 4 | 0 | 6.1721461275e-15 | 0.0039458683 |
| headed | 100 / 67 | 4 / 4 | 0 | -1.2490009027e-16 | 0.0039462273 |

The shoulder values are retained raw floating-point Boolean integrals at numerical
zero, not finite missing shoulder material. Headed sagitta: L1=0.0201079038 mm,
L2=0.0313665286 mm, both <=c/4; plain control L2=0.0201079038 mm.

Cold CoACD: receiver 0.796318786 s, donor 0.496571956 s, total 1.292890742 s
(including worker overhead). The next two builds hit both caches and invoke no
additional native CoACD. Full proxy-build/acceptance times: 1.9281 s, 0.4687 s,
0.4496 s respectively. No clipped fragments were discarded in this fixture.

## Dynamic gate: stop at zero gravity

| Gate | Result | Evidence |
|---|---|---|
| zero gravity | FAIL | fixed displacement 0; donor max displacement 34943.1311 mm, allowed <0.015 mm |
| no-head withdrawal | NOT_RUN | stopped after first dynamic gate failure |
| headed retention | NOT_RUN | stopped after first dynamic gate failure |

The solver completed its integration (`rollout.status=PASS`), while `stable=False`
and `gate_pass=False`. Completion is not physical acceptance. Donor motion was
already 0.0348757 mm at the first 0.001 s sample; later motion grows dramatically.
Peak recorded penetration during the trajectory is 16.9315 mm. This is an
unacceptable zero-gravity contact response, not gravity-induced tipping.

Initial penetration met the specified c/4 check, but there were 606 initial
inter-body contacts. The present radial-first half-space ordering produces 67
receiver far blocks and 65 donor far blocks, plus the analytic locks. Numerous
contact constraints and the far-field partition layout are concrete follow-up
diagnostic targets; they are not a proven explanation of the dynamic failure.
For example, an axial-first ordering could be studied later without changing
the Boolean difference, but has NOT been implemented or simulated here.

No friction, precision, geometry, contact settings, timestep or verdict threshold
was tuned after the failed gate. No further load gate or real workflow was run.
`next_stage_allowed=false`; the model wrappers/pipeline continue to refuse this
gate. Stage B requires review before any further physics adaptation.

## Disclosure

This is SNAP3D formula reimplementation (Manifold / MuJoCo-CoACD), using the public
X-Part light interface. The paper's IPC directly handles meshes; this adapter
uses coarse cached convex far proxies and analytically generated lock-zone convex
pieces. Static representation acceptance has passed for these fixtures, while
dynamic locking has not. This is not an end-to-end successful reproduction.

Machine-readable records: `zone_proxy_gate_20261105.json`,
`zone_proxy_failure_20261105.json`, `zone_proxy_runtime_identity.json` and
`zone_proxy_preserved_identity.json`. No server addresses, credentials or personal
absolute paths are included in these reports.
