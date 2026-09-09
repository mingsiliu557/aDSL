#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT=/vepfs_default/chanxueyan/lhp/lms/aDSL
ADSL_PYTHON=/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python
QUEUE_TOOL="$REPO_ROOT/adsl-core/tools/gpu_render_queue.py"
MANAGER="$REPO_ROOT/experiments/gpu_render_queue/manager.sh"
QUEUE_ROOT=${ADSL_GPU_RENDER_QUEUE:-$REPO_ROOT/temp/gpu_render_queue}
SESSION=${ADSL_GPU_RENDER_TMUX_SESSION:-adsl_gpu_renderer}

usage() {
    echo "usage: control.sh {start|wait|status|stop}" >&2
}

command=${1:-}
case "$command" in
    start)
        "$ADSL_PYTHON" "$QUEUE_TOOL" init --queue-root "$QUEUE_ROOT" >/dev/null
        if "$ADSL_PYTHON" "$QUEUE_TOOL" status --queue-root "$QUEUE_ROOT"             | grep -q '"worker_live": true'; then
            echo "GPU render worker is already live."
            exit 0
        fi
        if tmux has-session -t "$SESSION" 2>/dev/null; then
            echo "tmux session exists but worker is not live: $SESSION" >&2
            echo "Inspect it before retrying: tmux capture-pane -pt $SESSION -S -120" >&2
            exit 1
        fi
        run_id=$(date -u +%Y%m%dT%H%M%SZ)
        run_root="$QUEUE_ROOT/runs/$run_id"
        mkdir -p "$QUEUE_ROOT/runs"
        tmux new-session -d -s "$SESSION"             bash "$MANAGER" "$QUEUE_ROOT" "$run_root"
        echo "Started tmux manager: $SESSION"
        echo "Queue: $QUEUE_ROOT"
        echo "Run: $run_root"
        echo "The GPU worker becomes live after volc finishes allocating."
        echo "Wait for it with: $0 wait"
        ;;
    wait)
        wait_timeout=${ADSL_GPU_START_WAIT_SECONDS:-1800}
        wait_started=$(date +%s)
        while ! "$ADSL_PYTHON" "$QUEUE_TOOL" status --queue-root "$QUEUE_ROOT" \
            | grep -q '"worker_live": true'; do
            if ! tmux has-session -t "$SESSION" 2>/dev/null; then
                echo "tmux manager exited before the worker became live." >&2
                exit 1
            fi
            wait_now=$(date +%s)
            if (( wait_now - wait_started >= wait_timeout )); then
                echo "Timed out waiting ${wait_timeout}s for the GPU worker." >&2
                exit 1
            fi
            sleep 2
        done
        echo "GPU render worker is live: $QUEUE_ROOT"
        ;;
    status)
        "$ADSL_PYTHON" "$QUEUE_TOOL" status --queue-root "$QUEUE_ROOT"
        if tmux has-session -t "$SESSION" 2>/dev/null; then
            echo "tmux_session=$SESSION state=present"
        else
            echo "tmux_session=$SESSION state=absent"
        fi
        ;;
    stop)
        "$ADSL_PYTHON" "$QUEUE_TOOL" stop --queue-root "$QUEUE_ROOT"
        echo "Graceful stop requested."
        echo "A running Blender job will finish; pending jobs remain queued."
        echo "Do not kill tmux. Use '$0 status' until worker_live=false."
        ;;
    *)
        usage
        exit 2
        ;;
esac
