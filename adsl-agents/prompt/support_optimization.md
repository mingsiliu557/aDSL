Support optimization uses ray-estimated support volume S (mm^3) and actual
print-part count N. For adoption, compare S_hat=S/D^3, where D is the diagonal
of each version's assembled bounding box in millimetres. NSV=S/V is descriptive
only: increasing V inside an unchanged bounding box cannot earn adoption.
This is a centroid-ray proxy, not slicer material consumption. A fixed baseline
supplies tau = layer_height_mm * total_risky_projected_area_mm2, one baseline
layer of projected risky surface; tau_hat=tau/D_baseline^3. This is an engineering
tolerance, not an error bound. A split or same-count edit requires
delta_S_hat < -tau_hat; a merge allows delta_S_hat <= tau_hat.
Keep the original comparison baseline throughout candidate and interface repairs.
Independent necessary repairs do not require support gain. A candidate still needs
all original appearance, topology, standing, geometry and publication conditions.
Keep task nominal dimensions, units, required appearance/features, semantic material
ownership, root ID/frame and functional interfaces. Never globally shrink the object
or inflate its volume to improve a ratio. Diagonal normalization removes uniform-scale
advantage; it does not prove arbitrary dimensional changes meet the task requirements.
Read assigned_source, then only necessary evidence pointers. State a bounded hypothesis
or NO_PROPOSAL/NO_CHANGE; do not invent measured gains. Support regions and transforms
are in printed millimetres; invert the transform for local source placement.
With a partition objective, existing 24-orientation selection supplies the measured
and published pose; it is not an NSV-minimizing search. Do not spend a source edit
copying these automatically published rotations. Regroup only when explicitly
permitted, using action regroup_print_parts with grouping_change
(operation, source_part_ids, target_print_parts, connection_changes).
Use separately named semantic instances for local regrouping; an old four_legs ID
cannot be split into invented IDs. Build from original pre-connector bodies/children,
not historical STL meshes. Each target print part needs an independent body.
On merge, remove internal connectors. On split, use local Boolean cuts to assign
overlapping child material to exactly one piece; do not export the original overlap
as separate print parts. Keep mounting shoulders and required unnamed body details.
Recreate every cross-part interface using connect() and update every affected paired
frame; components declares semantic ownership and does not extract geometry.
Engineering: combine all current tools in ONE coordinated proposal. Explain the
specific semantic reassignment, expected print-pose benefit, affected connections
and material ownership. An unmeasured candidate is a hypothesis, not a measured gain.
With NO_PROPOSAL, state a source/semantic-granularity/interface-space limitation or
specific rejected local hypothesis after reading source. All selected checks must
run again; a support improvement cannot excuse required physical or visual failures.
Without a partition objective and with orientation editing disabled, shape-enabled
mode A requires one print part with identity print rotation from the initial output
through every repair; normal bed translation may change. Identity rotation assumes
the generated aDSL use pose is upright; NSV_up uses this same pose convention.
