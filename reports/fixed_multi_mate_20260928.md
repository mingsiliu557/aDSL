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
