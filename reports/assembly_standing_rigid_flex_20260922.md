# Assembly standing: rigid flex CPU validation

Publication update (2026-09-28): this backend is now included in master alongside
the microcrack repair. The historical checks below predate commit `5c885b4`, which
made settling diagnostic only. Initial-contact validity still gates acceptance.
See [current publication verification](contact_mesh_publication_20260928.md).
The old `temp/standing_rigid_flex_20260922/` artifacts referenced below were removed
during the user-requested temp cleanup; current controls are recorded separately.

## Scope and decision

- Baseline master: `54e7df1470c9829c5645d73067d39d3c75868ad1`; installed MuJoCo **3.12.0**.
- Added opt-in `standing.collision_backend = "rigid_flex"`. Default remains `coacd` for now; there is **no automatic fallback** between backends. Existing experiment configurations/results are not rewritten.
- Only standing collision representation, its diagnostic validity handling, and targeted tests changed. No changes to FEA, overhang, topology, connector/source geometry, agent review, density/friction/duration or the 25-degree/settling thresholds.
- No generation API, GPU, tetrahedralization, mesh repair or new dependency installation. All five real checks completed a full **5 seconds of gravity-only simulation** on CPU.
- The CoACD preprocessing bottleneck is absent on this path. A successful run does not itself mean stability PASS. SF03 remains **INDETERMINATE**; see its initial-intersection evidence below.

## Representation and contact thickness

Each final local-mm surface mesh is converted to metres and assigned to one `dim="2"` flex. Its single `body` name binds every vertex rigidly to that part's independent freejoint body. Explicit original-material mass/COM/inertia and existing assembly transforms are retained. No welds or fixed parts other than the floor. Display meshes have `contype=conaffinity=0`; flex contacts have both masks enabled, `internal=false`, `selfcollide=none`, zero margin and gap.

Radius is explicit: `min(0.01 mm, minimum positive single-sided fit / 20)` unless configured. For these 0.2-mm clearance inputs, each shell radius is **0.01 mm**; together they occupy **0.02 mm**, leaving **0.18 mm** lateral clearance. Zero/negative clearance or radii consuming the fit are unsupported, not silently repaired. Initial touching shoulder/floor surfaces do have the expected small shell overlap; contact logs preserve this.

CoACD and its convex-proxy equivalence checks are skipped only for rigid flex. Existing final-solid input validity checks still apply. Original float64 vertices/faces feed the flex without simplification or tetrahedra. Rendering remains based on the original surface at simulated rigid body poses.

Initial penetration beyond paired shell radii plus the **existing** mesh numerical bound invalidates the dynamic verdict (`INITIAL_CONTACT_INTERPENETRATION`). The raw trajectory is retained, but it does not generate tipping/exit geometry-repair findings. This is an initial-contact validity check, not a new topology rule or a modification of the standing thresholds. Known MuJoCo execution errors/nonfinite state/time reset remain unverified through the existing failure protocol.

Official basis: [MuJoCo modeling: rigid flex](https://mujoco.readthedocs.io/en/stable/modeling.html#deformable-objects) and [flex XML: body/radius/contact](https://mujoco.readthedocs.io/en/stable/XMLreference.html#deformable-flex). Rigid surface contact is not connector strength, insertion-path or manufacturing validation.

## Final verification results

| Input | Verdict | Observed behavior | Native simulation wall time | Timeout |
| --- | --- | --- | ---: | ---: |
| Existing seated two-part fixture | PASS | No tip/exit; settled; peak tilt 0° | 0.706 s | 120 s |
| Existing gravity-exit fixture | FAIL | Exit at 0.060 s, still outside at 5 s; not confused with assembly common motion | 0.333 s | 120 s |
| Existing narrow-block tipping control | FAIL | Peak relative tilt 74.451° > 25° | 0.216 s | 120 s |
| Saved T bracket | PASS | Peak 0.001161°; no exit; settled | 0.875 s | 120 s |
| Saved SF03 initial full-size asset | INDETERMINATE | Initial deep material intersection; observed peak 1.292°, no exit, still moving at 5 s; trajectory diagnostic only | 6.723 s | 300 s |

Saved-asset full subprocess costs (imports, input validation, simulation and figures): **T bracket 9.69 s, SF03 15.40 s**. `saved_final/summary.json` records these separately from native simulation time. Process-group termination uses the existing checker executor; budgets were not extended.

### SF03 limitation: pre-existing geometry intersection, not established flex convexification

SF03 source SHA256: `6d108e45e57275ee2e647dc15f316daa6f4a46893a5177bf0306e4e5b3abb5bc`.

- Initial deepest contact: `seat_base` / `leg_front_left`, about **−30.2497 mm** (far beyond 0.02-mm shell thickness). Initial contact file includes part IDs, triangle element IDs and world-mm positions. There are similar seat/leg contacts, but only this worst pair was independently checked.
- Read-only intersection of these same saved final solids, placed by their recorded assembly transforms: **141930.8602 mm³**. Intersection bounds: X `[-207.5,-152.5]`, Y `[-177.5,-122.5]`, Z `[359,420]` mm. This confirms initial material overlap in the asset; it is not merely a thin-shell effect. No source or mesh was changed.
- The dynamics still ran to 5 s for diagnostic recording. Final maximum linear/angular speed: **0.011529 m/s / 0.050667 rad/s**, above the unchanged **0.001 m/s / 0.01 rad/s** settling limits. This does not establish whether physical motion versus numerical contact response dominates.
- Do not report the absence of a >25° tip as stable PASS. Do not turn this standing diagnostic into an automatic instruction to thicken or resize the connector.

**Default decision:** leave rigid flex opt-in for this delivery. Small controls and the T bracket validate the route, but SF03 has an unsuitable initially interpenetrating contact state. No alternative engine, contact-parameter tuning, longer simulation, or geometry repair was attempted. This restriction does not mean rigid flex cannot handle real assets; SF03's contact calculation now finishes promptly and exposes a concrete input limitation.

## Files and artifacts

- Implementation: `adsl-agents/assembly_standing.py`.
- Targeted tests: `tests/test_assembly_standing.py`.
- All diagnostics: `temp/standing_rigid_flex_20260922/` (new directory, old experiments preserved).
- Small native controls: `controls_final/test_real_rigid_flex_three_con0/{seated,exit,tipping}/results/checkers/assembly_standing/`.
- Saved assets: `saved_final/{t_bracket,SF03}/checkers/assembly_standing/`.
- Each run saves `model.xml`, lossless local solids, `rigid_flex_contact.json`, `initial_contacts.json`, `final_contacts.json`, `trajectory.json/.npz`, keyframe PNGs, `result.json`, stdout/stderr and stage progress. Saved-asset `input.json` binds original source/manifest hashes, implementation hash, configuration, timeout and CPU execution; hashes checked again after evaluation.
- SF03 [initial frame](../temp/standing_rigid_flex_20260922/saved_final/SF03/checkers/assembly_standing/simulation_0000.png) / [5-second frame](../temp/standing_rigid_flex_20260922/saved_final/SF03/checkers/assembly_standing/simulation_0250.png).
- T bracket [5-second frame](../temp/standing_rigid_flex_20260922/saved_final/t_bracket/checkers/assembly_standing/simulation_0250.png).
- `controls_v2` / `saved_v1` retain the first successful measurement before the explicit initial-contact validity guard; final classification uses `controls_final` / `saved_final`.
- First pytest invocation failed before any simulation because the requested output parent directory did not yet exist. The directory was created; this was a test-launch error, not a flex failure. No inputs or tolerances were adjusted.

## Tests and use

- Final targeted run, with real native controls: **5 passed in 31.53 s**.
- Related lightweight regression: **25 passed, 6 skipped in 5.93 s**. Skips are explicit native opt-in tests, not physics successes. No full FEA/CoACD/Blender suite or API executed.
- Real geometry assertions check exact flex vertices/faces, one freejoint per part, display collision disabled, finite shell budget, original inertia, real interpart/floor contacts, positive/negative outcomes and deep-initial-contact invalidation.

Enable only in the chosen run's physics config (all other settings unchanged):

```json
"standing": {
  "collision_backend": "rigid_flex",
  "rigid_flex": {"radius_mm": 0.01, "margin_mm": 0.0}
}
```

The fragment is **not** a full physics configuration; retain existing density, friction, time step, duration and settling values. The radius override is optional; the gap-based default is preferred. Use the existing `checker_spec` / `run_assembly_checks` caller with an explicit timeout. No new workflow or CLI mode was added.

Actual local verification commands (use a fresh output path if repeating; do not overwrite this evidence):

```bash
export PYTHONPATH=/jiigan-hp/lms/aDSL/experiment/runtime/mujoco-py310:/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/assembly_physics_runtime
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
ADSL_TEST_RIGID_FLEX=1 /vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q tests/test_assembly_standing.py --basetemp=<new-parent-existing>/<new-test-directory>
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python temp/standing_rigid_flex_20260922/run_saved.py <new-run-label>
```

The saved-asset helper is a two-input diagnostic runner only, not a batch experiment framework. It neither edits nor executes model source; it reads saved meshes and runs only standing.
