# Six-case request-error isolation and current SSH API check

Base runner: `b3180a0a42f1e8817868b27de98c06b8d3de837c`, branch `feat/benchmark-six-method-comparison`.

The original official lamp Planner failed with HTTP408 before a plan or initial source existed. The new launcher had classified every `APIError` as a shared fault, overriding the earlier single-case isolation policy.

## Change

- `experiments/benchmark_six/run.py`: request API errors (408, temporary429/5xx, connection/timeouts) retain diagnostics and finish only the affected job after native finite retries. Explicit authentication, permission/account, frozen input/version, import/disk and confirmed infrastructure errors still pause.
- A generation without usable native output records `API_INTERRUPTED`, unknown usage for failed calls and no automatic replay. An interruption caught by the native workflow does not replace its usable returned selection. Common final image-review interruptions record `INDETERMINATE` and continue.
- `evaluate.py`: API interruptions retain the case denominator and unknown measurements; they are not represented as measured geometric or appearance failures. README documents this behavior.
- The two directed test files cover actual SDK exception classes, request-worker and final-review paths, retained output, and a twelve-job fake batch that proceeds after one408.

## Validation

`/tmp/adsl_six_envs_20261006T174926Z/ours/bin/python -m pytest -q -p no:cacheprovider tests/test_benchmark_six_runner.py tests/test_benchmark_six_evaluation.py`

**43 passed in 7.92s**. `git diff --check` passed. Language-model workflow fixtures are mocked; existing small geometry/topology tests use real native libraries/subprocesses.

## Real API checks — 2026-10-07 UTC

Same SSH endpoint `localhost:28317`, personal credential and `gpt-6-sol`:

| Check | Result | Seconds |
|---|---|---:|
| Model list | HTTP200; target model listed | 0.514 |
| Short text | HTTP200/completed/OK | 8.610 |
| Original complete image Planner | HTTP408; stream closed before response.completed | 53.742 |
| Minimal request with the same image | HTTP200/completed/Floor lamp | 9.726 |

The complete Planner probe reused the archived failed instructions/input and official `ObjectPlan` schema in an independent diagnostic session. It did not invoke Coder or create a benchmark source candidate. Diagnostic retries were0, with each local HTTP request recorded; production remains timeout900/max_retries2. Three new model POSTs and one model-list GET; known successful usage944tokens, failed Planner usage unknown.

The service is reachable, authenticates and handles small text/image requests. Full Planner requests are still not reliably completing. This evidence does not locate the interrupted stream to the gateway versus its upstream provider, or prove every other case will fail.

Evidence: `/jiigan-hp/lms/aDSL/experiment/benchmark_six_20261006T174926Z/diagnostics/api_20261007T024355Z/{README.md,summary.json,planner_input.json,minimal_image_input.json}`.

The original frozen batch, failure records and profiles were not rewritten or restarted. Its original code snapshots remain available; changing the launcher does not silently invalidate or bypass its frozen fingerprint.
