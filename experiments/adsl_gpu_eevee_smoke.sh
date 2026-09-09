#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT=/vepfs_default/chanxueyan/lhp/lms/aDSL
LMS_BASHRC=/vepfs_default/chanxueyan/lhp/lms/.bashrc
ADSL_PYTHON=/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python
INPUT_GLB="$REPO_ROOT/temp/prior_evidence/stepcode-api-create-smoke/scene.glb"
if (( $# > 0 )); then
    OUTPUT_DIR=$1
    if [[ -e "$OUTPUT_DIR" ]]; then
        echo "Refusing to overwrite existing output: $OUTPUT_DIR" >&2
        exit 2
    fi
else
    OUTPUT_DIR=$(mktemp -d /tmp/adsl_gpu_eevee_smoke.XXXXXX)
fi

source "$LMS_BASHRC"

if ! nvidia-smi -L 2>/dev/null | grep -q '^GPU '; then
    echo "GPU smoke requires an allocated NVIDIA GPU worker." >&2
    exit 2
fi

# Keep the user-local GLVND libEGL loader because the GPU image does not ship
# libEGL.so.1.  Select NVIDIA's vendor implementation explicitly so the Mesa
# fallback from the login node cannot win vendor discovery.
unset LIBGL_DRIVERS_PATH
unset __EGL_VENDOR_LIBRARY_DIRS
NVIDIA_EGL_VENDOR=/usr/share/glvnd/egl_vendor.d/10_nvidia.json
if [[ ! -f "$NVIDIA_EGL_VENDOR" ]]; then
    echo "NVIDIA EGL vendor manifest not found: $NVIDIA_EGL_VENDOR" >&2
    exit 2
fi
export __EGL_VENDOR_LIBRARY_FILENAMES="$NVIDIA_EGL_VENDOR"

export PYTHONPATH="$REPO_ROOT/adsl-core:$REPO_ROOT/adsl-agents${PYTHONPATH:+:$PYTHONPATH}"
export ADSL_RENDER_ENGINE=BLENDER_EEVEE

test -x "$ADSL_PYTHON"
test -f "$INPUT_GLB"
mkdir -p "$OUTPUT_DIR"

date -u +%FT%TZ
hostname
nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv,noheader
printf 'ADSL_RENDER_ENGINE=%s\n' "$ADSL_RENDER_ENGINE"
printf 'LD_LIBRARY_PATH=%s\n' "${LD_LIBRARY_PATH:-}"
printf '__EGL_VENDOR_LIBRARY_FILENAMES=%s\n' "$__EGL_VENDOR_LIBRARY_FILENAMES"

"$ADSL_PYTHON" -u -m adsl.tools.render \
    --glb-path "$INPUT_GLB" \
    --output-dir "$OUTPUT_DIR" \
    --width 1024 \
    --height 1024 \
    --elevations 15 \
    --num-camera-per-layer 8 \
    --render-samples 256
test "$(find "$OUTPUT_DIR" -maxdepth 1 -type f -name 'render_*.png' | wc -l)" -eq 8
test -s "$OUTPUT_DIR/meta.json"
printf 'ok\n' > "$OUTPUT_DIR/GPU_EEVEE_SMOKE_OK"
printf 'GPU_EEVEE_SMOKE_OK output=%s\n' "$OUTPUT_DIR"
