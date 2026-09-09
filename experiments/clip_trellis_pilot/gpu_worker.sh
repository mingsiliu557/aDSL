#!/usr/bin/env bash
set -Eeuo pipefail

if (( $# != 1 )); then
    echo "usage: gpu_worker.sh RUN_ROOT" >&2
    exit 2
fi

RUN_ROOT=$(realpath -e "$1")
REPO_ROOT=/vepfs_default/chanxueyan/lhp/lms/aDSL
LMS_ROOT=/vepfs_default/chanxueyan/lhp/lms
ADSL_PYTHON="$LMS_ROOT/envs/adsl/bin/python"
MANIFEST="$RUN_ROOT/manifest.json"
TRELLIS_SOURCE=$(<"$RUN_ROOT/baseline/source_path.txt")
TRELLIS_ENV=$(<"$RUN_ROOT/baseline/environment_path.txt")
TRELLIS_PYTHON="$TRELLIS_ENV/bin/python"
mkdir -p "$RUN_ROOT/logs" "$RUN_ROOT/status"
if [[ -f "$RUN_ROOT/status/worker_error.txt" ]]; then
    mv "$RUN_ROOT/status/worker_error.txt" "$RUN_ROOT/logs/worker_error.previous.txt"
fi
exec >> "$RUN_ROOT/logs/worker_preflight.log" 2>&1
trap 'rc=$?; echo "worker error: line=$LINENO rc=$rc" > "$RUN_ROOT/status/worker_error.txt"; exit "$rc"' ERR

require_file() {
    if [[ ! -e "$1" ]]; then
        echo "required path missing: $1" >&2
        return 1
    fi
}

read -r mount_target mount_type < <(findmnt -T "$RUN_ROOT" -n -o TARGET,FSTYPE)
echo "data mount: target=$mount_target type=$mount_type"
case "$mount_target:$mount_type" in
    /jiigan-hp*:hpvs_fs*) ;;
    *)
        echo "unexpected data mount: target=$mount_target type=$mount_type" >&2
        exit 2
        ;;
esac
test -w "$RUN_ROOT"
require_file "$MANIFEST"
require_file "$ADSL_PYTHON"
require_file "$TRELLIS_PYTHON"
require_file "$TRELLIS_SOURCE"
test -x "$ADSL_PYTHON"
test -x "$TRELLIS_PYTHON"
echo "preflight paths ok"

source "$LMS_ROOT/.bashrc"
if ! nvidia-smi -L | grep -q '^GPU '; then
    echo "A GPU allocation is required." >&2
    exit 2
fi
unset LIBGL_DRIVERS_PATH
unset __EGL_VENDOR_LIBRARY_DIRS
export __EGL_VENDOR_LIBRARY_FILENAMES=/usr/share/glvnd/egl_vendor.d/10_nvidia.json
export PYTHONUNBUFFERED=1
export PYTHONPATH="$REPO_ROOT"
export http_proxy=http://127.0.0.1:7892
export https_proxy=http://127.0.0.1:7892
export HTTP_PROXY=http://127.0.0.1:7892
export HTTPS_PROXY=http://127.0.0.1:7892
export U2NET_HOME=/jiigan-hp/lms/aDSL/experiment/model_cache/rembg

date -u +%FT%TZ > "$RUN_ROOT/status/started_at.txt"
hostname > "$RUN_ROOT/status/hostname.txt"
nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv,noheader > "$RUN_ROOT/status/gpu.txt"

if "$TRELLIS_PYTHON" -u -m experiments.clip_trellis_pilot.run_trellis --manifest "$MANIFEST" --source-root "$TRELLIS_SOURCE" --model-root /jiigan-hp/TRELLIS --resume > "$RUN_ROOT/logs/trellis.log" 2>&1; then
    trellis_rc=0
else
    trellis_rc=$?
fi

if "$ADSL_PYTHON" -u -m experiments.clip_trellis_pilot.render_batch --manifest "$MANIFEST" --python "$ADSL_PYTHON" --repo-root "$REPO_ROOT" --resume > "$RUN_ROOT/logs/render.log" 2>&1; then
    render_rc=0
else
    render_rc=$?
fi

if "$TRELLIS_PYTHON" -u -m experiments.clip_trellis_pilot.score_clip --manifest "$MANIFEST" --output-dir "$RUN_ROOT/clip" --device cuda > "$RUN_ROOT/logs/clip.log" 2>&1; then
    clip_rc=0
else
    clip_rc=$?
fi

printf '%s\n' "$trellis_rc" > "$RUN_ROOT/status/trellis.exit_code"
printf '%s\n' "$render_rc" > "$RUN_ROOT/status/render.exit_code"
printf '%s\n' "$clip_rc" > "$RUN_ROOT/status/clip.exit_code"
date -u +%FT%TZ > "$RUN_ROOT/status/finished_at.txt"

if (( clip_rc != 0 )); then
    exit "$clip_rc"
fi
echo "Pilot finished; TRELLIS=$trellis_rc render=$render_rc CLIP=$clip_rc"
