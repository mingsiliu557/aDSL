# Planned-check batch failure and targeted retry

Original batch: `/jiigan-hp/lms/aDSL/experiment/planned_adsl_edits_20260914T184915Z`.

## Verified causes

- SF01/SF07/SF11/SF13/SF21/SF27: `ValueError: input changed` before planner invocation.
  `restore_geometry_evidence()` correctly saved updated input hashes, but `worker()`
  then overwrote state.json using its stale pre-restoration dictionary when adding
  protection. Actual file comparison: only analysis_geometry.json was added; zero
  existing files changed. No editing candidates or planner API calls occurred.
- SF05/SF20: exterior calibration rejected non-watertight input collision shells.
  This is unverified overhang measurement, not structural FAIL. No geometry repair,
  tolerance relaxation or blind rerun performed. The calibration prerequisite still
  stops these cases before the multi-checker loop; this limitation is not fixed here.
- SF06/SF16/SF25: original published input assets unavailable.
- SF03: normal no-proposal completion, original retained, no improvement established.

## Minimal change

Reload state.json immediately after geometry-evidence restoration before assigning
the manual protection scope. The immutable-input guard is retained. SF03's reviewed
resume already had its manifest, so it did not exercise first-time restoration;
the earlier smoke therefore missed the stale-state overwrite in fresh cases.

Validation: 4 targeted tests passed, including first-time restoration through the
worker's planner boundary and case-local timeout isolation. No additional geometry
experiments or broad test suite were run.

## Authorized retry

Only the six code-blocked cases are resubmitted in a fresh independent output root:
`/jiigan-hp/lms/aDSL/experiment/planned_adsl_retry_20260915T013158Z`.
Each retry retains the original aDSL asset, 4-round/2-edit limit, and 30-minute wall
budget. Prior failures remain unchanged. New API accounting uses the SAME shared
ledger, not a new 100-million-token allowance. Debit before retry: 810,881 including
conservatively bounded unknown usage; no pending RESERVED requests.

The submission wrapper accepts explicit case IDs and starts a proxy only when
needed, cleaning up only its owned proxy. A normal batch shell exit does not imply
every model or checker passed; per-case edit_status.json and edit_results.csv govern.
