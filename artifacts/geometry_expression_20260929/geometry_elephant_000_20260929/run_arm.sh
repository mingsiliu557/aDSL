#!/usr/bin/env bash
set -eo pipefail
source /vepfs_default/chanxueyan/lhp/lms/.bashrc >/dev/null 2>&1
cliproxy_use >/dev/null 2>&1
source /vepfs_default/chanxueyan/lhp/lms/aDSL/temp/geometry_expression_20260929/env.sh
unset ADSL_GPU_RENDER_QUEUE
export OPENAI_AGENTS_DISABLE_TRACING=1 ADSL_RENDER_ENGINE=CYCLES PYTHONUNBUFFERED=1
ELEPHANT_ROOT=/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/geometry_elephant_000_20260929
ELEPHANT_ARM=$1
export PYTHONPATH=/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/geometry_expression_20260929/pythonpath/$ELEPHANT_ARM
exec python /tmp/adsl_geometry_20260929/experiments/geometry_expression/run_demo.py --cases "$ELEPHANT_ROOT/cases.json" --case elephant_000 --arm "$ELEPHANT_ARM" --output "$ELEPHANT_ROOT/$ELEPHANT_ARM" --model-profile /tmp/adsl_geometry_20260929/adsl-agents/configs/llm/cliproxy-gpt-5.6-sol.yaml --session-root /tmp/adsl_geometry_demo_sessions --timeout 300
