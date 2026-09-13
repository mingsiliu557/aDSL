#!/usr/bin/env bash
set -Eeuo pipefail

REPO=/vepfs_default/chanxueyan/lhp/lms/aDSL
LMS_BASHRC=/vepfs_default/chanxueyan/lhp/lms/.bashrc
PYTHON=/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python

source "$LMS_BASHRC"
# One batch owns the shared proxy lifecycle. Never stop a pre-existing proxy
# or a replacement started by another caller.
mkdir -p "$REPO/local_experiment"
exec 9>"$REPO/local_experiment/.stepcode-batch.lock"
flock -n 9 || { echo "Another batch owns the StepCode proxy." >&2; exit 2; }
pid_file="$LMS_STEPCODE_ROOT/run/header-proxy.pid"
if [[ -r "$pid_file" ]]; then
    previous_pid=$(<"$pid_file")
    if [[ "$previous_pid" =~ ^[0-9]+$ ]] && kill -0 "$previous_pid" 2>/dev/null; then
        echo "StepCode proxy already in use (PID=$previous_pid); refusing to take ownership." >&2
        exit 2
    fi
fi
lms_proxy start
owned_pid=$(<"$pid_file")
cleanup_proxy() {
    rc=$?
    trap - EXIT
    if [[ -r "$pid_file" && "$(<"$pid_file")" == "$owned_pid" ]]; then
        lms_proxy stop || true
    else
        echo "Proxy owner changed; not stopping another process." >&2
    fi
    exit "$rc"
}
trap cleanup_proxy EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
lms_proxy health

set +e
"$PYTHON" -u "$REPO/experiments/standing_fea_30/run_batch.py" "$@" 9>&-
rc=$?
set -e
exit "$rc"
