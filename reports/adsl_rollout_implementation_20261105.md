# Complete aDSL XML model alignment and v5 artificial gate

Base: `snap3d@13672eb`. Code/tests and CPU artificial fixtures only. No real
cases, actual CEM optimization, Hunyuan, X-Part, model/API calls, weight downloads
or benchmark evaluation were run.

仿真设置与 aDSL 站立评测相同，保留了论文的地面件固定和判据，没有使用 IPC。
Here matching simulation settings refers to integrator, timestep, contact/material
settings, unspecified solver defaults and complete source XML construction.
Duration 2 s, fixed ground and SNAP3D tau_p/tau_r remain explicit protocol
exceptions, not a claim that this is the aDSL 5 s/20-degree evaluation protocol.

## Vendor and adapter

Source: `fabrication_ablation@9e1f01d`, full commit
`9e1f01dc32812bf649dc2f58fdc027c7c3dd8d7d`,
`adsl-agents/assembly_standing.py`.

The complete `xml_model`, `flex_contact_settings` and
`_legacy_flex_contact_options` are copied into `snap3d/vendor/rigid_flex.py`.
The partial `add_rigid_flex` helper and its partial-block AST record are removed.
The sole AST difference in xml_model is its dependency import: the originally
unused `mm_matrix` import is redirected from aDSL to the local vendor copy.
That function is copied unchanged from the same commit's
`adsl-core/core/assembly_topology.py`. This avoids depending on an installed aDSL
package or inserting fake modules into the import namespace. The XML statements,
inertia calculation and contact logic are unchanged.

`provenance.json` records whole upstream hashes, original/relocated xml_model AST
hashes and the import relocation. `function_ast.json` covers the resulting
functions; the separate test reverses only that import and verifies the complete
original XML function AST. `standing_reference.json` contains only the frozen
standing/material values and source hash, not unrelated private paths from the
original configuration.

SNAP3D supplies one body per existing part, with a COM-local mesh and a millimetre
assembly transform. This retains the existing COM origin used for displacement
and relative scores. The source XML performs its usual common ground alignment,
unit conversion, mass/inertia and display/flex construction. Only after calling
the vendor function does SNAP3D remove fixed bodies' freejoint elements and set
the prescribed gravity. The sole authorized diagnostic additionally changes
flex condim from 3 to 1 with friction 0; tests verify that its XML differs only
in condim/friction attributes.

`angle`, `relative_score`, `select_edges`, fixture material geometry and
tau_p/tau_r are unchanged. Velocity observation via mj_objectVelocity is read-only:
world XYZ velocities and displacement samples are recorded without affecting
dynamics. Initial shell contact depth remains diagnostic only.

## Parameter alignment

Source values: `benchmark/configs/selection_v1.json`, physics.standing/material;
implicitfast/default solver setup from the complete source xml_model.

| Parameter | v4 | v5 | Basis |
|---|---|---|---|
| timestep | 0.001 s | 0.002 s | source standing |
| integrator | implicitfast | implicitfast | source XML literal |
| iterations | explicit 100 | absent; MuJoCo default (100 in this environment) | source XML does not override |
| solver / cone / tolerance | not explicitly overridden | not explicitly overridden | source MuJoCo defaults |
| production friction | [0.5,0.005,0.0001] | same | source standing |
| gate and ordinary control friction | [0,0,0] | [0.5,0.005,0.0001] | now use formal config |
| density | 1240 kg/m^3 | same | source material |
| backend | partial manually adapted rigid_flex XML | complete vendored xml_model | source code reuse |
| shell radius | min(0.01 mm,c/20) | same | source settings |
| ordinary condim | 3 | 3 | source contact |
| compiler inertiafromgeom | explicit false | absent (source default) | complete source XML |
| duration | 2 s | 2 s | retained SNAP3D exception |
| ground piece | fixed | fixed by removing freejoint after XML | retained SNAP3D exception |
| tau_p / tau_r | 15D/256 / 10 degrees | unchanged | retained SNAP3D protocol |
| zero-gravity gate motion bound | <1e-4 D | unchanged: <0.015 mm | original gate |

No source settling/20-degree standing rule replaces the SNAP3D optimization
criteria. Source standing uses 5 s, but this task retains 2 s. No solver/contact
parameter search was performed. All actual settings and before/after values are
also in `adsl_rollout_source_verification.json`.

## Tests and technical retry

Final CPU-only command in the existing compatible environment:

```text
OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 PYTHONPATH=. python -m pytest -q tests
63 passed in 10.72s
```

Includes complete vendor/import-normalized AST identity, standalone XML execution
without aDSL import, exact frozen config assertions, solver-default XML checks,
fixed/free joint handling, COM/unit/inertia behavior, diagnostic-only attribute
differences, ordered stop/control behavior, first-ten-step vector/speed logging,
zone_proxy compatibility and existing geometry/repair/CEM-stub tests.

The first native v5 attempt completed its first integration but failed JSON
serialization before producing a valid gate verdict. The existing rotation-led
score calculation could yield a NumPy boolean. A minimal output-only fix converts
the already-computed score to Python float, preserving its formula and threshold;
a rotation-dominant JSON regression covers it. That technical attempt, log and
failure note are retained under `runs/lock_gate_v5_serialization_attempt_01`.
The valid v5 below reran the same settings; no physical parameter changed.

## Actual CPU v5 results

Eight configured CPU threads, CUDA hidden. D=150 mm, dt=0.002 s, 2 s integration,
formal friction, unchanged material and connector geometry. Original v1-v4
directory identities were verified unchanged. Results: `runs/lock_gate_v5`.

| Test | Gate outcome | Rollout wall time | Flex triangles | Contacts initial / peak / final | Donor max displacement | Max linear speed |
|---|---|---:|---:|---:|---:|---:|
| zero-gravity lock | FAIL | 2.597020 s | 1128 | 50 / 50 / 0 | 0.14224123 mm | 0.000415915 m/s |
| no-head withdrawal | NOT_RUN | null | null | null | null | null |
| headed retention | NOT_RUN | null | null | null | null | null |
| ordinary coplanar boxes | diagnostic only | 0.402269 s | 24 | 14 / 18 / 0 | 0.87867209 mm | 0.000440434 m/s |
| same boxes, condim=1, zero friction | diagnostic only | 0.402433 s | 24 | 14 / 18 / 0 | 0.61861062 mm | 0.000309941 m/s |

All three actual integrations complete 2.0000000000000013 simulated seconds;
fixed-body displacement is exactly 0. `rollout.status=PASS` means integration
completed. Ordinary SNAP3D `stable=true` under tau_p/tau_r does not override the
stricter zero-gravity calibration: 0.14224123 > 0.015 mm, hence `gate_pass=false`,
overall gate FAIL and `next_stage_allowed=false`.

The v4 lock motion was 74982.6109 mm; the aligned v5 lock motion is 0.142241 mm.
The large ejection is absent in v5, but the unchanged gate still fails. Several
settings/model-construction details changed together, so this is not an isolated
causal attribution to timestep, friction or one solver flag.

Initial depth for the lock is 0.014648437425 mm, and for both box controls
0.014584281077 mm. Shell r=0.00732421875 mm, paired shell thickness=0.0146484375 mm
and c=0.146484375 mm. Depth remains nonblocking. End-of-rollout box translation is
[-0.0384794,0.0299282,0.8773188] mm with formal settings, and approximately
[1.09e-16,-1.43e-17,0.61861062] mm with condim=1/zero friction.

## Required first-ten-step diagnostic

Same boxes, unchanged density, dt, duration, pinned body, geometry and solver;
only flex condim=1 and friction=[0,0,0]. Displacements are from the initial donor
pose, in world mm; speed is measured in m/s.

| Step | Time s | dx mm | dy mm | dz mm | Linear speed m/s |
|---:|---:|---:|---:|---:|---:|
| 1 | 0.002 | 2.25e-19 | 1.13e-19 | 0.000154191 | 0.0000770954 |
| 2 | 0.004 | 4.51e-19 | 2.25e-19 | 0.000431861 | 0.000138835 |
| 3 | 0.006 | 6.87e-19 | 3.42e-19 | 0.000826982 | 0.000197561 |
| 4 | 0.008 | 9.12e-19 | 4.44e-19 | 0.001332917 | 0.000252967 |
| 5 | 0.010 | 1.11e-18 | 5.16e-19 | 0.001904084 | 0.000285584 |
| 6 | 0.012 | 1.30e-18 | 5.79e-19 | 0.002502101 | 0.000299008 |
| 7 | 0.014 | 1.47e-18 | 6.30e-19 | 0.003110275 | 0.000304087 |
| 8 | 0.016 | 1.60e-18 | 6.31e-19 | 0.003722599 | 0.000306162 |
| 9 | 0.018 | 1.72e-18 | 6.33e-19 | 0.004334924 | 0.000306162 |
| 10 | 0.020 | 1.85e-18 | 6.35e-19 | 0.004947249 | 0.000306162 |

First-ten-step maximum speed: 0.000306162418 m/s; complete 2 s maximum:
0.000309941346 m/s. The formal-friction boxes' first-ten-step maximum is
0.000440341299 m/s, complete maximum 0.000440434068 m/s. Exact vector records for
both controls are in `adsl_rollout_gate_20261105.json` and the companion CSV.

Residual drift persists with tangential friction removed. This is consistent
with a normal shell-contact/depenetration response followed by free drift, but
the present control does not establish a unique cause. No parameters were tuned
to eliminate it. No later load gate, model, real case or CEM was run. Results
are returned for review as requested.
