# Joint workflow field-contract audit (2026-09-15)

Base: local bf6b2bd plus uncommitted changes; previous production baseline 3e3732e.
No real API/geometry experiment was run during this audit. Previous batch remains stopped.

| Boundary | Observed issue | Minimal correction / limitation |
|---|---|---|
| Critic target → AST | SF03 supplied `ChairLegs.__init__: description`; exact symbol lookup rejected all four proposals before Coder | Joint prompt requests bare symbols; accept only an existing exact symbol before a colon, preserve attached restrictions in evidence; unknown symbols still rejected |
| Inline source → tool gate | Complete source was supplied, but missing redundant read_file event aborted SF01 | Joint-only removal of redundant tool-event requirement; old overhang behavior retained |
| Cached measurement → feedback | Cache report outside agent workspace broke relative_to | Copy cached report/artifacts into current round checker directory and remap artifact paths; no remeasurement |
| Gate candidate → physical targets | Placeholder `local_edit` ID could not match actual checker findings | Joint gate proposals reference actual actionable findings provided to Coder |
| Topology metrics → acceptance | Final component count was in result metrics, not pairwise finding.metric; 20→5 was uncounted | Joint targeted one_piece FAIL→FAIL compares valid integer final component counts; partial improvement remains FAIL; appearance, hard-regression and measurement-comparability guards retained |
| Index JSON → localization | Invalid bounds/null could raise ValidationError | Joint baseline/candidate parser records UNAVAILABLE and allows source inference; preserves raw index, does not fabricate IDs or swallow unrelated IO errors |
| Planner → resolved spec | Existing whitelist/profile validation and frozen configuration preserved | Unselected/missing profiles cannot become fabricated FEA conditions |
| Error → final assets | Prior FLOW_ERROR exits preserved retained but stopped batch | No false success; existing shared-flow stop retained; API isolation/retry policy is not redesigned here |
| Generation → planned joint mode | Current runner requires pre-existing original_urdf/source/renders | Still an existing-asset edit runner, NOT a prompt-to-3D experimental entry point |

Validation: 86 related simulated tests passed, including old overhang isolation,
scope restrictions, invalid index handling, partial progress with regression guards,
and cached report paths. These are not real experiment success claims.

## Experimental interpretation

Historical aDSL and ours were independently generated from the same prompts.
The recent planned_* runs instead edited historical aDSL assets; they are local-edit
experiments and must not be presented as the same independent-generation protocol.
Do not resume that runner to claim a prompt-generation result.

Smallest proposed new entry: generate a fresh seed per arm directly from prompt
without historical source/model/image input; baseline follows ordinary aDSL,
ours freezes a checker plan/print calibration from its OWN generated seed and
reuses isolated joint refinement. Count the initial generation/review round and
remaining refinement rounds explicitly under the agreed four-round total.
This entry is not implemented by merely renaming edit to generate: current joint
validation requires original assets and the iteration dispatcher assumes they exist.
No experiment may be launched under a misleading input provenance.

## Subsequent implementation and current command

The generation connection is now implemented in `run_prompt_batch.py`.
`ObjectWorkflow.generate` creates initial code normally. At its first successful
execution, the opt-in subclass freezes the plan from its OWN model and enters
joint checks in the same runtime, before appearance approval. An absolute four-round
ceiling includes prior execution/debug rounds; this is not a separately budgeted
completed-generation-plus-edit experiment. Ordinary aDSL and old overhang mode are unchanged.

Per the user's final scope, only ours is generated; aDSL uses the historical table.
No aDSL generation API call is permitted by this runner. Eight-case command:

```sh
python -m experiments.planned_checks.run_prompt_batch batch \
  --output /vepfs_default/chanxueyan/lhp/lms/aDSL/local_experiment/NEW_PROMPT_RUN \
  --cases SF03 SF01 SF27 SF07 SF11 SF13 SF21 SF05
```

Load the existing API client environment first; the independent proxy must be online.
No per-case 30-minute or total-candidate cap; single-checker limits, four rounds,
eight-hour batch deadline and shared 100-million-token ledger remain in effect.
Outputs include generation_results.csv, comparison_12.csv and COMPARISON_12.md
(the latter filenames are historical naming; rows follow the selected case list).
Historical missing metrics remain unavailable, not zero.

### 2026-09-15 prompt-handoff corrections

- An initial overhang failure no longer aborts the case. Independent checks and
  visual/engineering review continue. Without a valid frozen initial print scale,
  candidate overhang remains INDETERMINATE / MEASUREMENT_CONDITIONS_UNAVAILABLE;
  do not relabel the new candidate with the initial model's geometric failure.
- Final approval receives the active joint-mode flag directly from the executing
  loop; the pre-generation user_input.json is not authoritative for dynamic checks.
  Required FEA FAIL/ERROR/INDETERMINATE/missing forbids approved=true.
- 94 targeted mocked tests pass, including the two complete dynamic-handoff paths.
  This does not constitute successful real physics validation.
- The stopped prompt8_ours_20260915 SF03 approved=true flag is invalid: its actual
  required FEA result was FAIL. Preserve old evidence; exclude that flag from success
  claims. New outputs are separate and use the corrected publication rule.

### 2026-09-15 candidate return-contract correction (batch stopped)

- In `prompt8_fixed_20260915`, SF01's Coder applied patches successfully, but
  all four candidates were rejected before execution with `TypeError: 'NoneType'
  object is not subscriptable`. These are workflow failures, not ineffective
  physical repairs. The original asset remains retained.
- Root cause: the prompt handoff wrote top-level `overhang_experiment`, whereas
  `_repair()` reads `request.overhang_experiment`. The empty nested options selected
  ordinary repair semantics (return None), then the candidate caller accessed
  `patch_result['status']`. Correct only the writer to the existing nested contract;
  do not change ordinary repair behavior, checker logic or candidate acceptance.
- The regression now runs the actual handoff and real `_repair()` with a mock
  model for CHANGED, NO_CHANGE and TOOL_ERROR, with valid/invalid calibration.
  It checks reservation transition, source hashes and preservation of the parent.
  Six relevant test files: 98 passed (34 direct + 64 adjacent), no API or geometry.
- Per user request, stopped this batch and its owned worker during SF27. Files,
  independent API service and GPU keeper are preserved. SF03 separately recorded
  upstream HTTP 500 / TLS handshake EOF; this fix does not resolve that API error.
  No automatic restart or budget reset was performed.

### 2026-09-15 independent-tool and joint-gate corrections

- Keep selected FEA in the checker list when topology is unavailable. Return
  INDETERMINATE / TOPOLOGY_DEPENDENCY_UNAVAILABLE without solving; standing,
  overhang and visual review remain independent. The shared executor's explicit
  `require_topology` flag is enabled only for joint mode; ordinary FEA-only calls
  are unchanged.
- Preserve invalid/NEEDS_SPEC tool-plan entries in `unverified_plan_tools` and
  final unverified lists. Missing required plan entries forbid joint approval.
  CSV summaries distinguish plan status from execution status. An all-invalid
  plan may still preserve/review the generated asset without claiming success.
- Reject a verified one_piece component-count increase even when both topology
  results remain FAIL and overhang improves; this check does not depend on the
  proposal targeting topology. Existing partial improvement and unknown-state
  handling remain in effect.
- Persist the complete effective joint request under runtime_config.request,
  including checker specs, check_first, repair policy and experiment options.
  Joint CLI resume cannot increase the saved round ceiling via its default.
  This is configuration recovery, not an automatic replay or new resume runner;
  stopped historical workspaces and token reservations are unchanged.
- Unexpected candidate programming exceptions (TypeError/AttributeError/KeyError/
  AssertionError) propagate to the existing workflow error boundary. Save a local
  traceback and candidate FLOW_ERROR; publish retained and stop the batch instead
  of spending every round on the same bug. Known tool/API errors remain distinct;
  a single-case API failure does not automatically pause independent cases.
- Validation: 166 passed across ten existing lightweight test files. New tests
  exercise real handoff, real repair/acceptance/publication with mocked model,
  assets and checkers; missing/all-invalid plans; regression rejection; restored
  request/round ceiling; and mocked batch stop/continue. No real API, Blender,
  FEA or batch was run. Old compact-feedback mock now supplies the real request
  field `overhang_experiment` instead of failing before its assertion.
