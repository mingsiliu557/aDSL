# Standing XML compatibility correction — 2026-10-23

Base: `action_v1@f18fe4a51621a3306b3a79869bde884a2be20d87`. This report accompanies the compatibility correction; its commit is available through this file's Git history.

## Cause and change

The installed MuJoCo 3.16.0 rejects `flex/contact.internal`. The parent mixed50 run therefore recorded `ANALYSIS_UNAVAILABLE` rather than an observed standing result. This affected both native Ours feedback and the separate final physical evaluation. It was not an API connection failure or evidence of tipping.

`adsl-agents/assembly_standing.py::_legacy_flex_contact_options` checks the actual solver model capability: libraries retaining `MjModel.flex_internal` still receive `internal="false"`; libraries where that field and XML option were removed omit the attribute. Diagnostic-only XML construction without the optional MuJoCo package retains the legacy explicit value. Other missing dependencies still propagate.

Contact radius, friction, gravity, timestep, inertia, collision backend, observation length, tipping/interface-exit thresholds, contact-depth warning semantics and final acceptance rules are unchanged. CoACD XML does not use this helper.

Official references: [MuJoCo changelog](https://mujoco.readthedocs.io/en/latest/changelog.html) records removal of the deprecated option and associated structures; [3.3.7 XML reference](https://mujoco.readthedocs.io/en/3.3.7/XMLreference.html#flex-contact) documents the prior option and false default. Capability detection also preserves explicit false on older libraries whose default differed.

## Verification actually executed

```sh
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 python -m pytest -q \
  tests/test_assembly_standing.py tests/test_assembly_standing_verdict.py \
  tests/test_assembly_physics.py \
  -k 'not real_rigid_flex_three_controls and not real_mujoco_with_exact_fixture_proxies_only'
```

Result: **39 passed, 4 skipped, 2 deselected**, 5.65 seconds. This includes new capability-branch tests and compilation/one real step with the installed MuJoCo. The skips and deselections are not claimed as passes.

In the existing A100 Slurm allocation, the artificial native CPU control test was also executed:

```sh
ADSL_TEST_RIGID_FLEX=1 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
  python -m pytest -q tests/test_assembly_standing.py::test_real_rigid_flex_three_controls
```

Result: **1 passed**, 18.33 seconds. All controls completed 5 seconds of simulation:

| Artificial control | Verdict | Peak tilt | Interface exit |
|---|---|---:|---|
| Seated joint | PASS | 0° | none |
| Hanging joint | FAIL | 0.0000121° | first observed at 0.060 s |
| Narrow tilted block | FAIL | 74.4513° | none |

The test checks actual contacts and retained failure findings. It demonstrates the solver interface and controls, not manufacturing performance on benchmark objects. Older solver behavior is covered by capability-branch tests; a separate old-version physical run was not performed.

## Experiment handling at the initial correction

The following records the initial retry setup, not the current allocation or provider. Subsequent queue progress and resource changes are recorded in the experiment ledger.

The parent `mixed50_ssh_sol61_20261023` queue was stopped at user request. Its original checkpoint and current job were archived before recording the pause. The active `DensiCrafter_159/official` call was marked user-interrupted with incomplete usage; it was not relabeled as an API or geometry failure. The existing A100 allocation and its shell were held at that point.

Only the 19 already mesh-valid aDSL cases are eligible for the correction retry. Their saved source/GLB/assets and mesh-gate records are hash-bound. The new `mixed50_ours_standing_retry_20261023` directory reruns Ours using the same references/tasks and 10-round/9-repair native budget. Saved aDSL assets receive fresh physical evaluation in new files without new aDSL generation. Previous attempts, usage, reports and selected assets remain historical; the remaining seven aDSL cases stay paused.

The initial retry reused SSH gpt-6.1-sol and the existing A100 Cycles render adapter, with FEA disabled. Its submission records the patch hash and exact Standing file hash. The live checkpoint, rather than this report, is authoritative for progress and case outcomes. Publishing the correction does not restart a job or change historical results.
