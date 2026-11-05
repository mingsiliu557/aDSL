# Rigid-flex collision backend and artificial v4 gate

Base: `snap3d@0770c78`. Scope: backend integration, CPU tests, unchanged artificial
fixtures and the explicitly requested coplanar-box control. No Hunyuan, X-Part,
model/API calls, weight downloads, real cases, CEM optimization or benchmark
evaluation were performed.

## Exact vendor extraction

Source: `mingsiliu557/aDSL`, `fabrication_ablation@9e1f01d`, full commit
`9e1f01dc32812bf649dc2f58fdc027c7c3dd8d7d`, file
`adsl-agents/assembly_standing.py`.

`snap3d/vendor/rigid_flex.py` includes the exact `flex_contact_settings` and
`_legacy_flex_contact_options` functions and the necessary `xml_model.values`
formatter. The rigid_flex XML branch is wrapped as `add_rigid_flex`: only its
trailing loop-control `continue` is omitted when extracting into a helper. All
construction statements, display masks, dim=2 flex, contact attributes and
compatibility option selection retain their original ASTs and behavior.

`provenance.json`, `function_ast.json` and `rigid_flex_block_ast.json` identify the
source, whole upstream SHA256 and function/block AST hashes. These were also
checked directly against the specified Git source, independently of the copied
file. `reports/rigid_flex_vendor_verification.json` records all checks true.
Existing vendor Python files retain their original bytes. The new code imports
without aDSL installed or imported at runtime.

## Backend integration

- `configs/default.yaml`: `rollout.collision_backend` now defaults to `rigid_flex`.
  The only config change is that backend field. All other production parameters
  retain their values from 0770c78. A new config hash invalidates old gates.
- `snap3d/rollout.py`: rigid-flex consumes rebuilt material meshes, not convex
  hulls or proxies. Each supplied material body's origin remains its true COM;
  the vendor mesh view is translated once into that local frame. Material mass
  and inertia, ground plane, fixed body set, free joints, integrator, time step,
  duration, gravity and motion rules are unchanged.
- Display geoms have `contype=conaffinity=0`, density 0; original surface triangles
  are used by rigid dim=2 flexes. No remeshing, deformation DOFs, hole closure or
  material geometry change is introduced by this backend.
- The exact settings function gives r=min(0.01 mm,c/20) by default and rejects
  paired shell thickness >=c with `RIGID_FLEX_CONTACT_UNVERIFIED`.
- Rigid-flex initial cross-body count/depth is recorded after mj_forward but
  never blocks the verdict. Its `pass` and `bound_mm` are null and
  `affects_verdict=false`. The shell-clearance input check remains mandatory.
- Contact ownership resolves `contact.geom` and `contact.flex`; a flex geom ID of
  -1 is never used as a negative index into geom_bodyid. Final contact pairs can
  therefore still identify material body IDs for the retained optimization code.
- Results retain wall time, simulated time, flex triangle counts and initial,
  peak and final contact counts. Solver-warning early returns retain available
  counts without pretending that partial integration completed.
- `snap3d/pipeline.py`: the rigid-flex rollout/CEM callback branch bypasses all
  proxy builders/validators. It records a collision summary using the existing
  per-attempt evidence file location. No actual CEM is run in this task.
- The explicit `zone_proxy` branch, geometry/cache code and depth gate remain.
  Its verifier pins its internal check to zone_proxy even if passed the new
  default config, so it always inspects the supplied convex representation.
- `scripts/lock_gate.py`: unchanged lock fixture geometry, D=150, same ordered
  rules. The old zone-proxy implementation is retained. The new branch stops
  after a failed gate, adding only the required connector-free control when the
  failed gate is zero gravity.

The artifact protocol is `snap3d_fixed_ground_mujoco_rigid_flex_v1`, distinct from
the saved zone-proxy/CoACD protocol. This is still a disclosed SNAP3D formula
reimplementation, not the paper's IPC mesh-contact implementation.

## Tests

CPU only; final command in the existing compatible environment:

```text
OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 PYTHONPATH=. python -m pytest -q tests
55 passed in 8.98s
```

This includes all prior 44 zone-proxy/geometry tests, with zone-specific tests
explicitly selecting that backend; 11 new cases cover exact source and XML block
identity, display collision masks, radius/COM frame and inertia, paired-shell
rejection, nonblocking initial penetration, flex/ground contact ownership,
pipeline proxy bypass, failure-stop/control behavior, native rigid-flex free fall,
warning metadata and the zone-default compatibility boundary. Standalone import
also explicitly imports the new vendor module while rejecting aDSL imports.

Two immediate post-copy test attempts observed pre-update return behavior on the
compute node. Actual imported paths/bytecode were inspected, the project's own
bytecode refreshed, and the final unchanged source above passed the full suite.
No physics parameter was altered to address this deployment/test issue.

## Actual v4 run

CPU only, eight configured threads, CUDA hidden. Artifacts are under
`runs/lock_gate_v4`, with old v1-v3 directories retained and content identities
verified unchanged. No CoACD is called and no proxy cache is constructed by v4.

The existing gate's friction [0,0,0] is preserved for both the lock fixture and
the control; production friction [0.5,0.005,0.0001] is unchanged. Both have fixed
lower body, free upper/donor body, zero gravity, dt=0.001 s and requested duration
2 s. The control is two coplanar plain boxes, no connectors, scaled once to
D=150 with the same nominal c for the same shell settings.

For the lock: c=0.146484375 mm, r=0.00732421875 mm, paired thickness=0.0146484375 mm,
and remaining nominal clearance=0.1318359375 mm. Initial measured depth is near
the expected paired shell thickness and is not classified as a geometry failure.

| Test | Verdict | 2 s rollout wall time | Flex triangles | Contacts initial / peak / final | Maximum free-body displacement |
|---|---|---:|---:|---:|---:|
| zero_gravity lock | FAIL | 1.037829670 s | 1128 (542 donor, 586 receiver) | 50 / 50 / 0 | 74982.6109 mm |
| no_head withdrawal | NOT_RUN | null | null | null | null |
| headed retention | NOT_RUN | null | null | null | null |
| coplanar boxes, diagnostic only | ejection observed | 0.785766408 s | 24 | 14 / 17 / 0 | 141420.1957 mm |

Both actual integrations completed approximately 2 s (`status=PASS` means solver
completion), but `stable=false`. The lock has `gate_pass=false`; the boxes are
diagnostic only and do not override the failed gate. Fixed-body displacement is
exactly 0 in both runs. The lock requires donor motion <0.015 mm at zero gravity.
It moves 0.14217675 mm after the first 0.001 s; the plain box moves 0.13307183 mm
by 0.002 s. Both later exhibit very large motion.

Initial cross-body depths are 0.014648437425 mm for the lock and 0.014584281077 mm
for the plain boxes. They are recorded as thin-shell diagnostics, not blocking
tests. Final gate status is FAIL, `next_stage_allowed=false`.

## Timing extrapolation requested for CEM

Using the completed lock rollout and 32*3=96 calls/edge:

```text
1.0378296699 s/call * 96 = 99.6316483 s/edge (about 1.66 minutes)
```

This is a 96-call extrapolation from a failed artificial lock sample, not a
validated optimization result or strict runtime upper bound. It excludes geometry
rebuild, retries/redraws and extra initial/final validation. Unrun load gates have
no measured cost. Actual CEM remains blocked and was not executed.

## Interpretation and stop

The coplanar control fails without any connector or zone proxy. Thus the current
zero-gravity fixed/free rigid-flex contact setup has a failure beyond the lock
geometry alone; it is not evidence that the lock dimensions by themselves are
wrong. Root cause within contact response, model adaptation or solver behavior
has not been established by this one control. Do not generalize this result to
other protocols or silently treat solver completion as acceptance.

No parameters were tuned, no additional dynamics experiments were run, and the
no-head/headed gates were not attempted after the first failure. No model or real
case was started. Machine-readable gate and early trajectory summaries are in
`rigid_flex_gate_20261105.json` and `rigid_flex_diagnostic_20261105.json`.
