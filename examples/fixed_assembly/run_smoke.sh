#!/usr/bin/env bash
# One fresh prompt, StepCode, no physics checker. No GPU allocation or env install.
set -eo pipefail
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_dir"
source /vepfs_default/chanxueyan/lhp/lms/.bashrc
set -eo pipefail
experiment_dir="${1:?Supply a NEW output directory}"
python_bin=/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python
proxy_owned=0
proxy_pid=''
finish() {
  result=$?
  trap - EXIT
  if [[ "$proxy_owned" == 1 ]] && [[ -r "$LMS_STEPCODE_ROOT/run/header-proxy.pid" ]] &&
     [[ "$(<"$LMS_STEPCODE_ROOT/run/header-proxy.pid")" == "$proxy_pid" ]]; then
    lms_proxy stop || true
  fi
  echo "fixed assembly smoke finished: exit=$result"
  exit "$result"
}
trap finish EXIT
if ss -ltn '( sport = :44949 )' | tail -n +2 | grep -q .; then
  lms_proxy health
else
  lms_proxy start
  proxy_owned=1
  proxy_pid="$(<"$LMS_STEPCODE_ROOT/run/header-proxy.pid")"
  lms_proxy health
fi
unset ADSL_GPU_RENDER_QUEUE
export ADSL_RENDER_ENGINE=CYCLES
export ADSL_RENDER_WIDTH=512 ADSL_RENDER_HEIGHT=512 ADSL_RENDER_SAMPLES=32
export OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1
export PYTHONUNBUFFERED=1
"$python_bin" -m adsl.agents.cli --model-config adsl-agents/configs/llm/stepcode-gpt-5.6-sol.yaml create \
  'Generate a simple two-piece T-shaped insertion bracket, made from a horizontal rectangular crossbar and a vertical rectangular stem. In its assembled pose it has an upright capital-T silhouette: crossbar 60 mm wide, 20 mm deep, 12 mm high at the top; stem 20 mm wide, 12 mm deep, extending 50 mm below the crossbar. The total assembled size is exactly 60 x 20 x 62 mm. Use exactly two separately printable parts and one central rectangular tab-slot interface: the tab belongs to the stem and inserts upward into the underside of the crossbar. Keep a substantial shoulder and mount material, with a shallow lead-in on the tab. Use an explicit positive clearance of 0.2 mm per side only as an uncalibrated geometric demonstration, not a proven locking fit. No bolts, glue, moving joints or decorative elements. Choose consistent remaining joint dimensions and assembly frames yourself using the fixed assembly API. Write one complete program. Preserve the T silhouette and the stated dimensions during any repair.' \
  --output "$experiment_dir" --task-id fixed_assembly_t_bracket \
  --max-rounds 3 --fixed-assembly-config examples/fixed_assembly/config.json
