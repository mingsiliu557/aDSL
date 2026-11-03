# Single and split print modes

`fixed_assembly.print_mode` defaults to `split`. Missing, null, and explicit
`split` retain the previous frozen configuration hash. Run manifests and assembly
results record the resolved mode.

For `single`, set `require_multiple_parts=false`, disable
`repair_policy.print_partition_editable`, and omit `physics.overhang.partition_objective`.
The Planner must put every semantic component in exactly one print part named
`plan.root_part`, with no connections. Only a failed single-plan check gets one
Planner retry; parsing, schema and dimension errors retain their existing behavior.
Saved plans are checked without retry.

NSV single mode requires `physics.overhang.shape_editable=true`.
With `repair_policy.print_orientation_editable=true`, the checker searches the
existing 24 rotations for minimum support volume. It does not use a partition
reference. With that permission false, printing stays upright. Area single mode
supports only fixed upright printing.

The generated source always declares an identity printing rotation. The automatic
recommendation is separate and may be nonidentity. Invalid single drafts enter
implementation repair; geometry edits retain the existing physical checks and
support acceptance rules. Regrouping and manual orientation actions are rejected.
Only the retained version's PASS layout, bound source, manifest and STL may be
published. Unavailable layouts publish no recommended STL; identity/hash conflicts
raise errors.

The old split and mode-A paths remain available. This feature adds no new checker
thresholds or benchmark protocol.
