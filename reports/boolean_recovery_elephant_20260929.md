# Boolean recovery and elephant display

Base: constructive geometry branch c9396a2. Required changes are in the public Blender exporter and assembly normalization-report adapter. Existing checker readback already uses exact-coordinate deduplication and was left unchanged.

## Behavior

Original operands are retained until the complete Boolean node passes validation. Existing zero-area cleanup runs first. Invalid results (including zero-zero-area but open/nonmanifold/duplicate-face results) fall back to the same Manifold Boolean. Only valid operands are accepted. Float32 conversion uses a bounded attempt list (no simplification, one coordinate ULP, two ULPs), exact-coordinate welding and degenerate-edge cleanup. Acceptance checks zero area, edge/vertex manifoldness, winding, shell count, AABB change, sampled bidirectional vertex-to-surface distance and volume change. The sample distance is not a certified Hausdorff bound. Failed candidates are discarded; no hole filling or broader proximity weld.

Materials, object transforms and original operand geometry survive the recovery interface. The GLB exporter verifies each expected mesh node and triangle count after writing; omitted mesh primitives now fail execution. Assembly mesh_normalizations includes recovery diagnostics.

## Validation

```sh
ADSL_TEST_FIXED_REAL=1 ADSL_TEST_GEOMETRY_REAL=1 python -m pytest -q -p no:cacheprovider tests/test_boolean_recovery.py tests/test_zero_area_tessellation.py tests/test_microcrack_welding.py tests/test_constructive_geometry.py tests/test_fixed_assembly_empty_mesh.py tests/test_fixed_assembly_visual_only.py
```

**92 passed, 0 skipped**, 44.52 seconds. Includes native Blender execution/rendering, union/difference/intersection recovery at translated positions with materials, failed-input rollback, genuine missing-face rejection, original trunk GLB/STL through existing topology reader, and assembly normalization-report propagation. Log: `reports/boolean_recovery_smoke.log`.

## Same-source elephant

Original B/source.py SHA256: `8214009f19a423f017ff4eb716e0ede9338862d0550c7f47b710170b1dd78f9f`. No new LLM calls, no source patch, no formal critic/checker run, no FEA. CPU renderer: same eight views, 512², 32 samples, neutral material. Full-scene export 8.16 s; rendering 22.60 s. New GLB contains 16712 triangles; the actual trunk is visible in the rendered views. Five final objects carry recovery metadata; numerical problems were not limited to the trunk.

Current display and source/GLB/logs: `artifacts/geometry_expression_20260929/geometry_elephant_000_20260929/`. B points to `B_reexport_recovery/`, with code file hashes and individual recovery measurements in reexport_result.json. The original B logs/assets are unchanged; old comparison images are retained under diagnosis/before_boolean_recovery. This is an exporter correction using the original generated design, not a new Agent shape-improvement sample or manufacturing approval.
