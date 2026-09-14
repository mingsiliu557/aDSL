#!/usr/bin/env bash
set -Eeuo pipefail
REPO=/vepfs_default/chanxueyan/lhp/lms/aDSL
tag=${1:-$(date -u +%Y%m%dT%H%M%SZ)}
if (( $# )); then shift; fi
case_arguments=""
while (( $# )); do
    [[ "$1" == "--case" && $# -ge 2 && "$2" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "Expected --case ID"; exit 2; }
    case_arguments+=" --case $2"
    shift 2
done
[[ "$tag" =~ ^[A-Za-z0-9_-]+$ ]] || exit 2
session="adsl_overhang_$tag"
output="$REPO/local_experiment/overhang_paired_$tag"
[[ ! -e "$output" ]] || { echo "Output already exists: $output"; exit 2; }
bash -n "$REPO/experiments/overhang_feedback/run_paired.sh"
mkdir -p "$output/provenance"
git -C "$REPO" rev-parse HEAD > "$output/provenance/commit.txt"
git -C "$REPO" diff -- adsl-agents experiments/overhang_feedback \
    experiments/support_requirement_critical_surfaces/analyze.py experiments/workflow_checkers/run.py \
    > "$output/provenance/changes.patch"
cp "$REPO/experiments/overhang_feedback/paired_assets.json" "$output/provenance/"
cp "$REPO/experiments/overhang_feedback/exterior.py" "$REPO/adsl-agents/overhang_edit.py" "$output/provenance/"
tmux new-session -d -s "$session" \
    "bash -o pipefail -c 'bash \"$REPO/experiments/overhang_feedback/run_paired.sh\" \"$output\" $case_arguments 2>&1 | tee -a \"$output/batch.log\"'"
tmux set-option -w -t "$session" remain-on-exit on
printf 'session=%s\noutput=%s\n' "$session" "$output" | tee "$output/submission.txt"
