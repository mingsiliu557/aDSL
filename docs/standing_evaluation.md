# Material-component Standing observation and physical reporting

Current protocol: `material_component_standing_v1`. This upgrades the former
`independent_contact_warning_v1` root-only observations. Historical results keep
their original protocol and cannot be relabeled or mixed with new measurements.

## Bodies, units and interface tracking

Each original print part is evaluated as material and split into independent,
cavity-aware connected components. The shared core `material_components(solid)`
preserves owned cavity boundaries; it does not fill holes or join small gaps.
No union is performed across print parts. Every component receives its own free
body, mass, inertia and collision representation. N remains the original print
part count, not the number of bodies. Invalid material, missing input, invalid
mass/inertia or unsupported collision proxies still prevent a successful
observation. Negative-fit elastic press fits remain unsupported.

`_prepare_simulation_bodies(report, meshes)` provides a single `body_layout` to
`xml_model`, `simulate` and `save_frames` through a required keyword argument.
Meshes retain their original part-local millimetre coordinates and assembly
transforms. Single components retain the original part ID. Component IDs are
unique and deterministic. `artifacts.body_mapping` records original ownership,
net volume, transforms, root selection, interface ownership and actual joint
addresses without embedding meshes. It does not modify the original manifest.

The root component is the largest net-volume material component of the declared
root part; deterministic component order breaks ties. Initial masses and world
centres of mass are first aggregated per original print part before applying
the existing initial directional gravity-moment diagnostic. That diagnostic,
root finding ID and source-index entry points retain their meanings. Component
findings map directly back to their original print part, not to a newly invented
semantic ID or a claimed global tipping cause.

Singleton interface endpoints retain existing tracking. Multipart endpoints
bind only on unique material-query evidence: the tab query intersects actual
material, and the slot receiver uses the existing four outside wall precision
bands. Zero or multiple owners are unavailable or ambiguous; no nearest-body
substitution is made. Other components continue to be observed. This ownership
only identifies which body is tracked; it does not certify interface geometry,
mechanical locking or load bearing.

## Observation and verdict

Every body uses its own initial rotation and the existing sampled tilt formula:
`degrees(acos(clip((R_current @ R_initial.T)[2,2], -1, 1)))`.
All bodies share the original sampling times, including initial and final frames.
Only a peak strictly greater than `configuration.tilt_threshold_deg` is tipping;
the configured threshold is not replaced by another constant. Pure yaw is not
tilt. Existing duration, timestep, gravity, friction, contact radii, settling
policy and interface-exit queries are retained. Ground-contact bodies are free.

- Any confirmed root/component tipping or declared interface exit gives FAIL.
  Later incomplete observation or a known solver error cannot erase that event.
- PASS requires the complete configured observation of all expected components
  and required interfaces, without any confirmed failure.
- Otherwise the original error/unavailable status and reason are retained. No
  missing observation is replaced with zero tilt or a fabricated negative flag.

`standing_observation.observed_tipping`, root peak/final tilt and duration keep
their original root meanings. Three scalars are added:

- `component_tilt_verified`: all expected bodies completed tilt observation.
- `observed_component_tipping`: true for any confirmed body tilt; false only
  after complete negative observation, otherwise null. This includes the root.
- `max_component_tilt_deg`: the maximum measured body peak, or null if unknown.

Full measurements remain in `metrics.component_tilts` and trajectory artifacts.
Event filtering never changes these measurements or the verdict. Root tipping
keeps `SELF_WEIGHT_TIPPING`. Non-root events produce
`SELF_WEIGHT_COMPONENT_TIPPING` only if earlier than an observed root event;
same-frame/later events are omitted from additional findings. Missing times are
retained as `UNORDERED`. If no root tip was observed, confirmed non-root events
remain. Filtering occurs before selecting the highest retained peak per original
print part, with deterministic time/ID tie breaks. Earlier events do not imply
causation. Interface exits retain `SELF_WEIGHT_INTERFACE_EXIT`.

Initial contact depth remains a nonblocking warning. Topology is independent;
Standing PASS does not override required Topology failures or prove collision
model correctness, manufacturability or structural continuity. A loose component
that only translates/falls without exceeding the tilt threshold or triggering a
declared interface exit is not detected as a failure by this protocol.

## Feedback and reporting

Engineering, Coder and Code Critic receive the same bounded three-scalar summary
and retained finding scalars through the existing feedback adapter. Component
lists, contact arrays and trajectories remain in source-bound report files; the
feedback budget is unchanged. Adding peak tilt to the shared domain whitelist
also preserves the existing root finding's measured peak in `key_values`.

`classify_standing_record` requires the new protocol and complete component
evidence for PASS, checks finite nonnegative peaks against the recorded actual
configuration, and rejects contradictory flags/peaks. `COMPONENT_TIPPING` comes
only from retained component findings. Root tipping and interface-exit reason
counts keep their meanings. Terminal failures remain in the frozen denominator;
pending work remains incomplete. Statistics never introduce a new repair finding.

New automatic reports consume `admission_record.connectivity`; historical
connectivity-only outputs remain a separate path. Standing, connectivity, NSV24
and N retain their own protocol/identity bindings. The existing required-checker
publication and candidate acceptance rules are not relaxed.

## Validation and subsequent experiments

This change is verified with deterministic fake solver trajectories, report
stubs and artificial CPU geometry only. Real MuJoCo, model calls, rendering,
GPU experiments and benchmark remeasurement have not been executed for the new
protocol. Earlier physical validation reports describe their historical protocol.

A future comparison must rerun Standing for all methods over the same frozen
cases and assets under this protocol, writing independent outputs. Unchanged
connectivity/NSV24/N may then be reused with matching identity. A SNAP3D-style
fixed-ground assembly-retention experiment requires separate authorization,
conditions and displacement/rotation definitions; this implementation does not
claim to reproduce it or run a second simulation.
