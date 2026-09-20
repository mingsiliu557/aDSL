#!/usr/bin/env bash
# One archived failed source, with visual/code review only. No new generation.
set -eo pipefail
repo=/vepfs_default/chanxueyan/lhp/lms/aDSL
lms=/vepfs_default/chanxueyan/lhp/lms
cd "$repo"
source "$lms/.bashrc"
set -e
owned_proxy=0
cleanup() { if [[ "$owned_proxy" == 1 ]]; then lms_proxy stop; fi; }
trap cleanup EXIT
if ! lms_proxy status >/dev/null 2>&1; then
    lms_proxy start
    owned_proxy=1
fi
lms_proxy health
export PYTHONPATH="$repo${PYTHONPATH:+:$PYTHONPATH}"
export ADSL_RENDER_ENGINE=CYCLES ADSL_RENDER_WIDTH=512 ADSL_RENDER_HEIGHT=512 ADSL_RENDER_SAMPLES=32
export OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1
unset ADSL_GPU_RENDER_QUEUE
"$lms/envs/adsl/bin/python" -u experiments/fixed_assembly_prompt/verify_candidate.py "$@"
