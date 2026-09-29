# SF06 enhanced API — full workflow

One fresh prompt-to-3D SF06 run, enhanced branch `feat/constructive-geometry@7ebfe4b`.
Only ours; no baseline replay and no prior source/plan/renders supplied.

- Planner → Coder → CPU eight-view Image/Code review → Topology, Overhang, Standing, FEA → existing Engineering/Coder feedback loop.
- 5 evaluation rounds maximum, 4 shared source changes, existing 7200-second repair budget. No automatic fresh rerun or extra budget.
- CLIProxy gpt-5.6-sol; CPU Cycles, 512², 32 samples, 8 views.
- Frozen 900×800×900 mm, 1 mm/scene unit, fit 0.2 mm. Original SF06 specifies 900 mm target height; width/depth are this run's armchair specification.
- Standing rigid_flex, 5 seconds / 25 degrees. Overhang Dapper alpha .3, Rvox .1, 24 rotations; grouping/orientation edits enabled.
- FEA reuses existing chair profile: isotropic PLA proxy, seat 1000 N down, back 300 N +Y, gravity, ideal fixed floor and bonded interfaces; XY load/support coverage expanded for the wider footprint. 10 mm mesh, 25 MPa / 5 mm screening limits. These are experimental assumptions, not inferred upholstery or wood properties.
- Export mode remains the existing `visual_only`; its geometric verdict is NOT_EVALUATED. Independent Topology and physical checker statuses remain separate. Enabling a checker does not predeclare it PASS.
- Only the chair is printable geometry. The original black-background wording is treated as presentation, not a floor/backdrop print part.

`job.json`, `input.json`, `physics.json` freeze the run. `preflight.json` records native dependency checks and module paths. `generate/api_calls/` and stage inputs preserve actual model instructions/input and tool records; sessions are snapshotted on completion.

Status: `status.json`, live output: `console.log`; final summary: `completion.json`, detailed result: `generate/assembly_result.json`.

Detached session: `adsl_sf06_geometry_full_20260929`. No continuous supervision after successful submission.
