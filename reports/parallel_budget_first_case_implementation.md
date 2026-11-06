# Ordered parallel CEM with reduced budget

Base: `snap3d@1203685`. This continuation changes execution cost and the explicitly
requested CEM budget. It does not change connector geometry, PGE decisions,
stability thresholds, integration settings, material density, friction or vendor
function bodies. Historical runs and reports remain intact.

## Implementation

- `snap3d/cem.py::optimize`: ordered sampling and reconstruction in the parent,
  followed by ordered consumption of process-pool results. `cem.workers` defaults
  to 8 and is capped by process CPU affinity. A generation is prepared before
  results are consumed in both serial and parallel modes, so process completion
  order cannot change the RNG sequence, covariance fit or selected candidate.
- First-generation new draws cycle through as-built 45°, normal 10°, normal 45°.
  Seven new draws allocate 3/2/2; 31 allocate 11/10/10. Redraw attempts are included
  in the recorded family counts. Theta0 remains the first candidate each generation.
- Budget: population 8, elite 2, generations 2. Initial variance, standard-deviation
  floor, max_draws 320, complete covariance, theta0 retention and global score
  non-regression are unchanged. The first **ordered, globally eligible** stable
  result is adopted and ends the edge search. Outstanding pool work is terminated.
  Already-started extra work is recorded separately from consumed results. Without
  a stable result, existing local-score selection/adoption remains in use.
- `snap3d/rollout.py::_rigid_flex_xml`: after vendor XML generation, remove display
  geoms (`contype=0`) and their unused mesh assets. Flex, inertia, collision
  settings, fixed-body handling and memory configuration remain unchanged.
- `snap3d/pipeline.py`: reconstruct candidates sequentially from immutable PGE
  material plus the complete current parameter set; workers run physics only.
  Record candidate order, per-generation time, per-edge time, adoption, early stop,
  and worker start/completion/failure events. Atomic event replacement distinguishes
  terminated in-flight work from completed observations. True physics/program
  errors remain errors, rather than geometric redraws.
- `scripts/continue_case.py`: verify frozen file hashes and unchanged physics /
  unchanged non-budget CEM parameters, compare no-display initialization to the
  saved full baseline, then run CEM, export and terminal zero-gravity validity.
  The 0.1-second probe, cost extrapolation and 24-hour stop guard are deleted.
  No replacement time limit is introduced.

## 与论文不同之处

| 项目 | 当前实现 |
| --- | --- |
| 仿真器 | MuJoCo rigid_flex，使用 aDSL 站立配置，替代 IPC |
| 最终穿透判定 | 末帧跨件接触深度 ≤ D/256；布尔交集体积另行记录 |
| 碎薄片判定 | 新增分量按平均厚度 2V/A < D/256 清除 |
| CEM 预算 | population/elite/generations=8/2/2（论文为32/8/3）；第一代三族按比例轮流分配；稳定候选提前停止 |

This is a formula reimplementation. Prior disclosures still apply: public X-Part
light, Manifold Boolean editing in place of the paper's editing grid, selected
connector cross sections, unpublished covariance details, and the previously
requested material-support warnings. The four-row change table above does not
claim those earlier implementation differences have disappeared.

## Validation

Command: `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 python -m pytest -q`.
**85 passed**. No model, real-case generation or long MuJoCo run is part of these
tests. Native tests initialize tiny synthetic XML models and retain existing short
fixture simulations; parallel CEM tests use stub physics.

New tests in `tests/test_parallel_cem.py`:

- `test_parallel_matches_serial_sampling_results_and_adoption` (four sequences):
  serial vs parallel sampling plans, geometric redraws, ordered results and adoption.
- `test_stable_early_stop_adopts_first_ordered_eligible_candidate`: ordered stable
  adoption and rejection of global regression.
- `test_no_stable_candidate_uses_existing_local_score_ranking`.
- `test_first_generation_family_allocation`: K=32 and K=8, with original cap samplers.
- `test_budget_workers_and_unchanged_sampling_parameters`.
- `test_display_removal_preserves_compiled_mass_inertia_flex_and_contacts`: exact
  compiled mass/inertia/flex comparison and initial contacts on coplanar boxes.

`tests/test_parallel_continuation.py` verifies hash-bound reuse, unchanged physics,
no baseline/probe rerun, and completed vs terminated worker accounting. Existing
CEM, flow, rigid_flex and XML-once tests are updated; obsolete guard/probe tests
are removed. Vendor source/AST provenance tests pass.

## First real case

Case: `DensiCrafter_124`. New evidence root:
`runs/first_real_DensiCrafter_124_parallel_budget_v4/assembly`.
The preceding PGE/CGR and completed baseline remain in
`runs/first_real_DensiCrafter_124_manifold_memory_v3/assembly`.

The reuse manifest binds 67 original/copied files, including all PGE/CGR assets,
contact parameters, completed baseline and original XML. Original files are not
modified. The copied baseline keeps the original model hash and a relative
reference to that original XML; the new no-display model has its own hash.
Generation, decomposition, repair, PGE and CGR are not rerun. No benchmark suite,
model call, remeshing or weight download is performed.

Run status and measured results will be appended after completion. Measurements
are reported as observed, without tuning parameters to improve this case.

Initial-state comparison completed: flex elements **391,244**, initial cross-body
contacts **250**, maximum initial depth **0.016460784986 mm**, identical to the
saved baseline. Initialization took **75.195 s** without display assets versus the
hash-verified earlier **81.224 s** with displays. This compares two observations
under potentially different concurrent load; it is not a controlled speedup claim.
Actual-workspace regression: **85 passed in 18.68 s**; `git diff --check` passed.
