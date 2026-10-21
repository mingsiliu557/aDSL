# Offline physical main table

The table reports **Standing success rate, structural connectivity rate, NSV24,
and print-part count N**. These are offline evaluation interfaces, not additional
Agent checkers or publication permissions. Raw IVR remains available through
`physical.raw_ivr()` and historical evidence, but is not a main-table column.

## Structural connectivity

Protocol: `print_plan_structural_connectivity_v1`.

1. For each predetermined print part, reuse `union_print_mesh()` on its complete
   material, including connectors and cavities. Internal continuity means exactly
   one positive-volume material component. There is no within-part distance
   threshold, vertex displacement, hole filling, or new mesh repair.
2. Transform every complete part into final assembled millimetres. Let D be the
   diagonal of their combined AABB. The frozen configuration uses
   `delta_mm = max(0.005 * D, 0.0)`.
3. Connect two print-part nodes if their material distance is at most delta.
   A positive checked Manifold intersection gives distance zero (also covering
   containment). Otherwise use FCL collision/triangle distance on the complete
   material boundary. AABB distance can exclude an edge, never establish it.
4. Overall success requires **both** all parts internally connected and the
   interpart graph connected. Another part cannot rescue a broken print part by
   bridging its disconnected components. One valid print part has a connected
   interpart graph; zero parts do not constitute success.

`status=PASS` means the measurement completed; `overall_pass` is the structural
verdict. Completed failures preserve `INTERNAL_PART_DISCONNECTED` and/or
`INTERPART_GRAPH_DISCONNECTED`. Success has `reason_codes=[]` and both component
predicates true. `UNAVAILABLE` has `overall_pass=null` and retains already known
failures without fabricating unknown values. Both failure counts can overlap.

Queries preserve hollow material: an object separated inside a cavity is not a
solid intersection. We do not fill positive shells returned by decomposition.
FCL is a floating-point triangle-level query, not symbolically exact geometry.
The comparison is `distance <= delta`, with no additional threshold tolerance.
The existing material kernel's length bound and all backend versions are saved.
Negative noncollision distances, failed intersections and backend failures are
unavailable; there is no sampling fallback. Full details include graph groups,
pairs, distances, intersection evidence and FCL witness points when available.

This metric depends on the frozen print grouping. It is not a representation
invariant, insertion/strength check, or proof of no interpenetration. Predetermined
groups must not be changed after seeing the connectivity result.

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
> n=…；Standing 与结构连通成功率按各自预定适用案例统计。不可测原因和逐例名单见补充材料。

## Standing and denominators

`classify_standing_record()` and `summarize_standing()` are unchanged. The
Standing protocol is `independent_contact_warning_v1`. Terminal generation,
input or simulation failures count as unsuccessful, not observed tipping.
Pending tasks prevent a completed success rate. Standing applicability and
structural applicability are separate frozen booleans; the latter defaults to
true for the single-object task set. Do not exclude failures after evaluation.
Missing NSV remains null. Protocol, measurement configuration and asset binding
conflicts raise errors rather than pruning samples. Online Topology/Standing
failures do not remove an otherwise measurable offline geometry result.

Static verification of the current common evaluator:

- `assembly_standing.analyze()` requires one connected material volume per
  rigid print body. `simulate()` builds one free-joint body per print part.
- `experiments/benchmark_six/evaluate.py:official_manifest()` transforms and
  combines every object scene node into one declared print part, then its
  `physical_checks()` uses the same assembly Standing path. Disconnected material
  therefore remains unsupported in that path; it is not silently welded into
  one simulated rigid body.
- Older/native official-aDSL simulation paths outside this common evaluator
  have **not been confirmed here**. Their rigid-body semantics must be recorded
  separately before mixing results. In any implementation that fixes disconnected
  material as one body, Standing cannot establish material continuity; this new
  metric supplies that separate observation. This change does not alter any
  rigid-body partition or simulate an existing case.

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
      "protocol_id": "independent_contact_warning_v1",
      "checker": {}, "generation_status": "COMPLETED",
      "geometry_input": {
        "kind": "fixed_assembly", "manifest_path": "final/assembly_manifest.json",
        "manifest_sha256": "<sha256 of selected final manifest>",
        "source_path": "final/source.py"
      }
    }],
    "baseline": [{
      "case_id": "example", "applicable": true,
      "state": "TERMINAL", "protocol_id": "independent_contact_warning_v1",
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
does not run union/distance/support measurement. Without it, the CLI evaluates
offline geometry, writes per-case details and the existing summary interface's
JSON/CSV. It never runs source, exports a replacement model, invokes models or
simulates Standing. Output must be a **new** directory. Inputs stay unchanged.

The supplied config uses existing 45-degree overhang and 0.2-mm bed exclusion
settings, with floor=0. No automatic scale normalization is performed. Audit CSV
records D, 0.005D, fit offsets, units and asset hashes. A fit offset larger than
delta is a review flag, not evidence that material distance exceeds delta:
shoulders/end faces may already touch. No floor is inferred from Ours or tuned
against success rates. Existing normalized evaluation assets retain their
previous scale, which must be identified separately from native generated scale.

Details, missing causes and exact NSV/N sample IDs accompany the table. The legacy
review builder uses the new column names but does not trigger these additional
measurements; its old support records remain unmeasured under this protocol.

## Sources and limits of adaptation

- [ShapeAssembly, appendix E](https://rkjones4.github.io/pdf/shapeAssembly.pdf):
  distance connectivity/Rootedness inspiration. Its ground node and 2%-diagonal
  threshold are not reproduced here; our joint internal/interpart condition is
  task-specific and has no ground node.
- [SNAP3D appendix](https://arxiv.org/html/2609.13146): contact-distance scale
  inspiration only; its facing-area/normal contact rules are not reproduced.
- [Trimesh CollisionManager](https://trimesh.org/trimesh.collision.html) and
  [python-fcl](https://github.com/BerkeleyAutomation/python-fcl): triangle-level
  collision/distance queries. Backend availability and precision remain explicit.
