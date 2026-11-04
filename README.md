# SNAP3D 公式重实现（Manifold / MuJoCo-CoACD）

Independent research implementation. Not official SNAP3D code. No dependency on an
installed aDSL checkout. No formal benchmark metrics or training are implemented.

## Frozen provenance

`upstream.lock.json` records exact source commits and Hugging Face revisions.
The shape model is Hunyuan3D-2 (`hunyuan3d-dit-v2-0`, shape only); decomposition
uses public X-Part light with P3-SAM. Upstream files are unmodified. Models are not
included. Resolve ancillary model checkpoints (including background removal and
Sonata if required) before inference; offline mode must not fetch unpinned assets.

Vendor source is aDSL `f65b0dbb9c0e3a48d45f870cdb7091949aa2472a`.
`vendor/provenance.json` records source hashes; `function_ast.json` tests copied
functions against the frozen source. No license file was found at that aDSL
revision: provenance is retained, no new license is assigned to that code.
Upstream third-party license terms continue to apply.

## Environment

Geometry dependencies are pinned in `pyproject.toml`; use a separate environment
or the existing compatible environment without upgrading it. Hunyuan generation
and X-Part can use separate environments and exchange files. The dependency
versions used for actual tests are in `runs/environment.json`. No model inference
compatibility is claimed from geometry test results.

```bash
python -m pytest -q tests
PYTHONPATH=. python scripts/lock_gate.py --output runs/lock_gate_NEW
```

Long operations run in tmux inside an existing Slurm allocation. Never use GPUs
outside the allocation, request resources automatically, or install a keeper.

## Mandatory gate and stage execution

The lock gate compares zero gravity, an unheaded pin, and a headed pin under the
same withdrawal load. Ground pieces are fixed. Only the load isolation fixture
uses zero friction; production values remain in `configs/default.yaml`.
Failure or unavailability stops real inference and CEM. A large initial contact
impulse is not an acceptable lock. Simulation initialization alone is not success.

After the gate passes, wrappers accept only local weight snapshots. Set the
appropriate upstream repository on PYTHONPATH, use each model's environment,
and supply `--gate` with the passing report to both wrappers:

```bash
python -m snap3d.generate --image input.png --weights models/Hunyuan3D-2 --output runs/case_generation --gate runs/lock_gate_NEW/gate.json
python -m snap3d.decompose --mesh runs/case_generation/whole.ply --weights models/Hunyuan3D-Part --output runs/case_parts --gate runs/lock_gate_NEW/gate.json
python -m snap3d.pipeline --run runs/case --gate runs/lock_gate_NEW/gate.json --stage initialize --whole runs/case_generation/whole.ply --raw-parts runs/case_parts
# Then --stage repair, pge, cgr, rollout, cem, export, in that order.
```

Stage outputs are immutable; retries require a new run. Mesh files and config
hashes are checked on resume. Failure records preserve the stage and error.
The fixed pilot order is DensiCrafter_124, DensiCrafter_104,
Toys4K_deer_moose_027. The first real output may already be stable; use a synthetic
unstable fixture for a CEM validation in that event. No full benchmark is launched.

## Geometry and protocol

Hunyuan Y-up maps to Z-up with a proper rotation. Once, at entry, scale the whole
mesh to a 150 mm longest side and put its minimum z on the ground. Apply exactly
the same transform to all parts; freeze the resulting diagonal D. No candidate
renormalization. X-Part's own fix_mesh step is recorded separately from our
boundary-loop repairs, and silently missing upstream exports are rejected.

Our added repair is limited to simple planar loops (diameter <= 0.01D,
planarity <= 0.0001D). Existing vertices and other parts are unchanged. No voxel
rebuilding, large-hole closure, fragment deletion or bridging. Invalid open
material has null original volume.

PGE uses original material operands, actual cumulative loss, the 35% cap and
lowest-margin ownership reversal. Each ownership can reverse once; unsatisfied
caps stop the case. Enclosing sphere radii use the AABB centre and farthest
vertex, a deterministic implementation choice. Clipping may not add islands.

Contact uses actual FCL distance and area-weighted facing-area estimation;
bidirectional minimum is our convention. Anchors use a D/512 raster and EDT,
then actual triangle projection. No connection is added from an AABB alone.

The chosen connector is a 64-sided circular stem with a circular head, a narrow
throat and an enlarged blind cavity. Head thickness is D/256. No straight enlarged
head sweep is removed through the throat. No elastic insertion path is claimed.
All candidates rebuild from immutable post-PGE geometry and the complete current
connector set; no old candidate slots persist.

MuJoCo uses explicit true-material inertia and CoACD collisions (mm -> SI).
Initial ground IDs remain fixed throughout a search. Every frame contributes to
3D displacement and SO(3) rotation; final contacts determine moving groups. This
fixed-ground optimization protocol is not the online aDSL standing protocol or
the paper's IPC protocol. CEM is 32/8/3, keeps theta0 each generation, uses full
covariance, bounded redraws and a fixed per-edge global baseline. Non-improving or
failed candidates do not replace the incumbent. Self-selected values and numeric
conditioning are in the config/source and are not claims about paper defaults.

Final GLB contains the actual connector geometry. Meshes, proxy meshes, trajectories,
source/weight identity, repairs, losses, theta, simulation counts and timings are
retained. Formal NSV, connectivity and CLIP evaluation is intentionally absent.

Sources: https://arxiv.org/abs/2609.13146 ;
https://github.com/Tencent-Hunyuan/Hunyuan3D-2 ;
https://github.com/Tencent-Hunyuan/Hunyuan3D-Part .
