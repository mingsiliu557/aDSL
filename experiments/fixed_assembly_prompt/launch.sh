#!/usr/bin/env bash
# Native prompt-to-3D, isolated from the earlier existing-asset conversion.
set -eo pipefail
repo=/vepfs_default/chanxueyan/lhp/lms/aDSL
lms=/vepfs_default/chanxueyan/lhp/lms
cd "$repo"
source "$lms/.bashrc"
set -e
if ! cliproxy_status; then
    echo 'Start CLIProxy in its own tmux first: bash experiments/cliproxy_session.sh adsl_cliproxy' >&2
    exit 1
fi
cliproxy_use
export PYTHONPATH="$repo${PYTHONPATH:+:$PYTHONPATH}"
export ADSL_RENDER_ENGINE=CYCLES ADSL_RENDER_WIDTH=512 ADSL_RENDER_HEIGHT=512 ADSL_RENDER_SAMPLES=32
export OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1
unset ADSL_GPU_RENDER_QUEUE
"$lms/envs/adsl/bin/python" - <<'PY'
import json, os, urllib.request
request=urllib.request.Request(os.environ['CLIPROXYAPI_BASE_URL'].rstrip('/')+'/models',
    headers={'Authorization':'Bearer '+os.environ['CLIPROXYAPI_API_KEY']})
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(request, timeout=15) as response: models=json.load(response)['data']
assert any(m['id']=='gpt-5.6-sol' for m in models)
print('CLIProxy health/model check passed (no credentials logged).', flush=True)
PY
"$lms/envs/adsl/bin/python" -u experiments/fixed_assembly_prompt/run.py "$@"
