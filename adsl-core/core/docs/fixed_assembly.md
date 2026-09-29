# Optional fixed print assembly

Public entrypoints: `FixedAssembly`, `InterfaceFrame`, `TabSlot` from `adsl.core`.
This supplements, and does not change, `Asset.attach_part()` or `Asset.fixed()`.

`FixedAssembly(root_id, mm_per_unit)` uses keyword arguments and selects explicit
print parts via `add_part(part_id, body, components=(...))`. Semantic children stay
inside their selected print piece. Root placement can use `root_frame`.

`connect(interface_id, tab_part=..., slot_part=..., tab_frame=..., slot_frame=...,
parameters=TabSlot(...), parameter_name=..., tab_port=..., slot_port=...)` creates
both mating geometries. Process connections in order starting from root_id;
at least one endpoint must already be placed. Tab/slot are geometric roles:

- Only slot placed: `T_tab = T_slot @ F_slot @ inverse(F_tab)`.
- Only tab placed: `T_slot = T_tab @ F_tab @ inverse(F_slot)`.
- Both endpoints placed: check `T_tab @ F_tab == T_slot @ F_slot`, then create
  both mating geometries without moving either part.
- Neither endpoint placed: reject the connection with its ID and endpoints.

Additional connections can occur before the last new part is placed. Every physical
interface needs a separate ID and endpoint ports; the same pair of parts may have
multiple interfaces using distinct ports. A port is occupied per (part, tab/slot
role, name). For example, a stem and crossbar can use `left_tab/left_slot` and
`right_tab/right_slot`: the first connection places the stem, the second checks
alignment and generates another interface. Distinct names alone do not establish
that the interfaces occupy different geometry. Build material for all interfaces.
Connection evaluation order does not establish a feasible physical insertion path.
`scene = assembly.scene()` is the final static Asset.

InterfaceFrame's origin is the stop plane, +X is tab width, +Z insertion into the
receiver, on both sides. T maps part-local to assembly coordinates; F maps interface
to part-local coordinates. Transforms are applied once to copies of local part
geometry, not twice to already baked world coordinates. Exploded display copies
do not modify the assembly.

`MATE_FRAME_MISMATCH` reports the connection ID, endpoints, translation_error_mm
and rotation_error_deg. Repair the frames, dimensions or connection arrangement
and re-execute the candidate; existing poses are never automatically adjusted.
API, validation and export share `np.allclose(..., rtol=0, atol=1e-8)` on world
matrices. This tolerance uses scene units for translation, not mm or manufacturing
clearance; fit_offset_mm does not participate. Rejected connections preserve saved
geometry, parent links, transforms and connection records.

TabSlot takes width_mm, thickness_mm, insertion_mm, slot_depth_mm, fit_offset_mm,
root_overlap_mm, opening_extension_mm and lead_in_mm. Slot width/thickness are the
nominal tab size plus twice the SINGLE-SIDED fit offset. Depth clearance is separate.
Lead-in is a 45° tip chamfer, not a global numeric tolerance. Explicit mounting
overlap must be real material; the geometry evaluator checks it.

Before writing meshes, the shared exporter normalizes exact-zero triangles on a
copy. Adjacent vertices with exactly equal local coordinates may be merged only
along a manifold zero-length edge shared by the affected polygons. Those polygons
are triangulated before welding, and every positive triangle must retain exactly
the same coordinates, winding and material. This also handles redundant edges
inside closed meshes, where the boundary-crack pass has no search region.
Otherwise, bad planar polygons use the existing local retriangulation path.
Both paths preserve geometric vertex positions and connectivity, and commit only
after closure, manifoldness, winding and geometric invariants are checked. Small
positive faces are not filtered; no distance or planarity tolerance is increased.
The same evaluated mesh supplies STL, display and downstream checks; successful
normalizations are recorded in each part's `mesh_normalizations`. An unresolved
`EVALUATED_MESH_DEGENERATE` is an evaluation error, not established evidence of a
source-design defect. This pass does not fill open boundaries or repair arbitrary
degenerate triangle meshes. Successful exact-edge cleanup records merged vertex
and zero-area counts, zero displacement and positive-surface preservation in
`mesh_normalizations`. Rejection diagnostics distinguish collinearity from
nonplanarity and report plane distance/bound in scene units and coincident vertices.

For fixed-part exports with known mm_per_unit, a second bounded pass uses BMesh
find_doubles/weld_verts only inside individual triangular boundary loops. A pair
must be within two float32 ULPs per coordinate, 1e-4 of the loop's longest edge,
and 1e-4 mm displacement. No cross-loop, cross-shell or cross-part search occurs.
Only a closed, manifold, consistently wound copy with preserved faces/materials,
connectivity and bounded geometric deviation is committed. Otherwise the original
mesh remains for downstream checking. APPLIED/SKIPPED/REJECTED diagnostics include
boundary counts and maximum displacement in mesh_normalizations. This handles
eligible numerical cracks, not arbitrary holes or intentional fitting gaps.

Independent `assembly_topology` measures all unordered print-part pairs in their
assembled positions, including connected and multi-interface pairs. Final local-mm
solids are placed by assembly transforms; printing orientation does not change the
result. Numerical overlap within the existing float32 coordinate/surface-area bound
is tolerated. Only explicitly declared negative-fit tab regions are exempt, and
only for their own part pair; positive-fit slots are already empty material.

Scope version 2 reports `kind="pair"`, `pair_id`, `part_ids`, overlap volumes,
numerical bounds and an assembly-mm AABB. `UNDECLARED_PART_INTERFERENCE` means
remaining material intersection exceeds that numerical bound. The AABB/its center
locate the result, not an exact penetration depth, contact point or unique source
cause. Unavailable queries remain INDETERMINATE. Inspect body dimensions, local
clearance and related connection frames; after a coordinated source correction,
re-export and recheck. Older topology results are remeasured before reuse. These
checks do not establish assembly insertion paths, fastening retention or strength.

See `examples/fixed_assembly/README.md` and `t_bracket.py` in the repository for the
end-to-end opt-in config, validation and outputs. Dimensions and fit are geometric
demonstrations until calibrated on a real printer/material. See also the four-part,
four-interface `two_shelf_frame.py` fixture. Cycles are consistency checked, not
solved by moving existing parts. Articulation and automatic print-part segmentation
remain unsupported.


## Optional partition objective

`physics.overhang.partition_objective` enables `dapper_fdm_2015` with alpha=0.3,
r_vox=0.1, orientation_set=axis_aligned_24 and layout=independent_bed. Each final
print solid, including connectors, is evaluated against one immutable body reference.
Score `(reference_voxels - gap_voxels) / print_part_count**0.3` is maximized; negative
numerators are preserved and do not universally penalize extra pieces. This adopts
Dapper's objective, not its packing algorithm or a slicer's material/time estimate.
Report `voxel_pitch_mm`; coarse cells can hide connector details. Missing inputs
produce null scores, never zero. Original overhang area remains at the authored pose.

`RepairPolicy.print_partition_editable` defaults to false. When enabled, Engineering
may propose one local `regroup_print_parts` split/merge, preserving body shape/root
and updating every affected connector. Necessary physical/appearance repair remains
separate from soft optimization. Unknown, equal, worse or different-reference
scores cannot replace a qualified baseline for optimization.

The selected manifest's part_declarations/connections describe actual grouping;
initial plan.json is not rewritten. Use assembly_result.json.print_layout and
print_parts for the recommended print orientation/STL, not the authored-pose STL.
The layout, score, body reference, source and all checker results are bound to the
same selected version. Assembly/use transforms and Standing inputs are unchanged.
