# Fixed assembly: original prompt → new program

## 2026-09-20: single saved-source visual loop validation

`fixed_assembly.validation_mode="visual_only"` explicitly skips closed-volume,
connectivity, mating-volume, interference and size measurement. The default
`geometry` mode is unchanged. Paired TabSlot Boolean construction and frame
placement still execute. Display/export reuse the final per-part triangle meshes;
no Manifold union/validation is performed in visual mode. Serialization consistency
is compared under the existing float32 coordinate bound (triangle sets, independent
of face/vertex order); this checks exported files, not physical geometry validity.

Every iteration attempts rendering. Fixed assembly now shares ordinary aDSL's
generation-review functions: requirement, plan/checklist, current images, round
budget and review history. It does not use candidate-preservation review or send a
failed initial render as a baseline. Image approval does not trigger Code Critic
just because geometry is `NOT_EVALUATED`; Code runs after Image rejection (or when
no render is available), retaining aDSL's original correction authority.

Missing/omitted geometry is identified, blocks full appearance approval, and remains
repairable. No render skips only Image Critic. `display_available` describes mesh
availability, not semantic completeness, and is not positive evidence sent to the
Critics. Repair uses the last working candidate; retained approval requires the
existing visual/code decision and consistent exports.
`geometry_validation=NOT_EVALUATED` even on visual success. The original geometry
mode and four-checker implementations remain; no physical checker runs here.
The export/version adapter remains separate; this is shared review, not a rewrite
of all iteration scheduling. See
`reports/fixed_assembly_generation_review_alignment_20260920.md` for the code
comparison, tests and remaining Code/Image disagreement limitation. No real model
run was made for this alignment change.

One existing failed SF03 source, no Planner, at most one edit, stepcode only:

```sh
bash experiments/fixed_assembly_prompt/verify_visual_stepcode.sh \
  --saved-case local_experiment/fixed_assembly_prompt_20260919T074507Z/SF03 \
  --source local_experiment/fixed_assembly_prompt_20260919T074507Z/SF03/generate/rounds/round_02/candidates/01_assembly_or_appearance/source.py \
  --output local_experiment/fixed_assembly_visual_NEW/SF03
```

Use a fresh output. The script stops only a stepcode proxy it started. It does not
use CLIProxy or launch other cases. Below documents the earlier prompt-to-3D run.

For the subsequently requested five-round run, add `--max-rounds 5` with a NEW
output directory. This means initial review plus at most four edits/reviews, not
five forced edits. Approval or explicit no change may finish earlier. Default is
still two rounds. This option does not change geometry mode, acceptance or models.

This is **not** `fixed_assembly_existing` (the preserved asset-conversion study).
It calls native `ObjectWorkflow.generate()` with an empty source file and fresh
sessions. SF07, SF03, SF13 use exact prompts from `standing_fea_30/case_manifest.json`.
Original text and added uniform manufacturing requirements are separately stored
in each `input.json`. No old source, model or generated render is read as input.
Saved historical runner commands have no `--image`; references are empty. Historical
serialized requests no longer exist, so that provenance limitation is recorded.

Before calling the model, freeze demonstration dimensions: SF07 120×120×120 mm,
SF03 90×80×180 mm, SF13 100×32×200 mm; 1 mm/source unit and +0.2 mm single-sided
clearance. No print grouping or interface position is prescribed. At least two
print parts and the existing tree/TabSlot API are required.

Each case: one initial full program, at most one source repair (`max_rounds=2`).
Existing code execution, 120 s geometry timeout, 300 s rendering timeout, image/code
review, geometry gates and retained publication are unchanged. No articulation or
four physical checkers. Production files are not modified by this experiment.

Run **only SF07 first**, inspect its actual model-input audit and generated assembly
API use, then continue the other two only if no common implementation error:

```sh
bash experiments/cliproxy_session.sh adsl_cliproxy

mkdir -p local_experiment/fixed_assembly_prompt_NEW
bash experiments/tmux_session.sh adsl_assembly_prompt_first "$PWD" \
  bash -o pipefail -c 'bash experiments/fixed_assembly_prompt/launch.sh \
    --root local_experiment/fixed_assembly_prompt_NEW --cases SF07 \
    2>&1 | tee local_experiment/fixed_assembly_prompt_NEW/first.log'

bash experiments/tmux_session.sh adsl_assembly_prompt_rest "$PWD" \
  bash -o pipefail -c 'bash experiments/fixed_assembly_prompt/launch.sh \
    --root local_experiment/fixed_assembly_prompt_NEW --cases SF03 SF13 \
    2>&1 | tee local_experiment/fixed_assembly_prompt_NEW/rest.log'
```

Create the root before `tee` (`mkdir -p ...`), or call `prepare(root)` without API
first. Do not run these commands over a completed directory to regenerate cases.
Replace `NEW` and session names with new names; inspect existing proxy status first
instead of launching a second copy. Current and historical run locations are listed
in `local_experiment/README.md`; completed startup diagnostics are under `diagnostics/`.
Known invalid candidate meshes (`EXPORTED_FILE_INVALID` with the existing mesh
validity reason) reject that candidate but do not imply a batch-wide defect.
Actual export mismatch, missing files/IDs, unknown read failures and API/flow/input
errors still pause. An old SF07 export-prefix pause can be reclassified from its
saved manifests, calls and audit into `SF07/continuation_gate.json`; its result and
edit budget remain unchanged, and SF07 is not rerun. A retained historical
`paused.json` is not the current continuation status; consult the continuation
record and each later case's result/log instead.
The first-case gate prevents later-case launch before evidence exists; no automatic
replay of started cases. CLIProxy runs in its own tmux; experiment launchers only
check/use it and never start or stop it. An experiment failure must not shut down
the proxy. Stop the service explicitly with `cliproxy_stop` when requested.

Evidence: `CASE/input.json`, `generate/plan.json`, `stage_inputs/`, `api_calls/`,
`input_audit.json`, new `generate/original/source.py`, all repair candidates,
`assembly_versions.json`, `assembly_result.json`, mesh/assembly/exploded images,
session snapshot and `result.json`. In this study `original` means newly generated
initial program, never a historical model. When all candidates fail, retained is
an unapproved diagnostic, not a fabricated successful assembly or an old asset.

The small model wrapper logs actual SDK model inputs and per-call usage including
calls preceding a tool failure. It does not add retry, limits, historic token
carry-forward or cumulative accounting. Unknown failed-call usage remains unknown.
Static API audit is checked alongside runtime manifest; source strings alone are
not proof of successful geometry. Physical retention/manufacturing is unverified.
