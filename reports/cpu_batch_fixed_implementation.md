# Fixed CPU batch: three cases, four CEM workers each

Base: `snap3d@5d6dc3d`. This execution update replaces the earlier calibration
prerequisite. No separate calibration or automatic concurrency selection is run.
The latest requested arena capacity is **512M** (superseding 256M for this batch).

- Fixed concurrency: 3 case processes, 4 CEM workers per case, up to 12 concurrent
  candidate simulations. The case process also performs its own baseline and
  final zero-gravity run. OMP_NUM_THREADS, OPENBLAS_NUM_THREADS and MKL_NUM_THREADS
  are 1. Physics settings, method rules and CEM 8/2/2 with early stop are unchanged.
- Dispatch waits below 4 GiB of effective available memory; running work continues.
  The threshold considers system MemAvailable and inherited Slurm/cgroup limits,
  subtracting reclaimable inactive-file cache from cgroup usage. Concurrency is
  never automatically reduced or increased.
- Four active wall hours per case across resumes; timeout terminates that case's
  process group and continues. Hash-verified completed cases/stages are reused.
  Failures remain terminal for this batch and are not individually repaired/tuned.
- The previously failed `ABO_B07MNBYN11` is kept as failed with reason
  `NONMANIFOLD_AFTER_PGE`. Other instances are classified using post-PGE stage and
  structured nonmanifold edge/vertex evidence; unrelated failures retain their own
  codes/details. Unknown observations stay null.
- Worker event records add PID and OS high-water RSS on success/error. Case-main
  high-water RSS is written on ordinary exit, including exceptions. The supervisor
  samples the process tree every second, so killed processes retain sampled lower
  bounds when no final OS peak exists. System memory/CPU and task RSS sums are
  recorded separately; whole-node measurements include other jobs. These additions
  do not alter the rollout XML, time steps, collision/physics values or verdicts.
- Every case row includes stage status/time, faces/fallback, PGE decisions/overlap,
  placed/skipped connectors, movement/rotation/stability/zero validity, resource
  peaks and failure detail. Failure groups list case IDs. NONMANIFOLD_AFTER_PGE is
  separately counted, with both simplification-requested and actually-applied
  flags; executing the no-op stage does not imply the geometry was simplified.

## Tests and execution evidence

Tests cover the memory dispatch pause, post-PGE nonmanifold classification,
simplification flags in failure grouping and worker RSS/PID fields, alongside the
existing deadline/resume, simplification and simulation regressions. Exact command:

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 python -m pytest -q`

Measured results and execution status are appended after verification. The current
100-row table and report are maintained by the batch under
`runs/cpu_batch_400k_20261106/`. No new Hunyuan/X-Part inference, weight download,
mesh repair outside the existing per-case flow or standalone rollout is started.

The five-item method-deviation table and zero-gravity explanation remain in each
batch report. Thin-shell separation/drift is a supported mechanism, not an
unmeasured claim that it dominates every final-invalid case.

## Verified launch

Implementation: `57ac7a2`. **101 tests passed in 9.16 s** in the implementation
workspace. The frozen controller was started in tmux with the fixed 3×4/512M
configuration. The first three cases reached CGR; no standalone calibration was
run. The known previous failure remains terminal with NONMANIFOLD_AFTER_PGE.
`cpu_batch_fixed_summary_snapshot.csv` is an explicitly incomplete 100-row launch
snapshot; the run directory's CSV/report/resource files update automatically.
No batch success rate is reported while cases are pending.
