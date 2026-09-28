# Ordered multi-interface fixed assembly — implementation record

Baseline: `334939cc5ee1ae940d02cff3d043705ae446330f`. Isolated checkout:
`/tmp/adsl_multi_mate_20260928`, branch `feat/fixed-multi-mate`.
All checks use CPU and `/tmp/adsl_multi_mate_python`, with resolved core/agents
module paths verified against this checkout. Original workspace changes are excluded.
Artifacts and full smoke logs: `/tmp/adsl_multi_mate_results_20260928/`.

## Stage 1 — Plan/API

Bidirectional placement and consistency-checked supplemental connections retain the
existing schema. Ports are unique per part and role. Both Boolean trees are built
on copies before committing; validation replays the ordered graph and checks all
saved world frames. Existing matrix tolerance remains `rtol=0, atol=1e-8` in scene
units; translation diagnostics are millimetres and rotation diagnostics degrees.

`python -m pytest -q tests/test_fixed_assembly.py tests/test_fixed_assembly_multi_mate.py -k 'not real_'`:
**63 passed, 6 deselected** (`stage1.log`). These are API/Plan tests, not evaluated
Boolean geometry. Includes forward/reverse/closure failure atomicity, interleaved
connections, same-pair interfaces, corrupted declarations and legacy tree/single-part behavior.

## Stage 2 — Export and feedback

The exporter shares the API world-frame predicate and records both residuals on
mismatch. Four connections survive manifest serialization, plan deltas (three
planned plus one added), generate/resume inputs, review/repair context, and retained
version selection. The initial plan file remains unchanged.

Stage 2 requested files plus `tests/test_fixed_assembly.py`, excluding `real_`:
**101 passed, 8 deselected** (`stage2.log`). Manifest/selection tests use fake mesh
export and are declaration/routing evidence only. The mismatch recovery test runs
an actual source subprocess: `connect()` rejects `interface=second` with 1 mm
translation error before CSG evaluation; `execution_error.json` and repair evidence
carry that diagnostic. The repaired candidate export/review is mocked.

## Stage 3 — Agent instructions and real task entries

Planner/Coder, the Code Critic API reference, both task requirement templates and
example documentation now describe all three successful connect behaviors, full
interface coverage and geometric roles. Frozen historical input bytes are preserved.

Requested prompt/entry tests plus `tests/test_generation_review_contract.py`:
**73 passed** (`stage3.log`). Runtime/docs search found only the existing
`three_parts_prompt.txt` describing its particular tree fixture; it is not a general
API restriction and is unchanged. No model API was called.
