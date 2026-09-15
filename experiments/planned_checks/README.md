# Planned checks: first bounded delivery

Status: **plan-only and baseline measurement**, not the completed mixed-edit agent.
Current master at implementation start: `3e3732e`; local source-inference changes
are preserved. Each run records effective code hashes, not just this commit.

The existing overhang-only iteration has tool-set assumptions in result extraction,
proposal gating, acceptance, and publication. Following the approved scope fallback,
this delivery does not loosen them or duplicate the loop. `batch` explicitly refuses
to expand. The opt-in mixed `assess_candidate(planned_checks=True)` branch is tested
but **not connected to live edits**. No original models are regenerated or published.

## What runs

- Prepare the original twelve Ours published assets, including relative URDF mesh
  dependencies and renders, in a new data-disk output root. Missing input remains
  a row (SF16 has no published GLB/URDF). Source/index mismatch is unavailable input.
- StepCode planner sees prompt, full source, existing images and configured profiles.
  Four entries, no commands/parameters from the model; one validation correction.
  Correction preserves already-valid tool decisions. Invalid/missing/duplicate tool
  entries become per-tool PLAN_INVALID with no executable spec; valid siblings
  continue. A partial plan is frozen as PLAN_PARTIAL, not a complete validation pass.
  Invalid topology blocks only FEA execution, not standing/overhang. Unparseable
  whole responses and shared budget/integrity errors still cannot be guessed away.
  Applicability reasoning remains
  reviewable: JSON/profile validation cannot prove the planner interpreted use well.
- Freeze proposed/resolved plans, input hashes and profile files. The topology
  registry currently offers existing one_piece only; unsupported detachable/load-path
  requirements must be unresolved rather than invented. FEA assumptions are shown in
  full and may be declined as NEEDS_SPEC. FEA outer timeout is 900s in this batch only.
- The `smoke` subcommand currently means **baseline-only** SF03/SF07/SF27, not a
  real edit smoke. Existing independent checker processes are used, topology blocks
  FEA, other unavailable results do not abort sibling measurements. Per-tool stage
  admission reserves its full configured timeout plus five seconds. This can cause
  FEA to be budget-skipped; it is never counted as PASS.
- Per-case 1800s includes its planning time; batch deadline is fixed at first prepare
  plus eight hours. Completed/interrupted requests and measurements are not replayed.
  Checker process cleanup reuses existing isolation. Kernel uninterruptible disk I/O
  cannot be guaranteed killable by a Python timer. No new batch daemon is added.

## API budget and remaining gate

The planner wraps every actual SDK model request, not just each agent invocation.
Per the user's updated budget rule, `cliproxy_token_budget.json` alone has the
100,000,000 input+output token cap. StepCode is recorded separately in
`stepcode_usage.json` and does not consume that cap. Historical `token_budget.json`
from the StepCode-only runs remains unchanged as evidence and is not imported into
the new API ledger. Cached/reasoning subtotals are not added twice. SDK retries are disabled. Unresolved responses retain
their reservation and block further requests. Local model metadata advertises
`gpt-5.6-sol` context=272000; reserve that plus the output limit for each call and
record the metadata hash. This bound assumes the proxy honors the advertised model;
it does **not** independently certify upstream retry/account billing. Never describe
it as a provider-side account quota. Model metadata changes block calls for review.

`cliproxy-gpt-5.6-sol.yaml` contains only the env-var credential reference and localhost
endpoint. It is prepared but not activated: image/tool/usage compatibility, upstream
retry accounting, and the actual mixed-edit smoke are still pending. The experiment
entrypoint rejects the new endpoint until that gate is implemented and verified.

## Commands

Use `/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python` as Python, and a NEW
`/jiigan-hp/lms/aDSL/experiment/<batch>` directory as OUTPUT:

```sh
python experiments/planned_checks/run.py plan-only --output OUTPUT
python experiments/planned_checks/run.py measure-only --output OUTPUT
python experiments/planned_checks/run.py smoke --output OUTPUT
python experiments/planned_checks/run.py summarize --output OUTPUT
# Intentional fail-closed gate, not an operational editing batch:
python experiments/planned_checks/run.py batch --output OUTPUT
```

`run_stepcode.sh OUTPUT` submits plan-only, the three baseline smoke cases
(SF03/SF07/SF27), then `measure-only` for all 12 IDs. Already measured cases
are skipped without repeating checker calls. Missing inputs remain in the table.
A workflow exception stops the script; individual tool failures remain recorded
and do not block independent cases. This is baseline checking, not mixed editing.
It reuses an existing healthy StepCode proxy without stopping it; starts/stops only
its own otherwise. No per-case proxy restart. Use tmux with `tee` for console + file.
No persistent human/model monitoring is required after launch.

Outputs: tool_plan_table.csv, case_results.csv, costs.csv, REPORT.md, request budget
ledger, input copies/hashes, proposed/resolved plans, and checker artifacts.
Measurement-only output marks appearance as unreviewed and joint approval false.
Costs not emitted by a checker are not invented; no fine-grained rendering/meshing
timing or representative-image report is claimed complete in this first delivery.

Remaining before full B→C→D: share the isolated loop across the new mode without
weakening existing guards; wire mixed acceptance and retained publishing; add
cross-entry budget/publishing mocks; execute real StepCode edit smoke; verify new API.
This delivery alone demonstrates neither editing improvement nor tool-plan causality.

## Request failure recovery

Planner API errors, malformed model responses and request timeouts are case-local:
record PLAN_ERROR, preserve any validated first-response partial plan, and continue
independent cases. No automatic retry or candidate-budget reset. Programming errors,
changed frozen inputs/configuration and storage errors still stop the batch.
The submission script checks already frozen smoke plans before new API requests.
StepCode unresolved usage stays RESERVED (unknown, not zero or measured usage);
it does not block a different case, but the failed model instance cannot replay.
The new API retains its strict unresolved-usage gate and 100-million-token limit.

## Updated semantic FEA planning assumption

The user authorizes the existing fixed-profile screening, not a reconstruction of
the prompt's real-world material. Planner receives `fea_screening_assumptions`:
select chair/table/shelf profiles by functional use, keep existing PLA material,
forces/directions and scale. The actual solver already fixes all three translational
DOFs of global minimum-Z support nodes (at least three); this rule was previously
missing from the planner's view because it lives in analyzer code, not profile JSON.
Do not skip solely for wood/metal/cloth versus PLA or absent JSON support fields.
Do retain NEEDS_SPEC if the existing functional region or support idealization
cannot apply. Actual region matching/topology/support validity remains an execution
gate. A selected FEA profile is neither a passed check nor real-material certification.
