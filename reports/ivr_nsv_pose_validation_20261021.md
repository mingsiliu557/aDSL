# IVR applicability, NSV pose selection and saved snowman validation

Base HEAD: `c07b6ea0e601619d42f2cb05f7d2bc257b5dab74`, branch `action_v1`.
This is the earlier validation-stage record, now included with the related
`action_v1` publication. Unrelated working-tree changes are not included.
The intermediate IVR main-table design below was subsequently superseded by
[physical main-table v2](physical_main_v2_20261021.md); raw IVR remains a diagnostic.
Machine-readable results and actual measurement-code hashes:
[sanitized evidence](ivr_nsv_pose_validation_20261021.json).

## Changes

- `benchmark/metrics/physical.py::summarize_physical_methods`: IVR applicability
  is evaluated per case and method. Only confirmed single-part outputs are
  excluded. Unknown or failed multipart measurements remain applicable.
  Multipart methods use their common measurable cases; a sole applicable
  multipart method can report its measured IVR. Exact method/case lists are
  retained. NSV and N common-set rules are unchanged.
- `adsl-agents/support_volume.py::best_support_pose`: measure the existing ray
  support proxy at all existing 24 axis-aligned rotations and select minimum S,
  using rotation index for deterministic ties. For a fixed part volume this
  also minimizes NSV over that finite set. Any failed searched pose prevents
  claiming a complete minimum. Input meshes are not changed.
- `adsl-agents/assembly_overhang.py::partition_measurement`: NSV mode D uses
  this S-selected pose for support, regions and recommended STL. When a valid
  Dapper reference exists, its diagnostic G is measured at the same selected
  pose. Support measurement can proceed without a Dapper reference; missing
  reference/layout integrity still prevents publication. The existing area
  pose search, fixed-upright mode A, scoring formulas and publication gates
  are unchanged.
- `experiments/benchmark_six/evaluate.py::print_measurements`: the common
  evaluator also selects minimum S over 24 poses. Its existing area/G
  diagnostics and 150-mm scale are retained. New support configuration hashes
  include `min_support_axis_aligned_24_v1`; old G-selected NSV must not be
  silently mixed with these results.
- `build_review.py`, support instructions and protocol documents describe
  IVR applicability and the new finite-set support pose policy.
- One pre-existing test fixture was corrected: topology specs come from
  `assembly_topology.checker_spec()`, not the physics factory that does not
  support that checker name. Production behavior was not changed for this fix.

No geometry repair, support formula, S and S/D³ acceptance condition, tolerance,
new agent, model call or FEA change is part of this patch.

## Validation

Raw logs and isolated assets are under
`temp/checker_protocol_v3_validation_20261021/` (local evidence, not committed).
Counts below are separate runs with overlapping tests; they must not be added.

| Run | Result | Log |
|---|---|---|
| Initial protocol/metrics targeted set | 250 passed, 2 failed; both failures were the topology fixture factory above | `targeted.log` |
| Corrected fixture cases | 2 passed, 145 deselected | `fixture_fix.log` |
| Protocol, feedback, recovery, action and partition regressions, before pose-search change | 451 passed, 1 deselected, 105.96 s | `regression.log` |
| First pose-search targeted set | 138 passed, 3 failed; new fixture lacked solid transport file and old assertion omitted bed translation | `nsv_pose_tests.log` |
| Corrected fixtures and final pose/IVR regressions | **249 passed, 34.14 s** | `nsv_regression.log` |

The final run used:

```bash
python -m pytest -q -p no:cacheprovider \
  tests/test_support_volume.py tests/test_support_overhang.py \
  tests/test_support_workflow.py tests/test_benchmark_six_evaluation.py \
  tests/test_physical_metrics.py tests/test_partition_score.py \
  tests/test_actions.py tests/test_partition_selection.py
```

The earlier 451-test run additionally covered Standing verdicts, agent
feedback, benchmark runner, partition feedback, fixed-assembly recovery and
three existing fake-physics workflow tests. The separate native three-control
Standing test was deselected; FEA and complete benchmark runs were not started.

Tests include real artificial T-mesh support measurement, failure of one
searched ray pose, identical measurement/STL poses, no-reference measurement,
old area/actions/partition behavior, and single-part baseline plus multipart
Ours IVR. This is not a claim of exhaustive equivalence.

## Saved snowman: actual measurements

Case `DensiCrafter_21`, saved candidate `attempt_0003`; four actual print parts.
Source SHA-256:
`7c6f9eaccf4ec91f9aa9b1439e826721864a5f093e14235d43ed97016e4c323a`.
Manifest SHA-256:
`b7391a3ec2c4d0acf323586e9d7da2bcf3f3840a6a472dab6528aa5388958392`.

The first isolated run completed real Topology/Standing/Overhang evaluation in
131.90 s. After the pose change, only Overhang was remeasured, in 8.85 s,
reusing hash-bound final meshes and topology transport. The source was not
executed again. Native MuJoCo and mesh/ray measurements were used; Blender
generation, model calls and FEA were not run. Both jobs were managed in tmux
and have exited. Archived inputs and the isolated input assets retained their
hashes. No original ledger or accepted result was changed.

| Quantity | Actual result |
|---|---|
| Topology | PASS |
| Standing observation | PASS, 5.0 s; no observed tipping or interface exit |
| Peak / final tilt | 0.0016853379° / 0.0011441070° |
| Initial contact diagnostic | WARNING: 0.0313093443 mm vs 0.0205722046 mm reference |
| Raw IVR | 1.3668549433963797e-20 |
| Actual print parts N | 4 |
| Ray support measurement | PASS for all four parts and all 24 poses per part |
| Total S | 14280.740194281794 mm³ |
| Total V | 275199.44224439916 mm³ |
| Aggregate NSV, ΣS/ΣV | **0.05189232971482316 (5.189%)** |
| Ray backend | `trimesh_embree` |
| Overhang overall / partition layout | INDETERMINATE |

| Part | Selected rotation index | S (mm³) | V (mm³) |
|---|---:|---:|---:|
| body_core | 5 | 2267.3883462915237 | 250184.1007876841 |
| hat_piece | 6 | 11814.398629630155 | 24553.514322751038 |
| left_arm_piece | 23 | 94.62143420030507 | 221.22714763903173 |
| right_arm_piece | 16 | 104.33178415980875 | 240.59998632501666 |

All four stored 24-pose lists were checked against their selected minima.
Support matrices match recommended layout matrices; the four exported STL
hashes match the recorded layout. These files are diagnostic candidate outputs,
not published accepted layouts.

### Why overall Overhang is still INDETERMINATE

The unchanged partition reference construction reports:

```text
MeshEvaluationError: INPUT_GEOMETRY_INVALID: mesh is not closed, manifold and consistently oriented
```

Previously this skipped pose selection, so S/NSV were also unavailable despite
four valid final meshes (input-pose overhang area was already measurable:
4755.042598059693 mm²). The new independent S search produces complete support
metrics. The reference-dependent partition/layout check remains incomplete;
its INDETERMINATE status is not a ray failure or missing support total.

This patch deliberately does not repair the reference mesh or relax candidate
publication. No new optimization/adoption was performed. A Standing PASS does
not validate all contact modeling or manufacturing, and measurable NSV does
not make this previously unapproved candidate an approved product.

## Limits

- This is a single saved-candidate physical reevaluation, not a fresh
  Engineering → Coder run or evidence of autonomous optimization.
- No old minimum-S result exists for this candidate; the measured NSV is not
  claimed as a geometry improvement or a benchmark-wide gain.
- The 24 rotations give a finite-set minimum of the existing centroid-ray
  support proxy, not a continuous optimum or actual slicer material volume.
- The remaining partition-reference failure needs separate diagnosis if
  complete online candidate acceptance is required.
