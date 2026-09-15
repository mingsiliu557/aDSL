#!/usr/bin/env bash
set -eo pipefail
cd /vepfs_default/chanxueyan/lhp/lms/aDSL
source /vepfs_default/chanxueyan/lhp/lms/.bashrc
output=${1:?output required}
shift
if [[ $# == 0 ]]; then
    set -- SF01 SF03 SF05 SF06 SF07 SF11 SF13 SF16 SF20 SF21 SF25 SF27
fi
python_bin=/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python
pidfile=/vepfs_default/chanxueyan/lhp/lms/easycliproxyapi/run/cli-proxy-api.pid
owned_pid=${ADSL_OWNED_CLIPROXY_PID:-}
finish() {
    result=$?
    trap - EXIT
    if [[ -n "$owned_pid" && -r "$pidfile" && "$(<"$pidfile")" == "$owned_pid" ]]; then
        cliproxy_stop || true
    fi
    echo "planned edit batch finished: exit=$result; inspect per-case statuses, not just shell exit"
    exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if ! cliproxy_status; then
    cliproxy_start
    owned_pid=$(<"$pidfile")
fi
cliproxy_use
"$python_bin" -u -m experiments.planned_checks.run_edit_smoke batch --output "$output" \
    --cases "$@"
