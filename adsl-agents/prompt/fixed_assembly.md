# Optional fixed manufacturing assembly v1 (explicitly enabled)

Generate one complete program, not one LLM call per part. No articulation or
physical checker is enabled unless explicitly selected in the request. Preserve the requested visual appearance and exact
final size. Geometric mating is not proof of retention, strength or printability.

Planner: return FixedAssemblyPlan. Partition EVERY named semantic component into
explicit print_parts (id, components); a print part can contain several semantic
components. Provide root_part and ordered connections (id, tab_part, slot_part,
tab_port, slot_port, parameter_name, interface_type='tab_slot', insertion_direction,
fit_intent). Each physical interface must have its own connection ID and endpoint
ports. Process connections in order, starting from root_part. At least one endpoint
must already be placed. If exactly one endpoint is placed, connect() computes
the other endpoint's pose, whether that endpoint is the tab or the slot.
If both endpoints are placed, connect() checks that their world interface
frames agree, then creates both mating geometries without moving either part.
The same pair of parts may have multiple interfaces using distinct ports.
Plan all required interfaces, including those beyond the edges used to place
the parts. Build mounting material for every declared tab and slot.
Use meaningful port names, unique per (part, tab/slot role, port name); different
names do not prove different geometric locations. No unknown IDs or self connections.
Copy mm_per_unit and final_size_mm from the frozen request, not from a guessed
bounding box. In relations, explain initial print-group choices (bed faces, cantilevers, continuous appearance); do not invent voxel scores. Keep natural-language relations. Connection evaluation order is not
a physical assembly insertion sequence.
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
  generates BOTH sides with union/difference. Tab/slot identify geometric roles,
  not parent/child or placement order. With only the slot placed, compute
  `T_tab = T_slot @ F_slot @ inverse(F_tab)`; with only the tab placed, compute
  `T_slot = T_tab @ F_tab @ inverse(F_slot)`. With both placed, require
  `T_tab @ F_tab == T_slot @ F_slot` and keep both transforms unchanged.
  With neither placed, the call fails. Build sufficient material at each mount;
  DO NOT draw the tab or cut the slot
  yourself. Reuse the same parameter value for every use of its shared name.
- Finish with global `assembly` and `scene = assembly.scene()`; do not add material
  or transforms to scene afterwards. Root's placement may use root_frame. Local
  print-piece bodies are built before assembly; no manually guessed placement of
  the second part is needed. No `fixed()` joint is needed.
- `assembly.set_print_orientation(part_id, rotation_deg=(x,y,z))`: optional PRINT
  rotation in degrees, column-vector Rz @ Ry @ Rx; only use when the request allows
  print-orientation edits. STL is rotated then grounded. Local geometry, assembly
  pose and connector frames do not change. Legacy overhang optimization permits only
  this direction change. With partition_objective and print_partition_editable enabled,
  a structured regroup_print_parts proposal may also change grouping and affected
  interfaces. Never delete required body material or change measurement settings.

Read the connection list in both directions before designing bodies: a receiver
must reserve material for every incoming slot, while tab bodies reserve a shoulder
and embedded root for every tab. Include all adjacent connections, not just the
connection that first places a part. Derive incoming requirements from that list,
not another plan.
The exporter writes STL coordinates in mm, whole-assembly and exploded GLBs, and
an assembly_manifest. The exploded view is not the assembled target shape.

During repair, the initial print_parts and connections are a reference proposal,
not immutable requirements. Keep the existing grouping by default; only revise it
when feedback or source evidence justifies a minimal change. A semantic class or
container need not be a continuous print piece: select its independent children
as separate print instances when needed, preserving their semantic hierarchy and
required visible components. Repeated pieces may reuse a class, but each separate
print piece needs its own instance and connection. Explain the changed locations
and reasons; do not rewrite the initial plan.json. Repairs may add supplemental
connections. MATE_FRAME_MISMATCH identifies the interface, endpoints, translation
error in mm and rotation error in degrees. Correct the relevant frames, dimensions
or connection arrangement and re-execute the entire candidate; connect() never
moves already placed parts to resolve a contradiction. The shared matrix tolerance
is 1e-8 in scene units (rtol=0), not a fit clearance or a millimetre threshold.
Change relevant bodies AND
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

When partition_objective is enabled, the checker searches 24 print rotations and
publishes recommended print_layout/STL assets. Do not spend an extra edit writing
those rotations into source. Follow an authorized grouping_change using original
pre-connector bodies/children. On merge, remove internal connectors. On split,
assign overlapping child material to exactly one piece with local Boolean cuts,
retain mounting shoulders and other unnamed body details, then generate every
cross-part connector using connect(). Keep root ID/frame, full body union, frozen
scale/fit/dimensions and all visible requirements. Explain expected G/N tradeoff;
measured same-reference Dapper score decides benefit only after required checks.
Initial plan.json remains historical; selected manifest is the actual grouping.

Appearance API limits for generated source and repair:

The current public appearance controls are color and alpha. The GLB exporter
creates a Principled material with default shader settings; the source API has no
roughness/specular setter. Do not invent such methods or replace required geometry
with decorative geometry to simulate gloss. A missing unsupported material call
alone does not establish a visible finish failure. Judge the rendered evidence;
if an essential finish is visibly unmet and cannot be expressed, report that
limitation honestly rather than claiming it was repaired or automatically approved.
Optional wood grain must not become a mandatory feature.
