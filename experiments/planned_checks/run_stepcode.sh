#!/usr/bin/env bash
# Baselines only: plan all, check three smoke cases, then the remaining assets.
set -eo pipefail
cd /vepfs_default/chanxueyan/lhp/lms/aDSL
source /vepfs_default/chanxueyan/lhp/lms/.bashrc
output_dir="$1"
python_bin=/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python
proxy_owned=0
finish() {
  result=$?
  trap - EXIT
  "$python_bin" experiments/planned_checks/run.py summarize --output "$output_dir" || true
  if [[ "$proxy_owned" == 1 ]]; then lms_proxy stop || true; fi
  echo "planned-checks worker finished: exit=$result; mixed editing remains disabled"
  exit "$result"
}
trap finish EXIT
# Do not displace a listener belonging to another experiment.
if ss -ltn '( sport = :44949 )' | tail -n +2 | grep -q .; then
  lms_proxy health
else
  proxy_owned=1
  lms_proxy start
  lms_proxy health
fi
# Resume any already frozen plans before making more API requests.
"$python_bin" experiments/planned_checks/run.py smoke --output "$output_dir"
"$python_bin" experiments/planned_checks/run.py plan-only --output "$output_dir"
"$python_bin" experiments/planned_checks/run.py smoke --output "$output_dir"
"$python_bin" experiments/planned_checks/run.py measure-only --output "$output_dir"
