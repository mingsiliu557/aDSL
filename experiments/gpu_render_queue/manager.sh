#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT=/vepfs_default/chanxueyan/lhp/lms/aDSL
QUEUE_BASE=/jiigan-hp/lms/aDSL/experiment/gpu_render_queue
ADSL_PYTHON=/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python
QUEUE_TOOL="$REPO_ROOT/adsl-core/tools/gpu_render_queue.py"
WORKER="$REPO_ROOT/experiments/gpu_render_queue/worker.sh"
QUEUE_ID=${ADSL_GPU_QUEUE_ID:-q-20250901110548-6w2bl}
FLAVOR_ID=${ADSL_GPU_FLAVOR_ID:-ml.pni2l.3xlarge}
HARD_TIMEOUT_SECONDS=${ADSL_GPU_ALLOCATION_TIMEOUT_SECONDS:-43200}

if (( $# != 2 )); then
    echo "usage: manager.sh QUEUE_ROOT RUN_ROOT" >&2
    exit 2
fi
QUEUE_ROOT=$1
RUN_ROOT=$2

"$ADSL_PYTHON" "$QUEUE_TOOL" init --queue-root "$QUEUE_ROOT" >/dev/null
queue_real=$(realpath -e "$QUEUE_ROOT")
if [[ "$queue_real" != "$QUEUE_BASE" ]]; then
    echo "Queue must resolve exactly to $QUEUE_BASE: $queue_real" >&2
    exit 2
fi
read -r mount_target mount_type < <(findmnt -T "$queue_real" -n -o TARGET,FSTYPE)
if [[ "$mount_target" != /jiigan-hp || "$mount_type" != hpvs_fs* ]]; then
    echo "Queue is not on the jiigan-hp data mount: $mount_target $mount_type" >&2
    exit 2
fi
if [[ -e "$RUN_ROOT" ]]; then
    echo "Refusing to reuse manager run directory: $RUN_ROOT" >&2
    exit 2
fi
mkdir -p "$RUN_ROOT"
run_real=$(realpath -e "$RUN_ROOT")
case "$run_real/" in
    "$queue_real"/runs/*) ;;
    *)
        echo "Run directory must resolve below $queue_real/runs: $run_real" >&2
        exit 2
        ;;
esac
bash -n "$WORKER"

manager_done=0
on_manager_exit() {
    rc=$?
    if (( manager_done == 0 )); then
        touch "$run_real/MANAGER_ABORTED"
    fi
    exit "$rc"
}
trap on_manager_exit EXIT
trap 'exit 130' INT TERM HUP

printf '[manager] START %s queue=%s flavor=%s\n'     "$(date -u +%FT%TZ)" "$queue_real" "$FLAVOR_ID" | tee -a "$run_real/manager.log"

set +e
timeout     --foreground     --signal=TERM     --kill-after=120s     "${HARD_TIMEOUT_SECONDS}s"     volc ml_devinstance launch       --resource_queue_id "$QUEUE_ID"       --flavor_id "$FLAVOR_ID"       bash "$WORKER" "$queue_real" "$run_real"     2>&1 | tee -a "$run_real/launch.log"
pipeline_rc=("${PIPESTATUS[@]}")
set -e
launch_rc=${pipeline_rc[0]}
tee_rc=${pipeline_rc[1]}

if (( tee_rc != 0 )); then
    printf '%s\n' "$tee_rc" > "$run_real/LAUNCH_LOG_FAILED.exit_code"
    manager_done=1
    exit 1
fi
if (( launch_rc != 0 )); then
    printf '%s\n' "$launch_rc" > "$run_real/MANAGER_FAILED.exit_code"
    if (( launch_rc == 124 || launch_rc == 137 || launch_rc == 130 || launch_rc == 143 ))         || [[ ! -f "$run_real/WORKER_FINISHED" ]]; then
        touch "$run_real/RELEASE_CHECK_REQUIRED"
    fi
    manager_done=1
    exit 1
fi
if [[ ! -f "$run_real/WORKER_FINISHED" ]]     || [[ ! -f "$run_real/SUCCESS" ]]     || [[ -e "$run_real/FAILED.exit_code" ]]; then
    touch "$run_real/MANAGER_INCOMPLETE"
    manager_done=1
    exit 1
fi

touch "$run_real/MANAGER_SUCCESS"
printf '[manager] SUCCESS %s\n' "$(date -u +%FT%TZ)" | tee -a "$run_real/manager.log"
manager_done=1
