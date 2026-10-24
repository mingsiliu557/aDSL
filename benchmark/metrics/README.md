# Offline physical main table

The table reports **Standing success rate, geometric facing-contact connectivity rate, NSV24,
and print-part count N**. These are offline evaluation interfaces, not additional
Agent checkers or publication permissions. Raw IVR remains available through
`physical.raw_ivr()` and historical evidence, but is not a main-table column.

## Geometric facing-contact connectivity

Protocol: `geometric_facing_contact_connectivity_v1`. The JSON table field remains
`structural_connectivity_rate` for compatibility; its displayed name is
**几何接触连通率（距离＋相向面积）**. Neither the old print-plan protocol nor
`geometric_proximity_connectivity_v1` may be relabeled or mixed with this protocol.
The distance-only graph and this facing-contact graph are different measurements.

1. Reuse `union_print_mesh()` for each saved print part, including actual connectors
   and cavities. Transform each material solid into its final assembled millimetre
   coordinates, then form the **global material union** in memory. Graph nodes are
   the actual material components of that union, not declared print parts or GLB
   nodes. Already shared material therefore has one node regardless of export
   grouping. Negative cavity boundaries remain assigned to their smallest
   containing positive boundary; an independent island inside a cavity remains a
   separate component. No vertices are moved and no gaps are filled.
2. D is the diagonal of the final assembled geometry's AABB. The frozen distance
   settings remain `delta_mm = 0.005 * D` and `delta_floor_mm = 0.0` for every method.
3. For each pair of components, let Ai be the area on the first component whose
   nearest surface point on the second is within delta and has an opposing outward
   face normal. Define Aj in the other direction. The fixed conventions are
   `n_i · n_j < 0` and `Aij = min(Ai, Aj)`. An edge requires **both**:
   `material_distance(i,j) <= delta` and `Aij >= 1.5e-5 * D²`.
   No extra angular parameter, ground edge, or declared mate/connect edge is added.
4. Overall success requires at least one valid material component and exactly one
   connected graph component. A chain is sufficient; all pairs need not be adjacent.
   One material component passes directly, while empty input is unavailable.
   Internal material counts per print part are diagnostic and never impose a
   separate failure condition.

`status=PASS` means measurement completed; `overall_pass` gives the graph verdict.
A measured disconnected graph reports `GEOMETRIC_GRAPH_DISCONNECTED`; a successful
one has no failure reasons and `graph_component_count=1`. Unavailable geometry,
distance, or failed facing-area queries give `status=UNAVAILABLE`, `overall_pass=null`
and an unknown graph count, with the particular cause retained. `part_count` stays
the original print-part count N, separately from `material_component_count`.
Each node's `part_ids` lists its original contributors only for provenance; a
globally connected component can contain material from several declared parts.

### Distance and signed-volume diagnostics

AABB distance can exclude a pair but cannot establish contact. The existing
Manifold intersection and FCL triangle-level distance queries operate on complete
material boundaries, including cavities. Positive material intersection gives
distance zero; otherwise FCL tests surface collision and minimum distance.
Negative noncollision distances and failed backends remain unavailable. There is
no vertex-sampling distance fallback and no extra distance tolerance.

If Manifold reports a negative intersection volume, the evaluator requires a
finite, closed, consistently oriented intersection boundary and recomputes its
signed volume by **exact rational summation of the stored float64 coordinates**.
The raw value, recomputed value, method and boundary face count remain in evidence.
This resolves a summation question about that finite boundary; it does not clamp
a negative value to zero, introduce a volume tolerance, alter the boundary, or
repair a mesh. A genuinely negative exact sum, invalid boundary, overflow or
positive-volume underflow remains unavailable. This arithmetic is not a proof
that the Boolean boundary exactly matches an ideal real-valued input surface.

### Area-weighted facing-surface sampling

`contact_area.py` uses `area_weighted_surface_sampling_v1`: **65,536 samples per
direction, seed 0**, with the same settings for all methods and cases. It does not
use adaptive triangle subdivision or deterministic area bounds. First clip the
source surface to the target AABB expanded by delta along each axis. This sampling
domain contains every source point that could be within delta of the target;
retaining extra points inside that box does not make them contact. The clipping
only restricts the query domain; it never changes a saved mesh or material node.

Within that domain, select triangles in proportion to their area and sample their
interiors uniformly. Batched Trimesh nearest-triangle queries supply the actual
closest surface point and its face normal. A sample is a hit only when its distance
is at most delta and the source/target outward **face** normals have `dot < 0`.
No nearest-vertex or interpolated smooth-normal substitute is used. For each
direction, the estimator is:

```text
area_estimate_mm2 = sampling_area_mm2 * hit_count / sample_count
```

Both directions are sampled, then the pair estimate is their minimum. After
sampling completes, an area edge passes when that estimate is at least
`1.5e-5 * D²`; the independent distance condition must also pass. Here `PASS`
means the estimator completed, **not that the exact contact area is certified**.
An estimate close to the threshold still receives the estimate-based verdict.
Input and nearest-point backend failures remain `UNAVAILABLE`; they are not
replaced by zero hits or a zero area.

Each direction saves `sample_count`, `seed`, `hit_count`, `sampling_area_mm2`,
`area_estimate_mm2`, and `area_interval_mm2`. The interval is the binomial Wilson
95% interval for the hit proportion, multiplied by the sampling-domain area.
Zero observed hits therefore have a nonzero upper uncertainty limit when the
domain has positive area. Only an empty eligible domain has `is_exact_zero=true`.
The pair saves the minimum estimate and the interval obtained from the two
directional endpoints. Those are two marginal intervals; their combination is
**not a joint 95% guarantee**. `threshold_in_sampling_interval` records threshold
sensitivity without silently changing the decision or resampling.

A fixed seed makes the configured sampling reproducible; it does not eliminate
sampling error or prove that every small contact region was sampled. Statistical
intervals do not bound floating-point, mesh, normal-orientation or nearest-face
ambiguity errors. Backend precision still matters near `dot = 0`, delta, or the
area threshold. The sample count, seed, algorithm and backend versions are saved
in the measurement configuration and its hash. Results from the previous adaptive
area estimator must not be pooled with these sampling results even though the
high-level contact protocol ID is unchanged. None of these sampling settings adds
a manufacturing tolerance.

The metric measures geometric contact proximity, not mechanical retention, strength,
insertion feasibility, or absence of interpenetration. Actual overlapping material
can become one node in the global union; that does not make the overlap physically
valid. The union exists only for this evaluation: saved meshes, print groups,
online Topology verdicts and publication/acceptance rules remain unchanged.

## NSV24 and N

Protocol: `support_volume_min24_v1`. For every complete union-material print part,
run the existing `support_volume()` at the existing ordered `rotations24()`.
Rotate local Z-up millimetres, translate the rotated minimum coordinates to the
bed, and choose minimum **S**, breaking ties by rotation ID. All 24 evaluations
must complete. Failure of one pose or part leaves the aggregate unavailable.

`NSV24 = sum_i min_r S_i(r) / sum_i V_i`, including connector material. V uses
each part's internal union; there is no cross-part union or equal averaging of
part ratios. All 24 S/V/status/bed transforms and the chosen ID are saved. This
is a finite orientation-set ray support proxy, not sliced material consumption
or a continuous optimum. G does not choose this offline result.

The new field is `record.support_evaluation`; `record.support` is preserved.
An old record without the new protocol gives `SUPPORT24_NOT_MEASURED`, even if
it contains a previous G-selected or S-selected support number. This prevents
implicit protocol conversion. Online mode A's upright restriction and existing
online orientation/layout logic are not changed by this module.

NSV and N share the exact all-method, same-configuration measurable case list.
N is the **predetermined print-part count**, not material component count; a
single part broken in two still has N=1. They are case means, not a pooled
dataset volume ratio. Table note:

> NSV 与 N 在所有对比方法同协议、共同可测的案例子集上分别取案例均值，样本数为
> n=…；Standing 与几何连通成功率按各自预定适用案例统计。不可测原因和逐例名单见补充材料。

## Standing and denominators

The current Standing protocol is `material_component_standing_v1`; classification
requires complete component-tilt evidence as well as the original observations. Terminal generation,
input or simulation failures count as unsuccessful, not observed tipping.
Pending tasks prevent a completed success rate. Standing applicability and
structural applicability are separate frozen booleans; the latter defaults to
true for the single-object task set. The full frozen-sample summary does not
exclude failures after evaluation. The explicitly curated dataset below is a
separately labeled subset, not a replacement for that full-sample success rate.
Missing NSV remains null. Protocol, measurement configuration and asset binding
conflicts raise errors rather than pruning samples. Online Topology/Standing
failures do not remove an otherwise measurable offline geometry result.

Current shared evaluator wiring (real simulations have not been rerun under this protocol):

- `assembly_standing.analyze()` splits each valid print part into cavity-aware
  material components and `simulate()` builds one independent free-joint body per
  component. Print-part count N remains distinct from simulated body count.
- `experiments/benchmark_six/evaluate.py:official_manifest()` transforms and
  combines every object scene node into one declared print part, then its
  `physical_checks()` uses the same component Standing path. Its disconnected
  material components therefore move independently under the same protocol as
  Ours; they are not rigidly bound together.
- Older/native official-aDSL simulation paths outside this common evaluator
  have **not been confirmed here**. Their rigid-body semantics must be recorded
  separately before mixing results. In any implementation that fixes disconnected
  material as one body, Standing cannot establish material continuity. The
  material-component protocol above changes the shared evaluator body partition;
  existing case outputs have not been rerun or relabeled by this implementation.

## Inputs, identity and units

Public functions in `geometry.py`:

```python
load_evaluation_parts(input_record: dict) -> dict
measure_connectivity(parts, *, delta_ratio, delta_floor_mm) -> dict
measure_support24(parts, *, overhang_threshold_from_horizontal_deg,
                  bed_tolerance_mm) -> dict
```

The loaded `parts` contain in-memory `mesh_mm`, face groups and
`assembly_transform_mm`. JSON outputs never serialize meshes. The material union
is cached within these objects for the two measurements, not written back to
source assets. Adapter choices:

- `fixed_assembly`: verify manifest hash, source hash and each STL hash using the
  existing reader contracts. Invert `print_transform_mm` on millimetre STL;
  multiply only assembly-transform **translation** by `mm_per_unit`. All declared
  parts must exist exactly once. Print layout or exploded coordinates are not
  used as assembly coordinates.
- `single_print_mesh`: explicit `length_unit_to_mm` and rigid
  `file_to_assembly_mm` establish local Z-up mm and assembled coordinates. Expand
  every GLB scene-node transform, then scale once. Only explicitly listed
  `excluded_auxiliary_nodes` are omitted. No first-object/largest-component
  shortcut. The whole asset is one predetermined print part. Optional face groups
  refer to the concatenated, lexically ordered retained scene nodes.

Asset SHA binds verified files, units, transforms, exclusions and print grouping;
it is **not** the manifest file hash. An optional pre-existing record asset SHA
must equal this composite. The loader returns it for freezing subsequent records.
Configuration SHA binds protocol, thresholds, kernel/ray/FCL versions and ordered
rotations, not case ID, D or asset identity. Changing a backend or floor requires
consistent remeasurement before comparison.

Example frozen input (`<...>` hashes must be replaced with actual SHA-256 values):

```json
{
  "methods": {
    "ours": [{
      "case_id": "example", "applicable": true,
      "connectivity_applicable": true, "state": "TERMINAL",
      "protocol_id": "material_component_standing_v1",
      "checker": {}, "generation_status": "COMPLETED",
      "geometry_input": {
        "kind": "fixed_assembly", "manifest_path": "final/assembly_manifest.json",
        "manifest_sha256": "<sha256 of selected final manifest>",
        "source_path": "final/source.py"
      }
    }],
    "baseline": [{
      "case_id": "example", "applicable": true,
      "state": "TERMINAL", "protocol_id": "material_component_standing_v1",
      "checker": {}, "generation_status": "COMPLETED",
      "geometry_input": {
        "kind": "single_print_mesh", "mesh_path": "final/baseline.glb",
        "mesh_sha256": "<sha256 of selected final mesh>", "length_unit_to_mm": 1.0,
        "file_to_assembly_mm": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],
        "excluded_auxiliary_nodes": []
      }
    }]
  }
}
```

This example intentionally has no Standing measurement: its terminal Standing
outcome is unsuccessful/observation unavailable. For a measured record, supply
the unchanged raw checker result and matching source hash. Do not invent a pass
or relabel an old-protocol result. Select the controller's retained final asset,
not the latest rejected candidate. Freeze the same case/applicability list for
all methods. Scene files must already have known axis/unit conventions.

## CLI and audit

The optional `benchmark` dependencies include python-fcl, trimesh, Manifold and
rtree. Run from a source checkout in the existing aDSL environment:

```bash
python -m benchmark.scripts.evaluate_physical_metrics \
  --records frozen_method_records.json \
  --config benchmark/configs/physical_main_v1.json \
  --output new_audit_directory --audit-only
```

`--audit-only` loads verified assets and reads assembled bounds/fit offsets; it
does not run union/distance/support measurement. With neither mode flag, the CLI
evaluates connectivity and support24, writes per-case details and the existing
summary interface's JSON/CSV. It never runs source, exports a replacement model,
invokes models or simulates Standing. Output must be a **new** directory. Inputs
stay unchanged.

For a connectivity-only reassessment of already frozen results:

```bash
python -m benchmark.scripts.evaluate_physical_metrics \
  --records frozen_reassessment_records.json \
  --config benchmark/configs/physical_main_v1.json \
  --output new_connectivity_directory --connectivity-only
```

This mode is mutually exclusive with `--audit-only`. Records must supply a
hash-bound `frozen_summary_ref` (path, JSON pointer and file SHA-256) to a complete
prior summary. Each record retains its original `standing_evaluation` with
`standing_evidence_ref`, `support_evaluation`, old `connectivity`, source/asset
identities, and actual `part_count`. The helper checks the frozen case order,
applicability, values, protocols, configuration and geometry identity before
measurement. Missing raw Standing checker files are not reconstructed: the
archived classified rows and summary are reused exactly, with their evidence.
These records require the connectivity-only reader, not a new Standing
classification through the full evaluator.

Only connectivity is remeasured. Standing, NSV24, N and their sample lists remain
unchanged, including when connectivity fails. Referenced old JSON files are
copied byte-for-byte into the new output; new refs retain their hashes and
pointers. A missing or conflicting reuse record raises an error instead of
triggering support search or selecting another model.

An explicitly requested deferral can be frozen in the top-level list
`deferred_connectivity=[{"method": "ours", "case_id": "example", "reason": "..."}]`.
These entries retain the old evidence, receive
`CONNECTIVITY_REEVALUATION_DEFERRED` with `details.measured=false`, and count as
PENDING in the new summary. They are not measured failures or excluded samples;
the affected method's full-set connectivity rate stays null and the summary is
INCOMPLETE. New measurements and reused metrics are listed in `input_identity.json`.

The supplied config freezes the facing-area ratio at `1.5e-5`, the normal dot
threshold at `0.0` (strict opposition), and the directional reduction at `min`.
It retains the existing 45-degree overhang and 0.2-mm bed exclusion settings, with
floor=0. No automatic scale normalization is performed. Audit CSV
records D, 0.005D, fit offsets, units and asset hashes. A fit offset larger than
delta is a review flag, not evidence that material distance exceeds delta:
shoulders/end faces may already touch. No floor is inferred from Ours or tuned
against success rates. Existing normalized evaluation assets retain their
previous scale, which must be identified separately from native generated scale.

Details, missing causes and exact NSV/N sample IDs accompany the table. The legacy
review builder uses the new column names but does not trigger these additional
measurements; its old support records remain unmeasured under this protocol.

## Final evaluation and dataset admission

The shared `experiments/benchmark_six/evaluate.py` final evaluator now measures
this connectivity protocol for **both** official aDSL and Ours, after the existing
final-asset adapter. It saves `connectivity_details.json`, a bound
`admission_record`, and `dataset_eligibility`. A prior physical or print-measurement
failure does not suppress this independent measurement when the final manifest is
available. It does not add an Agent checker, alter native approval, select another
candidate, trigger repair or change the aDSL-mesh-valid gate before Ours generation.
Frozen older runner snapshots are not rewritten by this change.

Admission requires a **completed and measurable** connectivity result with valid
asset/configuration/evidence binding. `status=PASS, overall_pass=false` is eligible:
a disconnected graph and Standing FAIL remain useful measured failures. Terminal
unavailable/missing measurements are excluded with reasons; unfinished outputs are
PENDING. Identity/protocol/configuration conflicts are errors, not ordinary failed
measurements to discard. The existing evaluation timeout budget is unchanged; its
exit classifies completed evidence or records unavailable, without measuring again.

`physical.select_measurable_pairs()` uses the same ordered cases and protocol for
all methods. A case enters the comparison only when **every** method is eligible;
otherwise the entire pair is excluded or pending. This is dataset membership, not
another physical pass/fail verdict. The original full-sample summary and excluded
assets/evidence remain available. This policy defines a result-filtered development
or presentation subset; it does not establish unbiased end-to-end performance.

For a completed, hash-bound measurement archive, derive that subset without any
measurement or Standing reclassification:

```bash
python -m benchmark.scripts.curate_measurable_pairs \
  --records completed/measured_records.json \
  --summary completed/summary.json \
  --output new_paired_dataset_directory
```

The new directory contains the included/excluded lists, method-specific reasons,
rebound relative references, and a table labeled as a common measurable subset.
Standing observations and per-case NSV24/N are reused exactly; only aggregation
over the explicit subset changes. N and NSV retain the same common measured
sample list within that subset. This command does not delete models or edit the
original case registry. A current dataset must use its explicit active index,
while original import indices remain archival provenance.

## Sources and limits of adaptation

- [ShapeAssembly, appendix E](https://rkjones4.github.io/pdf/shapeAssembly.pdf):
  distance connectivity/Rootedness inspiration. Its ground node and 2%-diagonal
  threshold are not reproduced here; this protocol uses all material components,
  a uniform 0.005D threshold, and no ground node.
- [SNAP3D §3.2](https://arxiv.org/html/2609.13146#S3.SS2) and
  [Appendix B](https://arxiv.org/html/2609.13146#A2): inspiration for combining
  distance with opposing-normal contact area, using the stated relative distance
  and area scales. Our nodes are global-union material components, not its edited
  semantic parts. The paper describes fixed-seed surface sampling but does not
  specify the sample count, directional reduction or numeric normal threshold.
  Our explicit `dot < 0`, bidirectional `min`, 65,536 samples per direction,
  seed 0, clipped sampling domain and Wilson uncertainty reporting are this
  project's conventions. This is an adaptation, not a reproduction of its
  complete contact-graph or physical evaluation.
- [Trimesh surface sampling](https://trimesh.org/trimesh.sample.html#trimesh.sample.sample_surface)
  and [closest surface points](https://trimesh.org/trimesh.proximity.html#trimesh.proximity.closest_point):
  area-weighted sampling and triangle-level nearest-point building blocks, not
  a built-in contact-area classification protocol.
- [Trimesh CollisionManager](https://trimesh.org/trimesh.collision.html) and
  [python-fcl](https://github.com/BerkeleyAutomation/python-fcl): triangle-level
  collision/distance queries. Backend availability and precision remain explicit.
