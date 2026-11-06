# Manifold-native PGE, collision workspace and first-case timing

Base: `snap3d@8ade97a`. Same frozen `DensiCrafter_124` repair input and assembly
transform. Old runs and model assets remain untouched. This run does not repeat
inference, decomposition or repair, download weights, simplify meshes, run an
artificial gate or perform formal benchmark evaluation.

## Changes

- `pge._cut` counts positive-volume Manifold decomposition components directly.
  Closure is normal Manifold status and nonempty material. Sliver area also uses
  Manifold. No trimesh round trip, MeshEvaluationError interception or
  INVALID_CUT_MATERIAL branch remains inside the cut.
- `connector.rebuild` counts components and measures output intersections on its
  existing Manifold solids. ASSEMBLY_INTERSECTION and THIRD_PART_INTERFERENCE
  require volume strictly greater than 1e-9 D³; the floor only suppresses noise.
- `_rigid_flex_xml` adds `size memory="1G"`, read from `rollout.memory` with that
  default. The vendor XML function and its AST identity are unchanged. No other
  previous configuration value changes.
- `rollout` records initialization, stepping and average per-step wall time.
  Results contain hashes and references, not XML text. One first-case model XML
  is saved; each later candidate keeps its actual model hash and explicitly says
  whether it matches that saved file. A zero-gravity model has a different hash
  and is not mislabeled as the archived gravity model. File hashing is cached
  against path, size and modification timestamp; the saved XML is not overwritten.
- `final_material_review` runs once at terminal export, on the retained gravity
  terminal poses. Per-objective stability continues to use the existing motion
  and final cross-part depth rules. Terminal zero gravity is still one separate
  validity observation.
- `scripts/continue_case.py` runs the requested 0.1-second gravity probe after CGR,
  then finishes the full two-second baseline even when the preview is expensive.
  It stops before CEM when projected case cost exceeds 24 hours; otherwise it
  continues through CEM/export/final validity. Geometry and method parameters
  are never adjusted in response to measured results.

## Timing interpretation

Per-step timing includes mj_step, state synchronization and observation capture.
Projection includes measured initialization/other non-stepping overhead plus the
full number of two-second steps. Nominal CEM budget is 32 × 3 = 96 simulations
per edge, with existing geometry redraws and sampling unchanged. Baseline-selected
edges are recorded. The cost guard uses the number selected from the full baseline, not every
contact edge. The preview before baseline completion separately reports all
placed edges as an upper bound; later dynamic edge-selection changes can alter
the final search cost.
The cost guard uses the larger of the probe projection and actual baseline time,
plus measured connector rebuild cost, already elapsed time and one terminal
zero-gravity rollout estimate. The terminal Boolean sweep and report/export I/O
are explicitly outside that timing estimate. These are cost estimates, not a
runtime guarantee or additional physical acceptance criteria.

## Tests

```text
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 PYTHONPATH=. python -m pytest -q tests
76 passed in 15.07s
```

Coverage includes a native Manifold vertex-touch cut rejected by the old mesh
reader, unchanged per-cut counts, 1G/custom memory XML, sub-noise intersections,
immutable single-file XML evidence with distinct candidate hashes, terminal-only
Boolean measurements, timing math and both sides of the cost guard. Tests assert
that cost limiting happens after the full baseline. Existing vendor provenance,
geometry, CEM, short MuJoCo and stage-continuation regressions pass.

## First-case observations

Results and relative artifact identities are recorded in
`manifold_memory_speed_results.json`. Stage execution success is distinct from
stability and terminal zero-gravity validity. A not-run observation is null, never
an invented PASS or observed fall. Method differences from IPC, public X-Part
light, selected connector geometry and previously requested material-warning
relaxations remain as disclosed in the project README and earlier report.

<!-- RESULTS -->

Current run state: **STOPPED_COST_LIMIT**, stage `rollout`.

| Stage | Status | Wall seconds |
| --- | --- | ---: |
| pge | PASS | 10.76888239569962 |
| cgr | PASS | 150.18458179803565 |
| rollout | PASS | 307.9174273372628 |
| cem | NOT_RUN | — |
| export | NOT_RUN | — |

| PGE pair | Trimmed part | Decision | Reasons | Remaining overlap mm³ |
| --- | --- | --- | --- | ---: |
| part_000 / part_001 | part_000 | retain | — | -1.02695629778e-15 |
| part_000 / part_002 | part_000 | retain | — | 1.60635393875e-15 |
| part_000 / part_003 | part_003 | retain | — | -5.33427468863e-16 |
| part_000 / part_004 | part_004 | retain | — | 5.55111512313e-16 |
| part_000 / part_005 | part_005 | retain | — | -5.89805981832e-16 |
| part_001 / part_002 | part_001 | retain | — | 1.81246077174e-15 |

Total residual overlap: 4.64743261236e-15 mm³; noise floor for this assembly: 0.00478464783228 mm³. Signed sub-noise residues are retained as raw numbers.

| Contact edge | Connector | Reason / warnings |
| --- | --- | --- |
| part_000--part_002 | PLACED | — |
| part_000--part_003 | PLACED | INSUFFICIENT_RECEIVER_MATERIAL |
| part_000--part_004 | PLACED | INSUFFICIENT_RECEIVER_MATERIAL |
| part_000--part_005 | PLACED | INSUFFICIENT_RECEIVER_MATERIAL |
| part_001--part_002 | PLACED | — |

| Speed measurement | Value |
| --- | ---: |
| Flex elements | 391244 |
| Completed probe steps | 50 |
| Initialization wall s | 78.79386474983767 |
| Step-loop wall s | 35.61440681712702 |
| Mean step wall s | 0.7122881363425404 |
| Complete probe wall s | 114.42578669404611 |
| Projected two-second rollout s | 791.0995162194595 |
| All-placed-edge preview CEM evaluations | 480 |
| All-placed-edge preview CEM with rebuild s | 381780.1557688415 |

Baseline-adjusted cost guard:

```json
{
  "flex_element_count": 391244,
  "measured_steps": 50,
  "mean_step_wall_seconds": 0.7122881363425404,
  "initialization_wall_seconds": 78.79386474983767,
  "projected_single_rollout_seconds": 791.0995162194595,
  "optimization_edge_count": 2,
  "nominal_evaluations": 192,
  "projected_cem_rollout_seconds": 151891.10711413622,
  "projected_cem_with_rebuild_seconds": 152712.0623075366,
  "baseline_selected_edges": [
    "part_000--part_004",
    "part_000--part_002"
  ],
  "placed_edge_count": 5,
  "edge_count_assumption": "edges selected for optimization from the full baseline",
  "measured_baseline_seconds": 307.15680181095377,
  "conservative_single_rollout_seconds": 791.0995162194595,
  "projected_case_total_seconds": 154093.55212720716,
  "limit_seconds": 86400,
  "excludes": "one terminal Boolean volume sweep and report/export overhead",
  "upper_bound_record_ref": "cost_guard.json",
  "simulation_repeated": false,
  "baseline_time_only_cem_with_rebuild_projection_seconds": 59795.061141103506,
  "stop_after_baseline": true
}
```

Baseline stable: False; global motion score: 1.5640164091790127; terminal cross-part depth: 0.15992776799602781 mm.

Terminal export and zero-gravity validity have no completed result yet; they are not marked PASS.

The original all-placed-edge upper bound remains untouched in cost_guard.json. The selected-edge calculation is separately saved in cost_guard_actual_selected.json; no simulation was repeated or changed. The current continuation runner now uses selected edges directly.

Probe-based CEM projection for selected edges: 42.420017 h.
The alternative projection from the actual full baseline is 16.609739 h (also an estimate, not measured CEM). The requested stop uses the 0.1-second probe projection; mean per-step time was lower during the full baseline.

Stopped after the completed full baseline under the requested >24 h estimate rule. CEM and final validity were not run.

Full baseline execution: 307.156802 s, including initialization 81.224057 s and 1000 steps in 225.658766 s (mean 0.225658766 s/step). It completed two simulated seconds without a simulator error.

Motion bounds: 9.873427760 mm and 10 degrees. Terminal cross-part depth 0.159927768 mm is below 0.658228517 mm; the baseline fails motion stability, not the depth predicate.

| Part | Pinned | Maximum displacement mm | Maximum 3D rotation degrees |
| --- | --- | ---: | ---: |
| part_000 | False | 6.229714968 | 15.640164092 |
| part_001 | True | 0.000000000 | 0.000000000 |
| part_002 | False | 0.801240328 | 5.393806277 |
| part_003 | False | 13.955196903 | 14.442797678 |
| part_004 | False | 4.835655450 | 8.698808820 |
| part_005 | False | 3.304705667 | 12.283759386 |

This is the connector-initialization baseline. No optimized CEM output exists, so it cannot establish the performance of a completed SNAP3D search.
