# Same-source deterministic export recovery

The original B/source.py is unchanged. No Planner/Coder or checker run, no FEA. CPU renderer uses the same eight-view/neutral/512px/32-sample settings as the original comparison.

- `source.py`: exact original B source, SHA256 in reexport_result.json.
- `exec_final/render/scene.glb`: complete regenerated elephant, including trunk.
- `views_final/`: eight actual rendered views.
- `reexport_result.json`: source hash, implementation file hashes, mesh counts, timings and actual per-object Boolean recovery reports.
- `exec_final/stdout.log`: exporter execution, no omitted-mesh success.
- `reexport.py`: reproduction driver (paths refer to the execution machine).

The operation is a deterministic exporter correction, not an additional Agent design improvement. This display run makes no manufacturing or physical approval claim. Original failed assets/logs remain in B/.
