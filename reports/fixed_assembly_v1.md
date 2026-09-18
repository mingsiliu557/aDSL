# Fixed assembly v1 — implementation and validation

Date: 2026-09-17. Starting commit: `6824a14c7485eabf1c19e64fcd34aee1cd1e4508`.
This report accompanies the fixed-assembly v1 code commit on master. Existing
unrelated deletions, keeper files and experiment assets were preserved locally and
are not part of the code commit; artifact links below refer to local experiment files.

## Scope and implementation

One paired interface (`tab_slot`), an explicit tree of print pieces, and one two-part
T-bracket demonstration. No physics checker, FEA, old case generation, environment
installation, GPU allocation or new agent role.

- `adsl-core/core/assembly.py`: public shared-parameter geometry and frame alignment.
- `adsl-core/core/export/export_assembly.py`: actual Boolean/Manifold exterior,
  independent mm STL pieces, assembly/exploded export, geometric evidence and hashes.
- `adsl-agents/models.py`, `prompts.py`, `prompt/fixed_assembly.md`, `cli.py`:
  opt-in validated plan/config; old planning schema and prompts remain unchanged.
- `service.py`, `fixed_assembly.py`, execution utilities: reuse original planner,
  coder, Image/Code Critic, repair budget, exact patch tools and version records;
  all repairs isolated and gated, selected version's source/assets/reviews published
  together. `overhang_edit.version_record` only gains optional extra hashed files.

Scale, final XYZ size and fit allowance are frozen outside generated code. A slot
is a real difference, not a URDF primitive approximation. Per-part topology, actual
material addition/removal, mount space, mate frames, undeclared collision and expected
CSG symmetric volume differences are checked. The entire assembly is not unioned.
Exact duplicate vertices only are welded within each object and counted.

## Local evidence

All products are under `local_experiment/fixed_assembly_v1_20260917/` on the project
disk. Earlier failed development checks remain in their original directories.

- `real_validation_v2/`: 6 real geometry variants passed their expected assertions:
  normal, shared-width change, non-axis-aligned placement, nominal interference,
  deliberately missing slot rejected, disconnected print part rejected.
- `render_validation/`: handwritten fixture exported, interface geometry PASS;
  measured geometry work 0.70 s (not including Python/Blender startup or rendering).
  CPU Cycles produced two assembly views and two exploded views at 512², 16 samples.
- `validation_final/`: **79 passed in 26.08 s**, including the six real variants;
  scale/fit freezing, actual Planner/Coder input, timeout evidence/process-group
  cleanup, candidate retention/budget and relevant ordinary/overhang/joint regressions.

Commands:

```bash
ADSL_TEST_FIXED_REAL=1 /vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q tests/test_fixed_assembly.py tests/test_prompts.py tests/test_execution_cleanup.py tests/test_refinement_budget.py tests/test_overhang_candidate_isolation.py tests/test_planned_prompt_generation.py --tb=short --basetemp=local_experiment/fixed_assembly_v1_20260917/validation_final
bash examples/fixed_assembly/run_smoke.sh local_experiment/fixed_assembly_v1_20260917/stepcode_smoke
```

## Real agent smoke

**Completed, exit 0**, in tmux `adsl_fixed_assembly_v1_20260917`; output
`stepcode_smoke/`, console `stepcode_smoke.console.log` under the above local directory.
The proxy started by this run was stopped by its exit handler; port 44949 was no
longer listening at the final check. No shared proxy or GPU keeper was stopped.
StepCode gpt-5.6-sol, no other API. One initial complete program, at most two repairs
including visual/code repairs. The hand-written fixture is not an input to the agent.
Fresh workspace; source, every candidate, decisions, images, meshes and actual usage
are preserved. Existing per-request API retry limits remain unchanged. Only a proxy
started by this run may be stopped by its cleanup handler.

| Item | Actual outcome |
| --- | --- |
| Old-flow compatibility | Relevant ordinary, overhang and joint mock regressions passed within the 79 tests |
| Shared interface and parameter changes | Real tests passed; missing slot and disconnected piece rejected |
| Generated interface | Tab 14 × 8 mm, insertion 8 mm, slot depth 9 mm, per-side clearance +0.2 mm, lead-in 0.8 mm |
| Actual material evidence | Added exposed tab 886.7413 mm³; removed receiver material 1088.6399 mm³; embedded tab root 89.6003 mm³ |
| Independent meshes | 2 STLs; both closed, one connected component each, 0 zero-area faces |
| Final assembled size | 60 × 20 × 62 mm; frozen scale 1 mm/source unit |
| Real agent result | Initial generated version accepted; geometry PASS, Image Critic approved |
| Code Critic | Retained in the workflow; not invoked in this run because Image Critic approved, as in the existing workflow |
| Repair attempts | 0 of at most 2 used; real repair/recovery effectiveness was NOT exercised by this already-valid generation |
| Version consistency | retained=original; published source, scene, all exported file hashes and review/manifest binding verified |
| Cost | 4 model requests, 24,169 input + 5,430 output = 29,599 tokens; 6,016 cached input tokens; currency cost unavailable |
| Time | About 126.24 s from persisted user input to published assembly result (filesystem timestamps, excludes proxy startup); geometry stage 0.6964 s excluding process startup/rendering |
| Renders | 8 assembled views + 2 exploded views; CPU Cycles, 512², 32 samples |
| Real attachment / FEA / standing / overhang | NOT VERIFIED / NOT RUN; no physical improvement or manufacturing-success claim |

Artifacts, relative to this report:

- [Generated source](../local_experiment/fixed_assembly_v1_20260917/stepcode_smoke/source.py)
- [Assembled GLB](../local_experiment/fixed_assembly_v1_20260917/stepcode_smoke/scene.glb)
- [Crossbar STL](../local_experiment/fixed_assembly_v1_20260917/stepcode_smoke/assembly/crossbar_part.stl)
- [Stem STL](../local_experiment/fixed_assembly_v1_20260917/stepcode_smoke/assembly/stem_part.stl)
- [Geometry manifest](../local_experiment/fixed_assembly_v1_20260917/stepcode_smoke/assembly/assembly_manifest.json)
- [Version and review result](../local_experiment/fixed_assembly_v1_20260917/stepcode_smoke/assembly_result.json)
- [Usage events](../local_experiment/fixed_assembly_v1_20260917/stepcode_smoke/usage.jsonl)

![Assembled bracket](../local_experiment/fixed_assembly_v1_20260917/stepcode_smoke/render/render_0001.png)

![Exploded underside view](../local_experiment/fixed_assembly_v1_20260917/stepcode_smoke/assembly/exploded_render/render_0001.png)

![Exploded upper view](../local_experiment/fixed_assembly_v1_20260917/stepcode_smoke/assembly/exploded_render/render_0002.png)

## Interpretation limits

PASS means interface geometry plus the separately recorded visual/code review, not
real fixed attachment. Insertion path, press-fit retention, strength, overhang and
actual print quality remain unverified. Positive 0.2 mm per-side clearance in this
demonstration may be loose; no fit value is claimed to work for all printers.
Numeric acceptance tolerance is derived from float32 export representation and
recorded separately from that fit allowance. No additional mesh repair is attempted.

The representation draws on Procedura's paired-interface/frame idea (§3.1–3.3):
https://arxiv.org/html/2608.26238v1. This implementation is independently written;
no Procedura source was copied, and its incremental pipeline is not adopted.
