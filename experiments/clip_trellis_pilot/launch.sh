#!/usr/bin/env bash
set -Eeuo pipefail

if (( $# != 1 )); then
    echo "usage: launch.sh RUN_ROOT" >&2
    exit 2
fi

RUN_ROOT=$(realpath -e "$1")
REPO_ROOT=/vepfs_default/chanxueyan/lhp/lms/aDSL
SESSION=adsl_clip_trellis_pilot
WORKER="$REPO_ROOT/experiments/clip_trellis_pilot/gpu_worker.sh"
LOG="$RUN_ROOT/logs/gpu_manager.log"

read -r mount_target mount_type < <(findmnt -T "$RUN_ROOT" -n -o TARGET,FSTYPE)
[[ "$mount_target" == /jiigan-hp && "$mount_type" == hpvs_fs ]]
mkdir -p "$RUN_ROOT/logs" "$RUN_ROOT/status"
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "tmux session already exists: $SESSION" >&2
    exit 1
fi

tmux new-session -d -s "$SESSION" bash -lc "volc ml_devinstance launch --resource_queue_id q-20250901110548-6w2bl --flavor_id ml.pni2l.3xlarge bash '$WORKER' '$RUN_ROOT' > '$LOG' 2>&1"

echo "Started $SESSION"
echo "Monitor: tmux capture-pane -pt $SESSION -S -120"
echo "Log: $LOG"
