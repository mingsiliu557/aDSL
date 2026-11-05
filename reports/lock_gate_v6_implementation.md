# Zero-gravity gate correction and v6 continuation

Base: `snap3d@43ef1ca192766a8b615f32795771e9921cbcc68d`.

The zero-gravity gate now passes after reclassifying its unchanged v5 trace.
The unheaded fixture withdraws as required. The headed fixture fails the retained
loading criterion. Execution stopped there; no parameters were adjusted and no
real case, CEM, model call or weight download was run.

## Verified source and threshold

[SNAP3D arXiv:2609.13146v1](https://arxiv.org/html/2609.13146),
Appendix A, *Validity check for the physics-based evaluation protocol*, specifies
free-part zero-gravity motion less than 0.5 cells. Appendix B, *Scale and fixed
settings*, defines one cell as D/256. Thus the requested ratio is 0.5/256 =
0.001953125. The numerical value agrees with the paper; the threshold's actual
location is Appendix A, with its unit definition in Appendix B.

The previous free-part bound 1e-4 D was an implementation deviation from the
planned half-cell bound. This change restores the planned value. It does not
change the pinned-body bound, which remains <=1e-4 D.

For this unchanged D=150 mm fixture:

- New zero-gravity free-body bound: displacement <0.29296875 mm.
- Pinned-body bound: displacement <=0.015 mm. All three measured pinned motions are zero.
- Clearance c=0.146484375 mm; insertion length l=2.9296875 mm.
- Unheaded withdrawal: final delta z <-l.
- Headed retention: final delta z >-2c and maximum displacement <l.

## Changes and validation

`configs/default.yaml` adds only `gate.zero_gravity_displacement_ratio` and its
source comments. `scripts/lock_gate.py::gate_verdict` reads this configured ratio
for both existing backend gate paths; the fixed-body, unheaded and headed criteria
are preserved. `reuse_zero_gravity` and `--reuse-zero-gravity MANIFEST` verify saved
file hashes, the unchanged simulation configuration (excluding only the added
gate block), simulator files, fixture STL hashes and connector parameters before
reusing the trace. `rigid_flex_gate` reclassifies it and continues the remaining
gates, retaining stop-on-failure. The saved box controls are referenced and reused.

No `snap3d/` simulation or vendor file changed. No geometry, shell radius, friction,
solver, timing or loading settings changed. The full result config hash is
`3680a5dc6848223c1112f44628708b650bb6807e88d1f02ba8e9eb24951fa992`; the previous simulation config hash is recorded separately.

Hash checks are in [lock_gate_v6_reuse_identity.json](lock_gate_v6_reuse_identity.json)
and [lock_gate_v6_verification.json](lock_gate_v6_verification.json).
All v1-v4 directory digests still match the previous committed preservation record.
All captured v5 file hashes are unchanged. v6 artifacts are under `runs/lock_gate_v6/`;
the reused zero trace cites `runs/lock_gate_v5/zero_gravity/result.json` and its hash.
Old results and their old gate verdicts were not overwritten.

## Results

Maximum displacement is the free donor's largest sampled displacement; speed is
its peak world linear speed. Each native rollout simulated 2 s. The zero-gravity
wall time below belongs to v5 and was not incurred again. Contact counts are the
original rollout-wide initial/final counts, not counts of unique part pairs.

| Gate | Verdict | Max displacement mm | Final delta z mm | Max speed m/s | Contacts initial / final | Wall time s | Origin |
|---|---|---:|---:|---:|---:|---:|---|
| zero_gravity | PASS | 0.142241230 | -0.062978240 | 0.000415915 | 50 / 0 | 2.597020 | v5 reused |
| no_head | PASS | 39.858302674 | -34.309465285 | 0.824290617 | 50 / 4 | 0.586756 | new v6 |
| headed | FAIL | 0.743244522 | -0.742854006 | 0.058968650 | 50 / 50 | 7.175329 | new v6 |

Zero gravity: 0.142241230 mm <0.29296875 mm, hence PASS. Its maximum displacement
is also below clearance c=0.146484375 mm. This within-c observation applies only
to the zero-gravity lock trace, not the gravity-loaded headed trace.

No head: final delta z=-34.309465285 mm is below -l, hence PASS. The ordinary
SNAP3D stability flag may be false for this intentionally withdrawing control;
the withdrawal gate is evaluated by its own unchanged criterion.

Headed: max displacement 0.743244522 mm remains below l, but final delta z
=-0.742854006 mm exceeds the allowed downward movement 2c=0.29296875 mm.
Therefore the gate FAILS. Native integration completed normally; this is a failed
retention observation, not a solver or execution error. `next_stage_allowed=false`.
There are no additional gates to run and real cases remain outside this task.

## Thin-shell drift and box-control evidence

The v5 initial lock penetration 0.014648437425 mm agrees numerically with the
two-sided shell thickness 2r=0.0146484375 mm (r=0.00732421875 mm). Initial contact
push-out under rigid_flex is the working explanation for its zero-gravity drift.
The saved plain-box control, which has no lock, also drifts: maximum displacement
0.878672090 mm with formal friction, and 0.618610622 mm with condim=1 and zero
friction. The latter displacement is along the contact normal, with negligible
x/y displacement. These controls support a shell-contact relaxation explanation
rather than a lock-only effect. They do not uniquely isolate every solver effect;
we did not change shell radii or parameters to establish a stronger causal claim.
These diagnostic boxes are not additional lock-gate successes.

## Tests and reproducible commands

`tests/test_gate_v6.py` adds nine checks (including parameterizations): configured
half-cell threshold and strict equality boundary; unchanged pinned-body bound;
hash-bound reuse preserving original bytes; refusal of changed result, XML, mesh,
simulator or simulation config; and continuation order, no zero/control rerun,
and stop-on-failure. All existing 63 tests also pass.

CPU commands, run from the project root in the existing generation environment:

```sh
CUDA_VISIBLE_DEVICES= OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 PYTHONPATH=. python -m pytest -q tests
CUDA_VISIBLE_DEVICES= OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 PYTHONPATH=. python scripts/lock_gate.py --output runs/lock_gate_v6 --reuse-zero-gravity reports/lock_gate_v6_reuse_identity.json
```

Tests: **72 passed in 9.88 s**, including the original 63. The gate command exits
**2**, deliberately reporting the headed gate failure; the recorded native
rollouts themselves completed. Both commands used only CPU in the existing
allocation. No new allocation was requested.

Machine-readable evidence: [results](lock_gate_v6_results.json),
[metrics CSV](lock_gate_v6_metrics.csv), [paper source](lock_gate_v6_paper_source.json),
[test output](unit_tests_gate_v6.txt), [execution output](lock_gate_v6_execution.txt).

This remains a SNAP3D formula reimplementation with MuJoCo rigid_flex and pinned
ground parts, not IPC. The new threshold does not establish connector retention;
the loaded headed gate still fails.
