# Optional fixed print assembly

Public entrypoints: `FixedAssembly`, `InterfaceFrame`, `TabSlot` from `adsl.core`.
This supplements, and does not change, `Asset.attach_part()` or `Asset.fixed()`.

`FixedAssembly(root_id, mm_per_unit)` uses keyword arguments and selects explicit
print parts via `add_part(part_id, body, components=(...))`. Semantic children stay
inside their selected print piece. Root placement can use `root_frame`.

`connect(interface_id, tab_part=..., slot_part=..., tab_frame=..., slot_frame=...,
parameters=TabSlot(...), parameter_name=..., tab_port=..., slot_port=...)` creates
both mating geometries and solves the new child's transform. Call connections in
receiver-first tree order. `scene = assembly.scene()` is the final static Asset.

InterfaceFrame's origin is the stop plane, +X is tab width, +Z insertion into the
receiver, on both sides. `M_child = M_receiver @ F_slot @ inverse(F_tab)`; these
transforms are applied once to copies of local part geometry, not twice to already
baked world coordinates. Exploded display copies do not modify the assembly.

TabSlot takes width_mm, thickness_mm, insertion_mm, slot_depth_mm, fit_offset_mm,
root_overlap_mm, opening_extension_mm and lead_in_mm. Slot width/thickness are the
nominal tab size plus twice the SINGLE-SIDED fit offset. Depth clearance is separate.
Lead-in is a 45° tip chamfer, not a global numeric tolerance. Explicit mounting
overlap must be real material; the geometry evaluator checks it.

See `examples/fixed_assembly/README.md` and `t_bracket.py` in the repository for the
end-to-end opt-in config, validation and outputs. Dimensions and fit are geometric
demonstrations until calibrated on a real printer/material. Multiple locating mates,
cycles, articulated parts and automatic print-part segmentation are unsupported.
