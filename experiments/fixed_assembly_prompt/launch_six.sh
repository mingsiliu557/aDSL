#!/usr/bin/env bash
# One frozen six-case batch; reuse an already running independent CLIProxy.
set -eo pipefail
repo=/vepfs_default/chanxueyan/lhp/lms/aDSL
lms=/vepfs_default/chanxueyan/lhp/lms
[[ $# == 1 ]] || { echo 'usage: launch_six.sh NEW_OUTPUT_DIRECTORY' >&2; exit 2; }
cd "$repo"
source "$lms/.bashrc"
set -eo pipefail
cliproxy_status
cliproxy_use
export PYTHONPATH="$repo${PYTHONPATH:+:$PYTHONPATH}"
export ADSL_RENDER_ENGINE=CYCLES ADSL_RENDER_WIDTH=512 ADSL_RENDER_HEIGHT=512 ADSL_RENDER_SAMPLES=32
export OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 ADSL_ASSET_EXECUTOR_TIMEOUT_SECONDS=300
unset ADSL_GPU_RENDER_QUEUE
"$lms/envs/adsl/bin/python" -u experiments/fixed_assembly_prompt/run_six.py --root "$1" 2>&1 | tee "${1}.console.log"
