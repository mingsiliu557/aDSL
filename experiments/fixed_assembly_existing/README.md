# Existing-asset fixed assembly validation

Only SF07 → SF03 → SF13 from `adsl_only_historical_8cases_3d_assets_20260915.zip`,
the historical **adsl** arm. Choices precede outcomes and are never substituted.
`prepare.py` archives provenance, source and renders, executes original sources and
freezes dimensions at 40 mm/source unit, with +0.2 mm single-sided clearance.
This is a geometric demonstration, not calibrated physical fixation.

`run.py` is an experiment adapter, not a change to production `edit()`.
The existing Planner receives the full original source, reference renders and
frozen configuration. Existing Coder patch tools perform one initial conversion.
The existing fixed-assembly loop evaluates it and allows **one** repair. Image
Critic always sees original/candidate renders; Code Critic keeps its existing
conditional correction role. No four physical checkers run.

All failures/candidates remain under `CASE/assembly_run`. Its internal `original`
is the first conversion, NOT the historical input. `CASE/original` is the immutable
historical reference. Only an approved inner retained version is published to
`CASE/final`; otherwise final contains the true original and explicitly reports
assembly unverified. A start marker prevents accidental paid replay. New candidates
reuse the existing exporter and 120 s geometry timeout; rendering keeps the existing
300 s executor limit. Closed/connected and final-file equality gates are unchanged.

Commands (from repository root, existing adsl Python on PATH):

```sh
PYTHONPATH=. python experiments/fixed_assembly_existing/prepare.py
bash experiments/cliproxy_session.sh adsl_cliproxy
bash experiments/tmux_session.sh adsl_assembly_existing_20260919 "$PWD" \
  bash -o pipefail -c 'bash experiments/fixed_assembly_existing/launch.sh \
    --root local_experiment/fixed_assembly_existing_20260919 \
    2>&1 | tee local_experiment/fixed_assembly_existing_20260919/run.log'
```

Cross-experiment token accounting and the cumulative 100-million-token gate are
disabled. Old experiment directories and ledgers are not restored or tracked down.
Per-run usage statistics and existing historical results remain preserved; candidate
budgets, API/model settings and timeouts are unchanged.
Start CLIProxy in its own tmux first. This launcher only checks/uses the proxy;
it never starts or stops it, even on failure. Stop explicitly when requested.
The tmux parent remains an interactive shell after exit.

Shared exporter/flow errors pause subsequent cases. Individual rejected geometry or
appearance keeps the original and continues. Failure classification is preliminary:
disconnected geometry may be inherited from the original, not necessarily a new
agent error; inspect source and manifest before assigning a geometric root cause.
No automatic retries, replacements, extra edits, checker or interface changes.

Observed SF13 limitation: execution can fail before a manifest exists, but the
existing loop still supplies its expected path. Reading it aborted the single
repair. The experiment classifier now marks this separately and pauses any later
case; it does NOT repair the production feedback/tool logic or rerun SF13. The
original run record and a post-run audit are both retained. Failed Runner calls can
be absent from usage.jsonl; retain available request-level usage evidence when
reporting this run's total cost.
