# v6 Task A: review connectivity reporting

Baseline: `c358146b9553b4867db31716d5a8a6d5782580a7` (`action_v2`).

The review builder now uses `admission_record.connectivity` as its sole measurement source. It validates case/method/lifecycle, source and composite-asset bindings, evidence and the shared measurement configuration. The existing GLB identity remains separate. Missing records require reassessment and are not measured failures; pending records remain unfinished. Either condition makes every method connectivity rate null while retaining the frozen applicable denominator and completed evidence. Non-applicable cases do not enter the denominator.

Production changes are confined to `experiments/benchmark_six/build_review.py`. Tests add `tests/test_benchmark_six_review_connectivity.py` and align the existing report fixture in `tests/test_benchmark_six_evaluation.py` with the real result identity fields.

Validation used the existing project environment and the action_v2 private import overlay:

```bash
PYTHONPATH="$PWD/local_experiment/standing_localization_v2/import_overlay" \
PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
OPENAI_AGENTS_DISABLE_TRACING=1 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
<project-python> -m pytest -p no:cacheprovider -q \
  tests/test_benchmark_six_review_connectivity.py \
  tests/test_benchmark_six_evaluation.py \
  -k 'review_connectivity or reporting_uses_all_cases or report_builder_rejects_legacy_protocol or final_evaluator_uses_same_connectivity or final_connectivity'
```

Result: **40 passed, 19 deselected in 2.07 seconds**. Source diff whitespace checks passed. Tests include the actual evaluator result-assembly path under stubs. In the controlled example, the legacy connectivity rate of 0 becomes 1/3 for both methods; each retains one observed graph failure and one unavailable measurement. Standing, NSV24, N, denominators and metric sample lists are exactly unchanged. Input JSON/job/evidence bytes are unchanged, and both output JSON files contain the same connectivity data.

Missing entries have `evaluation_outcome=null` and `REASSESSMENT_REQUIRED`; pending entries are `PENDING`. Markdown shows null as `unknown` with lists and status. The metric note is:

> 几何接触连通率使用 geometric_facing_contact_connectivity_v1：δ=0.005D、相向法向点积<0、双向接近面积取min，面积阈值1.5e-5D²。

This fixes automatic reporting for new evaluator results. Historical 33-case paper data remain on the independent reassessment-output path; this task did not execute reassessment, connectivity-only evaluation, real geometry, MuJoCo, models, rendering or benchmark runs. No push or merge was performed.
