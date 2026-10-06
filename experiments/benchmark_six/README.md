# Frozen six-case comparison

`run.py` runs the six cases in the order in `CASE_ORDER`, with `official` followed
by `ours` for each case. All twelve jobs run serially. Each arm gets one native
initial generation and at most nine source repairs (`max_rounds=10`). It never
regenerates a started job or uses offline measurements to choose a candidate.

The batch root supplies `config/cases.json`, `config/physics.json`, and
`config/envs.json`. Cases may be a list or `{"cases": [...]}` and must contain
`case_id` (or `id`), `requirement`, `input_image`, `input_sha256`,
`final_size_mm`, `voxel_pitch_mm`, and `standing_applicable`. Only these task
fields are read; ground-truth meshes and selection/risk metadata are excluded
from generation inputs. Both arms receive the same reference bytes and common
requirement, which states the dimensions, millimetre units, and intended use pose.

Environment entries are `official`/`ours` or `arms.official`/`arms.ours`, each
with `python`, `code_root` (or `repo`), and `profile`. `common_env` configures the
shared CPU renderer. Both child imports and the launcher verify module files
against the appropriate code root. Profiles use `http://127.0.0.1:28317/v1`.
Proxy variables are removed. Both arms preserve their native SDK clients and
the frozen profile retry setting; no transport implementation is replaced.

Official uses its unmodified native `ObjectWorkflow.generate(ObjectRequest)`
planner, coder, and critics. Ours directly instantiates the existing workflow's
FixedAssembly branch with `visual_only`, `mm_per_unit=1`, `fit_offset_mm=0.2`,
the case dimensions, topology/overhang, and standing except for the flying
dragon. Partition repair is enabled with the existing Dapper objective
`alpha=0.3`, `r_vox=0.1`, 24 axis-aligned rotations, and independent beds. FEA is
disabled. The native `RepairPolicy` time budget stays at its existing default.

Both generation renderers use CPU software Eevee, eight original native camera
views, 512 by 512 pixels, and 64 samples. Each isolated environment contains the
same thin `sitecustomize` render-kwargs adapter because official's native render
helper lacks environment controls. It adjusts resolution/samples only; native
scene construction, cameras, algorithm, and upstream source files are retained.

For a prepared root, perform the read-only/schema preflight and submit the batch:

```sh
python experiments/benchmark_six/run.py --run-root "$RUN_ROOT" --preflight-only
python experiments/benchmark_six/run.py --run-root "$RUN_ROOT"
```

The launcher starts a separate generation worker in each arm's interpreter and
invokes `evaluate.py --run-root ROOT --case ID --arm ARM` in the common ours
environment. Evaluation is bounded at 3600 seconds; timeout retains partial
evidence and the case denominator and continues with the next job. Common final
appearance assessment makes one additional read-only critic call per available
model using its reference and eight unified final native views (CPU Cycles,
512/64). It receives no arm label or source tools, sends no feedback to
generation, and creates no new candidate. Missing unified views produce
`INDETERMINATE`; generation images are not substituted. Its usage is recorded
separately from source-repair usage.

`jobs/<case_id>/<arm>/job.json` records native selected source/GLB/assembly
manifest, native render paths, approval/selected round, usage, and exact artifact
hashes. Ours' native partition score is read only from the selected version's
explicit checker report, bound to source and manifest hashes. `generation_failed.json`
retains individual failed designs. Actual role instructions, inputs, outputs,
SDK items, and tool events are archived in `generation/role_calls/`. SQLite
sessions live in batch-specific `/tmp` directories during execution, then are
backed up into each job's evidence directory.

`checkpoint.json` and `results.json` show `RUNNING`, `PAUSED`, or `COMPLETED`.
Each checkpoint refreshes `review/README.md` and `review/summary.json`, including
the per-arm measurements, request/token usage, and available paired images at
`review/<case_id>/comparison_0001.png` through `comparison_0008.png`. Paired
images keep official on the left and ours on the right, without independent
object crops or scaling. The report builder's hash is frozen with the batch.
API/public infrastructure faults pause the batch. A single generated design's
execution failure remains in the denominator and allows later jobs to proceed.
Resume skips completed jobs only when all frozen inputs/code/profile and selected
artifact/evaluation hashes still match. A started generation or common review
without a terminal record requires inspection; invoking the runner never resets
its budget or automatically repeats its model call.

Focused tests run without any API call:

```sh
python -m pytest -q tests/test_benchmark_six_runner.py tests/test_benchmark_six_evaluation.py
```
