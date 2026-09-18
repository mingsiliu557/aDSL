# Fixed assembly v1

Opt-in static manufacturing assembly: one `tab_slot` type, explicit print parts,
one complete aDSL program, and the original Image/Code Critic responsibilities.
All four physical checkers are disabled; requesting one records it as unsupported,
not PASS. This is not the `planned_checks` or overhang experiment mode.

`t_bracket.py` is the hand-written geometry fixture; it is NOT provided to the
prompt-to-3D smoke agent. The smoke prompt specifies dimensions and interface
intent, and the agent chooses its program and frames using the public API.

## Usage

See `adsl-agents/prompt/fixed_assembly.md` for the API/frame convention.
The millimetre conversion, fit_offset_mm and final assembled XYZ extent are frozen by config.
Slots get one single-sided clearance adjustment. An interface's local +Z points
from the tab body into the receiver on BOTH sides. The stop planes coincide.
Incoming slots are derived from the single connection list. Receiver-first tree
placement only; cycles, duplicate ports and multiple locating mates are errors.

An explicit print part may contain many semantic children. Never select a subtree
twice or select both ancestor and descendant. Copies are independent instances.
Only the helper adds tab/slot material; bodies supply mounting material.

```bash
# Small tests, no API:
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q tests/test_fixed_assembly.py

# Same fixture with real Boolean/export parameter variants (120 s each maximum):
ADSL_TEST_FIXED_REAL=1 /vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q tests/test_fixed_assembly.py -k real_boolean_export

# One new API run: capture both terminal and log; use a new output directory.
set -o pipefail
bash examples/fixed_assembly/run_smoke.sh local_experiment/fixed_assembly_smoke_NEW 2>&1 | tee local_experiment/fixed_assembly_smoke_NEW.log
```

The script uses the existing StepCode `gpt-5.6-sol` profile and CPU Cycles. It
starts a proxy only if no listener exists and stops only the proxy it owns.
No credentials are written to the repo. Initial generation + at most two repairs
(`max_rounds=3`), including visual/code repairs, failures and no-change attempts.
Existing SDK retries/time limits remain; no additional retries or edit budget.

## Export and evidence

- `assembly_versions.json`: immutable original, isolated attempts, retained pointer.
- `assembly_result.json`: matching selected source hash, reviews and stop reason.
- `assembly/assembly_manifest.json`: mm scale, local-to-assembly and mm printing
  transforms, interface parameters, finite/closed/connected meshes, actual added
  and removed material, mount-space/stop-plane evidence, symmetric volume comparison,
  undeclared interference checks, fixed dimensions and exported file hashes.
- `assembly/*.stl`: actual Boolean exterior, numerical coordinates in millimetres
  (STL itself has no unit metadata); local part shifted to bed Z=0 without scaling.
- `scene.glb`, `render/*.png`, `assembly/exploded.glb`,
  `assembly/exploded_render/*.png`: assembled and display-only disassembled views.

Blender evaluates union/difference. Manifold unions shells only within an individual
print piece, without hole filling or proximity welding. Exact duplicate vertices
within the same mesh are merged and counted. No global union joins print pieces.
GLB retains the original evaluated CSG/materials; STL has those meshes' merged exterior.
Geometry validation uses a documented float32-derived numerical bound, separate
from the physical fit allowance. Every part must be a single closed oriented solid.

Every candidate regenerates geometry and images. Only interface geometry AND
appearance approval selects it. A failed/no-change candidate never overwrites the
retained source, models or evidence. With no valid version, original diagnostic
assets/source are retained and approval remains false. Interrupted attempts retain
their budget reservation; resume does not repeat the model call. Resume uses the
saved round ceiling and rejects scale/plan changes.

Geometry success does not establish an unobstructed insertion path, press-fit
retention, structural capacity, print support requirements or real print success.
Positive clearance can be loose. Negative allowance is nominal interference only.
Calibrate on printer/material test coupons later. No FEA or other physical outcome
is claimed here. Arbitrary existing-model `edit` is intentionally unsupported in v1.
