# Constructive geometry display comparison

This uses existing aDSL inputs with curved geometry: SF06 (ABO armchair), SF21
(ShapeNet floor lamp), and T02-bookshelf (local audit). `cases.json` preserves their
original text and provenance. Historical source/assets are provenance only, never
model input. These case IDs are not claimed to be the original paper's test IDs.

The comparison is **API + prompt together**. A uses pre-enhancement local master
`8943a8306ddcce69ad0c043f0079c0a14f0518f1`; B uses this branch. It is not a comparison
against an unmodified upstream aDSL release, nor an ablation of the prompt alone.

Run the same `run_demo.py` with each environment importing that arm's `adsl.core`,
`adsl.agents`, and `adsl.tools`. The script records the resolved module paths and
commit. Install the core `geometry` extra for B (Manifold 3.5.2); both need the
existing Blender/render and Agent dependencies. Use the same model profile.

```bash
python experiments/geometry_expression/run_demo.py \
  --case SF06 --arm B --output /absolute/fresh/output/B/SF06 \
  --model-profile adsl-agents/configs/llm/cliproxy-gpt-5.6-sol.yaml \
  --session-root /tmp/adsl_geometry_demo_sessions --timeout 300
```

Repeat once for each arm/case: six initial samples total. A fresh directory is
mandatory; failures are retained. The runner calls only Planner and Coder, allows
at most one source-execution correction, exports GLB without URDF or FixedAssembly,
and ends at rendering. It does not enter any critic or checker loop. Rendering
errors and timeouts do not authorize changes to the generated model.

Both arms use CPU Cycles, four threads, 512×512, 32 samples, neutral material, gray
background, and `review_eight` (six orbit views + top/bottom), with identical camera
normalization. Original generated materials remain in the GLB. Rendering and
execution each have 300 seconds; model requests retain the profile's limits.

`demo_result.json` records actual status, execution attempts/timings, render time,
source hash, GLB bounds/counts, usage and static new-API call sites. Inspect
`analysis_geometry.json` and the exported GLB as evidence that calls were actually
executed. `planner/`, `coder_initial/` and optional `coder_execution_patch/` retain
actual system instructions, inputs, tool schemas, SDK message transcripts, outputs,
tool events and timing. Session databases are backed up to each output directory.
No result is labeled manufacturing-approved.

Focused validation (from the implementation checkout):

```bash
python -m pytest -q -p no:cacheprovider tests/test_constructive_geometry.py
ADSL_TEST_GEOMETRY_REAL=1 python -m pytest -q -p no:cacheprovider \
  tests/test_constructive_geometry.py tests/test_boolean_solver.py
python -m pytest -q -p no:cacheprovider tests/test_geometry_demo.py tests/test_prompts.py
```

Native smoke includes mesh/Boolean/hull composition, copying and transforms, layout,
GLB readback, source/geometry records and eight actual CPU renders. It does not run
manufacturing checks. A skipped native test is not proof of rendering support.

Implementation references: [Manifold 3.5.2 Python bindings](https://github.com/elalish/manifold/blob/v3.5.2/bindings/python/manifold3d.cpp),
[Blender Mesh.from_pydata](https://docs.blender.org/api/4.0/bpy.types.Mesh.html#bpy.types.Mesh.from_pydata).
