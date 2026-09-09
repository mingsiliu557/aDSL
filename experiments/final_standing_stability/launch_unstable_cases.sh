#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT=/vepfs_default/chanxueyan/lhp/lms/aDSL
PYTHON=/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python
ADSL_RUN=/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/adsl-run
CODEX=/vepfs_default/chanxueyan/lhp/lms/npm-global/bin/codex
QUEUE_ROOT=/jiigan-hp/lms/aDSL/experiment/gpu_render_queue
EXP_ROOT=/jiigan-hp/lms/aDSL/experiment/physics_analysis/01_unstable_case_validation_20260904
ATTEMPT_ID=${ADSL_STANDING_ATTEMPT_ID:-attempt_02}
if [[ ! "$ATTEMPT_ID" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]]; then
    echo "unsafe attempt id: $ATTEMPT_ID" >&2
    exit 2
fi
MANIFEST="$EXP_ROOT/case_manifest.json"
OUTPUT_ROOT="$EXP_ROOT/generated"
MODEL_CONFIG="$REPO_ROOT/adsl-agents/configs/llm/codex-cli-gpt-5.6-sol.yaml"
LOG="$EXP_ROOT/generation_manager_${ATTEMPT_ID}.log"
SUCCESS_MARKER="$EXP_ROOT/INITIAL_GENERATION_${ATTEMPT_ID}_SUCCESS"
FAILURE_MARKER="$EXP_ROOT/INITIAL_GENERATION_${ATTEMPT_ID}_FAILED.exit_code"

read -r mount_target mount_type < <(findmnt -T "$EXP_ROOT" -n -o TARGET,FSTYPE)
if [[ "$mount_target" != /jiigan-hp || "$mount_type" != hpvs_fs* ]]; then
    echo "experiment root is not on the jiigan-hp data mount" >&2
    exit 2
fi
test -w "$EXP_ROOT"
test -s "$MANIFEST"
test -x "$PYTHON"
test -x "$ADSL_RUN"
test -x "$CODEX"

exec >>"$LOG" 2>&1
started_at=$(date -u +%FT%TZ)
printf '[generation-manager] START attempt=%s %s\n' "$ATTEMPT_ID" "$started_at"

finished=0
on_exit() {
    rc=$?
    trap - EXIT
    if (( finished == 0 )); then
        printf '%s\n' "$rc" >"$FAILURE_MARKER"
        printf '[generation-manager] FAILED rc=%s %s\n' "$rc" "$(date -u +%FT%TZ)"
    fi
    exit "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT TERM HUP

export ADSL_GPU_RENDER_QUEUE="$QUEUE_ROOT"
export ADSL_GPU_START_WAIT_SECONDS=${ADSL_GPU_START_WAIT_SECONDS:-200000}
bash "$REPO_ROOT/experiments/gpu_render_queue/control.sh" wait

"$PYTHON" "$REPO_ROOT/experiments/final_standing_stability/run_unstable_cases.py" \
    --manifest "$MANIFEST" \
    --output-root "$OUTPUT_ROOT" \
    --split initial \
    --model-config "$MODEL_CONFIG" \
    --adsl-run "$ADSL_RUN" \
    --codex-binary "$CODEX" \
    --queue-root "$QUEUE_ROOT" \
    --max-rounds 2

touch "$SUCCESS_MARKER"
printf '[generation-manager] SUCCESS %s\n' "$(date -u +%FT%TZ)"
finished=1
