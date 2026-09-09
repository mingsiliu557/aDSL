# Engineering checker loop

These adapters expose the existing MuJoCo standing/progressive, CalculiX/Gmsh, and PrusaSlicer analyses to the object-agent workflow through one fail-closed JSON contract.

A checker spec defines only process execution policy. Its analysis config fixes the physical assumptions, loads, boundary conditions, and thresholds. The Coder never receives permission to edit those files.

For an existing asset, use `edit --check-first` so round 1 is an untouched baseline:

```bash
adsl-run edit "Preserve appearance; repair only mandatory checker failures." \
  --source path/to/source.py \
  --output local_experiment/checker_repair \
  --check-first --max-rounds 4 \
  --checker-config experiments/workflow_checkers/specs/fea_chair.json
```

Use `specs/progressive.json` for the fixed 1%-height, event-augmented partial-build scan. It is intentionally an unbonded rigid-body stress test, not a deposition simulation; real FFF decisions must also use slicer support/brim paths and a calibrated bed-adhesion model. Repeat `--checker-config` to require multiple gates. A round publishes only after its appearance review and every required checker return `PASS`. `FAIL` or `INDETERMINATE` becomes structured Engineering Critic feedback; `ERROR` stops as infrastructure failure and is never disguised as a geometry defect.

Each round stores:

- `checkers/<name>/result.json`: stable protocol result.
- `checkers/<name>/raw/`: complete analyzer evidence and solver files.
- `engineering_critique.json`: evidence-to-source repair mapping.
- `round_XX_source.py`: exact source checked in that round.

The FEA profile is a screening proxy: isotropic PLA, 0.9 m chair height, fully fixed bottom nodes, 1000 N seat load, 300 N back load, linear static and eigenvalue buckling. Passing is not print certification.
