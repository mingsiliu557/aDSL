# Ordered multi-interface fixed assembly — implementation record

Baseline: `334939cc5ee1ae940d02cff3d043705ae446330f`. Isolated checkout:
`/tmp/adsl_multi_mate_20260928`, branch `feat/fixed-multi-mate`.
All checks use CPU and `/tmp/adsl_multi_mate_python`, with resolved core/agents
module paths verified against this checkout. Original workspace changes are excluded.
Artifacts and full smoke logs: `/tmp/adsl_multi_mate_results_20260928/`.

## Stage 1 — Plan/API

Bidirectional placement and consistency-checked supplemental connections retain the
existing schema. Ports are unique per part and role. Both Boolean trees are built
on copies before committing; validation replays the ordered graph and checks all
saved world frames. Existing matrix tolerance remains `rtol=0, atol=1e-8` in scene
units; translation diagnostics are millimetres and rotation diagnostics degrees.

`python -m pytest -q tests/test_fixed_assembly.py tests/test_fixed_assembly_multi_mate.py -k 'not real_'`:
**63 passed, 6 deselected** (`stage1.log`). These are API/Plan tests, not evaluated
Boolean geometry. Includes forward/reverse/closure failure atomicity, interleaved
connections, same-pair interfaces, corrupted declarations and legacy tree/single-part behavior.

## Stage 2 — Export and feedback

The exporter shares the API world-frame predicate and records both residuals on
mismatch. Four connections survive manifest serialization, plan deltas (three
planned plus one added), generate/resume inputs, review/repair context, and retained
version selection. The initial plan file remains unchanged.

Stage 2 requested files plus `tests/test_fixed_assembly.py`, excluding `real_`:
**101 passed, 8 deselected** (`stage2.log`). Manifest/selection tests use fake mesh
export and are declaration/routing evidence only. The mismatch recovery test runs
an actual source subprocess: `connect()` rejects `interface=second` with 1 mm
translation error before CSG evaluation; `execution_error.json` and repair evidence
carry that diagnostic. The repaired candidate export/review is mocked.

## Stage 3 — Agent instructions and real task entries

Planner/Coder, the Code Critic API reference, both task requirement templates and
example documentation now describe all three successful connect behaviors, full
interface coverage and geometric roles. Frozen historical input bytes are preserved.

Requested prompt/entry tests plus `tests/test_generation_review_contract.py`:
**73 passed** (`stage3.log`). Runtime/docs search found only the existing
`three_parts_prompt.txt` describing its particular tree fixture; it is not a general
API restriction and is unchanged. No model API was called.

## Stage 4 — Real CPU geometry and topology

All required tests passed without GPU or model calls:

| Run | Result | Log |
| --- | --- | --- |
| New real multi-interface tests (`ADSL_TEST_FIXED_REAL=1`, `-k real_`) | 5 passed, 38 deselected; 79.27 s | `stage4_real.log` |
| Existing normal/tilted T-bracket real Boolean export | 2 passed, 33 deselected; 9.64 s | `stage4_legacy.log` |
| All fixed-assembly tests, topology/physics adapter tests, prompts and generation review contracts | 287 passed, 16 skipped; 20.08 s | `regression.log` |

The broad regression leaves opt-in native/export tests skipped; the seven required
real cases above were enabled and run separately. Its standing XML test confirms
four named independent bodies/freejoints and no weld. It does not measure interfaces
or simulate stability. `coacd` and `mujoco` are unavailable: **standing 多接口运行覆盖未验证**.
The optional model smoke was not run: **实际模型调用未验证**.

Real artifacts are under `/tmp/adsl_multi_mate_results_20260928/verified_real_runs/`:

| Case directory | Export result | Independent topology |
| --- | --- | --- |
| `test_real_four_part_cycle_geom0` | geometry PASS; 4 parts, 4 interfaces; 132 × 60 × 100 mm | 4/4 parts and 4/4 interfaces PASS |
| `test_real_same_pair_two_interf0` | geometry PASS; 2 parts, 2 interfaces; 60 × 20 × 62 mm | 2/2 parts and 2/2 interfaces PASS |
| `test_real_four_part_cycle_visu0` | export PASS; geometry NOT_EVALUATED; 4 parts, 4 declarations | 4/4 parts and 4/4 interfaces PASS |

Each directory contains `source.py`, `output/fixed_assembly_config.json`,
`output/assembly/assembly_manifest.json`, per-part STL/GLB and scene/exploded GLB.
Topology measurements are in `topology/checkers/assembly_topology/report.json`
and `result.json`, with per-item diagnostics beneath `items/`.
Full paths and measured values are indexed in
[/tmp/adsl_multi_mate_results_20260928/audit.json](/tmp/adsl_multi_mate_results_20260928/audit.json).

The fourth shelf interface has ~256 mm³ of exposed added tab, ~360.8001 mm³ of
removed slot and ~32 mm³ of embedded root. The same-pair second interface has
288, ~376.3200 and 24 mm³ respectively. Topology finds the tab, empty cavity and
insertion interval for every ID, rather than just counting manifest entries.
Geometry mode also passes existing final-file/scene consistency and dimension gates.
Visual-only mode retains NOT_EVALUATED even after independent topology passes.

Two real source negatives (translation and rotation) reject `lower_right` during
connect, before any STL/manifest is exported. The API tests independently verify
that these rejections leave every saved transform and geometry tree unchanged.
Their source and execution logs are under the two
`test_real_four_part_inconsi*` directories in `verified_real_runs`.

The first real test attempt exported all three valid fixtures, but the test passed
the render GLB directory to topology instead of the assembly directory used by the
existing workflow. This test invocation was corrected without changing the checker.
The first-attempt log and artifacts remain in `stage4_first_attempt.log` and
`real_runs/`; they are not the accepted verification evidence.

## Compatibility and commits

Public connect/Plan fields and manifest version are unchanged. Reverse placement,
cycles with consistent frames, and distinct same-pair ports now succeed. Reusing
a tab port is newly rejected, symmetrically with slot ports; validate rejects
mutated graphs and world frames. There is no insertion-path or holding-force claim.

- Stage 1: `227efe8` — Plan/API and atomicity.
- Stage 2: `b13b078` — export predicate and declaration/feedback regression.
- Stage 3: `cf8eae2` — agent instructions and task entry requirements.
- Stage 4: final commit on `feat/fixed-multi-mate` — fixtures, real validation and this record.

Implementation is isolated from the original workspace's uncommitted FEA/standing,
experiment and report changes. The previously retained SF03 case and repo `temp/`
contents are untouched. No optional dependency installation or model smoke was added.
