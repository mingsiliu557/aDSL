# 01 Final Standing Stability experiment

This directory contains a read-only experiment utility for the frozen aDSL
assets in `/jiigan-hp/lms/aDSL/experiment/audit_20260830`. It is intentionally
not integrated into the aDSL agent and is not a production checker.

The experiment computes support polygons and two uniform-density center-of-mass
The user-defined fall event is a maximum simulated body tilt strictly greater than 25 degrees. Free settling and perturbation-induced threshold crossing are reported separately.
models, then optionally cross-checks the result with CPU-only MuJoCo settling,
16-direction force, and small-impulse probes. All derived files are written
below the explicitly supplied output directory.

Run the full experiment with the project environment and data-disk MuJoCo
runtime:

```bash
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python \
  experiments/final_standing_stability/analyze.py \
  --input-root /jiigan-hp/lms/aDSL/experiment/audit_20260830 \
  --output-dir /jiigan-hp/lms/aDSL/experiment/physics_analysis/01_final_standing_stability \
  --mujoco-pythonpath /jiigan-hp/lms/aDSL/experiment/runtime/mujoco-py310 \
  --physics on
```

Use `--physics off` for the deterministic geometric pre-screen. The input tree
is never modified; the command rejects output directories inside it.
