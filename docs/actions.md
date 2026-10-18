# Internal Review / Edit Actions

`adsl.agents.actions` provides explicit keyword-only entrypoints for the existing
fixed-assembly workflow. These are internal Python calls, not new LLM tools or
planning decisions. Planner, Engineering proposals, model prompts, repair budgets,
checker results and candidate selection retain their existing contracts.

| Domain | Review | Edit |
| --- | --- | --- |
| Appearance and shape | Existing Image and Code Critics | Existing Coder feedback repair |
| Topology and connections | `assembly_topology` | Existing connection/geometry proposal |
| Standing | `assembly_standing` | Existing support/structure proposal |
| Overhang and grouping | `assembly_overhang` and existing comparison | Existing orientation, grouping or local geometry proposal |

## Execution entrypoints

- `review_image(workflow, *, ...)` calls `_review_generation_image` once.
- `review_code(workflow, *, ...)` calls `_review_generation_code` once.
- `edit(workflow, *, ...)` calls `_repair` once, preserving `reserved_attempt_id=None`
  and `allow_no_change=False` defaults.
- `review_physics(specs, *, execution, source, root, physics, domains=None,
  initial_topology_run=None, partition_reference=None)` calls `run_assembly_checks`
  once. Complete explicit signatures are defined in `actions.py`; no entrypoint
  accepts arbitrary keyword arguments.

The async entrypoints return the original object/None and propagate the original
exception. They neither copy payloads nor write records, reserve attempts, execute
candidates, retry or decide acceptance. The existing controller retains those duties.

`domains=None` passes the original specs unchanged, including legacy FEA selections.
An explicit tuple selects existing specs by `topology`, `standing`, `overhang` or
`fea`, mapped to the corresponding `assembly_*` name. Unknown domains/spec names
raise `ValueError` only on this explicit-selection path. Empty tuples select none;
missing domains do not create checkers; repeated domains do not repeat specs.
Filtering retains spec identity and relative order. The original runner still
orders Topology → Overhang → Standing → FEA and owns timeout/cache behavior.

The fixed-assembly main batch uses `domains=None`; its Overhang-only refresh uses
`domains=('overhang',)`. This preserves the previously selected checker set. FEA
compatibility is not an instruction to enable FEA.

## Existing rule entrypoints

- `regroup_precheck(proposal, *, purpose, continuing=False, assembly=None,
  editable=False)` owns the former `validate_partition_proposal` implementation.
  `assembly_topology.validate_partition_proposal` and `InvalidPartitionProposal`
  remain aliases to the same function/class.
- `reorient_postcheck(before_text, after_text)` delegates to the existing
  `orientation_only` predicate. The controller retains the conditional flag,
  rejection record and stop reason. Its separate scoring use of `orientation_only`
  is unchanged.
- `regroup_realization(intent, baseline_assembly, current_assembly, *, source_sha256)`
  owns the former declaration comparison. `fixed_assembly._grouping_realization`
  remains an alias. MATCH proves only declaration agreement, not physical separation
  or interface validity; None/UNAVAILABLE/MISMATCH retain their original meanings.

No new result models, manifest fields, retry policies, repair permissions or geometry
algorithms are introduced. Physics/orientation adapters are imported inside their
entrypoints to avoid new circular dependencies or eager native imports.

## Offline verification

`tests/test_actions.py` covers signatures, identity/exception passthrough, domain
selection, aliases, cached Topology, Overhang-only refresh, controller routing and
orientation rejection. Models, executors and physical processes are stubbed. Related
regressions must be selected explicitly: the existing physics/partition test files
also contain native tests, which are outside this refactor's validation boundary.
