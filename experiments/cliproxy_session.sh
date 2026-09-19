#!/usr/bin/env bash
# Dedicated proxy/log terminal, independent of experiment lifetimes.
set -euo pipefail
repo=/vepfs_default/chanxueyan/lhp/lms/aDSL
session=${1:-adsl_cliproxy}
bash "$repo/experiments/tmux_session.sh" "$session" "$repo" bash -c '
    source /vepfs_default/chanxueyan/lhp/lms/.bashrc
    set -e
    cliproxy_start
    cliproxy_status
    printf "CLIProxy runs independently. Ctrl-C only stops this log view; use cliproxy_stop explicitly to stop the service.\n"
    tail -n 0 -F /vepfs_default/chanxueyan/lhp/lms/easycliproxyapi/logs/service.log
'
