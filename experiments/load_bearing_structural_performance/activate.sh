#!/usr/bin/env bash
# User-level FEA runtime. Source this file; it does not install system packages.
export ADSL_FEA_ROOT=/vepfs_default/chanxueyan/lhp/lms/fea_runtime
export ADSL_CCX_BIN="$ADSL_FEA_ROOT/calculix-2.23/ccx_2.23"
export ADSL_GMSH_ROOT="$ADSL_FEA_ROOT/gmsh-4.15.2"
export ADSL_FEA_SYSROOT="$ADSL_FEA_ROOT/sysroot"

adsl_fea_python() {
  env \
    LD_LIBRARY_PATH="$ADSL_FEA_SYSROOT/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    PYTHONPATH="$ADSL_GMSH_ROOT/lib${PYTHONPATH:+:$PYTHONPATH}" \
    /vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python "$@"
}

adsl_gmsh() {
  env \
    LD_LIBRARY_PATH="$ADSL_FEA_SYSROOT/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$ADSL_GMSH_ROOT/bin/gmsh" "$@"
}
