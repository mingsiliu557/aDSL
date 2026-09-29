# SF06 enhanced API — no FEA

A fresh prompt-to-3D SF06 run on feat/constructive-geometry@7ebfe4b, ours only.

Enabled: Image/Code reviews, Topology, Overhang (Dapper partition scoring), Standing (rigid_flex, 5 s / 25 degrees), and the existing Engineering/Coder repair loop.
FEA is disabled: absent from checker_specs and physics; no external force or support-region configuration is supplied.

CPU Cycles, 512×512, 32 samples, 8 views. Frozen size 900×800×900 mm, 1 mm/scene unit, fit 0.2 mm; uniform density 1240 kg/m³ is an experimental assumption.
Budget: 5 evaluation rounds, at most 4 shared source changes, existing 7200-second repair time budget. No baseline arm or automatic rerun.

Replaces geometry_sf06_full_20260929 at the user's request. That task was stopped before initial source returned; FEA never ran. Its original input and interrupted call records are preserved there.

Task: adsl_sf06_geometry_no_fea_20260929.
Progress: status.json and console.log. Final summary: completion.json; detailed selection/checkers: generate/assembly_result.json.
The existing visual_only exporter contract remains separate from independent checker results; no statuses are predeclared PASS.
