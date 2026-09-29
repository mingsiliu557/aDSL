#!/bin/bash
source /vepfs_default/chanxueyan/lhp/lms/.bashrc >/dev/null 2>&1
cliproxy_use >/dev/null 2>&1
source /vepfs_default/chanxueyan/lhp/lms/aDSL/temp/geometry_expression_20260929/env.sh
unset ADSL_GPU_RENDER_QUEUE
export OPENAI_AGENTS_DISABLE_TRACING=1 ADSL_RENDER_ENGINE=CYCLES
DEMO_ROOT=/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/geometry_expression_20260929
DEMO_ARM=$1
export PYTHONPATH=$DEMO_ROOT/pythonpath/$DEMO_ARM
for DEMO_CASE in SF06 SF21 T02-bookshelf; do
 python /tmp/adsl_geometry_20260929/experiments/geometry_expression/run_demo.py --case "$DEMO_CASE" --arm "$DEMO_ARM" --output "$DEMO_ROOT/$DEMO_ARM/$DEMO_CASE" --model-profile /tmp/adsl_geometry_20260929/adsl-agents/configs/llm/cliproxy-gpt-5.6-sol.yaml --session-root /tmp/adsl_geometry_demo_sessions --timeout 300 > "$DEMO_ROOT/${DEMO_ARM}_${DEMO_CASE}.log" 2>&1
 printf '%s %s exit=%s time=%s\n' "$DEMO_ARM" "$DEMO_CASE" "$?" "$(date -u +%FT%TZ)" >> "$DEMO_ROOT/completions.log"
done
