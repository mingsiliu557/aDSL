#!/usr/bin/env bash
set -eo pipefail
source /vepfs_default/chanxueyan/lhp/lms/.bashrc >/dev/null 2>&1
cliproxy_use >/dev/null 2>&1
source /vepfs_default/chanxueyan/lhp/lms/aDSL/temp/geometry_sf06_no_fea_20260929/env.sh
python - <<'PY'
import json,os,urllib.request
request=urllib.request.Request(os.environ['CLIPROXYAPI_BASE_URL'].rstrip('/')+'/models',headers={'Authorization':'Bearer '+os.environ['CLIPROXYAPI_API_KEY']})
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(request,timeout=15) as response:models=json.load(response)['data']
assert any(m['id']=='gpt-5.6-sol' for m in models)
print('CLIProxy model preflight PASS; credentials omitted.',flush=True)
PY
exec python -u /vepfs_default/chanxueyan/lhp/lms/aDSL/temp/geometry_sf06_no_fea_20260929/run_case.py
