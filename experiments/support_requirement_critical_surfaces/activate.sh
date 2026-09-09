#!/usr/bin/env bash

export ADSL_PRUSASLICER_ROOT="/vepfs_default/chanxueyan/lhp/lms/tools/prusaslicer/2.4.0/sysroot"
export ADSL_PRUSASLICER_BIN="${ADSL_PRUSASLICER_ROOT}/usr/bin/prusa-slicer"

adsl_prusaslicer() {
  LD_LIBRARY_PATH="${ADSL_PRUSASLICER_ROOT}/usr/lib/x86_64-linux-gnu:${ADSL_PRUSASLICER_ROOT}/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" \
    "${ADSL_PRUSASLICER_BIN}" "$@"
}
