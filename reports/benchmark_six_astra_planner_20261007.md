# Six-case Astra configuration and Planner response transport

Date: 2026-10-07 UTC. Worktree: `/tmp/adsl_six_method_comparison_20261006`, branch `feat/benchmark-six-method-comparison`; base `d9bd14036b265fa4acec1cd01d8285890b62a6d4`.

The intended model was `gpt-6-astra`. The first frozen batch incorrectly used `gpt-6-sol` for both methods. This is a configuration mistake; the historical run remains labelled Sol. No completed cases or source candidates existed when its first official Planner failed.

## Configuration correction

- Added `adsl-agents/configs/llm/autodl-personal-gpt-6-astra.yaml`: same SSH endpoint, credential reference, timeout 900 s, retries 2 and output cap 32768; only `params.model` differs from the Sol profile.
- Both-arm preflight rejects a model other than `gpt-6-astra` and records the effective configured model.
- Corrected persistent profiles and environment mapping are separate files under `config/astra_20261007/`. The old `config/envs.json`, profiles, frozen fingerprint, job records and original runner snapshot were not rewritten.

Run root: `/jiigan-hp/lms/aDSL/experiment/benchmark_six_20261006T174926Z`.

## Controlled real Planner requests

Both requests used the archived official lamp Planner instructions and input, the native `ObjectPlan` schema, the same Astra model and SSH API. Each diagnostic allowed one local HTTP POST, client timeout 900 s and retries 0. Neither called Coder, exported geometry or created a benchmark candidate.

| Request | Result | Elapsed |
|---|---|---:|
| Native `Runner.run`, nonstreaming | HTTP 408 / `APIStatusError`; stream closed before `response.completed` | 91.733 s |
| Native `Runner.run_streamed` | HTTP 200, completed response, parsed six-component `ObjectPlan` | 49.823 s |

The successful stream received `response.created` at 2.937 s, first text delta at 10.480 s and `response.completed` at 49.696 s. Known usage: 3220 input + 1307 output = 4527 tokens, including 132 reasoning tokens and 2944 cached input tokens. Failed-call usage is unknown. The successful call had cached input; this single pair does not establish a general latency speedup or prove that nonstreaming always fails.

The complete request had one 512×512 PNG (135543 bytes), 8881 instruction characters and a 709-byte public JSON schema. On the wire the request was approximately 192 KB. No malformed or unusually large input was established.

Evidence directories:

- `diagnostics/astra_planner_20261007T025429Z/`: nonstreaming summary and exact archived input.
- `diagnostics/astra_planner_stream_20261007T025652Z/`: streaming summary, event types/timings, exact input and parsed Plan. Delta and reasoning text are not in the event timing log.

## Timeout finding and bounded correction

The HTTP client really used 900 s for connect/read/write/pool and sent the matching read-timeout header. The remote service returned HTTP 408 before that deadline. Increasing the local wait alone cannot keep waiting after an HTTP error has already arrived. The separate 300 s asset-executor deadline applies after planning.

The port is an SSH TCP forward to the remote HTTP service. SSH keepalives do not set the HTTP request deadline. The existing SSH master accepted forwarding but refused new shell sessions; gateway/provider logs and the precise terminating component could not be obtained. There is no evidence for a specific remote 60/90 s timeout value.

Use the existing official SDK streaming path only for the comparison harness's Planner role, equally for both arms. Keep its input, schema, session, context, maximum turns and generation budgets unchanged. Consume the entire stream, require a completed response and valid final output, then record usage. Partial JSON, a closed stream or an error event must not become a successful plan. Coder, critics, engineering, checkers and production source trees retain their native calls.

Streaming sends output incrementally, but still requires final completion before use. See [official Responses streaming documentation](https://developers.openai.com/api/docs/guides/streaming-responses) and [official Agents SDK running guide](https://developers.openai.com/api/docs/guides/agents/running-agents). This is a supported transport path validated on one full ordinary Planner request. The independent FixedAssembly check below still fails, so it is not a complete service-side fix.

## Validation and remaining boundary

Final directed regression:

`/tmp/adsl_six_envs_20261006T174926Z/ours/bin/python -m pytest -q -p no:cacheprovider tests/test_benchmark_six_runner.py tests/test_benchmark_six_evaluation.py`

**56 passed in 8.22 s**; whitespace check passed. New streaming cases use the real installed Agents SDK with an offline model: typed output/session/usage, partial JSON without completion, provider error codes, incomplete output, cancellation cleanup and unchanged non-Planner calls. Existing batch isolation/evaluation coverage remains included. No live model is used by these tests.

Independent real integration used the corrected six-case `audited_runtime` and native `ObjectWorkflow._plan_generation` for the ours lamp request, with `FixedAssemblyPlan`, the original case/physics configuration and one diagnostic POST/retries 0. It did not run Coder or checkers. Evidence: `diagnostics/astra_ours_harness_20261007T030332Z/`.

- Requested model `gpt-6-astra`, `stream=true`, output cap 32768, timeout 900 s.
- HTTP 200 established the stream; first raw event arrived after 8.103 s.
- At **88.075 s**, the SDK raised `APIError`, code `request_timeout`, because the remote stream closed before `response.completed`. HTTP 200 here does not mean successful completion; there was no subsequent HTTP 408 recorded for this streaming call.
- Audit status ERROR, `response_completed=false`, no output saved, initial-source and repair counters both 0. Failed usage unknown; the existing request-error policy classifies it as an isolated API interruption.
- The ours Planner had 24629 instruction characters versus 8881 for the original official Planner. The independent request uses a different, more extensive native planning contract; its duration is not a controlled latency comparison against the official plan. A read-only prompt-loading audit found no duplicate injection: the +15748 characters are exactly +240 in the Planner template, +6005 in DSL documentation and +9503 for the fixed-assembly contract/newline. DSL and assembly text each appear once. Length alone is not established as the cause of disconnection.

Thus switching transport alone does **not** restore the complete assembly Planner. Both the nonstreaming HTTP 408 and streaming failure occur before the client's 900 s deadline. A remote time limit or upstream disconnection is plausible, but its exact cause/limit is unverified without the inaccessible server logs. Do not declare the API healthy or restart all twelve jobs based on the single ordinary-Plan success. No further live probes were added after the failed assembly check.

This turn made three Astra model POSTs: one ordinary nonstreaming failure, one ordinary streaming success, one assembly streaming failure. Only the successful 4527 tokens are known; two failed-call costs remain unknown.

No twelve-job batch was restarted, no FEA/GPU execution was performed, and failed-call costs were not recorded as zero. The historical paused batch remains preserved; a future launch must explicitly record the corrected model and runner version rather than replay an already-started candidate or reset its frozen budget.
