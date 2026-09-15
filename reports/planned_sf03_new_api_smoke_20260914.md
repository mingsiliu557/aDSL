# SF03: original aDSL -> planned checks smoke

Input: original aDSL SF03 from the 20260912T100549Z batch, not the historical ours
model. Output root: `/jiigan-hp/lms/aDSL/experiment/planned_adsl_edits_20260914T184915Z`.
Workspace: `local_experiment/planned_adsl_edits_20260914T184915Z/SF03/ours`.

## Actual result

- Normal completion, `no_actionable_proposal`, retained=original, approved=false.
- Topology PASS; standing PASS; overhang measurement PASS; FEA INDETERMINATE /
  MESH_INVALID. No structural failure or complete physical approval is claimed.
- Zero source-edit candidates. Engineering Critic read the source and identified
  the dominant overhang as involving the protected seat/leg region. The two small
  leg-grain regions lacked reliable evidence of measurable safe improvement.
- The agent explicitly declined to modify. Original model/render and matching
  checker results were published. No overhang or FEA improvement is claimed.
- Elapsed 759.44 seconds including the reviewed interruptions/recovery. Original
  1800-second deadline and max 2 candidates were not reset.

This verifies multi-checker feedback and normal no-change/retained publication,
not an actual candidate repair/acceptance. Joint candidate regression protection
was tested with mocks. Final related test run: 102 passed; no more tests or SF03
attempts were added after the user's stop-testing instruction.

## Diagnosed startup/recovery failures

1. Proxy exited after a nonpersistent tool session. Persistent tmux proxy passed
   image/tool/schema/usage probing: 2 requests, 3,358 actual tokens.
2. Historical publication omitted analysis_geometry.json. Recovered only from a
   generation round with matching source, GLB and URDF hashes; not rebuilt.
3. Interrupting the first case left an API reservation. Reviewed unknown usage
   remains charged at its full bound, never reported as zero or actual usage.

Old failures and version records remain archived. New API requests use one shared
100-million-token local-request ledger; StepCode is separate. Proxy-side retries
or account billing cannot be independently certified by this local ledger.

## Batch handoff

Submit the original 12 IDs, skip already completed SF03, and record missing
SF06/SF16/SF25 inputs without substitution or generation. Each other case has an
independent process, workspace, deadline, logs and candidate budget. Per-case
error/timeout does not abort independent cases. A recorded common FLOW_ERROR
pauses expansion. Inspect `edit_results.csv` and per-case `edit_status.json`.
