# 04 Support Requirement & Critical-Surface-Aware Support

This is a read-only experiment for the frozen aDSL assets. It does not modify
models or integrate a production checker.

The experiment combines two kinds of evidence:

- a triangle-normal precheck for downward overhang candidates;
- PrusaSlicer G-code roles for bridge infill, support material, and support
  material interface.

Support contact includes both the model underside nominally carried by interface
extrusions and support material growing from an existing upward-facing model
surface. It is not a zero-distance mesh intersection. Paths are buffered by
extrusion width and mapped back to source collision triangles. Critical surfaces
are explicitly selected with collision-name regexes and geometric face
selectors. A reliable critical surface with more than 0.01 mm2 nominal overlap
is a `VIOLATION`.

The generic PLA profile uses a 0.4 mm nozzle, 0.2 mm layers, a 45 degree
overhang threshold measured from the horizontal plane, everywhere rectilinear
support, three interface layers, and a 0.2 mm top contact gap. The semantic
real-world scales from experiment 03 are retained as metadata; printable meshes
are downscaled proportionally to at most 180 mm because the FEA scales are not
desktop-printer scales.

The runtime is user-local. On this host the official 2.8.1 Linux AppImages
cannot start because the old-distro build assumes unavailable desktop libraries
and the new-distro build requires GLIBC 2.36. The executable runtime therefore
uses Ubuntu Jammy's user-extracted PrusaSlicer 2.4.0 package, which provides all
FFF features required by this experiment. No package is installed system-wide.

```bash
source /vepfs_default/chanxueyan/lhp/lms/.bashrc
adsl_support_python experiments/support_requirement_critical_surfaces/analyze.py \
  --output-dir /jiigan-hp/lms/aDSL/experiment/physics_analysis/04_support_requirement_critical_surfaces
```

Use `--benchmark-only` for the synthetic cone, cantilever, and two-anchor bridge
fixtures, or repeat `--case CASE_ID` to select cases. All work is CPU-only.
Derived STL, G-code, logs, JSON, CSV, Markdown, and colored PLY overlays are
written below the output directory. In overlays, orange is overhang, blue is a
critical surface, red is nominal contact, and purple is overlap.
