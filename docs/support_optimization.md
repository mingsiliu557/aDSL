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

- More parts: require both `delta_S < -tau` and `delta_S_hat < -tau_hat`.
- Same part count: require both `delta_S < -tau` and `delta_S_hat < -tau_hat`.
- Fewer parts: require both `delta_S <= tau` and `delta_S_hat <= tau_hat`.

Both comparisons must pass. Enlarging the bounding box cannot override an absolute
support rejection. Equality at either negative threshold is not a support improvement.
A merge may accept equality at both positive thresholds.
Valid zero-support/zero-area baselines have tau=0. Ratio improvement from
increasing V within unchanged bounds is not an adoption criterion. These local rules do not prove global
monotonicity or absence of cycles; existing budgets remain unchanged.

The support decision only tightens the existing feasibility/appearance gates.
Independent necessary repairs do not require support reduction. A repair inside
an optional candidate must eventually pass both the original baseline's absolute-support/count
and normalized-support/count tests.
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
its same inequalities to raw evidence and normalized local copies. The result records
`absolute_decision` and `normalized_decision` separately (conclusion and rule); a check
not reached remains null. When both checks are available, an absolute rejection keeps
its rule as the final rule, otherwise the normalized rule applies. Missing comparison
evidence still rejects as support_unavailable and preserves any completed absolute decision.

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
The view is included in the 96 KiB feedback budget.

The canonical current-version summary is at top-level `support_evidence`.
In NSV requests, an identical nested copy is removed only when the complete strict
JSON content and all three source bindings (`source_sha256`, `source_version`,
`measurement_config_sha256`) match. If the top-level key is absent, the first
fully bound copy in the known feedback containers is promoted. Existing invalid
top-level values are not overwritten. Missing bindings or conflicting content
remain separate; `comparison_baseline` and control flags are not moved or deduplicated.
Raw controller feedback, measurements and the public payload interface are unchanged.

Same-source conflicting records retain an adjacent `support_evidence_provenance`
marker (strict input-content and current-view SHA256s). Removing unknown fields or
shortening explanations/regions must not make different original records eligible
for deduplication on a later projection. The input fingerprint is reused only when
its view hash matches; changed views are rebound before projection. Different earlier
previews of one raw record also retain their input distinction. View hashes track
the existing budget reductions, and both marker hashes count toward the same 96 KiB
budget. These are comparison markers, not source authorization or measurement proof.
Ordinary identical copies and single evidence records do not acquire markers.

`agent_feedback._feedback_view()` defines the original feedback-only byte scope;
`feedback_size_report()` reports its exact compact UTF-8 JSON size and at most 15
top-level/nested field sizes each. These exclude requirement/source/image inputs.
Parent and child sizes overlap and must not be summed. Oversize errors retain
`AGENT_FEEDBACK_TOO_LARGE`, `actual_bytes` and `limit_bytes`, with bounded field-size
lists; successful Agent payloads do not acquire size-report fields. The limit is
98,304 bytes; the earlier 32,768-byte limit remains the scope of historical replay
reports. All existing preview/reduction stages remain in their old order. The
larger limit may stop reduction earlier; it does not bypass source binding,
deduplication, strict JSON checks or the final feedback-only size check. It is not
the model's token limit or the byte size of the whole request.

For an explicitly described saved input, `scripts/feedback_size_report.py --input
<description.json> --output <replay-dir>` runs only the adapter on copied evidence.
`--adapter-source <statistics-only-snapshot.py>` selects the baseline implementation.
Each implementation's outputs are archived by content hash; both passes reset and
reuse the same isolated workspace path. A replay-only resolver maps original
absolute references to the copied files without rewriting their bytes/hashes.
Missing/outside-workspace evidence is not invented. Reports distinguish captured,
reconstructed and already-projected inputs, local deduplication savings, final
old/new savings, and reference availability. This is not a model or physical run.

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

## Assembly source-candidate views (v5, validation pending)

The fixed-assembly Engineering, Coder and Code Critic input adapter keeps each
finding and its judgement. In both area and NSV, a candidate may inline only
`feature_id`, `source_ids`, `method`, `relation_role`, `ambiguous`, and
`overlap_score`. Candidate order and the existing six-candidate preview are
preserved. `preview_info.source_candidates.full_ref` addresses the complete
original list even when `omitted_count` is zero (fields, rather than entries,
were omitted). Use the existing bounded `read_file` JSON-pointer interface when
locations or evidence are needed. Source candidates remain hints, not causes.

Compaction requires a readable complete list, matching finding and source
binding, verified file hash, and matching displayed candidates. Missing, stale,
conflicting or summary-only evidence keeps the old view. The private
`_COMPACT_ASSEMBLY_CANDIDATES` switch disables only this projection and its local
preview preservation at the same 96 KiB budget. No controller or permission
rule is changed. Any further budget-driven cropping keeps the original full
reference, without expanding previous previews.

NSV initial Planner alone receives `prompt/support_grouping.md`: semantic parts
can share a print part, and small attached features default to their parent
unless a printing/assembly benefit or explicit task requirement justifies a
feasible interface. Reasons in `relations` are hypotheses, not measured gains.
Area Planner does not receive that grouping addition. The four assembly code
roles receive a shared candidate-reading note in both modes; original API,
modeling and support-acceptance instructions remain in place.

The existing `scripts/feedback_size_report.py` now prepares equal-96-KiB
comparisons (including a legacy-candidates option), records candidate field
sizes separately from total feedback, verifies every candidate full reference,
and records exact retained-field and other-finding-field comparisons at the
compaction boundary. Those sizes overlap and must not be summed. Post-projection
archives cannot establish full raw-input savings. v5 tests and replay additions
are written but have not been executed in this task.
