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

Before writing meshes, the shared exporter locally retriangulates planar polygons
whose default tessellation contains exact-zero triangles. It preserves vertices,
materials and connectivity, and commits only after closure, manifoldness, winding
and geometric invariants are checked. Small positive faces are not filtered.
The same evaluated mesh supplies STL, display and downstream checks; successful
normalizations are recorded in each part's `mesh_normalizations`. An unresolved
`EVALUATED_MESH_DEGENERATE` is an evaluation error, not established evidence of a
source-design defect. This pass does not fill open boundaries or repair arbitrary
degenerate triangle meshes.

See `examples/fixed_assembly/README.md` and `t_bracket.py` in the repository for the
end-to-end opt-in config, validation and outputs. Dimensions and fit are geometric
demonstrations until calibrated on a real printer/material. See also the four-part,
four-interface `two_shelf_frame.py` fixture. Cycles are consistency checked, not
solved by moving existing parts. Articulation and automatic print-part segmentation
remain unsupported.
