# Optional fixed manufacturing assembly v1 (explicitly enabled)

Generate one complete program, not one LLM call per part. No articulation or
physical checker is enabled unless explicitly selected in the request. Preserve the requested visual appearance and exact
final size. Geometric mating is not proof of retention, strength or printability.

Planner: return FixedAssemblyPlan. Partition EVERY named semantic component into
explicit print_parts (id, components); a print part can contain several semantic
components. Provide root_part and ordered connections (id, tab_part, slot_part,
tab_port, slot_port, parameter_name, interface_type='tab_slot', insertion_direction,
fit_intent). Receiver is already placed, tab is a new child. No cycles, multiple
placement mates or unknown IDs. Copy mm_per_unit and final_size_mm from the frozen
request, not from a guessed bounding box. Keep natural-language relations.
Use the request's fixed fit_offset_mm; repairs must not change it to pass geometry.
Print-part IDs MUST NOT be `scene` or `exploded`: these names are reserved for
whole-assembly export files. Choose names such as `top_plate` or `leg_left`.
When fixed_assembly.require_multiple_parts is true, the actual result must retain
at least two print parts and one connector. Counts and grouping may change; do not
collapse everything into one piece or remove all interfaces to finish this task.
This is a task requirement, not an extra geometric check or a general single-part ban.

Coder API (all imported by `from adsl.core import *`):

- `FixedAssembly(root_id=..., mm_per_unit=...)`
- `assembly.add_part(id, body_asset, components=(...))`: selects exactly that local
  body and semantic subtree, copies it, and never automatically selects leaves.
- `InterfaceFrame(origin=(x,y,z), x_axis=(1,0,0), insert_axis=(0,0,1))`:
  local stop-plane centre; coordinates in scene units; orthonormal unit axes.
  +X is tab width, +Y thickness, +Z points FROM the tab body INTO the receiver.
  BOTH frames use this same direction (not opposing outward normals).
- `TabSlot(width_mm, thickness_mm, insertion_mm, slot_depth_mm, fit_offset_mm,
  root_overlap_mm=0.5, opening_extension_mm=0.5, lead_in_mm=0.0)`:
  tab runs from -root_overlap to +insertion along its frame Z. The receiver cutter
  runs from -opening_extension to +slot_depth. fit_offset is a SINGLE-SIDED slot
  allowance on width and thickness, applied once by the helper; positive clearance,
  negative nominal interference. Slot depth is at least insertion. Lead-in is a
  45-degree tip chamfer and must be less than half either cross dimension and less
  than insertion. Explicit overlaps are real dimensions, not numeric tolerances.
- `assembly.connect(id, tab_part=..., slot_part=..., tab_frame=..., slot_frame=...,
  parameters=shared_parameters, parameter_name=..., tab_port=..., slot_port=...)`:
  generates BOTH sides with union/difference and places the tab child by frames.
  Build sufficient material at each mount; DO NOT draw the tab or cut the slot
  yourself. Reuse the same parameter value for every use of its shared name.
- Finish with global `assembly` and `scene = assembly.scene()`; do not add material
  or transforms to scene afterwards. Root's placement may use root_frame. Local
  print-piece bodies are built before assembly; no manually guessed placement of
  the second part is needed. No `fixed()` joint is needed.
- `assembly.set_print_orientation(part_id, rotation_deg=(x,y,z))`: optional PRINT
  rotation in degrees, column-vector Rz @ Ry @ Rx; only use when the request allows
  print-orientation edits. STL is rotated then grounded. Local geometry, assembly
  pose and connector frames do not change. Overhang optimization permits only this
  direction change, not deleting material or changing measurement settings.

Read the connection list in both directions before designing bodies: a receiver
must reserve material for every incoming slot, while tab bodies reserve a shoulder
and embedded root. Derive incoming requirements from that list, not another plan.
The exporter writes STL coordinates in mm, whole-assembly and exploded GLBs, and
an assembly_manifest. The exploded view is not the assembled target shape.

During repair, the initial print_parts and connections are a reference proposal,
not immutable requirements. Keep the existing grouping by default; only revise it
when feedback or source evidence justifies a minimal change. A semantic class or
container need not be a continuous print piece: select its independent children
as separate print instances when needed, preserving their semantic hierarchy and
required visible components. Repeated pieces may reuse a class, but each separate
print piece needs its own instance and connection. Explain the changed locations
and reasons; do not rewrite the initial plan.json. Change relevant bodies AND
assembly/helpers in the SAME isolated candidate if necessary. Preserve the root,
frozen scale, fit allowance, requested dimensions and budget. Do not delete required
parts, cancel connection requirements or change checker/config files. Image/Code Critic
still judge appearance; their approval cannot override failed interface geometry
when validation_mode is geometry. When the frozen request sets validation_mode to
visual_only, the exporter's geometry gates are NOT RUN. Separately selected
assembly physics tools still execute; their results cannot be overridden by visual
approval. Without explicitly selected tools they remain NOT_EXECUTED. Generate the same
paired interfaces and positioned assembly, then use available images and source
review to repair visible issues. Export consistency remains required. A complete
visual/code approval is not an interface-geometry or manufacturing approval;
do not infer geometric failure from NOT_EVALUATED. Missing display geometry must
remain explicit and cannot pass complete appearance review.
If no appropriate repair exists, explicitly return
{"edit_action":"NO_CHANGE","reason":"..."}; do not make a dummy patch.
