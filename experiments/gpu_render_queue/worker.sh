#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT=/vepfs_default/chanxueyan/lhp/lms/aDSL
QUEUE_BASE=/jiigan-hp/lms/aDSL/experiment/gpu_render_queue
LMS_BASHRC=/vepfs_default/chanxueyan/lhp/lms/.bashrc
ADSL_PYTHON=/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python
QUEUE_TOOL="$REPO_ROOT/adsl-core/tools/gpu_render_queue.py"

if (( $# != 2 )); then
    echo "usage: worker.sh QUEUE_ROOT RUN_ROOT" >&2
    exit 2
fi
QUEUE_ROOT=$1
RUN_ROOT=$2
queue_real=$(realpath -e "$QUEUE_ROOT")
run_real=$(realpath -e "$RUN_ROOT")
if [[ "$queue_real" != "$QUEUE_BASE" ]]; then
    echo "Queue must resolve exactly to $QUEUE_BASE: $queue_real" >&2
    exit 2
fi
read -r mount_target mount_type < <(findmnt -T "$queue_real" -n -o TARGET,FSTYPE)
if [[ "$mount_target" != /jiigan-hp || "$mount_type" != hpvs_fs* ]]; then
    echo "Queue is not on the jiigan-hp data mount: $mount_target $mount_type" >&2
    exit 2
fi
case "$run_real/" in
    "$queue_real"/runs/*) ;;
    *)
        echo "Run directory must resolve below $queue_real/runs: $run_real" >&2
        exit 2
        ;;
esac

worker_done=0
on_worker_exit() {
    rc=$?
    if (( rc != 0 )); then
        printf '%s\n' "$rc" > "$run_real/FAILED.exit_code"
    fi
    touch "$run_real/WORKER_FINISHED"
    worker_done=1
    exit "$rc"
}
trap on_worker_exit EXIT
trap 'exit 130' INT TERM HUP

source "$LMS_BASHRC"
if ! nvidia-smi -L 2>/dev/null | grep -q '^GPU '; then
    echo "Persistent renderer requires an allocated NVIDIA GPU." >&2
    exit 2
fi

unset LIBGL_DRIVERS_PATH
unset __EGL_VENDOR_LIBRARY_DIRS
NVIDIA_EGL_VENDOR=/usr/share/glvnd/egl_vendor.d/10_nvidia.json
test -f "$NVIDIA_EGL_VENDOR"
export __EGL_VENDOR_LIBRARY_FILENAMES="$NVIDIA_EGL_VENDOR"
export PYTHONPATH="$REPO_ROOT/adsl-core:$REPO_ROOT/adsl-agents${PYTHONPATH:+:$PYTHONPATH}"
export ADSL_RENDER_ENGINE=BLENDER_EEVEE
export PYTHONUNBUFFERED=1

WORKER_TMP=$(mktemp -d /tmp/adsl_gpu_render_worker.XXXXXX)
export TMPDIR="$WORKER_TMP/tmp"
export CUDA_CACHE_PATH="$WORKER_TMP/cuda"
mkdir -p "$TMPDIR" "$CUDA_CACHE_PATH"

date -u +%FT%TZ
hostname
nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv,noheader
touch "$run_real/WORKER_STARTED"

"$ADSL_PYTHON" -u "$QUEUE_TOOL" serve     --queue-root "$queue_real"     --poll-interval "${ADSL_GPU_QUEUE_POLL_SECONDS:-0.5}"     --idle-timeout "${ADSL_GPU_QUEUE_IDLE_TIMEOUT_SECONDS:-7200}"     --job-timeout "${ADSL_GPU_RENDER_JOB_TIMEOUT_SECONDS:-900}"     --quiesce-timeout "${ADSL_GPU_QUIESCE_TIMEOUT_SECONDS:-60}"     --memory-tolerance-mib "${ADSL_GPU_MEMORY_TOLERANCE_MIB:-256}"

touch "$run_real/SUCCESS"
