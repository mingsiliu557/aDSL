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

The current offline main table uses **Standing success rate, structural
connectivity rate, NSV24 and N**. The full definitions, fixed-denominator rules,
geometry adapters, configuration identities, input examples and exact NSV/N
common-sample note are in [benchmark metrics](../benchmark/metrics/README.md).

Raw IVR and its original evidence remain available for separate multipart
analysis; single-part IVR N/A no longer filters any main-table metric. Existing
`record.support` is not promoted into `support_evaluation`: the new
`support_volume_min24_v1` protocol requires a complete offline 24-orientation
S search. Missing geometry measurements stay null/unsuccessful as appropriate.

Standing classification and the online final acceptance gate are unchanged.
The new geometry interface does not run Standing or alter its body partition.
New offline outputs must be written separately from historical records.

## Validation state

定向测试和相关回归已运行；已有雪人候选已在隔离副本中完成真实物理复测。
具体测试范围、结果、支撑朝向协议变化及仍存在的参考网格错误见
[验证报告](../reports/ivr_nsv_pose_validation_20261021.md)。
未调用模型、重新生成源码或运行整批 benchmark。
新协议输出的是规定仿真条件下的站立观察，不证明接触模型或实际制造完全正确。
