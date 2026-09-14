# Protected overhang-only local editing (2026-09-14)

## Scope and commands

The current experiment reuses **existing assets**, not random generation. Only
overhang measurement is enabled in the feedback arm; control has no physical
checker. Execution, rendering, Image Critic and source-grounded Code Critic remain.
Production topology/standing/FEA/fTetWild paths are not used by this experiment.

```bash
# Prepare assets only: no model generation, measurement or API requests.
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python experiments/overhang_feedback/run_pilot.py \
  --manifest experiments/overhang_feedback/paired_assets.json \
  --output-root local_experiment/overhang_example --phase prepare

# Explicit edit phase: calibrates baseline, then runs both arms through StepCode.
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python experiments/overhang_feedback/run_pilot.py \
  --manifest experiments/overhang_feedback/paired_assets.json \
  --output-root local_experiment/overhang_example --phase edit \
  --model-config adsl-agents/configs/llm/stepcode-gpt-5.6-sol.yaml

# Offline evaluation only; never changes either arm's selected model.
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python experiments/overhang_feedback/run_pilot.py \
  --manifest experiments/overhang_feedback/paired_assets.json \
  --output-root local_experiment/overhang_example --phase evaluate

# Submit all three phases in tmux. Owns one proxy lifecycle; no monitoring loop.
bash experiments/overhang_feedback/submit_paired.sh
```

The submission uses local CPU Eevee (512x512, 64 samples), the existing StepCode
model profile, and two source-edit attempts per asset/arm. Every initial edit,
debug/visual edit and engineering candidate uses the same persistent
`edit_attempts.jsonl` budget. API calls for planning/review are not source-edit
candidates. Failed candidates consume budget. Resume does not clear the ledger.
Both arms see the same task, initial images, protection list and model/render
configuration. Control uses normal aDSL editing/critic selection and never sees
area measurements, localized overhang evidence, offline report paths or
area-based acceptance. It is **not** reselected by the offline evaluator.

## Measurement and interpretation

### Shared patch-error recovery

The public `apply_patch` tool (both ordinary aDSL and experimental arms) still
requires exactly one literal match. Zero/multiple matches return short
`PATCH_MATCH_COUNT` feedback without writing. After the first mismatch, only
two further patch submissions are allowed in that candidate, including successful
submissions; re-reading does not reset this allowance. Existing model turn/time/
token limits and candidate budgets are unchanged. Additional model calls remain
in the normal usage totals; patch invocations are recorded in the diagnostic ledger.

`patch_diagnostics/*.jsonl` stores call/candidate IDs, exact old/new parameters,
match count, before/after hashes, outcome and correction allowance. Model feedback
contains only a short error and report path. A corrected, effective patch proceeds
to normal execution and acceptance. An unresolved or exhausted patch failure is
`TOOL_ERROR`, even after earlier changes; it is not an ordinary no-op. Filesystem
and access errors still raise. Successful patches use a same-directory temporary
file and atomic replacement, without fuzzy matching. No checker or candidate
acceptance criterion is changed.

Regression: `python -m pytest -q tests/test_patch_recovery.py`.

### Duplicate review images

Only in `overhang_experiment`, Image Critic and Code Critic now share an image
list deduplicated by file SHA256. Explicit 1-based `reference_indices`,
`baseline_indices`, and `candidate_indices` preserve every group's view mapping;
different candidate images are never dropped. The mapping is saved as
`image_input_mapping.json` and included in the version's review record. Duplicate
images do not prove geometric protection; existing acceptance remains required.
Ordinary aDSL requests keep their original image order and counts.

The saved SF03 initial assets yield 20 → 8 images, with serialized image input
15,734,567 → 6,296,087 bytes (about 60% less, excluding review text). This was a
local serialization check, not a new API run or evidence that upstream latency
is fixed. Regression command: `python -m pytest -q tests/test_overhang_review_images.py`.

### Console-entry import fix

The installed `adsl-run` entry point does not install the repository's
`experiments` namespace. Protection analysis now falls back to loading its
existing analyzer by its repository-relative file path, without changing global
`PYTHONPATH` or installing extra packages. Missing analyzer dependencies remain
explicit errors/unconfirmed protection, never automatic approval.
`tests/test_overhang_console_import.py` exercises this in a fresh isolated Python
process, where `experiments` cannot be imported normally. The SF03 smoke recovery
also rechecked the already-generated control candidate in this environment:
protection PASS, saved appearance PASS, no additional model call. Its first edit
remains charged; the failure and recovery evidence are kept separately.

### Isolated edits (fix based on `899408e`)

Only enabled `overhang_experiment` requests use this routing. Initial edits,
Image/Code Critic gate patches, Engineering Critic proposals and resume all use
the existing candidate execution/review/acceptance path. An appearance repair
can be reasonable yet rejected for worsening area; its source, assets, reviews
and rejection reason are retained. Image Critic and source-grounded visual
misjudgment correction remain enabled.

`overhang_versions.json` binds `original`, `retained` and each candidate to source,
GLB/URDF/mesh/render hashes, checker results and review records. Original is a
fallback, not an automatic appearance/protection pass. Feedback candidates must
pass `assess_candidate(overhang_optimization=True)`; control candidates use only
appearance/code review plus protection and never online area selection.

Every edit reserves one ID in the existing `edit_attempts.jsonl` **before** the
model call. The lower-level repair does not reserve again. No-op, failed code,
tool error and rejected candidates consume that attempt. A resumed unfinished
reservation is recorded as `INTERRUPTED`, not replayed or charged again. Shared
candidate/time budgets remain in force.

The experimental coder may explicitly return
`{"edit_action":"NO_CHANGE","reason":"..."}`. This is distinguished from
`NO_PATCH_UNEXPLAINED`, `NO_EFFECT` (successful tool, unchanged hash), and
`TOOL_ERROR`. None overwrites retained. Normal aDSL still requires its usual
patch tool event. Completed runs can contain unsuccessful attempts; inspect
their recorded statuses rather than interpreting completion as repair success.

All exits publish retained source **and its matching** generated files,
`checker_results.json` and `appearance_protection.json`; both result files carry
the selected version ID and record hash. Publication verifies copied file hashes
and reuses the version's measurement without invoking a slicer. Unmeasured assets
remain `INDETERMINATE / NOT_EVALUATED`. Measurement completion never means all
physical checks passed.

Mock-only regression command (does not submit experiments):

```bash
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q \
  tests/test_overhang_candidate_isolation.py tests/test_overhang_local_edit.py \
  tests/test_checker_fault_isolation.py tests/test_refinement_budget.py
```

The SF03 fixture uses 6119.80 → 6645.29 mm² solely to reproduce rejection of a
regressing gate candidate. It does **not** rerun or revalidate the real SF03
experiment. Real-tool smoke tests stay disabled; no API/Blender/Gmsh/batch work
is part of this fix.

- `PASS` in the adapter means **measurement completed only**, not absence of
  overhang, no support requirement or printing success. Candidate conclusion is
  separate: improved / unchanged / worsened / unevaluated.
- No 1% or other percentage improvement gate remains. Compare total geometric
  overhang area to the retained best, not nominal support-contact area.
- Freeze initial source-to-mm conversion, authored Z-up orientation, min-Z bed
  placement rule, angle, layer profile/hash, slicer version and export/Blender
  method. Candidate AABB does not determine its scale. The initial print-space
  extent remains constrained within 0.01 mm for both arms.
- Union **detector copies** of closed input shells with Blender EXACT. Do not
  modify source assets, use OCC, repair holes or fallback to concatenated
  overlapping surfaces. Open/invalid output or timeout is unverified. Such an
  asset may be ineligible without saying its geometry is unprintable.
- Union budget 300 seconds; slicing 900; feedback checker process 1200. The
  Boolean worker inherits the checker process group, so the outer checker
  timeout also terminates it. No alternative solver is tried.
- Absolute uncertainty combines a conservative float32-coordinate perturbation
  estimate, full area of classification-ambiguous triangles (angle/bed), and
  two baseline measurements' observed repeatability. Reduction must exceed the
  combined bounds of both compared measurements. This is a numerical assumption,
  not a certified bound on Blender geometric approximation or real printing.
- Bridge angle classification and slicer-generated support are reported
  separately. Support contact increase despite lower geometric area is an
  explicit tradeoff, never relabeled as comprehensive improvement.
- Failed/missing measurements never become zero and never qualify for selection.

## Protection and evidence

`paired_assets.json` manually specifies required visible features, local class
scope and protected world-space surfaces. Scope checks complement, not replace,
surface comparison and before/after image review. Parent/transform effects on
protected triangles cannot be excused by Code Critic. Exact triangle matching is
deliberately conservative: retessellation or ambiguous correspondence can reject
a visually equivalent candidate. It is not general shape equivalence.

O01–O04 have legacy whole-object collision meshes. Their explicitly selected
upper surfaces can be compared, but fine semantic ownership and precise handle
opening dimensions are **not confirmed**. The visual feature checklist is still
mandatory. SF03 uses named protected part surfaces. SF05 instead uses fixed
original source-index regions after verifying the index/source hash: whole
triangles intersecting those regions are protected, including nearby surfaces
conservatively. Selection covers float32 rounding; triangle equality itself is
not relaxed. No unspecified interface
or dimension is claimed to be certified. Failure to match a mandatory configured
surface prevents acceptance.

Engineering Critic receives current total area/contact metrics, at most three
largest geometric regions, existing source localization (or uncertainty), the
protection list, brief prior outcomes and remaining budget. Coder receives the
bounded proposal and relevant short evidence. Face lists/raw logs remain in
report files, accessible only through bounded reads. Optional optimization
findings are not fake physical failures; the agent may return no proposal.

The six asset versions are O01–O04 and SF03/ours, SF05/ours. O01/O02 share a
dataset object, as do O03/O04: these are not six independent sampled objects.
O05 has no complete initial export and is not silently replaced or regenerated.

## Outputs and limits

Each case keeps `original/`, all candidate workspaces and source snapshots,
current retained source/execution, measurement STL/PLY/G-code and raw reports.
`evaluate_results.json` includes original/final area and delta, both support
contact areas, whether support remains, visual/protection results, edit count,
timing/usage, selection and stopping reason. Unavailable/failed cases remain in
the result file. `batch.log` and tmux both retain output; proxy stops only at the
batch end and only if this batch still owns it.

This tests constrained editing with overhang evidence, not overall physical
feasibility, support-free manufacture, real print success or paper-level rates.
The local-feedback idea is inspired by [AgentsCAD](https://arxiv.org/html/2607.02448v1)
§4.4/§5.2 and [nurb checks](https://github.com/Shpigford/nurb/blob/main/src/nurb/checks.py).
Our budget, comparison and protection policy are project choices, not their
published settings. No orientation search or additional architecture is added.

## Historical pilot below (not the current selection policy)

The remaining description records the earlier generation/support-contact pilot.
Its percentage gate is retired; legacy `repair/all` commands now give an explicit
migration error instead of silently executing the old policy.

The 2026-09-14 bugfix uses explicit Blender bmesh EAR_CLIP triangulation of
Boolean n-gons, not the loop-triangle cache. No vertices or small faces are
deleted. Connected-component extraction explicitly disables trimesh's implicit
hole repair. Failed preparation is propagated as NOT_READY; final evaluation
does not retry failed baseline calibration. `submit_paired.sh TAG --case O03
--case SF03` selects only these existing cases.

### Paper-source overhang feedback pilot

This experiment separates slicer support burden from the MuJoCo progressive-tipping
stress test. It uses original CAP3D and MARVEL level-2 captions, with no
manufacturability wording in baseline prompts.

## Frozen sample

The selector predeclares four feature-bearing groups:

- ShapeNet mug with a handle.
- ShapeNet chair with armrests.
- ShapeNet table/desk with a tabletop and drawer.
- ABO floor lamp with a curved arm.

Both CAP3D and MARVEL captions must mention the defining feature. Candidates are
sorted by SHA256 of `dataset:object_id`; rank 0 is selected before generation.
This produces four objects and eight prompts. The 3:1 ShapeNet:ABO object ratio
preserves the paper's 60:20 ratio after excluding Objaverse.

Objaverse is not silently represented. Its local captions are absent. The
immutable MARVEL Objaverse CSV is 3,789,414,322 bytes, and Hugging Face's row
server currently fails because the repository CSV files have incompatible
columns. Downloading that full file would violate the small-subset constraint.

## Paired comparison

1. Generate a one-round CPU baseline from the untouched source caption.
2. Analyze it with the fixed PrusaSlicer profile.
3. Only baselines with nonzero nominal support-contact area enter repair.
4. Copy the exact baseline `source.py` into `adsl-run edit --check-first`.
5. Add the required overhang checker while retaining the normal Image/Code
   critics.
6. Compare baseline and selected repaired rounds.

The primary gate is at least 1% lower nominal support-contact area, no increase
in geometric overhang area, and a print-space AABB delta of at most 0.01 mm.
The fixed slicer profile uses PLA, 0.4 mm nozzle, 0.2 mm layer height, 45 degree
overhang threshold, and everywhere rectilinear support.

This is a conditional mechanism pilot, not a paper-level success-rate estimate.
It does not model thermal warping, surface scarring, or print certification.

## Freeze prompts

```bash
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python \
  experiments/overhang_feedback/select_prompts.py \
  --metadata-root /jiigan-hp/lms/aDSL/datasets/prompt_sources \
  --output local_experiment/overhang_prompt_pilot_20260906/case_manifest.json
```
# Exterior-area feedback clarification (2026-09-14)

The opt-in feedback arm now tells Engineering Critic that the metric counts
Boolean-unioned exposed overhang, not duplicate internal faces. A proposal should
explain which exposed surfaces it expects to reduce and whether it creates new
undersides; insufficient evidence is a valid reason to propose nothing.

The next critic request includes the attempted action/intention (explicitly not a
verified geometric effect), actual changed source symbols when available, original
and candidate total areas, the existing comparison/rejection reason, and remaining
budget. Full patches and face lists are not included. Current saved records do not
establish cross-version exterior-region correspondence, so local area deltas are
explicitly UNCERTAIN; ranking/region IDs and AABB matches are not treated as proof.
Legacy or failed attempts may have missing summaries/measurements, never fake zeros.
Image/Code Critic, protection, acceptance, thresholds and retained publication are
unchanged. No SF03 rerun or real API call was made for this change.

Mock validation: `python -m pytest -q tests/test_overhang_candidate_isolation.py
tests/test_overhang_local_edit.py` — 40 passed, 3 skipped. The new request-level
test rejects a worsened candidate, verifies feedback in the next actual runtime
input, then returns no proposal with one attempt remaining and retains the original.

## Suggested next assets (not launched)

Use the existing `paired_assets.json` protection scopes without enlarging them:

| Asset | Overhang regions to inspect, based on saved source | Allowed edit | Protection / limitation |
| --- | --- | --- | --- |
| O01 white mug with metallic handle | Curved grip and horizontal mounting undersides; focus on lower mount/transition | `CurvedMetalHandle`, noncritical lower transitions only | Preserve hollow white body, rim, metallic loop and opening, and original upward-facing surfaces. Opening dimensions are only visually constrained. |
| O03 dark wooden open-arm chair | Seat underside and long horizontal arm undersides; only arms are in scope | `OpenArmAssembly`, undersides/transitions only | Preserve curved back, flat seat, four legs, open armrest space, wood appearance and original upper surfaces. Do not fill the arm opening or change protected top surfaces. |

Both have saved source/GLB/URDF under
`local_experiment/overhang_prompt_pilot_20260906/baseline/`.
These region descriptions are source-based hypotheses, not new measured local
area attribution. Legacy baseline reports contain no local overhang regions and
contain multiple un-unioned components (O01: 23; O03: 60); their total areas are
not a current Boolean-exterior baseline and do not establish current measurement
readiness. Future execution must use the existing measurement entry, not those old
numbers for acceptance. SF03's failed experiment remains unchanged; no extra
attempts are proposed for it. O01/O02 share a dataset object, as do O03/O04, so
choosing O01 and O03 avoids presenting two variants as independent objects.
