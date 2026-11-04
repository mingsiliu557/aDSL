# SNAP3D independent implementation — 2026-11-05

## Actual delivery

Independent sibling project; aDSL tracked files and both upstream source repositories
remain unchanged. Source commits and weight metadata revisions are frozen in
`upstream.lock.json`. Weight snapshots have NOT been downloaded. The three image-only
pilot inputs are hash-bound in `configs/pilot_inputs.json`; no requirements are passed.

Implemented modules: generation/decomposition wrappers, bounded local hole repair,
PGE, sampled facing-contact graph/anchor, circular mushroom lock geometry, true-inertia
fixed-ground MuJoCo adapter, per-edge CEM, immutable-base rebuilding, export and stage
CLI. These are research implementation components, not an end-to-end validated system.
Vendor functions are copied from aDSL f65b0db; AST equivalence and standalone imports
are tested. No aDSL material-component implementation is copied or modified.

## Validation actually performed

Command (existing geometry environment, allocated compute node):

```text
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONPATH=. python -m pytest -q tests
```

**21 passed in 8.75 seconds.** Includes real small artificial MuJoCo rollout, fixed
body/free fall, COM-origin correctness, FCL distance, contact-area anchor, local hole
repair, PGE cap, two-connector rebuilding, mocked CEM population/global constraints,
normalization, source identity and standalone vendor checks. Model inference was not
part of these tests. CEM's real lock/physics optimization has NOT been validated.

An earlier vendor-hash test failed because the source AST hash was generated with a
different Python version. Regenerated using the actual test interpreter; copied
functions were not changed. An initial gate run was deliberately stopped after
identifying the body-origin issue: it used world origins instead of per-part COMs.
Its partial files and interruption record remain under `runs/lock_gate_v1`.

## Mandatory artificial gate: blocked

Corrected fixture uses its actual diagonal (sqrt(18²+18²+24²) mm). Each body's local
origin is its true material COM; mesh coordinates and inertial position agree.
The corrected gate ran in tmux within the existing allocation, using eight CPUs.
The first receiver CoACD call did not complete within the explicit 300-second per-part
operational limit (301.06 s including process overhead). Status:

**UNAVAILABLE / COACD_TIMEOUT — not observed instability and not invalid geometry.**

The selected collision threshold remains one quarter of the connector clearance,
approximately 0.00854 mm for this fixture. No collision precision, friction, connector
clearance, MCTS parameters or geometry was relaxed to obtain a pass.

The 300-second native-process watchdog is an implementation addition to bound cost;
it is not a physical threshold. Failed gates stop immediately: the no-head and headed
load tests were NOT executed. This is not evidence that their locking behavior passes
or fails. See `lock_gate_20261105.json`, `lock_gate_failure_20261105.json`, and original
STLs/logs under `runs/lock_gate_v2`.

## Stage status and limits

- Geometry/stub/standalone tests: completed, 21 passed.
- Real CoACD lock validation: incomplete, blocked by receiver decomposition timeout.
- Model environments and ancillary checkpoint setup: not installed/validated.
- Model weights: only immutable revisions resolved, no weight download.
- First real case, real CEM, remaining two cases: not started, per mandatory gate.
- Formal connectivity, NSV, CLIP and full benchmark: not run.
- Model API calls: zero; aDSL tracked changes: zero.

Do not call this end-to-end reproduction successful. Next work belongs to collision
adaptation: inspect receiver proxy-decomposition cost and whether the chosen backend
can represent the lock at the fixed precision. Do not silently increase tolerance,
replace the receiver by a convex hull, or advance to CEM using a failed gate.

## Disclosed implementation choices

Circular 64-sided peg/head, blind counterbore, D/256 head thickness; AABB-centred
bounding sphere for PGE; earliest lowest-margin ownership reversal with each pair
flipped at most once; bidirectional minimum facing area; D/512 anchor raster;
near-planar small-loop repair only; fixed-ground MuJoCo-CoACD replacing IPC.
All simulation/CEM constants are in `configs/default.yaml`. Upstream X-Part's own
fix_mesh is separately recorded by the wrapper. Real wrappers remain unverified.

No remote repository was configured or pushed. Existing GPU allocation is retained;
no new GPU allocation or keeper was created.
