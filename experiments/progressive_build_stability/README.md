# 02 Progressive Build Stability experiment

This experiment reconstructs each model at successive horizontal build heights.
The default grid is 1% of total height plus geometry birth/completion events.
It does not modify source models or integrate a production checker.

It reports both an unbonded partial resting freely on the build plane and the
gravity overturning resistance that finite bed adhesion must provide. A MuJoCo
peak tilt strictly greater than 25 degrees is a fall.

```bash
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python \
  experiments/progressive_build_stability/analyze.py \
  --input-root /jiigan-hp/lms/aDSL/experiment/audit_20260830 \
  --output-dir /jiigan-hp/lms/aDSL/experiment/physics_analysis/02_progressive_build_stability \
  --mujoco-pythonpath /jiigan-hp/lms/aDSL/experiment/runtime/mujoco-py310 \
  --height-step-fraction 0.01 --physics all
```

Use `--physics off` for geometry only or repeat `--case CASE_ID` to select cases.
MuJoCo is CPU-only. Transient partial STLs are placed under `--scratch-dir` and
deleted after each case. This stage checks rigid-body toppling only. Bending,
buckling, and FEA require a later separate analysis with physical scale,
material, layer orientation, adhesion, and process loads.
