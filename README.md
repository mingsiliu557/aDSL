# SNAP3D 公式重实现（Manifold / MuJoCo rigid_flex）

Independent research implementation, not official SNAP3D code. No aDSL installation
is required. Hunyuan3D-2 and public X-Part light remain pinned in
`upstream.lock.json`; auxiliary checkpoints are pinned separately. No upstream
source is modified. Models, private runs and formal benchmark metrics are absent
from this repository.

## Execution

Install the dependencies in `pyproject.toml` in a compatible environment. The
inference environments can be separate, exchanging mesh files. Explicit inference
requires local, hash-verified snapshots; imports and geometry tests do not download
weights or invoke models.

```bash
python -m pytest -q tests
python -m snap3d.generate --image input.png --weights models/Hunyuan3D-2 --output runs/case_generation
python -m snap3d.decompose --mesh runs/case_generation/whole.ply --weights models/Hunyuan3D-Part --output runs/case_parts
python -m snap3d.pipeline --run runs/case --stage initialize --whole runs/case_generation/whole.ply --raw-parts runs/case_parts
# Then --stage repair, pge, cgr, rollout, cem, export, in that order.
```

Artificial gates are historical diagnostics, not prerequisites. Explicit stage
reruns can overwrite stage outputs. Configuration and code changes are recorded
as warnings; asset identity verification remains. Incomplete upstream exports are
recorded with the readable exported parts. Program errors, unreadable input and
nonfinite or failed simulation remain errors, rather than fabricated successes.
Long jobs run inside an authorized Slurm allocation. Do not use unallocated GPUs,
release shared resources, or install keepers.

## Current geometry and simulation protocol

One transform maps upstream Y-up to Z-up, chooses a 150 mm longest edge and places
the assembly on the ground. D is frozen from the assembled parts, before editing.
Open parts use trimesh hole filling, followed when necessary by PyMeshFix, with
joining and smallest-component removal disabled. The repair intervention is
recorded; no maximum opening size or patch-planarity gate remains.

PGE retains the score, insertion tests, deterministic pair order, per-cut overlap,
closure and component-count checks, and lowest-margin ownership reversal. New
fragments with average thickness `2V/A < D/256` are removed: this is our chosen
interpretation of thin slivers. Rejected cuts roll back locally. Exhausting the
35% cumulative-loss reversal options is recorded as a warning.

CGR uses triangle distance and symmetric, area-weighted facing samples. An anchor
failure skips only its edge. Each connector is rebuilt from immutable PGE material.
Axis angles above 55 degrees and third-part peg intersections remain infeasible;
shaft-radius and insertion-length lower bounds also remain. Material support,
rooting, anchor-location, disconnection and assembly-intersection diagnostics are
warnings. **The paper also rejects sockets lacking material support; making that
a warning is an explicit requested deviation.** Circular mushroom-head geometry
and fixed CEM variance details are selected implementation choices.

Only rigid_flex is exposed. The complete aDSL XML function and its dependencies
are vendored with source and AST provenance; their function bodies remain unchanged.
The copied XML contains an unused legacy branch, which SNAP3D never selects.
The CoACD and zone-proxy modules, configuration and exposed rollout paths are
removed. Simulation uses aDSL standing settings, 2 seconds under gravity, and the
SNAP3D displacement/rotation thresholds. Parts whose minimum z is at most 0.005D
are pinned; that set remains fixed throughout CEM. Initial thin-shell contacts are
diagnostics. End-frame cross-part depth <= D/256 is the soft-contact adaptation of
intersection-free; actual final-pose Boolean volumes are recorded independently.

CEM retains 32 candidates, 8 elites, 3 generations, theta0 per generation, bounded
redraws, full covariance and the per-edge global-motion constraint. Infeasible
candidates redraw; an exhausted edge leaves the incumbent unchanged and search
continues with other edges. Baseline and CEM calls perform gravity only.

After the actual connector-bearing STL/GLB exports, one zero-gravity run records
final-output validity using the paper half-cell displacement bound. It does not
control CEM or erase exported geometry. Stable motion, soft-contact depth, and
final zero-gravity validity are separate recorded conclusions.

## Evidence and limitations

Current implementation and first-case measurements are in
`reports/flow_continuation_implementation.md`. Earlier reports and artificial
traces preserve their original protocols; they are not results of the current
configuration. No new artificial gates, generation models or formal benchmark
measurements are run by this continuation.

The paper uses IPC and a 256-cubed editing grid. This project uses MuJoCo rigid_flex,
Manifold Boolean operations, public X-Part light and explicitly disclosed geometry,
repair and contact approximations. It is a formula reimplementation, not an exact
reproduction of the authors' simulator or unpublished implementation.

Sources: [SNAP3D, sections 3 and Appendices A/B](https://arxiv.org/html/2609.13146),
[Hunyuan3D-2](https://github.com/Tencent-Hunyuan/Hunyuan3D-2),
[Hunyuan3D-Part](https://github.com/Tencent-Hunyuan/Hunyuan3D-Part).
