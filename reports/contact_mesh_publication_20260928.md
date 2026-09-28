# Publish SF13 microcrack repair and rigid_flex standing

Date: 2026-09-28. Base commit: `5c885b426b82a0d058b5b7e1d0c7d6de208aac81`.

This publication includes the complete existing implementation used for SF13:

- Bounded BMesh boundary welding in the common fixed-part export path, after
  exact-zero tessellation handling. A candidate copy must pass closure,
  manifoldness, winding, connectivity and geometric-displacement checks before
  STL, GLB and downstream checkers receive it; rejected changes roll back.
- Opt-in `standing.collision_backend = "rigid_flex"`: final surface meshes remain
  independent rigid bodies, preserving concave mating geometry without CoACD.
  Initial-contact and simulation-validity checks remain active.
- The standing criterion from `5c885b4`: during the full observation, no sampled
  root tilt above the limit and no interface exit. Settling remains diagnostic.

The default backend is still `coacd`; users select rigid_flex explicitly in the
existing physics configuration. No new collision tuning or mesh edits are added
by this publication. Existing FEA changes, GPU scripts, historical deletions,
large assets and API logs are excluded.

## Verification

The exact selected files were copied onto clean `5c885b4` in
`/tmp/adsl_contact_mesh_publish_20260928`. Python imports were checked to resolve
there, excluding unrelated dirty-worktree changes. Real CPU regressions use
`ADSL_TEST_FIXED_REAL=1`, `ADSL_TEST_RIGID_FLEX=1`, Blender 4.0.0 and MuJoCo 3.12.0.

Result: **151 passed** in 248.76 seconds. One pytest plugin rewrite warning; no failures or skips.

Log: `/tmp/adsl_contact_mesh_publish_evidence_20260928/tests.log`.
Coverage includes local welding and rollback, preserved export/checker geometry,
zero-area normalization, real multi-interface assemblies, standing/exit/tipping
controls, initial-contact invalidity, and the new standing verdict.

## SF13 evidence carried forward

Implementation SHA256 values were compared before publication. Both mesh export
files exactly match `microcrack_repair_20260928/implementation_sha256.json`; the
standing file exactly matches `standing_criterion_20260928/comparison.json`.
No SF13 generation, parameter tuning or additional Agent calls were needed.

- Top cap: 36 → 0 open edges; maximum movement 0.00000762939453125 mm.
- Fifth shelf: 12 → 0 open edges; maximum movement 0.000003814697265625 mm.
- Topology: all 8 parts and 24 interfaces PASS; 64 export-consistency checks PASS.
- Latest CPU standing recheck: PASS; 251 samples cover all 24 interfaces, no exits;
  peak root tilt 0.156340744°, final 0.004636397°. `settled=false` is retained.
  Motion matches the previous run exactly; the acceptance criterion changed.

Mesh evidence: `local_experiment/sf13_multi_mate_20260928T111227Z/microcrack_repair_20260928/`.
Standing evidence: `temp/sf13_microcrack_standing_20260928/standing_criterion_20260928/`.
The earlier reports' exclusions describe the verdict-only commit `5c885b4`;
this publication supersedes that scope. Old outcomes and local artifacts remain
unchanged; large mesh/trajectory files are not embedded in Git history.
