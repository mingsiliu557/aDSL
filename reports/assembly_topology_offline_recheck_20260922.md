## Material Passport

- Origin Skill / Mode: academic-research-suite / experiment-agent run
- Date: 2026-09-22 UTC
- Base commit: `558de6ec58de560f5ac17174ccf973d9de9b7975`; the path fix and subsequent float64 transport fix are committed with these reports. Experiment outputs under `temp/` remain local, not GitHub assets.
- Verification: topology actually re-executed on 12 saved final assets; no regeneration, source repair, or new API requests. Image scores reused, not re-evaluated.
- Version: offline_topology_path_fix_v1

## Scope and fix

The offline caller previously passed the saved rendering ExecutionResult to the checker. Its GLB was `asset/render/scene.glb`; the shared executor resolves `{asset_dir}` from the GLB parent, so it incorrectly searched for `asset/render/assembly_manifest.json`.

`experiments/fixed_assembly_prompt/run_paired.py::evaluate_topology()` now copies that execution record and changes only the GLB reference to `output_root/assembly/scene.glb`, matching the existing online assembly adapter. The real manifest and final print meshes remain in `asset/assembly/`. There is no path search or fallback to another candidate, no CSG re-execution, and no checker/threshold/repair-loop change.

Online repair was not affected by this bug. The corrected offline statuses match all six feedback-arm retained online statuses. Neither the retained selections nor their source/mesh/review hashes were changed by re-evaluation.

## Tests and actual execution

```bash
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q tests/test_fixed_assembly_paired.py
OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 PYTHONPATH=/vepfs_default/chanxueyan/lhp/lms/aDSL /vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -u temp/recheck_assembly_topology_20260922.py --original temp/assembly_topology_paired_fresh_20260921T173631Z --output temp/assembly_topology_offline_recheck_20260922T014722Z
```

- Unit tests: **14 passed, 3.60 s**. Cover actual assembly-directory selection, missing manifest remaining an error, non-mutation of rendering execution/retained/source, and independent image assessment after checker failure. Test geometry and model responses are mocked.
- Actual recheck: **12 jobs finished, exit code 0, 198.67 s**. Original timeout/configuration unchanged. No manifest FileNotFoundError remains in outer checker logs.
- New generations/repairs/API calls: **0 / 0 / 0**. Original records and selections verified unchanged. Incomplete geometry measurements are still INDETERMINATE, not PASS or confirmed disconnection.
- Outputs: [corrected results CSV](../temp/assembly_topology_offline_recheck_20260922T014722Z/case_results.csv), [metrics](../temp/assembly_topology_offline_recheck_20260922T014722Z/metrics.json), [completion evidence](../temp/assembly_topology_offline_recheck_20260922T014722Z/completed.json).
- Each `<case>_<arm>/` contains selection hashes, evaluation.json, and raw checker reports. `run.json` records the exact patch/configuration. Original `offline/`, case_results and metrics are preserved as historical erroneous evaluations; use the corrected directory for topology comparisons.

## Final retained results

Both arms are independent fresh prompt-to-3D with connectors. `wo` excludes topology feedback; `w` includes it. Image columns are the earlier independent final Image assessment, not generation-time Image/Code arbitration or a human manufacturing verdict.

| Case | wo Image | w Image | wo topology | w topology | Unverified items / selection detail |
|---|---|---|---|---|---|
| SF07 | FAIL | FAIL | INDETERMINATE | PASS | wo interface local query unavailable; w retained attempt_0004 passes geometry but independent Image flags surface artifacts. |
| SF03 | FAIL | FAIL | INDETERMINATE | INDETERMINATE | wo legs open and backrest unmeasurable; w backrest remains unmeasurable, blocking its interface. |
| SF13 | FAIL | FAIL | PASS | INDETERMINATE | w retained original has open shelf/side meshes. Some repair candidates passed topology but not other acceptance conditions, so are not selected here. |
| SF02 | FAIL | FAIL | INDETERMINATE | INDETERMINATE | Both selected originals have open backrest/leg meshes and blocked dependent interfaces. w had topology-PASS repair candidates, but no jointly accepted version. |
| SF10 | FAIL | FAIL | INDETERMINATE | INDETERMINATE | Underframe not measurable; dependent interface unverified. w original diagnostic rendering also omits mesh nodes. |
| SF16 | FAIL | PASS | PASS | PASS | w retained attempt_0001 passes both final measurements; wo Image flags the requested back-panel slant. |

| Metric | wo | w | Difference |
|---|---|---|---|
| Topology PASS | 2/6 (33.3%) | 2/6 (33.3%) | 0 percentage points |
| Independent Image PASS, reused | 0/6 (0%) | 1/6 (16.7%) | +16.7 percentage points |
| Both PASS | 0/6 (0%) | 1/6 (16.7%) | +16.7 percentage points; SF16 only |

Each arm has four topology-INDETERMINATE assets and zero confirmed whole-checker FAILs. The denominator above is all six selected cases, not only measurable survivors. There is no net topology pass-count gain in this batch. These small independent generations do not establish general superiority, and topology PASS is not proof of insertion, retention, strength, or successful printing.

## Remaining issues / unchanged boundaries

- Open/unmeasurable meshes and local interface-query limitations remain; this patch only fixes the offline input path.
- Generation-time Image/Code disagreements and failed repair candidates remain intact. No rejected candidate was selected retrospectively.
- No FEA, standing, overhang, legacy topology, new modeling backend, or new optimization budget was enabled.
