# Default exterior backend: Manifold

New overhang measurements and newly prepared paired experiments default to
`manifold_union` (Python `manifold3d==3.5.2`, optional dependency
`pip install -e '.[overhang]'`). The renderer and aDSL model generator are unchanged.
Existing configurations explicitly specifying `blender_exact_union` retain the
Blender path. Backend/version are part of frozen measurement conditions: do not
compare a new Manifold candidate with a saved Blender baseline. Use a new baseline
and output directory for a new experiment, without overwriting old results.

The detector uses original transformed shell vertices through Mesh64, checks each
operand's Manifold status, and unions with batch Boolean Add in the existing timed
subprocess (default 300 seconds). No welding, hole filling, tolerance relaxation,
small-part deletion, or automatic backend retry. Input status is saved in
`exterior_input_acceptance.json`; failure remains unavailable measurement, not zero
area. Existing finite-coordinate, positive-area, volume/winding checks remain.
Image/Code Critic, candidate acceptance, protection, rendering and other physical
checkers are unchanged.

## Evidence and limitation (2026-09-14)

O03 candidate's 64 saved input shells and original's 60 shells were all accepted
without welding. Candidate Blender output had 2 zero-area triangles; Manifold had
0. Wall time was 2.723 vs 1.022 seconds; original 1.671 vs 0.978 seconds.
However, original overhang area was 18438.23 mm² (Blender) vs 19115.55 mm²
(Manifold), and sampled reverse boundary discrepancy reached 7.168 mm despite
almost identical bounding boxes and volumes. Very small positive-area faces remain.
Thus default selection is a workflow choice, **not a proof that Manifold's exterior
or area is more accurate**. Candidate Manifold area 19099.35 mm² is only 16.20 mm²
lower than original, below the combined existing uncertainty bound; no reliable
improvement was established. Both still generate slicer support.

Do not hide cross-backend disagreement or infer printability from a closed mesh.
Local full evidence remains in `local_experiment/manifold_exterior_20260914/`.
The mesh status guarantee is not a certified external-boundary equivalence test.

Small validation: `pytest -q tests/test_overhang_manifold.py
tests/test_support_requirement_critical_surfaces.py tests/test_overhang_local_edit.py`.
Tests cover overlap union, a preserved gap, rejected open input, subprocess default,
and frozen backend mismatch. No API or batch experiment is required.
