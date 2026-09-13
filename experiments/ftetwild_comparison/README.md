# Independent fTetWild comparison

This is an offline experiment, not a production checker or automatic repair backend.
No source models, checker thresholds, material/load/support settings are edited.

Run (refuses to overwrite existing case directories):

```sh
local_experiment/ftetwild_20260913/env/bin/python -u experiments/ftetwild_comparison/run.py
```

## Fixed protocol (before observing results)

- Inputs: saved Blender GLB for SF27/ours **original wood-knot model** (not its rejected repair), SF03/ours passed final, SF20/adsl real-gap control.
- Apply scene transforms, then rotate glTF `(x,y,z)` to authored `(x,-z,y)`; physical scales are the original FEA height scales: 1.2 m / 0.9 m / 1.6 m respectively. No rescaling to improve solver behavior.
- SF20 OCC evidence: main post to upper capital gap 0.005 authored units, 2.03821656 mm at the original FEA scale. Preserve every object, including decoration.
- Envelope **0.1 mm** for each case, relative epsilon = 0.0001 / physical bounding-box diagonal. This is an experimental fidelity budget, not a production threshold change. fTetWild uses smaller internal working epsilons; log those separately, do not confuse them with the requested envelope.
- Target edge length = 4% physical height, corresponding to the original fine-level nominal size (not a claim of equal mesh resolution or convergence). AMIPS stopping energy 10; maximum 80 passes; stage 2; coarsening/simplification native defaults. No parameter retries.
- Use input winding numbers; no open-boundary smoothing, flood filling, enforced manifold surface or orientation correction. Native fTetWild simplification may still change geometry inside its envelope: validate it, do not assume preservation.
- Per case: generation 300 seconds; validation 180 seconds; 2 CPU affinity cores and 6 GiB virtual address-space hard limit. Parent terminates/reaps the process group on timeout. No GPU needed. Serial execution.
- Preserve hashes of GLB/source/analytic manifest/config/topology evidence, raw OFF input, logs, NPZ and VTU tet mesh, PLY boundary and JSON results. Production environment is read-only; `wildmeshing==0.4.1` and `meshio==5.3.5` are installed in a separate venv with read-only access to existing scientific packages.

## Measurements and limits

- Corner Jacobian determinant is six times signed tetrahedron volume (linear tetrahedra). Zero, negative and nonfinite are invalid; small **positive** determinants are reported as warnings, not removed/rejected on size alone. Report nonmanifold faces. Preserve returned ordering.
- Solid connectivity uses shared tetrahedron faces, not merely vertices or triangle-soup object count. Compare with saved OCC final assembly components; representation differences must be investigated rather than silently accepted.
- Reference GLB objects have duplicate positions from normal/material seams. Only for reference queries, reindex **exactly identical positions within each object**. No tolerance weld or cross-object joining. Check closedness and orientation before union-volume estimation.
- Measure sampled output-to-input triangle distance, and sampled **exposed** input-to-output distance (exclude buried overlap surfaces using union membership). 4,000 area-weighted samples, seed 20260913. These are sampled estimates, **not certified Hausdorff bounds**.
- Compare tetrahedron volume with 30,000 uniform bounding-box union-membership samples, recording approximate binomial 95% uncertainty. Raw object signed-volume sum is also recorded but double-counts overlaps and is **not** the union volume. An open reference object leaves union-volume acceptance unverified.
- Map saved gap endpoints to output boundary and report their solid component IDs, endpoint deviations, and projected separation. Endpoint mapping is reliable only within the fixed envelope. This is local gap evidence, not a proof that all other gaps survived. Changed component count, unreliable mapping, open reference or inadequate sampling keeps geometry unverified; a detected new connection is adverse evidence.

## CalculiX gate

Only consider a solve after positive mesh validity and acceptable boundary, volume and connection checks. Missing evidence is not a pass. The original solver uses straight-sided **C3D10** (`SecondOrderLinear=1`), whereas this output is C3D4. Any conditional integration must explicitly construct shared midside nodes, verify CalculiX node order/Jacobians, and reuse original semantic loads/support/material with recorded mapping. No C3D4-vs-C3D10 physical pass-rate comparison; no settings changes to gain a pass. This script deliberately does not invoke CalculiX automatically.

The separate `sf03_fea.py` implements this conditional step for SF03 only after all three meshing attempts finish. Its 18 input objects are verified axis-aligned boxes, permitting exact union-volume measurement by box-coordinate partition (not a mesh edit). The fixed-envelope volume screen is `abs(volume change) <= envelope * input surface area`, alongside the existing boundary/component measurements; this is a limited numerical screen, not a certified geometry proof. The script constructs globally shared midside nodes in the order verified against the local CalculiX 2.23 `shape10tet.f`, checks all four integration-point Jacobians against the original corner determinant, and reuses the existing deck writer, region selector and result parser read-only. Total integration budget: 300 seconds / 2 CPUs / 6 GiB, preserving the original solver's 900-second per-solve setting under the shorter outer diagnostic deadline. One mesh level only; no physical pass-rate or convergence claim.

## Primary references

- [fTetWild repository](https://github.com/wildmeshing/fTetWild)
- [Official Python binding and parameters](https://wildmeshing.github.io/python/)
- [Binding implementation](https://github.com/wildmeshing/wildmeshing-python/blob/master/src/tetrahedralize.cpp)

The published wheel version is pinned; its bundled fTetWild git revision is not exposed by package metadata and must not be presented as current master.

## Production decision (2026-09-13)

Production FEA remains **OCC → Gmsh → CalculiX**. Keep the existing process-group
timeouts, short localized failure feedback, independent checker execution and
asset-preserving candidate acceptance. fTetWild is an offline comparison only:
no production dependency, automatic fallback or backend switch is introduced.

The one authorized SF27 rerun with a 900-second budget also timed out (900.04 s).
At pass 13, the last reported maximum AMIPS was 25.587 (target 10), with 1,023,333
intermediate tetrahedra. No final mesh was exported, so validity and geometric
preservation remain unverified. No FEA was run or further retry scheduled.
SF20/adsl was confirmed as the originally selected real-gap control; its output
gap-preservation test remains incomplete. See the follow-up report in
`reports/ftetwild_sf27_900_20260913.md`.
