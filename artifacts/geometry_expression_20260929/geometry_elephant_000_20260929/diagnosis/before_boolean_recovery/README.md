# elephant_000: one-image A/B display

Reference: public same-ID PLY from [Yang2001/toys4k_meshes](https://huggingface.co/datasets/Yang2001/toys4k_meshes), pinned in reference/provenance.json. Rendered with the existing CPU renderer. The user explicitly approved this source. The original .blend and original aDSL input/run were not available; this is a fresh comparison, not a claim to reproduce an identified paper sample.

Only reference/views/render_0002.png (copied to reference/input.png) was supplied to the models. Other reference views in the collage are display context only. Original target geometry/code was not provided. Actual Planner/Coder/patch image bytes were decoded and hash-checked identical for both arms.

A: pre-enhancement local master 8943a83. B: enhanced constructive API and prompts. Both use the same gpt-5.6-sol profile, one initial generation, at most one execution-error patch, 300 s execution and 300 s rendering. CPU Cycles, 512², 32 samples, 4 threads, review_eight, neutral/gray. No critics, checkers, FEA, URDF or FixedAssembly. This compares the API + prompt bundle, not either component in isolation.

| Arm | Status | Source lines | Triangles | Execution patches | Export seconds | Render seconds | New API call sites |
|---|---|---:|---:|---:|---:|---:|---|
| A | rendered | 180 | 42150 | 0 | 16.99 | 36.63436755537987 | {'Polygon': 0, 'linear_extrude': 0, 'rotate_extrude': 0, 'hull': 0} |
| B | rendered | 220 | 7365 | 1 | 26.37 | 30.8357051089406 | {'Polygon': 2, 'linear_extrude': 2, 'rotate_extrude': 0, 'hull': 3} |

[Selected views: reference / A / B](comparison.jpg), [all eight views](all_views.jpg).

Folders A/ and B/ contain exact prompts, inputs, outputs, tool call messages, source snapshots, GLB, eight images, source_index, analysis_geometry, usage and session snapshots. comparison_data.json records matching input bundles, image hashes and statuses. Geometry API use is not by itself proof of better shape fidelity. No manufacturing/physical conclusions are drawn.
