# Independent Standing observation and physical reporting

Protocol: `independent_contact_warning_v1`.

This protocol changes Standing verdicts; it is not equivalent to the previous
initial-depth gate. Implementation and test text are provided; validation has
not been executed for this change.

## Online results

Topology and Standing run independently in the existing sequential checker
batch. Topology's source/manifest-bound cached solids remain reusable, but its
overall status and interface acceptance do not authorize or prevent simulation.

Standing still requires one connected material volume per rigid print body,
complete declared bodies, valid mass/inertia, finite rigid transforms and
connection parameters, and a supported collision representation. Negative-fit
elastic press fits remain unsupported. CoACD proxy quality validation remains.
Removing interface acceptance does not weld disconnected material or waive
collision representation checks.

| Observation | Standing status |
| --- | --- |
| Complete observation, no tipping or interface exit | PASS |
| Complete observation with tipping and/or an exit at any time | FAIL |
| Missing input, unsupported representation or incomplete observation | Existing INDETERMINATE/ERROR |
| Only initial depth exceeds shell-plus-precision reference | No status change; WARNING |

The existing thresholds, observation duration, timestep, friction, collision
radius and gravity remain unchanged. Peak tilt and final tilt are different
measurements. An interface that exits then returns still counts as an exit.

New `metrics.standing_observation` contains `status` (PASS/FAIL/UNAVAILABLE),
`observed_tipping`, `observed_interface_exit`, `peak_tilt_deg`,
`final_tilt_deg`, and `duration_s`. Unavailable observations use null for all
these measurements, not zero/false. Partial trajectories remain artifacts.

New `metrics.contact_diagnostic` contains `status` (OK/WARNING/UNAVAILABLE),
`code`, `maximum_penetration_mm`, `reference_bound_mm`, and
`affects_standing_verdict=false`. The compatibility field
`initial_contact_check.verified` only means that the old depth reference was
met. It no longer gates the dynamic observation. CoACD does not fabricate the
rigid-flex shell depth diagnostic; it retains its proxy checks.

Every newly executed Standing result, including early failures and transport
timeouts, carries the protocol in `assumptions.standing_protocol`. Existing
historical reports are not upgraded or relabeled.

Declared TabSlot query geometry and relative poses are used to observe exits;
this does not prove the actual tab exists. A model may pass Standing and fail
Topology. Interpenetration may artificially stabilize contact. A Standing pass
does not validate the collision model, prove printability, or override the
required-checker gate on final publication.

## Agent input

`checker_summary` carries the bounded observation/contact blocks, protocol,
original source/manifest hashes, and `result_ref` with `/metrics`.
Contacts and trajectories remain in the report. This summary is counted in the
existing feedback budget and survives the normal projection.

Engineering receives current checker evidence; Coder receives saved feedback.
Code Critic runs before the current round's physics: any available observation
is explicitly previous-candidate evidence with its original source binding.
Warnings introduce no new finding or repair permission. Topology failures,
observed tipping and exits retain their existing repair routes.

## Evaluation interfaces and records

Pure functions are in `benchmark/metrics/physical.py` (the repository uses
`benchmark/`, not `benchmarks/`). They do not invoke a checker or modify reports.

- `classify_standing_record(record, *, protocol_id)`
- `summarize_standing(records, *, protocol_id)`
- `raw_ivr(part_volumes, pair_intersections)`
- `physical_table_row(record, *, protocol_id)`
- `summarize_physical_methods(method_records, *, protocol_id)`

Records contain `case_id`, frozen boolean `applicable`, explicit state
PENDING/RUNNING/TERMINAL, `protocol_id`, raw `checker`, generation status,
source/asset identity, and available physical measurements. Source bindings
must agree when present. A supplied checker without the matching protocol is
rejected rather than interpreted under new rules.

Complete Standing PASS maps to evaluation PASS. A completed failure, terminal
generation failure, timeout, unsupported input, solver error or unavailable
observation maps to evaluation FAIL, with its original observation retained.
Failure reasons include GENERATION_FAILED, INVALID_INPUT, UNSUPPORTED_INPUT,
SIMULATION_ERROR, TIMEOUT, OBSERVATION_UNAVAILABLE, TIPPING, INTERFACE_EXIT and
UNKNOWN. Multiple reasons may coexist. Errors are not counted as observed
tipping. Pending work is PENDING; a summary containing pending work is
INCOMPLETE and does not publish a completed success rate.

An outer evaluation timeout does not erase a Standing observation already
completed before a later rendering/printing measurement timeout. If Standing
has not completed, the terminal record reports failure with the known reason.
This statistical classification never becomes a new Agent finding.

## Main table

| Field | Definition |
| --- | --- |
| Standing success rate ↑ | Complete observation PASS / all frozen applicable cases |
| Raw IVR ↓ | Sum of all unordered final-part pair intersections / sum of final-part volumes |
| NSV ↓ | Existing ray support-volume proxy: sum S / sum V |
| N | Actual final print-part count |

Raw IVR includes connector material and does not subtract allowed interference.
Single-part IVR is not applicable. All expected pair measurements must exist;
missing measurements stay null. A negative intersection bounded by the
producer's existing numerical volume tolerance is treated as zero in the
statistic; the signed raw evidence is preserved. No new Topology tolerance is
introduced.

The common evaluator retains its existing 150-mm task scale and 24 axis-aligned
orientation policy. It calls the existing `support_volume()` once at the
already G-selected print pose of each final part. This is not an NSV-minimizing
orientation search or a slicer measurement. An unavailable part makes aggregate
NSV unavailable; G and area are not substituted for S.

All methods use the same frozen cases and applicability, including terminal
failures. IVR, NSV and N means report their respective common measurable case
lists. NSV comparison also requires matching measurement configuration hashes.
The main table has no separate validity/coverage columns. The accompanying
`physical_metrics.json` retains per-case raw statuses, missing reasons,
measurement counts, failed/pending cases and the exact sample lists. No
method-specific result-based case deletion is performed.

`experiments/benchmark_six/evaluate.py` saves records and per-case main metrics;
`build_review.py` writes the primary table and evidence appendix. Legacy
evaluations without this record/protocol are rejected for mixed aggregation.
The new pure metrics source is included in newly frozen source fingerprints;
existing batches and their archives are not migrated by this change.

## Validation state

测试已编写，尚未运行；仿真、模型、诊断及 benchmark 均未执行。
新协议输出的是规定仿真条件下的站立观察，不证明接触模型或实际制造完全正确。
