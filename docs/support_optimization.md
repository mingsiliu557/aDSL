# Opt-in support-volume optimization

Implementation status: code and tests written; tests and experiments have not been run.

The fixed-assembly workflow reads two optional `physics.overhang` keys:
`support_metric` (`area`, default, or `nsv`) and `shape_editable` (strict boolean,
default false). Missing defaults are not inserted into frozen request data.
The legacy area path retains its original objective and prompt resources.

NSV evaluates a centroid/downward-ray support-volume proxy on the final individual
print solids, including connectors. Per-face contribution is projected risky area
multiplied by distance to the nearest lower hit, or to the bed for a genuine miss.
The input mesh is copied and bed-normalized; it is not repaired or rescaled.
Embree is preferred when importable; otherwise the trimesh triangle intersector
requires rtree. Known missing/failed backends produce unavailable measurements,
never zero support. No dependency is installed by this change.

All declared pieces must be measured. The aggregate is sum(S)/sum(V), not the mean
of individual ratios. The ray result is not slicer support-material consumption;
centroid sampling and triangulation can affect the estimate.

## Adoption

For each new optional candidate, freeze its baseline and define
`tau = layer_height_mm * baseline.total_risky_projected_area_mm2` (mm³).
This is one layer over the sum of risky face projections, not a unique footprint,
printer accuracy bound or statistical uncertainty estimate.

For comparison only, let `D = norm(assembled_size_mm)`, the diagonal of each
version's assembled bounding box. Use `S_hat = S/D**3` and
`tau_hat = tau/D_baseline**3`. This prevents proportional support reduction from
uniform model shrinkage being counted as a shape improvement. No mesh, scale,
manufacturing STL or raw measurement is modified.

- More parts: accept the support tradeoff only when `delta_S_hat < -tau_hat`.
- Same part count: require `delta_S_hat < -tau_hat`.
- Fewer parts: allow `delta_S_hat <= tau_hat`.

Equality at `-tau_hat` is not a support improvement. A merge may accept equality at
`+tau_hat`. Valid zero-support/zero-area baselines have tau=0. Ratio improvement from
increasing V within unchanged bounds is not an adoption criterion. These local rules do not prove global
monotonicity or absence of cycles; existing budgets remain unchanged.

The support decision only tightens the existing feasibility/appearance gates.
Independent necessary repairs do not require support reduction. A repair inside
an optional candidate must eventually pass the original baseline's normalized-support/count test.
Backend/config differences or missing results are not silently comparable.
Missing, nonfinite or zero-diagonal sizes also reject comparison. Geometry-mode
manifests provide assembled_size_mm at the top level; visual-only output provides
it in the successful canonical_scene_consistency record. Both are existing fields.

The controller records baseline/candidate diagonals, their dimensionless normalized
support values, delta_normalized_support and normalized_tolerance. Existing
`delta_support_mm3` and `tolerance_mm3` retain actual mm³; they must not be read as
the normalized decision values. Raw S, V and NSV continue to describe the output.
A resumed candidate's baseline evidence retains its original assembled_size_mm.
The low-level support_postcheck Action retains its interface; the controller applies
its same inequalities to normalized local copies and labels the returned data.

## Modes and evidence

A is NSV + shape-editable + no partition objective + no print-orientation edit
permission. Actual output must be a single same-ID piece with identity print
rotation. Translation for bed placement may change. Rotation uses rtol=0 and
atol=1e-9, a numerical comparison convention. A source failure with no output does
not establish a pose; the first actual output must satisfy A. An actual initial
multi-part/rotated output stops as a mode mismatch. Identity rotation assumes the
generated aDSL use pose is upright; NSV_up must use the same upright convention.

D uses the existing partition reference and 24-orientation machinery. S is
measured at the pose chosen by the original G strategy, not an NSV-minimizing
search. Authorized local shape changes may refresh the reference; semantic
ownership and root frame must remain intact. The refreshed measurement, reference
and final STL pose must agree before adoption. Shape-only edits keep grouping;
regroup edits retain the existing intent and realization checks. NSV prompts retain
the implementation guidance: name semantic instances separately; provide the
complete grouping_change; build independent bodies from original bodies/children;
remove internal connectors on merge; explicitly cut overlapping material into
unique ownership on split; preserve required mounting shoulders; rebuild each
cross-part connect/frame; combine current checks in one Engineering proposal.

`support_evidence` is an explicit bounded model view. It includes source identity,
measurement hash, S/V/NSV/N/tau, printed transforms, up to three regions per item,
file references, interface count and existing physical statuses. Internal Dapper
results remain in raw checker records but are not a competing Agent objective.
The view is included in the existing 32 KiB feedback budget.

Checkpoints preserve working baseline, retained, qualified, partition reference
(including None), and baseline feedback. Rejection, unfinished budget exhaustion,
NO_CHANGE, unavailable comparison or interruption restores those bindings together.
Candidate evidence remains historical; final publication uses the retained version.

Keep nominal task dimensions and units. There is no new automatic scale checker;
diagonal normalization removes uniform-scale advantage but does not prove arbitrary
dimension changes satisfy the task. Unknown scale protocols must be resolved before
interpreting raw S changes as equal-size manufacturing gains. No automatic model runs or dataset admission are introduced.

## Interfaces

- `support_volume.support_volume(mesh_mm, *, overhang_threshold_from_horizontal_deg, bed_tolerance_mm)`
- `support_volume.aggregate(rows)`
- `actions.support_postcheck(baseline, candidate, *, tolerance_mm3)`
- `actions.print_pose_postcheck(baseline_parts, candidate_parts)`
- `actions.regroup_precheck(..., support_metric='area', shape_editable=False)`

Existing Action execution wrappers and model output DTOs are unchanged.
