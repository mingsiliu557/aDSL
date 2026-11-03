# Single print mode implementation

## Baseline and scope

The current image-only branch and tracked material-cavity fixes were preserved in
`c5ef6a1`, then merged into `action_v2` at `b680aaa` (previous tip `fe70c1b`).
The feature commit containing this report implements the print-mode v2 plan on
that merged baseline. Experimental assets and unrelated untracked files were not
included. No benchmark was resumed.

## Changes

- `adsl-agents/models.py`: optional mode and same-model configuration conflict.
- `adsl-agents/service.py`: cross-configuration checks, narrowly scoped one-retry
  Planner validation, saved-plan validation, and early run-manifest mode.
- `adsl-agents/fixed_assembly.py`: mode normalization, fixed root identity,
  metric-independent candidate checks, implementation repair, final validation,
  hash-compatible split configuration and retained-version publication.
- `adsl-agents/assembly_overhang.py`: independent single-part support search and
  layout; shared existing recommended-STL/region export block.
- `adsl-agents/prompts.py`, `assembly_topology.py`, `repair_controller.py`:
  single-mode geometry-only instructions and action rejection at existing gates.
- `experiments/densicrafter_image_only/workflow.py`: the existing alternate
  Planner entry applies the same narrow retry before freezing its planned size.
- `tests/test_single_print_mode.py`: stub workflow, publication, configuration,
  action, Planner, resume, hash and support acceptance tests.

Five requested functions:

| File | Function | Line |
|---|---|---:|
| adsl-agents/fixed_assembly.py | print_mode | 33 |
| adsl-agents/fixed_assembly.py | check_single_plan | 42 |
| adsl-agents/fixed_assembly.py | single_part_check | 57 |
| adsl-agents/fixed_assembly.py | publish_single_print | 79 |
| adsl-agents/assembly_overhang.py | single_orientation_measurement | 142 |

## Validation

Final targeted run: **258 passed**. Command (use the existing project Python):

```sh
python -m pytest -q tests/test_single_print_mode.py tests/test_support_workflow.py tests/test_support_overhang.py tests/test_actions.py tests/test_densicrafter_image_only.py tests/test_fixed_assembly_prompt.py tests/test_partition_feedback.py::test_selected_check_order_and_topology_fail_does_not_gate tests/test_partition_feedback.py::test_split_ownership_root_and_merge_connectivity_guards tests/test_partition_feedback.py::test_semantic_instance_grouping_and_initial_action_guard tests/test_partition_feedback.py::test_engineering_receives_api_reference_without_planner_assignment tests/test_partition_selection.py::test_grouping_realization_contract tests/test_partition_selection.py::test_grouping_realization_stale_manifest_is_unavailable
```

Additional existing controller/recovery/snapshot regression: **23 passed,
33 deselected**; deselection excludes unrelated/native execution tests:

```sh
python -m pytest -q tests/test_fixed_assembly.py tests/test_fixed_assembly_recovery.py tests/test_fixed_assembly_snapshot.py -k 'old_schema or actual_planner or geometry_timeout or empty_source_resume or plan_errors or mixed_mode or rejected_geometry or successful_candidate or no_change_and_errors or changed_frozen or snapshot or backup_warning or initial_execution_error or candidate_execution_error or initial_geometry or source_evaluation'
```

A static comparison also verified 24 default split prompt combinations equal to
the prior `fe70c1b` implementation. `git diff --check` passed.

Validation limitation: an earlier overly broad test selection accidentally
entered a native artificial-geometry partition fixture. It was interrupted
(exit 143), and is not counted as a passing regression. The final commands above
use the selected stub/logic paths. No model calls, real-case generation, Standing
simulation, rendering or benchmark were run. Full native partition regression
was not completed; no manufacturing improvement is claimed.
