#!/usr/bin/env bash
set -e
ELEPHANT_ROOT=/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/geometry_elephant_000_20260929
while [ ! -f "$ELEPHANT_ROOT/A/demo_result.json" ] || [ ! -f "$ELEPHANT_ROOT/B/demo_result.json" ]; do sleep 15; done
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python "$ELEPHANT_ROOT/compare.py"
