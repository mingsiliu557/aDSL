# v6 Task B: independent material-component Standing

Date: 2026-10-24. Source baseline: `action_v2@c358146b9553b4867db31716d5a8a6d5782580a7`.
Task A is separately committed as `37ddfde2160ac69e2c1fd2fc5f2647a4be154496`; this report accompanies the subsequent Task B commit. The original action_v1 worktree and historical experiment outputs were preserved. No push or merge was performed.

## Implementation and interfaces

| Location | Implemented contract |
| --- | --- |
| `adsl-core/core/assembly_topology.py:29` | `material_components(solid)` retains cavity ownership and material reconstruction. Its algorithm AST matches the former evaluator helper after excluding its now-unnecessary self-import. |
| `benchmark/metrics/geometry.py:289` | The existing private helper is a thin wrapper; connectivity protocol, thresholds and graph verdict are unchanged. |
| `adsl-agents/assembly_standing.py:83` | `_prepare_simulation_bodies(report, meshes)` constructs cavity-aware independent bodies, deterministic collision-free IDs, original-part mapping, largest-net-material root and interface ownership. |
| Same file, `:229` | `_body_mapping` serializes identity, ownership, volumes, transforms, root/interface evidence and actual body/joint addresses without meshes. |
| Same file, `:317`, `:564`, `:741` | `xml_model`, `simulate`, `save_frames` require keyword-only `body_layout`; no fallback silently binds disconnected islands together. |
| Same file, `:375` | Initial body mass/world inertial centres are aggregated per original part before the unchanged root-moment diagnostic. Singleton centres preserve their original values exactly. |
| Same file, `:715`, `:780` | Event-order filtering precedes per-part finding aggregation. Unfiltered observations decide FAIL/PASS/unavailable; incomplete observations do not erase confirmed failures. |
| `adsl-agents/agent_feedback.py:121` and its domain whitelist | Three component scalars and bounded event fields reach Engineering/Coder/Code Critic; component arrays and trajectories remain in files and the 96 KiB budget is unchanged. |
| `benchmark/metrics/physical.py:34`, current physical config | New protocol, component completeness/configuration checks, contradictory flag/peak rejection and retained-finding-only component reason mapping. |
| `docs/standing_evaluation.md`, benchmark metrics README | Updated modeling, reporting, limitations and historical-protocol boundaries. |

The protocol is `material_component_standing_v1`. Only otherwise-valid material rows whose sole failure is `INTERNAL_PART_DISCONNECTED` are allowed through to component preparation. Missing/invalid material, mass/inertia, finite transforms, unsupported negative-fit press fits and proxy-quality conditions remain input restrictions. Topology results and initial contact-depth warnings do not gate Standing. The existing required-checker publication/acceptance rules were not relaxed; the workflow controller, Action API and prompts were not modified.

Components keep original part-local millimetre coordinates and assembly transforms. N remains the original print-part count. No cross-part union, delta welding, geometry repair or interface insertion is introduced. Multi-component endpoints use the existing tab intersection volume bound and four outside receiver-wall bands. Unique ownership binds a body; zero/multiple ownership remains unavailable/ambiguous. Known interface-query failures are isolated to the affected interface and recorded, while TypeError/KeyError and other unknown programming errors propagate. Material extraction/physical validation is outside that recovery boundary.

Each body uses its own initial rotation, the original tilt formula and configured threshold. Pure yaw is not tilt; equality at the configured threshold is accepted. Sample times are shared. Completion uses the pre-existing rounded assessment-time convention, avoiding an incomplete result caused solely by floating-point time accumulation. Root observations retain their original meanings. Known later solver errors preserve sampled events and their error codes; unknown errors are not swallowed.

## Output examples and bounded feedback

The following is a deterministic two-body, one-print-part fake-trajectory example, not a generated snowman or a real simulation:

```json
{
  "body_mapping": {
    "part_to_components": {"whole": ["body", "head"]},
    "root_part_id": "whole",
    "root_component_id": "body",
    "interfaces": {}
  },
  "standing_observation": {
    "status": "FAIL",
    "observed_tipping": false,
    "observed_interface_exit": false,
    "peak_tilt_deg": 0.0,
    "final_tilt_deg": 0.0,
    "duration_s": 0.04,
    "component_tilt_verified": true,
    "observed_component_tipping": true,
    "max_component_tilt_deg": 90.0
  },
  "retained_component_finding": {
    "rule_id": "SELF_WEIGHT_COMPONENT_TIPPING",
    "part_ids": ["whole"],
    "component_id": "head",
    "first_tipping_seconds": 0.02,
    "event_order": "ROOT_NOT_OBSERVED"
  }
}
```

The finding example abbreviates the existing CheckerFinding schema: its actual original-part IDs are in `region.part_names` and scalar evidence is in `domain`. Root tipping retains its original finding ID and localization. Findings are filtered using strict first-event ordering, then the largest retained peak per original part is selected. Same-frame/later events do not duplicate root failure feedback; unknown ordering is retained as UNORDERED. The full raw component table and maximum remain unchanged by this filtering. Earlier events are not claimed to be causal.

The shared whitelist also adds the already-measured root `key_values.peak_tilt_deg`; the existing localization boundary test explicitly checks its original 33.5-degree value and repeated projection. Component failure findings are physical_violation/geometry findings with original print-part names and use the existing SourceIndex and repair routes.

## Validation actually executed

All seven inspected module origins resolved to this action_v2 worktree, using the existing private import overlay rather than changing shared editable installs. Local identity evidence is in `local_experiment/v6_implementation/import_origins.json`.

Final command (existing project interpreter substituted for `<project-python>`):

```bash
PYTHONPATH="$PWD/local_experiment/standing_localization_v2/import_overlay" \
PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 OPENAI_AGENTS_DISABLE_TRACING=1 \
ADSL_SESSION_ROOT="$PWD/local_experiment/v6_implementation/sessions" \
ADSL_TEST_RIGID_FLEX=0 ADSL_TEST_FIXED_REAL=0 \
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 \
<project-python> -m pytest -p no:cacheprovider -q \
  tests/test_benchmark_six_review_connectivity.py \
  tests/test_benchmark_six_evaluation.py tests/test_benchmark_six_runner.py \
  tests/test_benchmark_geometry_metrics.py tests/test_physical_metrics.py \
  tests/test_agent_feedback.py tests/test_assembly_standing.py \
  tests/test_assembly_standing_verdict.py tests/test_standing_tipping_localization.py \
  tests/test_fixed_assembly_multi_mate.py tests/test_actions.py \
  -k 'not real_solver and not real_rigid_flex and not test_real_four_part and not test_real_same_pair'
```

**Final result: 544 passed, 7 deselected in 79.44 seconds; exit code 0.** The seven deselections are native solver/rigid-flex/real-object checks. There are no test failures. `git diff --check` passed. The log is `local_experiment/v6_implementation/final_regression.log`. Earlier stage results are diagnostic runs and are not added to this total.

Coverage includes cavity-preserving decomposition, independent dual-backend XML/proxy/body mapping, query ownership and query-error isolation, largest-net-volume root, nonsequential joint addresses, original root localization, an upright root with a rolling loose component, threshold/yaw/recovery boundaries, raw-versus-filtered event evidence, known failures followed by incomplete observation/solver errors, unknown-program-error propagation, metrics/protocol validation, source-bound bounded feedback and projection idempotence, and both real consumer entry points under stubs. Task A's report invariants also pass under new-protocol fixtures.

An initial full run passed 536 tests before adding eight interface-query isolation cases. During validation, the default execution sandbox prevented reliable local asyncio thread wakeups: a lambda-only thread test reproduced the wait, while the same test with local IPC permitted completed immediately. Final SDK tool-boundary tests used that execution permission with tracing disabled and fake model runtimes. No SDK/product workaround or live model call was introduced. Historical default session-root paths were overridden only in the test environment.

## Independent diagnostics and remaining boundary

1. New report wiring is verified independently from Standing protocol changes; historical connectivity-only outputs are not imported into job reports.
2. Cavity-aware component extraction was moved without changing its algorithm or existing connectivity verdict rules. Whole-object/part source and cached input identity checks remain.
3. The fake runtime confirms component findings reach the existing Engineering/Coder repair input and the final evaluator receives the same checker result. This is a wiring/verdict validation, not evidence that a model can repair a real asset.
4. All-component observations do not detect displacement-only loss without excessive tilt or declared interface exit. Contact warnings and collision-model limitations remain. Old root-only reports cannot be relabeled as this protocol or mixed into a new-protocol table.

No real MuJoCo simulation, native smoke, model call, generated case, GPU computation, rendering, FEA, support orientation search on historical assets or benchmark reassessment was executed. Artificial material/FCL/support fixtures in offline unit tests run on CPU; this scope has no existing GPU implementation to accelerate. Previously requested GPU allocations were not modified by this implementation.

A future all-method frozen-asset Standing remeasurement and any SNAP3D fixed-ground comparison require separate authorization and independent output directories. Existing 33/53-case results were neither overwritten nor claimed to improve. No manufacturing-performance conclusion is drawn from these tests.
