# Standing: finite self-weight observation without a settling gate

Date: 2026-09-28. Baseline: `377dc44f5dd723cefdf42f46f8691d1e063f7e3d`.

Residual motion previously made an otherwise valid standing observation
INDETERMINATE. Per the agreed acceptance rule, a valid run now passes when no
sampled root tilt exceeds the configured limit and no interface exits during
the entire configured observation (this case: 5 seconds, 25 degrees).
Earlier tipping/exit events still fail even if the final pose recovers.
Speeds, settling settings, and `settled` remain diagnostic; they no longer gate PASS.
Geometry/proxy validity, interface verification and execution error handling are
unchanged. This does not establish static equilibrium or long-term stability.

## Scope

The implementation changes only verdict selection, summary wording and the
reported criterion in `adsl-agents/assembly_standing.py`. Example documentation,
a test comment, and focused verdict regressions are synchronized.

The existing uncommitted rigid_flex backend, microcrack repair, FEA changes,
experiment artifacts and historical file deletions are excluded from this commit.
The exact publication checkout is tested separately from that dirty workspace.

## Validation

- Exact publication checkout: **17 passed, 1 skipped, 6 deselected** using
  `tests/test_assembly_standing_verdict.py tests/test_assembly_physics.py -k 'not real_'`.
  Imports were routed into `/tmp/adsl_standing_verdict_20260928`, with no editable
  installation changes. The skipped item is an existing opt-in integration test.
- Working-tree validation: **22 passed, 1 skipped, 6 deselected**, including
  `tests/test_assembly_standing.py` with `ADSL_TEST_RIGID_FLEX=1` and selection
  `not real_ or real_rigid_flex_three_controls`. The native CPU test covers a
  seated assembly, interface exit and tipping; the latter two remain FAIL.
- Seven new focused tests use real fixture geometry and mocked physics results:
  residual motion passes; an earlier tip/exit still fails after recovery;
  unverified interfaces/proxies and simulation errors cannot become PASS.
  These tests do not establish collision accuracy. A first workspace invocation
  omitted the existing MuJoCo runtime path and failed dependency lookup; rerunning
  with that path produced the successful counts above.

Logs: `/tmp/adsl_standing_verdict_evidence_20260928/isolated_tests.log` and
`workspace_tests.log`. No Agent calls or GPU use.

## SF13 CPU recheck

Saved separately in `temp/sf13_microcrack_standing_20260928/standing_criterion_20260928/`:
`report.json`, `comparison.json`, model, trajectory and contact records.
Source SHA256: `a4212de6738c3c598eb53286bcb10036ef26cad5c7eeb6fbd5d5c5a8e43eb6f2`.

- Same repaired geometry and original physics settings; 5 seconds, timestep 2 ms.
- **PASS** under the new criterion; `settled=false` remains reported.
- Peak root tilt **0.156340744 degrees**; final **0.004636397 degrees**.
- All **251 samples cover all 24 interfaces**; no exit detected.
- Initial contact validity passes; maximum qpos difference from the old trajectory
  is **0**. Original INDETERMINATE report is preserved.

This native recheck uses the working tree's previously implemented, uncommitted
rigid_flex backend. It validates the new verdict on the actual SF13 trajectory;
it does not imply that this backend or the mesh repairs are included in the
publication commit. The remote CoACD backend was not rerun end-to-end here.
