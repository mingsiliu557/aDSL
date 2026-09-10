# Engineering checker loop

These adapters expose the existing MuJoCo standing/progressive, CalculiX/Gmsh, and PrusaSlicer analyses to the object-agent workflow through one fail-closed JSON contract.

A checker spec defines only process execution policy. Its analysis config fixes the physical assumptions, loads, boundary conditions, and thresholds. The Coder never receives permission to edit those files.

For an existing asset, use `edit --check-first` so round 1 is an untouched baseline:

```bash
adsl-run edit "Preserve appearance; repair only mandatory checker failures." \
  --source path/to/source.py \
  --output local_experiment/checker_repair \
  --check-first --max-rounds 4 \
  --checker-config experiments/workflow_checkers/specs/fea_chair.json \
  --repair-policy-config experiments/workflow_checkers/repair_policy.json
```

## Topology before FEA

Every asset execution now writes `analysis_geometry.json` beside
`source_index.json`. It preserves analytic primitives, Boolean structure,
semantic part paths, feature IDs, and source spans before GLB/URDF flattening.
The topology checker and FEA use this manifest when it is available.

Use both topology and FEA for a load-bearing chair:

```bash
adsl-run edit "Preserve appearance; repair only mandatory checker failures." \
  --source path/to/source.py \
  --output local_experiment/checker_repair \
  --check-first --max-rounds 4 \
  --checker-config experiments/workflow_checkers/specs/topology_chair.json \
  --checker-config experiments/workflow_checkers/specs/fea_chair.json \
  --repair-policy-config experiments/workflow_checkers/repair_policy.json
```

The `load_path` profile resolves every configured load separately, requires
every final solid entity in each matched load part to reach a lowest support
solid, and retains that entire support-connected component for FEA. The
`one_piece` profile checks connectivity across all final OCC solid entities;
multiple entities inside one semantic part are allowed when other parts connect
them. Point-only contact, edge-only contact, and a positive gap do not count as a
bond. A failure records both nearest semantic endpoints, closest points,
distance, source spans, and their lowest common semantic ancestor as the local
connector scope. FEA remains `INDETERMINATE` until topology passes; a separate
topology FAIL is the source-editable geometry finding.

This checker supports aDSL cube, cylinder, sphere, and the existing
UNION/DIFFERENCE/INTERSECT hierarchy. Unsupported analytic constructs return
`INDETERMINATE`; the system does not infer connectivity from overlapping
AABBs or silently repair a triangle mesh.

Use `specs/progressive.json` for the fixed 1%-height, event-augmented partial-build scan. It is intentionally an unbonded rigid-body stress test, not a deposition simulation; real FFF decisions must also use slicer support/brim paths and a calibrated bed-adhesion model. Repeat `--checker-config` to require multiple gates. A round publishes only after its appearance review and every required checker return `PASS`. `FAIL` or `INDETERMINATE` becomes structured Engineering Critic feedback; `ERROR` stops as infrastructure failure and is never disguised as a geometry defect.

## Feedback protocol and source localization

The adapters emit protocol-v2 `CheckerResult` records while the runner remains
compatible with protocol-v1 checkers. A v1 result is upgraded conservatively:
missing metrics, regions, units, and source locations remain missing rather than
being filled with synthetic zeroes. Each typed finding distinguishes physical
violations, insufficient evidence, missing semantics, and infrastructure errors;
only `geometry` or enabled `design_variable` findings are source-editable.

Every successful asset execution creates `source_index.json`. It combines Python
AST spans with the runtime `Asset` hierarchy and feature AABBs. Literal
`attach_part` names can resolve directly; loops, generated names, unsupported
DSL patterns, and ambiguous overlaps are explicitly `partial` or `unresolved`.
Checker-space points or bounds are matched only when an explicit coordinate
transform is available. AABB overlap is candidate recall evidence, not causal
confidence. Global failures such as tipping return a conservative set of
support/mass-distribution candidates and retain their ambiguity.

## Candidate repair and acceptance

The Engineering Critic returns typed `RepairProposal` values referring only to
finding, feature, and source IDs supplied by localization. The deterministic
controller enforces the JSON repair policy, creates each candidate from the
current baseline, and permits edits only inside the localized top-level class
scopes. It also verifies immutable checker inputs and keeps print orientation
fixed by default.

All registered required checkers are rerun for every executable candidate. A
candidate is accepted only when a target violation becomes `PASS` or improves
beyond its rule tolerance, previously passing gates do not regress, other
comparable violations do not worsen beyond tolerance, and visual/function
review approves it. Metrics are compared only within the same rule and analysis
context; they are never combined into an aggregate score. Rejected candidates
remain on disk and do not overwrite the baseline. Candidate count and elapsed
time are bounded by `repair_policy.json`, while fingerprints prevent retrying an
identical proposal against the same source and checker context.

Each round stores:

- `analysis_context.json`: hashes, coordinate/units metadata, fixed poses, and
  required checker contexts.
- `source_index.json`: runtime features and AST source spans for this source.
- `checkers/<name>/result.json`: protocol result with typed findings.
- `checkers/<name>/raw/`: complete analyzer evidence and solver files.
- `findings.json`: all normalized and localized findings.
- `localization.json`: candidates, methods, evidence, and unresolved reasons.
- `engineering_critique.json`: critic interpretation and human-readable notes.
- `repair_proposals.json`: bounded machine-readable candidate proposals.
- `candidates/<id>/`: isolated source, execution, checker evidence, critic
  evidence, and deterministic `decision.json`.
- `round_XX_source.py`: exact source checked in that round.

The workspace-level `repair_history.jsonl` records fingerprints, decisions, and
rejection reasons across rounds. Published artifacts include the final
`source_index.json` corresponding to the accepted source.

Keep the active workspace, SQLite session database, and other frequent small
writes on the local project disk. Mounted experiment storage can transiently
fail SQLite writes even when large-file access works; copy completed render and
solver artifacts to the data disk for archival instead.

## Known boundaries

The first implementation deliberately does not claim exact face-to-source
provenance, causal diagnosis from an overlap, or complete analysis of arbitrary
dynamic Python. Missing transforms or semantic inputs block automatic repair.
Visual review is supporting evidence rather than a proof of function. Physical
checker PASS remains a result under the recorded proxy assumptions, not print
or safety certification.

The FEA profile is a screening proxy: isotropic PLA, 0.9 m chair height, fully fixed bottom nodes, 1000 N seat load, 300 N back load, linear static and eigenvalue buckling. Passing is not print certification.
