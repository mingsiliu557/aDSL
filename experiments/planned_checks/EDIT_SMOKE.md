# Existing aDSL assets: planned-check local-edit smoke

## Scope

Use the original aDSL arm from `topology_standing_fea_12_cpu_20260912T100549Z`,
not historical ours assets. The original source/GLB/URDF/render stay immutable.
SF03 is supervised before submitting the remaining original 12 IDs. Missing
SF06/SF16/SF25 published assets remain INPUT_UNAVAILABLE; no regeneration or
substitution. Manually bounded component scopes are in `run_edit_smoke.py`.
Exact measured surface protection plus appearance review is conservative; it
does not certify all functional dimensions. Unmeasurable protection stops editing.

## Implementation and limits

`overhang_experiment.mode=planned_checks` is an explicit separate opt-in branch
using the existing isolated version/candidate loop. The historical name of the
options container and version file is retained to avoid copying the workflow.
Ordinary aDSL and overhang-only guards remain. Four checker results are preserved;
topology blocks only dependent FEA. Overhang PASS means measurement, not printability.
Existing joint acceptance rejects hard regressions and uncertain protection;
soft area decrease cannot offset a hard regression. Retained files and evidence
are selected together, and every attempted candidate remains on disk.

Per case: at most 4 rounds and 2 edit attempts, 1800-second total wall budget.
Parent process terminates the worker and descendant process groups on timeout.
Single-case errors/timeouts/missing inputs do not stop unrelated cases. A recorded
FLOW_ERROR pauses expansion. Completed cases and interrupted requests are not
automatically replayed. Required checks not executed remain unverified.

API: `gpt-5.6-sol` on the user's CLIProxyAPI, after a real image/tool/schema/usage
probe. Every new-API request shares `local_experiment/planned_checks_new_api/`
token accounting, capped at 100,000,000 local-request tokens including probes.
API-error usage remains charged at the full request bound as BOUNDED_UNKNOWN;
it is not reported as measured usage. Unresolved interrupted reservations still
block new requests until reviewed. This ledger cannot independently verify
proxy-side retries/provider-account billing. StepCode has a separate ledger and
two transient-error retries with 30/60-second backoff, within the original deadline.

## Commands

Source LMS `.bashrc`, then `cliproxy_use`. Keep the proxy in its dedicated tmux.
Use `/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python`:

```sh
python -m experiments.planned_checks.run_edit_smoke batch --output OUTPUT --cases SF03
# Only after the supervised smoke has no common workflow defect:
python -m experiments.planned_checks.run_edit_smoke batch --output OUTPUT --cases SF01 SF03 SF05 SF06 SF07 SF11 SF13 SF16 SF20 SF21 SF25 SF27
```

OUTPUT must be a fresh data-disk experiment directory. Original copies, plans,
calibration and per-case status/logs are there. Candidate workspaces stay in
`local_experiment/<OUTPUT-name>/<case>/ours` to avoid shared-disk SQLite errors.
`edit_results.csv` includes failures. A timeout may preserve only the original
assets and the durable version ledger: do not claim final publication completed.

## First smoke input bug

Historical public assets omit `analysis_geometry.json`; the matched original
generation round contains it. Input staging now restores it only when source
hash and exact GLB/URDF hashes match. This is evidence recovery, not reconstruction.
The first SF03 run's topology ERROR and dependent FEA skip are preserved.
Reviewed continuation keeps its original deadline, usage and candidate count.
No new mesh backend, kernel tolerance, material, load or solver setting is changed.
