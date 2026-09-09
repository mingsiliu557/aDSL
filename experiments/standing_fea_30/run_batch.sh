#!/usr/bin/env bash
set -Eeuo pipefail

REPO=/vepfs_default/chanxueyan/lhp/lms/aDSL
LMS_BASHRC=/vepfs_default/chanxueyan/lhp/lms/.bashrc
PYTHON=/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python

source "$LMS_BASHRC"
lms_proxy start
trap 'lms_proxy stop >/dev/null 2>&1 || true' EXIT INT TERM HUP
lms_proxy health

set +e
"$PYTHON" "$REPO/experiments/standing_fea_30/run_batch.py" "$@"
rc=$?
set -e
exit "$rc"
