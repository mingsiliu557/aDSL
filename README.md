# SNAP3D 公式重实现（Manifold / MuJoCo-CoACD）

Independent research implementation. Not official SNAP3D code. No dependency on an
installed aDSL checkout. No formal benchmark metrics or training are implemented.

The current default is `rollout.collision_backend: rigid_flex`; `zone_proxy`
remains available explicitly. Complete aDSL XML and standing settings are used,
with SNAP3D pinned ground and its own motion criteria.

The artificial phase is closed by explicit user acceptance of the saved soft-contact
contrast: zero gravity passes the paper half-cell criterion, no-head withdrawal
is 34.3 mm, and the headed part remains in contact near 0.743 mm downward motion.
The added <=2c detail was a local rigid-contact gate, not a paper threshold; its
original FAIL and all traces remain preserved. The accepted handoff has
`next_stage_allowed=true`; no new artificial simulation was performed. See
`reports/first_real_preparation.md` for the parameter audit and current first-case
status. The first real case reached six closed parts but PGE split part_003
from one material component to two, so later stages were not run. No parameter
was changed to pass it. This acceptance does not assert exact zero terminal velocity or a drop
of only c: the recorded headed drop is about 5.1c under soft contact.

## Frozen provenance

`upstream.lock.json` records exact source commits and Hugging Face revisions.
`auxiliary.lock.json` pins Sonata and the background-removal checkpoint too.
The shape model is Hunyuan3D-2 (`hunyuan3d-dit-v2-0`, shape only); decomposition
uses public X-Part light with P3-SAM. Upstream files are unmodified. Models are not
included. Resolve ancillary model checkpoints (including background removal and
Sonata if required) before inference; offline mode must not fetch unpinned assets.

Vendor source is aDSL `f65b0dbb9c0e3a48d45f870cdb7091949aa2472a`.
The additional rigid-flex settings, compatibility options and complete
`xml_model` are copied from `fabrication_ablation@9e1f01d`. Its unused mm_matrix
dependency is copied locally too: only that import path is relocated. Source,
original/relocated function AST and dependency hashes are recorded separately.
`vendor/provenance.json` records source hashes; `function_ast.json` tests copied
functions against the frozen source. No license file was found at that aDSL
revision: provenance is retained, no new license is assigned to that code.
Upstream third-party license terms continue to apply.

## Environment

Geometry dependencies are pinned in `pyproject.toml`; use a separate environment
or the existing compatible environment without upgrading it. Hunyuan generation
and X-Part can use separate environments and exchange files. The dependency
versions used for actual tests are in `runs/environment.json`. No model inference
compatibility is claimed from geometry test results.

```bash
python -m pytest -q tests
PYTHONPATH=. python scripts/lock_gate.py --output runs/lock_gate_NEW
```

Long operations run in tmux inside an existing Slurm allocation. Never use GPUs
outside the allocation, request resources automatically, or install a keeper.

## Mandatory gate and stage execution

The saved lock comparison uses zero gravity, an unheaded pin, and a headed pin
under the same withdrawal load, with production friction throughout. Ground
pieces are fixed. The original strict verdict is preserved and a separately
identified user-approved soft-contact handoff authorizes the first real case.
No failing measurement is relabeled as an unchanged strict-rule PASS.

Every real objective call first receives the paper's zero-gravity validity check
(<0.5D/256 for every free part). Its fixed-body set is reused under gravity. Final
actual material intersections are also checked, as Appendix B requires. Failed
validity is recorded and does not trigger parameter tuning.

With the accepted handoff, wrappers accept only local weight snapshots. Set the
appropriate upstream repository on PYTHONPATH, use each model's environment,
and supply `--gate` with the passing report to both wrappers:

```bash
python -m snap3d.generate --image input.png --weights models/Hunyuan3D-2 --output runs/case_generation --gate reports/lock_gate_soft_contact_handoff.json
python -m snap3d.decompose --mesh runs/case_generation/whole.ply --weights models/Hunyuan3D-Part --output runs/case_parts --gate reports/lock_gate_soft_contact_handoff.json
python -m snap3d.pipeline --run runs/case --gate reports/lock_gate_soft_contact_handoff.json --stage initialize --whole runs/case_generation/whole.ply --raw-parts runs/case_parts
# Then --stage repair, pge, cgr, rollout, cem, export, in that order.
```

Stage outputs are immutable; retries require a new run. Mesh files and config
hashes are checked on resume. Failure records preserve the stage and error.
This authorization covers only the fixed first case DensiCrafter_124. If already stable, CEM records zero candidate evaluations. No new artificial
fixture is run in this phase. No full benchmark is launched.

## Geometry and protocol

Hunyuan Y-up maps to Z-up with a proper rotation. Once, at entry, scale the whole
mesh to a 150 mm longest side and put its minimum z on the ground. Apply exactly
the same transform to all parts; freeze the resulting diagonal D. No candidate
renormalization. X-Part's own fix_mesh step is recorded separately from our
boundary-loop repairs, and silently missing upstream exports are rejected.

Our added repair is limited to simple planar loops (diameter <= 0.01D,
planarity <= 0.0001D). Existing vertices and other parts are unchanged. No voxel
rebuilding, large-hole closure, fragment deletion or bridging. Invalid open
material has null original volume.

PGE uses original material operands, actual cumulative loss, the 35% cap and
lowest-margin ownership reversal. Each ownership can reverse once; unsatisfied
caps stop the case. Enclosing sphere radii use the AABB centre and farthest
vertex, a deterministic implementation choice. Clipping may not add islands.

Contact uses actual FCL distance and area-weighted facing-area estimation;
bidirectional minimum is our convention. Anchors use a D/512 raster and EDT,
then actual triangle projection. No connection is added from an AABB alone.

The chosen connector is a 64-sided circular stem with a circular head, a narrow
throat and an enlarged blind cavity. Head thickness is D/256. No straight enlarged
head sweep is removed through the throat. No elastic insertion path is claimed.
All candidates rebuild from immutable post-PGE geometry and the complete current
connector set; no old candidate slots persist.

MuJoCo uses explicit true-material inertia (mm -> SI). The default rigid-flex
backend uses each rebuilt material surface directly, with collision-disabled
display meshes and rigid dim=2 flexes. Shell radius is min(0.01 mm, c/20), with
paired radii strictly less than c. Initial cross-body depth is diagnostic only.
The optional zone_proxy backend retains its cached CoACD and analytic zones,
including its initial c/4 proxy check.
Initial ground IDs remain fixed throughout a search. Every frame contributes to
3D displacement and SO(3) rotation; final contacts determine moving groups. This
fixed-ground optimization protocol is not the online aDSL standing protocol or
the paper's IPC protocol. CEM is 32/8/3, keeps theta0 each generation, uses full
covariance, bounded redraws and a fixed per-edge global baseline. Non-improving or
failed candidates do not replace the incumbent. Self-selected values and numeric
conditioning are in the config/source and are not claims about paper defaults.

Final GLB contains the actual connector geometry. Meshes, proxy meshes, trajectories,
source/weight identity, repairs, losses, theta, simulation counts and timings are
retained. Formal NSV, connectivity and CLIP evaluation is intentionally absent.

Sources: https://arxiv.org/abs/2609.13146 ;
https://github.com/Tencent-Hunyuan/Hunyuan3D-2 ;
https://github.com/Tencent-Hunyuan/Hunyuan3D-Part .

## Zoned proxies: implemented, dynamic gate still blocked

This section describes the historical v3 optional `zone_proxy` backend; the
historical v4 and v5 rigid-flex results are recorded below.

The new adapter caches CoACD proxies of each immutable PGE base at
`far_threshold_ratio=0.01` of the frozen D. All connector exclusion prisms are
subtracted from every part's far proxies using convex half-space pieces. Locks
use analytic 64-gon stems/heads, layered sleeve sectors and a cap. Sector angles
are read from the actual Manifold cylinder, and sector sagitta is bounded by c/4.
There is no surrounding far-field/sleeve proxy in local z [-c,c]: this omits a
depth-c entrance layer on each side; the analytic pin itself remains. Exported
material geometry and inertia are unchanged.

The native worker watchdog is 300 seconds per base part. Cached STL hashes,
configuration and base geometry identity are checked before reuse. Rollout never
falls back to whole-part fine CoACD: absent proxies give `PROXY_MISSING`. Both
pipeline and artificial gate check cavity clearance, shoulder coverage and
initial inter-body contact depth before dynamics. The new config hash invalidates
old passing gates. Vendored files, friction, connector dimensions, MCTS settings
and rollout acceptance thresholds are unchanged.

Actual CPU-only validation of D=150 fixtures is recorded in
`reports/zone_proxy_implementation_20261105.md`. Geometry/stub stage: 42 passed,
two real MuJoCo unit tests deferred. Full regression: 44 passed, including those
two tests and all original 21 tests. All three fixtures passed static proxy
acceptance. Cold far CoACD took about 1.29 seconds total, with subsequent cache
hits. **The zero-gravity dynamic gate failed by ejecting the donor.** The no-head
and headed dynamic gates were not run. Do not advance to models, real cases or
CEM with this gate. The original `runs/lock_gate_v1` and `v2` are unchanged; new
artifacts are under `runs/lock_gate_v3`.

```bash
OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 PYTHONPATH=. python -m pytest -q tests -k 'not real_mujoco'
OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 PYTHONPATH=. python -m pytest -q tests
# Manual CPU-only gate, unique output directory; stop on its first failure:
OMP_NUM_THREADS=8 OPENBLAS_NUM_THREADS=1 PYTHONPATH=. python scripts/lock_gate.py --output runs/lock_gate_NEW
```

This remains SNAP3D formula reimplementation (Manifold / MuJoCo-CoACD), using
analytic lock-zone collisions instead of the paper's IPC mesh collisions.
Static proxy acceptance is not evidence of stable dynamic contact.

## Rigid-flex v4 artificial validation (historical)

55 CPU unit tests passed, including original zone-proxy coverage, exact vendored
AST checks, XML masks/radius/body-local coordinates, shell-clearance rejection,
flex contact IDs, nonblocking initial depth, pipeline proxy bypass and gate-stop
behavior. No CoACD or proxy construction is performed in rigid-flex mode.

`runs/lock_gate_v4` preserves the unchanged D=150 fixture and motion thresholds.
The 2 s zero-gravity rollout completed in 1.0378 s wall time, using 1,128 flex
triangles and 50 initial contacts; the donor was ejected, so the gate failed.
The specified connector-free coplanar boxes also eject the free body (24 flex
triangles, 14 initial contacts, 0.7858 s wall time). The no-head and headed load
gates were not run. No parameters were tuned. The 96-call extrapolation from
the observed lock rollout is about 99.63 seconds/edge, not a validated optimization
cost or bound for real cases. No models, weights, real cases or CEM were run.

## Complete aDSL model and aligned settings: v5

The default rollout delegates XML, true material inertia and flex construction
to the complete source `xml_model`. SNAP3D builds one COM-local material/body
layout per part and removes only fixed bodies' free joints afterwards. Zero
gravity is an explicit postconstruction option for the original gate.

Simulation settings follow `selection_v1.json` standing/material: dt=0.002,
friction [0.5,0.005,0.0001], density 1240, rigid_flex; implicitfast and unspecified
solver defaults come from source XML. The custom iterations override is removed.
Both gates and the ordinary coplanar control now use formal rollout friction.
The 2 s duration, pinned ground, tau_p=15D/256 and tau_r=10 degrees remain SNAP3D
protocol choices. IPC is not used.

63 CPU tests passed. The valid v5 zero-gravity lock rollout has 0.142241 mm
maximum donor motion, exceeding 0.015 mm, so no-head/headed load gates are NOT_RUN.
This is dramatically smaller than the v4 ejection, but the acceptance rule is
unchanged. The ordinary coplanar boxes drift 0.878672 mm. The sole conditional
diagnostic (same boxes, condim=1, zero friction) drifts 0.618611 mm. The first ten
steps and measured velocities are recorded. No additional parameter search,
models, weights, real cases or CEM followed.

Artifacts are in `runs/lock_gate_v5`; a first technical JSON serialization failure
is retained as `runs/lock_gate_v5_serialization_attempt_01`. Its fix converts the
already-computed score to a Python float without changing the score or verdict.
The complete parameter table and results are in the v5 implementation report.
