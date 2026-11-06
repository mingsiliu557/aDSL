# Budget-only simplification and resumable 100-case CPU batch

Base: `snap3d@40a2904`. Only repaired-mesh preprocessing and batch execution are
added. Existing PGE/CGR/connector rules, simulation settings and stability/validity
thresholds are unchanged. The resource configuration uses 256M arena capacity;
the completed first-case comparison gave exactly equal per-part maximum
movement/rotation to the saved 1G baseline. This capacity is not total process RAM.

## Changes

- `snap3d/preprocess.py::simplify_parts`: use quadric decimation, proportional
  largest-remainder face targets with deterministic ID ties, one PyMeshFix attempt
  on non-volume output, and original-mesh fallback if it is still invalid.
  PyMeshFix does not join parts or remove the smallest components. Under-budget
  meshes are returned untouched. Per-part records include faces, signed relative
  volume change, closure, repair attempt and fallback.
- `configs/default.yaml`: `preprocess.max_faces_per_case: 400000`, with an explicit
  compute-budget deviation comment. No physics value is changed in this file.
- `snap3d/pipeline.py`: repair → simplify → PGE; old configurations without the
  preprocessing block still feed repair into PGE. D remains frozen before editing.
- `scripts/cpu_batch.py`: one fixed batch, hash-bound stage resume, case-level
  four-hour accumulated active-wall deadlines, process-group termination on
  timeout/interruption, case-local failures, CSV/JSON/Markdown summaries.
  Completed output is hash-verified before skipping. Failed/timeout cases remain
  recorded and are not individually retuned. Pending cases keep the aggregate
  success rate incomplete.
- `pyproject.toml`: quadric backend `fast-simplification==0.1.12`; the CPU execution
  environment is separate, reusing the existing numeric/physics libraries and
  PyMeshFix. Shared generation/evaluation environments are not modified.

The cap is applied before PGE, to the sum of repaired part surfaces. Added
connector geometry and Boolean subdivisions can increase later flex counts.
Original-mesh fallback takes priority over the cap: an over-budget retained mesh
is explicitly recorded, not silently removed, simplified again, or claimed to
meet 400k. There is no volume-change acceptance threshold.

## Tests

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 python -m pytest -q`

97 tests passed in the implementation workspace. New tests cover real quadric
closure, proportional targets, unchanged under-cap input, PyMeshFix recovery,
original fallback and cap overage, PGE consuming the simplified stage,
26 GiB/30-process concurrency bounds, resume identity, completed-asset hash checks,
process-group timeout, fixed denominator and summary fields. Existing tests pass.
Third-party pytest plugin auto-loading is disabled to avoid unrelated optional
web/dashboard dependencies; model and physics algorithms are unaffected.

## 与论文不同之处

| 项目 | 当前实现 |
| --- | --- |
| 仿真器 | MuJoCo rigid_flex 替代 IPC |
| 最终穿透判定 | 末帧跨件深度 ≤ D/256 |
| 碎薄片判定 | 按平均厚度 2V/A < D/256 |
| CEM 预算 | population/elite/generations=8/2/2，加稳定候选提前停止 |
| 网格简化 | PGE 前每例目标 ≤400,000 面；原件回退的超限例外逐例记录 |

Earlier disclosures still apply: public X-Part light, Manifold Boolean editing,
selected connector geometry and prior repair/contact adaptations. This remains a
formula reimplementation, not the unpublished official code or IPC simulator.

## Resource evidence and batch calibration

The first frozen case at 256M completed its two-second baseline. Peak process RSS
was 872,345,600 bytes (about 832 MiB); arena maximum was 83,192,264 bytes. Per-part
maximum displacements and rotations matched the original 1G baseline exactly.
Four concurrent workers running one measured generation completed eight rollouts;
parent plus workers peaked at 5,226,205,184 bytes RSS. The cgroup peak included
file cache and was higher; whole-node RAM additionally includes other users.

Before batch launch, the explicitly selected largest raw case
`ABO_B07MNBYN11` must finish repair/simplification/PGE/CGR and a fresh-process
2 s baseline. The larger of its OS peak RSS and the earlier measured reused CEM-worker peak determines
`min(30, floor(26 GiB / peak RSS))`. The selected case×CEM-worker product cannot
exceed that bound. Full eight-candidate waves are preferred, with remainder left
for preparation rather than creating extra case parents. Simulation threads are
set to one; geometry preparation may use native threads. Actual plan and calibration
numbers are saved in `runs/cpu_batch_400k_20261106/concurrency.json`.

The calibration is a launch prerequisite; no alternate case or tuning is used if
it fails. All 100 active inputs remain frozen. Summaries are in
`runs/cpu_batch_400k_20261106/summary_table.csv` and `report.md` and update as cases
finish. Stable observations, end-to-end completion and zero-gravity validity stay
separate. Zero-gravity failure can arise from thin-shell separation/drift, as in
the earlier control and first case; the batch does not assert this is the dominant
cause without per-case supporting diagnostics.
