#!/usr/bin/env bash
set -Eeuo pipefail
REPO=/vepfs_default/chanxueyan/lhp/lms/aDSL
PYTHON=/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python
source /vepfs_default/chanxueyan/lhp/lms/.bashrc
output=${1:?output directory required}
shift
mkdir -p "$output"
exec 9>"$REPO/local_experiment/.stepcode-batch.lock"
flock -n 9 || { echo 'Another batch owns the StepCode proxy; no experiment started.'; exit 2; }
pid_file="$LMS_STEPCODE_ROOT/run/header-proxy.pid"
if [[ -r "$pid_file" ]]; then
    previous_pid=$(<"$pid_file")
    if [[ "$previous_pid" =~ ^[0-9]+$ ]] && kill -0 "$previous_pid" 2>/dev/null; then
        echo "Proxy already in use; refusing to stop another task's proxy."
        exit 2
    fi
fi
lms_proxy start
owned_pid=$(<"$pid_file")
cleanup() {
    rc=$?
    trap - EXIT
    if [[ -r "$pid_file" && "$(<"$pid_file")" == "$owned_pid" ]]; then
        lms_proxy stop || true
    fi
    printf '%s\n' "$rc" > "$output/exit_code.txt"
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
lms_proxy health
cd "$REPO"
arguments=(--manifest experiments/overhang_feedback/paired_assets.json --output-root "$output" --max-candidates 2)
arguments+=("$@")
status=0
for phase in prepare edit evaluate; do
    echo "Starting phase: $phase"
    "$PYTHON" -u experiments/overhang_feedback/run_pilot.py "${arguments[@]}" --phase "$phase" \
        --model-config adsl-agents/configs/llm/stepcode-gpt-5.6-sol.yaml 9>&- || status=1
done
exit "$status"
