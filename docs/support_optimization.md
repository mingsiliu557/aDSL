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

- More parts: accept the support tradeoff only when `delta_S < -tau`.
- Same part count: require `delta_S < -tau`.
- Fewer parts: allow `delta_S <= tau`.

Equality at `-tau` is not a support improvement. A merge may accept equality at
`+tau`. Valid zero-support/zero-area baselines have tau=0. Ratio improvement from
increasing V is not an adoption criterion. These local rules do not prove global
monotonicity or absence of cycles; existing budgets remain unchanged.

The support decision only tightens the existing feasibility/appearance gates.
Independent necessary repairs do not require support reduction. A repair inside
an optional candidate must eventually pass the original baseline's S/N test.
Backend/config differences or missing results are not silently comparable.

## Modes and evidence

A is NSV + shape-editable + no partition objective + no print-orientation edit
permission. Actual output must be a single same-ID piece with identity print
rotation. Translation for bed placement may change. Rotation uses rtol=0 and
atol=1e-9, a numerical comparison convention. A source failure with no output does
not establish a pose; the first actual output must satisfy A. An actual initial
multi-part/rotated output stops as a mode mismatch.

D uses the existing partition reference and 24-orientation machinery. S is
measured at the pose chosen by the original G strategy, not an NSV-minimizing
search. Authorized local shape changes may refresh the reference; semantic
ownership and root frame must remain intact. The refreshed measurement, reference
and final STL pose must agree before adoption. Shape-only edits keep grouping;
regroup edits retain the existing intent and realization checks.

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
unknown scale protocols must be resolved before interpreting S changes as equal-size
manufacturing gains. No automatic model runs or dataset admission are introduced.

## Interfaces

- `support_volume.support_volume(mesh_mm, *, overhang_threshold_from_horizontal_deg, bed_tolerance_mm)`
- `support_volume.aggregate(rows)`
- `actions.support_postcheck(baseline, candidate, *, tolerance_mm3)`
- `actions.print_pose_postcheck(baseline_parts, candidate_parts)`
- `actions.regroup_precheck(..., support_metric='area', shape_editable=False)`

Existing Action execution wrappers and model output DTOs are unchanged.
