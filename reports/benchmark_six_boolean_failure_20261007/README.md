# Lightweight Boolean failure evidence

See [the report](../benchmark_six_boolean_failure_20261007.md). These files preserve the original candidate and runtime-only diagnostic trace. They are not a corrected source candidate. Full float64/float32 and EXACT/Manifold/BMesh NPZ snapshots remain at:

`/jiigan-hp/lms/aDSL/experiment/benchmark_six_main_20261007T045229Z/diagnostics/lamp_boolean_20261007`

The diagnostic script records Blender data before and after the existing operations, without modifying the production module. Replaying it performs only the upper-structure geometry evaluation; it does not call a language model or resume the batch. Its original local output paths are retained as execution provenance. `provenance.json` binds the published lightweight files by SHA256.
