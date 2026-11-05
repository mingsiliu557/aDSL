# Artificial-phase closure and first-real-case preparation

Base: `snap3d@25667e9956f1d89e4c1ca6dbe67815058708cff9`.
Current machine-readable snapshot: [first_real_status.json](first_real_status.json).

## Artificial gate closure: explicit soft-contact interpretation

No artificial simulation was repeated and no new control was run. No simulation
parameter changed. The original v5/v6 files and strict v6 verdict remain intact.

- Zero gravity passes the paper half-cell criterion, reusing the verified v5 trace.
- No head: downward withdrawal is 34.309465 mm (approximately 34.3 mm).
- Headed: final downward motion is 0.742854 mm (approximately 0.743 mm); the donor
  remains in contact with the receiver at the end, with 50 final contacts.

The terminal donor speed is 0.000100259757818 m/s (approximately 0.1003 mm/s)
and the final sampled angular rate is 0.00999057573066 rad/s. Thus “stationary” is
understood as near rest, not an exact zero velocity claim. The contact persists
and the trajectory settles near 0.743 mm instead of the 34.3 mm unheaded drop.
This is consistent with taking up the nominal axial clearance and being retained
by the shoulder under soft contact. The trace does not establish an exact free
fall of only c: c=0.146484375 mm and the observed headed displacement is about
5.1c, with additional soft-contact deflection.

The earlier headed downward limit <=2c was a local implementation detail with an
implicit rigid-contact assumption, not a SNAP3D paper acceptance threshold. At the
user's explicit instruction this phase is closed using the headed/unheaded
contrast under the unchanged soft-contact settings, and the lock simulation is
accepted for proceeding to one real case. This does not make the original strict
2c test pass. [lock_gate_soft_contact_handoff.json](lock_gate_soft_contact_handoff.json)
records both the original FAIL and the explicit new acceptance reason, with
`status=PASS` and `next_stage_allowed=true`. The runnable copy is
`runs/real_case_preparation_v1/gate_handoff.json`.

## Parameter fidelity and approved disclosures

[Full parameter table](first_real_parameter_audit.md),
[CSV](first_real_parameter_audit.csv), [JSON](first_real_parameter_audit.json).
The audit covers 58 rows and verifies 23 explicitly published numeric values.
All config values are unchanged from the base commit. Published PGE weights and
35% loss cap, CGR distance/area, connector limits and cone, CEM 32/8/3 and axis
families, duration, displacement/rotation criteria and half-cell validity match.
Sources are the [paper](https://arxiv.org/html/2609.13146), Appendix B and the
specific supporting sections identified in each row.

The paper's IPC contact engine is replaced by MuJoCo rigid_flex, with aDSL
`fabrication_ablation@9e1f01d` standing/material settings and complete vendored
XML. Pinned ground parts, 2 s rollout and the SNAP3D motion criteria remain.
The implementation does not use IPC.

It would be inaccurate to claim that simulator replacement is the only
implementation difference: inherited Manifold Boolean editing replaces the stated
256-cubed editing representation, the public X-Part release is light, and the
paper does not publish our exact circular section, head thickness, repair limits,
initial covariance values, sampling counts or other numerical details. These are
separated from published matches. The user explicitly approved continuing the
existing formula reimplementation with these disclosures; no voxel refactor,
unreleased-model substitution or tuning was performed.

## Code and tests

- `snap3d/pipeline.py`: normalization and frozen D use actual assembled raw-part
  bounds, so completion beyond the whole-mesh bounds cannot create spurious missing
  ground parts. The fixed 150 mm longest-size parameter does not change. Each real objective call first runs the unchanged native
  rollout under zero gravity; requires every free part to move <0.5D/256; reuses
  the resulting pinned set under gravity. Invalid or unavailable initialization
  is recorded and rejected. Actual material at final poses is tested for
  intersection, as the paper requires. A motion-only stable flag cannot override
  failed final geometry. Zero and gravity records expose triangle counts and
  native rollout wall times in `rollout_calls.json`.
- `snap3d/pge.py`: preserve component count exactly, as required in Appendix B,
  rather than accepting a decrease. Loss thresholds, scores and ownership remain.
- `snap3d/common.py`, `generate.py`, `decompose.py`: hash-check auxiliary checkpoints.
  Redirect only the upstream Sonata loader to its pinned local file, restoring
  module aliases afterward. This avoids an unpinned main-revision download and
  the upstream root-only cache path. Architectures, tensors and inference settings
  are unchanged; third-party source files remain unmodified. Local snapshot paths
  are resolved absolutely before upstream APIs to avoid their cache-prefix behavior.
- `scripts/fetch_weights.py`: select only frozen shape/X-Part inference files and
  pinned auxiliary checkpoints. No paint or training models are requested.
- `tests/test_first_real.py`: configured zero-gravity boundary, unavailable state,
  ordering/pinned-set reuse, invalid-case stop, final-pose intersections, local
  Sonata redirect/restoration, explicit handoff, PGE component preservation, actual-assembly normalization and
  absolute local-snapshot paths for both wrappers.
  The existing pipeline stub now supplies the complete zero/gravity observations.

CPU unit command: `OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 PYTHONPATH=. python -m pytest -q tests`.
**82 passed in 10.77 s**. This reruns unit tests, not the saved artificial gates.
The single-card CUDA import probe passed both Hunyuan3D and X-Part APIs.
No method or simulation parameter was tuned. Complete pinned-file content-ID
verification is in [first_real_weight_verification.json](first_real_weight_verification.json);
actual inference versions are in [first_real_inference_environment.json](first_real_inference_environment.json).

## First real-case run

Fixed case `DensiCrafter_124`, original reference image only; no per-case text.
Seed 42; unchanged model settings. The existing frozen input path is represented
publicly as `input/DensiCrafter_124/input.png`; its exact hash is recorded in the
run identity before inference. Both upstream source commits match
`upstream.lock.json`; local weight revisions and every checkpoint hash are
checked before use. The first attempt is in `runs/first_real_DensiCrafter_124/`; the same-case resource
retries are isolated in `runs/first_real_DensiCrafter_124_gpu_retry1/` and
`runs/first_real_DensiCrafter_124_gpu_retry2/`.

Environment is separate and reuses installed packages read-only; prior aDSL,
TRELLIS and DensiCrafter environments were not upgraded. Actual inference uses
PyTorch 2.6.0+cu124; the assigned single GPU is an A100 80GB. Geometry stages use
the existing frozen geometry environment. The tmux runner uses the existing
allocation with 8 CPUs and 40 GiB requested memory. Slurm confirmed allocated device
indices 4-7, and CUDA UUID mapping was checked. After the two single-device OOM
attempts, the successful decomposition used the upstream P3-SAM DataParallel on
physical GPUs 5 and 6. GPU type, effective batch, points, precision and seed stayed
unchanged. The step exposed the existing allocation, not new GPUs. No additional GPU allocation was requested. It does not request new resources.

Stages are generation -> decomposition -> initialize -> repair -> PGE -> CGR ->
rollout -> CEM -> export. An unavailable/failed stage records its cause and stops;
no alternate case, parameter adjustment or full benchmark follows. If already
stable, CEM naturally records zero candidate evaluations. Zero-gravity invalidity
is not silently bypassed. Only this first real case is authorized in this run.

| Stage | Status | Wall time s |
|---|---|---:|
| generation | PASS | 75 |
| decomposition | PASS | 79 |
| initialize | PASS | 2 |
| repair | PASS | 5 |
| pge | FAIL | 11 |
| cgr | NOT_RUN after failure | null |
| rollout | NOT_RUN after failure | null |
| cem | NOT_RUN after failure | null |
| export | NOT_RUN after failure | null |

Technical launch failures are preserved separately: the first shell path was not
shared with the compute node, and then the upstream API treated relative weights
as a cache-relative repository. Both were corrected before any inferred mesh
existed. The successful generation uses the same image, seed, checkpoint and
settings, and its former initialization directory is retained. The initial decomposition
attempt then hit GPU OOM with another process occupying approximately 28 GiB on
the same device. That attempt and its 275 s cost are preserved. A single idle 80GB A100 still exhausted memory (41 s attempt); the same
decomposition then succeeded in 79 s using the official DataParallel on two
allocated cards. Whole geometry was copied and byte-hash verified, not regenerated. Points, precision, seed and all model
settings stay unchanged. These were
infrastructure/API corrections, not parameter search or alternative-case selection.

The first real pipeline attempt has ended in a measured PGE rejection, not a
completed connector assembly. See [first_real_results.json](first_real_results.json)
and [first_real_pge_diagnostic.json](first_real_pge_diagnostic.json). The successful
X-Part stage produced six parts; all six were already closed, so no added hole
patch was applied. The frozen assembly diagonal is 168.50650043496887 mm, and the
six input parts contain 394,624 triangles in total.

PGE changed part_003 from one material component to two. It remained a valid
volume, falling from 4708.668201135604 to 3272.7553444991736 mm3: loss fraction
0.3049509532844398, below the 0.35 cap. The component-preservation condition, not
the volume cap, rejected it. All other parts remain one component. The base
version's old “reject increases” check would reject this 1->2 change too; it is
not caused solely by the new exact-count guard. A same-input, same-parameter CPU
diagnostic replay captured the rejection and took approximately 74.9 s including
tracing overhead, separately from the 11 s original PGE stage.

The paper mentions local-sliver cleanup without publishing its implementation
or threshold. Our Manifold implementation does not claim to reproduce that
unknown rule. No new deletion threshold or fragment removal was introduced to
make the case pass. This formula-reimplementation rejection is not proof that
the authors' unavailable implementation rejects the same object.

CGR, real zero gravity, gravity rollout, CEM and final connector export were not
run because their predecessor failed. Actual flex triangles and rollout time
are null, not zero. The available GLBs are the generated whole, raw six-part
assembly and normalized pre-PGE parts; none is labeled a final connector result.
No additional case was chosen and no method parameter was changed. The accepted
artificial handoff remains next_stage_allowed=true; it does not bypass the real
PGE acceptance condition.

The table records this ended attempt and its unrun stages. Runtime progress is in
`progress.json`; per-stage logs and outputs remain with the case. When reached, native flex triangles and individual zero/gravity rollout times
would be saved by the runner; this case did not reach those calls.
No formal CLIP, NSV or connectivity benchmark is launched by this task.

## Allocation check and final run state

Slurm assigned physical device indices 4, 5, 6 and 7. CUDA UUID verification
confirmed logical indices 0/1 corresponded to physical 5/6 for the successful
DataParallel run. The next model binding preference is 5,6,7,4, using free cards
as primary devices and admitting the already-allocated busy card only when its
remaining memory is sufficient. This is a current allocation record, not a
portable hardcoded model setting.

The real-case task has stopped at PGE. No model or native rollout task remains
running for this case. The user's allocation is retained. No keeper was added,
no other process was stopped, and no new allocation was requested. The raw
whole mesh and six-part outputs remain available; no final connector mesh exists.
All case source hashes still match the frozen code identity recorded at entry.
