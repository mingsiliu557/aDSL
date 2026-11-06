# First-case continuation after local gate removal

Base: `snap3d@ba4e154`. This change only affects the independent SNAP3D formula
reimplementation. Existing model outputs and historical reports are retained.
No generation, decomposition, model invocation, download, formal evaluation or
new artificial gate run is part of this continuation.

## Implementation

- `pipeline.paper_rollout`: one gravity simulation per objective. Stability uses
  unchanged motion bounds and final cross-part contact depth <= D/256. Final-pose
  Boolean intersection volumes remain diagnostic. The world floor is excluded
  from the cross-part depth predicate.
- `pipeline.evaluate_geometry`: configuration/source changes are recorded; stage
  reruns overwrite outputs. Failed local initialization records an edge skip.
  Exhausted CEM edges retain the incumbent and allow other edges to proceed.
  The exported STL/GLB includes actual connectors. Exactly one zero-gravity run
  on the exported assembly records final validity, independently of CEM.
- `rollout`: rigid_flex only; pin minimum-z <= 0.005D once and reuse those IDs.
  An all-pinned assembly is a measurable static assembly, not NO_FREE_PARTS.
  Nonfinite state, input errors and simulator failure remain errors.
- `contact` / `connector`: anchor-unavailable skips an edge; theta0 infeasibility
  skips its connector. CEM redraws infeasible candidates. Placement diagnostics
  are warnings; cone, third-part interference and dimensional bounds remain.
- `pge`: no cavity-parent reconstruction or PARENT errors. Component counts use
  `component_count`. Only newly split thin fragments are removed; deletion uses
  subtraction so remaining cavities are not filled. A finite but invalid cut
  is rejected locally; nonfinite diagnostics still propagate. Loss-cap reversal
  exhaustion is recorded rather than throwing a whole-case error.
- `repair`: closed inputs pass directly. Open inputs use trimesh and, if needed,
  PyMeshFix closure without joining components or removing smallest components.
  Failed closure still errors. Opening diameter, planarity and patch collision
  gates are removed.
- Old zone proxies, CoACD dependency/configuration, exposed rollout branches and
  their tests are removed. Vendored full XML/settings bodies retain AST identity;
  an unused legacy XML branch is not exposed by this project.
- Generation/decomposition/pipeline no longer require an artificial-gate file.
  The explicit snapshot-download command also drops that removed import; it was
  not executed. Readable partial decomposition exports record incompleteness.

## Rules and selected details

Source: [SNAP3D sections 3 and Appendices A/B](https://arxiv.org/html/2609.13146).
The second column discloses approximations and requested departures; this is not
an exact reproduction of IPC or unpublished implementation details.

| 论文规定的规则 | 我们补充的实现细节 |
| --- | --- |
| PGE scores, insertion tests, cumulative 35% limit; per-cut overlap/closure/component checks | Manifold instead of grid Booleans; new-fragment thickness 2V/A < D/256; exhausted reversals warn |
| Distance 0.005D and facing area 1.5e-5D² | Existing area sampling, bidirectional minimum, raster anchor implementation retained |
| Shaft/insertion minimums, 55° cone and third-part avoidance | Existing circular mushroom section and head thickness retained |
| Appendix B also rejects insufficient socket material | Explicitly relaxed to WARNING as requested; not claimed to be the paper rule |
| Pin ground-contact parts | Minimum-z <= 0.005D is our numerical contact threshold |
| Two-second gravity objective, 15D/256 motion and 10° rotation bounds | MuJoCo rigid_flex and unchanged aDSL simulation settings replace IPC |
| Intersection-free terminal geometry | Final cross-part depth <= D/256 is a soft-contact adaptation; Boolean volume is diagnostic |
| CEM 32 candidates, 8 elites, 3 generations, initialization retention and global-motion constraint | Existing initial variance, floor, redraw cap and numerical covariance conditioning retained |
| Appendix A half-cell zero-gravity validity | Applied once after export; does not veto CEM or erase output |
| Physical parts require closed material | Standard hole filling is additional preprocessing; no opening-size cutoff |

## Tests

```text
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 PYTHONPATH=. python -m pytest -q tests
71 passed in 14.19s
```

Tests include standalone vendor provenance/import, PGE score/loss/rollback/thin
fragments, connector geometry and immutable rebuilding, CEM/global constraints,
short real MuJoCo regressions and stubbed whole-stage execution. New coverage in
`tests/test_flow_continuation.py` checks per-edge skips, material warnings, ground
pinning, terminal-only zero gravity, source/config warnings, stage overwrite,
exhausted-edge continuation and propagation of genuine simulation/numeric errors.
No new artificial full-duration gate or model invocation was run by these tests.

## First-case run

Same `DensiCrafter_124` frozen repair input, in a new directory:
`runs/first_real_DensiCrafter_124_flow_v2/assembly`. Frozen input manifest hashes
are recorded in `reuse.json`. Old raw assets and old runs are not overwritten.
The original D and transforms are reused; no inference or repair is repeated.

Runtime results are recorded in `flow_continuation_results.json` and the table
below. Stage PASS means the stage executed, not that the assembly is stable or
its final zero-gravity validity passed. No parameter is adjusted in response to
this case's measured results.


| Stage | Result | Wall time (s) |
| --- | --- | ---: |
| pge | PASS | 11.153292 |
| cgr | PASS | 196.295479 |
| Gravity rollout initialization | MuJoCo FatalError; observation unavailable | 80.315229 |
| CEM | NOT_RUN | — |
| Final export stage | NOT_RUN | — |
| Final zero-gravity validity | NOT_RUN | — |

PGE retained 5 cuts and rolled back 1. It removed 1 new thin fragment. Residual overlap is 1841.535559120 mm³ (6457.286080402 grid cells). The cumulative volume cap is satisfied.

| Pair | Trimmed part | Decision | Reason | Residual overlap (mm³) |
| --- | --- | --- | --- | ---: |
| part_000 / part_001 | part_000 | retain | — | -0.000000000 |
| part_000 / part_002 | part_002 | retain | — | -0.000000000 |
| part_000 / part_003 | part_003 | retain | — | -0.000000000 |
| part_000 / part_004 | part_004 | retain | — | 0.000000000 |
| part_000 / part_005 | part_005 | retain | — | -0.000000000 |
| part_001 / part_002 | part_002 | rollback | INVALID_CUT_MATERIAL | 1841.535559120 |

| Contact edge | Connector | Reason / warning codes |
| --- | --- | --- |
| part_000--part_001 | SKIPPED | THIRD_PART_INTERFERENCE |
| part_000--part_002 | SKIPPED | THIRD_PART_INTERFERENCE |
| part_000--part_003 | PLACED | ASSEMBLY_INTERSECTION, INSUFFICIENT_RECEIVER_MATERIAL |
| part_000--part_004 | PLACED | ASSEMBLY_INTERSECTION, INSUFFICIENT_RECEIVER_MATERIAL |
| part_000--part_005 | PLACED | ASSEMBLY_INTERSECTION, INSUFFICIENT_RECEIVER_MATERIAL |
| part_001--part_002 | SKIPPED | THIRD_PART_INTERFERENCE |

The rebuilt material supplies **393,196 triangles** to rigid_flex. The runtime element-count field was not saved before initialization failed; it is not invented. The connector-bearing CGR asset remains available with its hash in the JSON evidence. It is not mislabeled as the unexecuted final export stage.

The first `mj_forward` exhausted MuJoCo’s internal collision stack:

```text
mj_stackAlloc: out of memory, stack overflow at mj_collideTree, line 1250
  max = 3949320, available = 1484912, requested = 7400036
 nefc = 0, ncon = 0
```

This is a simulator allocation error, not a connectivity/material warning, an observed fall, or an OS OOM-kill. No completed two-second gravity observation exists, so stability and terminal validity are null. Following the requested stop condition, CEM and final zero gravity were not launched. No mesh simplification, workspace-capacity increase, solver tuning, threshold relaxation or successful result is substituted. A possible separate follow-up is to enlarge only MuJoCo collision workspace capacity and rerun the same frozen CGR assembly; that change is not made here.

The initial PGE attempt exposed propagation of a local invalid-cut material diagnostic. That finite cut is now rolled back locally under the per-cut rule; numeric faults still propagate. `test_invalid_boolean_cut_rolls_back_but_nonfinite_geometry_is_fatal` covers this correction. Those initial diagnostics remain in the private run.

Supplementary geometry tests also passed (71 in 15.77 s) in the existing inference environment. A CPU-only synthetic open-sphere smoke exercised native PyMeshFix 0.18.1 after trimesh could not close the opening: the result was watertight. The primary test environment uses the pinned trimesh 5.1.1; supplementary native-tool checks use the existing trimesh 5.2.0 environment. No environment was upgraded and no model or weight was downloaded.
