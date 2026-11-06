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

Measurements below are observed results, without tuning parameters to improve this case.

Initial-state comparison completed: flex elements **391,244**, initial cross-body
contacts **250**, maximum initial depth **0.016460784986 mm**, identical to the
saved baseline. Initialization took **75.195 s** without display assets versus the
hash-verified earlier **81.224 s** with displays. This compares two observations
under potentially different concurrent load; it is not a controlled speedup claim.
Actual-workspace regression: **85 passed in 18.68 s**; `git diff --check` passed.

## Completed real-case continuation

Status: **COMPLETE**. Continuation wall time: **2101.065 s**. No guard/probe was run. Full structured evidence: `reports/parallel_budget_first_case_results.json`. All 67 reused hashes still match after completion.

The real-case XML also has identical deformable/flex elements, option/size settings, body poses and explicit inertials before/after display removal; display mesh assets change from six to zero.

| Motion result | Frozen baseline | After CEM |
| --- | ---: | ---: |
| Maximum displacement (mm) | 13.955196903120301 | 12.562802072470266 |
| Maximum 3D rotation (deg) | 15.640164091790128 | 13.796336981852983 |
| Global normalized motion score | 1.5640164091790127 | 1.3796336981852984 |
| Stable | False | False |

Generation numbers in human summaries are one-based. Candidate indices are one-based draw attempts, including geometric redraws; consumed-rollout counts distinguish feasible physics evaluations.

Maximum rotation here is the existing SNAP3D 3D rotation metric, not the separate aDSL benchmark tilt metric.

| Edge | Workers | Generation times (s) | Actual started/completed | Consumed | Adopted | Early stop |
| --- | ---: | --- | --- | ---: | --- | --- |
| part_000--part_004 | 8 | 416.323, 368.961 | 16/16 | 16 | True | None |
| part_000--part_002 | 8 | 418.101, 390.005 | 16/16 | 16 | True | None |

- `part_000--part_004`: edge wall time 789.467 s; first-generation drawn families {'as_built_45': 7, 'normal_10': 7, 'normal_45': 6}; feasible families {'as_built_45': 3, 'normal_10': 2, 'normal_45': 2}; worker accounting {'started': 16, 'completed': 16, 'failed': 0, 'terminated_inflight': 0}. Theta0 is recorded separately.
- `part_000--part_002`: edge wall time 812.098 s; first-generation drawn families {'as_built_45': 7, 'normal_10': 7, 'normal_45': 6}; feasible families {'as_built_45': 3, 'normal_10': 2, 'normal_45': 2}; worker accounting {'started': 16, 'completed': 16, 'failed': 0, 'terminated_inflight': 0}. Theta0 is recorded separately.

CEM total wall time: **1602.801 s**. Final zero-gravity rollout: **417.597 s**, validity **invalid**, bound 0.329114259 mm. Initial-contact diagnostic remains nonblocking. Terminal cross-part depth: 0.158823321 mm, reference bound 0.658228517 mm. Final-pose Boolean intersection volume is independently recorded as 951.502853 mm³.

| Reused successful stage | Recorded time (s) |
| --- | ---: |
| generation | 56.405 |
| decomposition | 40.197 |
| repair | 4.332 |
| pge | 10.769 |
| cgr | 150.185 |
| full_baseline | 307.157 |

Sum of retained successful stage costs plus this continuation: **2670.109 s**. This is not uninterrupted elapsed time from the first historical attempt; previous failed runs, waiting and the obsolete timing probe are excluded.

Reused PGE: 6 retained cuts, 0 rollbacks, residual overlap 4.64743261e-15 mm³. CGR: 5 connectors placed. These are preserved inputs, not new PGE/CGR measurements. No claim of formal benchmark performance follows from this one case.

CEM ends because the unchanged selection rule yields no further untried eligible edge, not because stability was reached. Both selected edges used their full 16-rollout budget; no early stop occurred. The final maximum displacement exceeds the 9.873427760 mm limit, and maximum rotation exceeds 10°. Zero-gravity maximum free displacement is 0.836189849 mm, above the half-cell bound. These are completed, failing physical observations; there was no solver/program failure. No batch run or 256M memory trial was started.
