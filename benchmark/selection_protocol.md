# ABO / Toys4K selection v1

This independent tool selects reference-image tasks by visible structural risks.
GT closure, connectivity, volume or physical checker results do not gate task
applicability or recommendations. No production Agent workflow, Planner/Coder
generation, FEA or generated-model benchmark is invoked.

Baseline: `master@2a11b196fb7c09a72747fd8f6977b433f0428bf9`.
Scripts and imported measurement implementations are hashed in cache records.
Paths are configurable in `configs/selection_v1.json`; large data stays outside Git.

## Sources and sampling

- [ABO](https://amazon-berkeley-objects.s3.amazonaws.com/index.html): official
  `3dmodels/metadata/3dmodels.csv.gz`, joined to official listings metadata by
  `3dmodel_id`. Individual GLBs and indexed catalog thumbnails only. Attribution:
  Amazon.com and Guillaumin et al.; CC BY 4.0. Snapshot hashes are in collection.json.
- [Toys4K](https://github.com/rehg-lab/lowshot-shapebias/tree/main/toys4k): official
  README requires an archive request. The configured existing Toys4k.csv supplies
  identifiers, checksums and categories. Assets come from the non-official mirror
  `lihong-cs/3dgeneration_baseline@365b279da42ee1f6aa612319f0b95a616c05f70b`,
  `toys4k_blend_files.zip` (31,576,633,609 bytes; SHA256
  `e326255aa624a7a582ee052aed737e6d91d484d43a504e0d8f4bd51354b9ca00`).
  Download `.part` resumes; archive size and full hash must match before publication.
  Extract only selected instance directories and explicitly listed resources. Match
  each `.blend` against the existing CSV checksum and retain instance paths. Record
  CSV provenance as a local cached CSV; the mirror is a download channel, not a
  replacement dataset identity or proof of redistribution rights.
- [SNAP3D selection](https://arxiv.org/html/2609.13146v1#A1) informs screening for
  valid observable references. Its pinned-support stability protocol is not used.

Seed 20261006. ABO quotas: 7 lamps, 6 chairs/stools, 6 tables, 6 cabinets/shelves.
Exact metadata family/model prefixes are collapsed before seeded sampling.
Inventory shortages are redistributed in fixed category order and recorded.
Past SF ABO identifiers and the repeatedly debugged elephant are excluded.
Classification uses specific product type first, then English titles and leaf
catalog names; ancestor "Lighting" categories are ignored. Existing IDs, source
hashes and manual decisions survive `--reclassify`. Conflicts are pending review.
Add five ABO models first, at most ten additional and sixty total records, keeping
seed/family rules and excluded debug IDs. Toys4K picks 25 identifiers by seeded category round-robin. These are development
candidates, not unseen final test assets; their families will need exclusion later.

## Geometry and preview

Original bytes remain unchanged. Blender applies hierarchy transforms. Its glTF
import already converts Y-up to Z-up; the adapter does not rotate twice. Geometry
is uniformly normalized to a 150 mm longest edge, centered XY and grounded Z.
Per-node original transforms, the normalization matrix, and cleanup operations
are recorded. Preview GLB export explicitly converts numeric mm to metres.
Measurement NPZ/STL stays in mm. Material/node counts are never semantic parts.

Only existing exact-zero-area and numerical microcrack methods operate on derived
copies. Exact-coordinate duplicates may be merged without moving vertices; global
winding may be reversed for an otherwise closed consistently oriented volume.
No arbitrary hole filling, thickening, missing-part deletion, or added base.
The existing union_print_mesh combines signed shells within each node, then all valid node volumes are unioned, preventing double-counted overlap and preserving cavities. Cavity walls are not counted as separate material islands.
Disconnected/unmeasurable objects cannot be forced into a standing rigid body.
Readable but non-volume assets retain previews for manual inspection.
These restrictions concern optional GT diagnostics only. A clear recognizable
reference can be recommended with open edges, multiple material islands, missing
physics reports or a valid standing FAIL. Missing/stale source identity or unusable
input images still require review; genuinely missing target geometry is a visual
input issue, not inferred from topology counts.

Eight fixed review views (6 orbit at 15 degrees elevation, top, bottom), each in
native and neutral materials: 512x512 transparent PNG, CPU Cycles, 32 samples,
4 threads, same scene normalization and camera distance across views. Native
view 2 (60 degrees from the front, -30 degrees in the Blender XY plane) supplies the input. Camera matrices/projection are
read back at each actual rendered frame, not from the final camera state.
No text in individual PNGs; white JPEG contactsheets identify cases.

## Tags and measurements

Allowed coarse tags only: complex_surface (curvature/section variation),
standing_sensitive (support risk, not a verdict), grouping_tradeoff (hypothesis
about split/merge), multipart_contact (visually contacting functional parts).
Tags must describe one visible structural risk, not be assigned as a four-tag
bundle. Prioritize small support footprints, thin legs, high bodies, large heads
or upper bodies and visible offsets for standing sensitivity. For printing/grouping,
consider protrusions in several directions, cantilevers and how splitting/merging
may change support demand and part count after trying possible print orientations.
Use-pose overhang alone does not prove a print challenge. Keep a small number of
ordinary controls. Never use aDSL failure or another method's success to select
cases; labels are not measured failures or demonstrated split/merge benefits.

One StepCode gpt-5.6-sol VLM attempt per exact image/text/model/prompt/schema key,
at most 50 cumulative attempts including 25 historical attempts. Serial labels,
cache lookup before budget check, unsuccessful same-key calls are not retried.
Use up to 25 new calls for Toys smoke and source-balanced recommendations, no credential
fallback. Store prompt, image hashes, structured output, usage and failure state.
Unavailable labels remain pending review, not unusable cases. A human may directly
confirm previews without a VLM. Image-based coding-agent reviews bind their actual
input-image hashes and identify the reviewer; they are development recommendations,
not human confirmations. Explicit human decisions take precedence. Automatic
labels are not GT.

At most ten development recommendations per source, with category coverage and
specific risk reasons. Recommend on clear recognizable inputs, an explicit use
scene/pose and clear task requirements. `task_applicability` and its compatible
`metric_eligibility` alias describe generated-model tasks (ELIGIBLE /
INDETERMINATE / NOT_APPLICABLE, each with a reason), not GT material measurability.
Appearance and printing tasks can apply even with invalid GT volumes or no GT
measurements. Natural independent ground-standing use and a clear pose establish
standing task applicability; wall-mounted, hanging and flying-use objects are
standing NOT_APPLICABLE while retaining appearance/printing uses. Ambiguous inputs
or poses remain pending review. Preserve ordinary controls as well as risk coverage.
All recommendations require user confirmation; excluded/confirmed manual choices
are retained, with conflicts recorded rather than silently overwritten.
Manual evidence that a product is wall-mounted or otherwise requires external
support prevents its recommendation for free-standing development evaluation,
even if its diagnostic ground simulation passes. Keep its measurements and
mark standing applicability separately from the physical verdict.
No split/merge candidates are constructed in this screening round. Default commands
collect/import/preview/label/build recommendations without running GT physics.
`preflight_candidates.py --measure` explicitly enables optional GT diagnostics;
`build_review_pack.py --shortlist-only` explicitly prepares a reliable-volume
diagnostic shortlist. Neither list nor a diagnostic verdict gates recommendations.
Existing diagnostics keep their frozen measured request/configuration and checked
input/output hashes; a new risk-label prompt does not convert them to current labels
or require re-simulation. Task applicability and GT diagnostics are counted separately.

Standing: single connected material union as an overall free rigid body, density
1240 kg/m³, gravity -9.81 m/s², existing rigid_flex surface contacts, 5 s, dt .002 s,
friction [.5,.005,.0001], 25 degree tipping threshold. The current implementation
checks sampled peak tilt throughout the observation; settling is diagnostic only.
This measures overall standing, not connection retention. Faults are unknown,
not physical FAIL. Pose uncertainty is recorded for manual confirmation.

Overhang: existing Dapper objective, alpha=.3, r_vox=.1, one frozen reference per
case, h=.1 times shortest reference AABB edge, N=1. Independently search all 24
axis-aligned proper rotations. Report G cells, G*h³ mm³, score, best transform,
and area at that G-selected orientation; area is not independently minimized.
G is an empty-column voxel support proxy, not actual slicer cost or print success.
Thin geometry/coarse voxel limitations remain visible. Unknown quantities are null.
The 150 mm normalization and these parameters document previews and optional GT
diagnostics only. Common cross-method scale, voxel settings and Dapper reference
quantities will be defined separately; this revision does not change the formula.

## Later generated-model evaluation boundary

After user confirmation, freeze tasks before method comparison. Reference meshes,
risk labels and GT diagnostics remain evaluator-side and do not enter generation
prompts. Reuse the existing topology checker for generated mesh, connectivity and
assembly validity; do not infer a GT connection graph from nodes. Whole-output aDSL
and assembly outputs use applicable checks, with interface-only metrics NOT_APPLICABLE
for a baseline with no interfaces. Generation failures, invalid meshes and unavailable
physics remain in each frozen case denominator, rather than silently dropping cases.
This round only documents that protocol boundary; it does not implement new checkers
or run methods.

Import/render per-process budget 300 s; checker budget 900 s. One candidate failure
does not stop another. Preview caches bind source/resources, normalization/pose/render settings and actual
import/cleanup/render code. Measurement caches bind derived material/pose/manifest,
physics, standing applicability and checker dependencies. VLM caches bind ordered
nine PNG hashes, text, effective model parameters, prompt and structured schema.
Classification/download/quota/report changes do not invalidate geometry. Never cache
zero placeholders. `preflight_v2.json` / `reference_measurement_v2.json` preserve old
records. Legacy migration requires proof of identical relevant configuration/code,
record/source/output hashes and historical dependency versions; unknowns rerun only
that stage. Historical VLM images do not match current images and are not migrated.
Copies of old artifacts live under `history/adjustment_20261006`.
Protocol changes invalidate only their affected stages.

## Commands

Use the existing project Python environment. CPU visibility must remain empty.

```sh
python benchmark/scripts/collect_candidates.py --config benchmark/configs/selection_v1.json --download --limit 1
python benchmark/scripts/preflight_candidates.py --config benchmark/configs/selection_v1.json --limit 1
# Run smoke using a separate data_root configuration, not the candidate data root:
python benchmark/scripts/smoke.py --config /absolute/smoke-config.json
python benchmark/scripts/collect_candidates.py --config benchmark/configs/selection_v1.json --download
python benchmark/scripts/collect_candidates.py --config benchmark/configs/selection_v1.json --reclassify --append 5
python benchmark/scripts/collect_candidates.py --config benchmark/configs/selection_v1.json --source Toys4K --case Toys4K_cat_057 --download
python benchmark/scripts/preflight_candidates.py --config benchmark/configs/selection_v1.json --workers 2
# Labels only chosen smoke/recommendation candidates, serial, within the cumulative budget:
python benchmark/scripts/preflight_candidates.py --config benchmark/configs/selection_v1.json --case Toys4K_cat_057 --labels --workers 1
python benchmark/scripts/build_review_pack.py --config benchmark/configs/selection_v1.json --shortlist-only
# Optional GT diagnostics only, if explicitly requested:
# python benchmark/scripts/preflight_candidates.py --config benchmark/configs/selection_v1.json --measure --measure-only --case <shortlist id>
# Default final step, without GT measurements:
python benchmark/scripts/build_review_pack.py --config benchmark/configs/selection_v1.json
```

Start the existing StepCode proxy in a persistent foreground terminal for VLM.
Do not put its token in the configuration, logs, Git or review package. Stop this
screening round at the review ZIP; do not expand to 100 cases or generation runs.
