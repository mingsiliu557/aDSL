# Offline feedback evidence

Four `captured_*.json` files contain the actual stub-runtime inputs produced by
Image Critic, Code Critic, Engineering and Coder adapters. No model call occurred.
`capture_evidence.json` records text UTF-8 byte counts and a real read_file JSON
pointer response. Numbers marked valid in this synthetic diagnostic are fixture
values, not a native geometry measurement.

Unzip `synthetic_original_evidence.zip` into a temporary workspace to inspect the
complete original manifest, evaluation report/result and bound source. Their
workspace-relative paths correspond to the captured inputs; absolute historical
paths inside raw report evidence retain their original test workspace. The exact
pointer tested was `/findings/0/domain/diagnostic/internal_metrics` in
`evaluation_feedback/result.json`. No historical benchmark or object asset is
included. The original full reports were not replaced by summaries.

`terminal_request_evidence.json` extracts persisted bookkeeping from the two
request-rejection stub tests: Engineering stops before reserving a Coder attempt;
an already reserved Coder attempt keeps TOOL_ERROR, request_error and source
hashes. The call-count assertions are in `tests/test_agent_feedback.py`.

`pytest_final.log` and `pytest_runner.log` are original test output logs.
`validation.json` lists separately executed stages; overlapping counts must not
be added. `implementation_evidence.json` binds artifacts to the implementation
commit and records the actual imported module roots.
