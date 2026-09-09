# 03 Load-Bearing Structural Performance

This is a fail-closed research experiment for the frozen aDSL assets. It is
not a production checker and does not edit source models.

The pipeline first checks whether collision geometry describes a single,
watertight load-bearing body. Only primitive URDF geometry with one exact
Boolean-union component is rebuilt as OpenCASCADE CAD, tetrahedralized by
Gmsh, and solved by CalculiX. Disconnected assemblies, open meshes and
ambiguous semantic load regions are reported rather than silently bonded.

For every solvable case it runs three quadratic-tetrahedral mesh levels and
two load categories:

- self weight (`rho=1240 kg/m3`, gravity 9.81 m/s2);
- the frozen semantic service loads in `case_config.json`.

The current material is an isotropic PLA screening proxy (`E=3.0 GPa`,
`nu=0.35`, nominal yield strength `50 MPa`). It is not a printed-part material
card. An anisotropic model is deliberately deferred until coupon data and
print directions exist.

Outputs include mesh/solver status, maximum displacement, maximum von Mises
stress, nominal safety factor, first positive linear buckling factor and
element-centroid hotspot coordinates. Results are screening evidence, not a
manufacturing certification: the base is fully fixed, loads are idealized,
linear elasticity is used, and layer adhesion, defects, creep and nonlinear
contact are absent.

Typical invocation:

```bash
source /vepfs_default/chanxueyan/lhp/lms/.bashrc
adsl_fea_python experiments/load_bearing_structural_performance/analyze.py \
  --output-dir /jiigan-hp/lms/aDSL/experiment/physics_analysis/03_load_bearing_structural_performance
```

Use `--benchmark-only` to execute the cantilever and Euler-column validation
without touching the frozen model inputs.
