Support optimization uses absolute ray-estimated support volume S (mm^3) and actual
print-part count N. NSV=S/V is descriptive only: increasing V cannot earn adoption.
This is a centroid-ray proxy, not slicer material consumption. A fixed baseline
supplies tau = layer_height_mm * total_risky_projected_area_mm2, one baseline
layer of projected risky surface. It is an engineering tolerance, not an error bound.
A split or same-count edit requires delta_S < -tau; a merge allows delta_S <= tau.
Keep the original comparison baseline throughout candidate and interface repairs.
Independent necessary repairs do not require support gain. A candidate still needs
all original appearance, topology, standing, geometry and publication conditions.
Keep task nominal dimensions, units, required appearance/features, semantic material
ownership, root ID/frame and functional interfaces. Never globally shrink the object
or inflate its volume to improve a ratio. The controller does not prove size retention.
Read assigned_source, then only necessary evidence pointers. State a bounded hypothesis
or NO_PROPOSAL/NO_CHANGE; do not invent measured gains. Support regions and transforms
are in printed millimetres; invert the transform for local source placement.
With a partition objective, existing 24-orientation selection supplies the measured
and published pose; it is not an NSV-minimizing search. Do not spend a source edit
copying these automatically published rotations. Regroup only when explicitly
permitted, using regroup_print_parts plus complete grouping_change. Independent
bodies, disjoint material ownership and every affected connect/frame remain required.
Without a partition objective and with orientation editing disabled, shape-enabled
mode A requires one print part with identity print rotation from the initial output
through every repair; normal bed translation may change.
