#!/usr/bin/env bash
set -Eeuo pipefail

if (( $# != 1 )); then
    echo "usage: prepare_runtime.sh RUN_ROOT" >&2
    exit 2
fi

RUN_ROOT=$(realpath -e "$1")
LMS_ROOT=/vepfs_default/chanxueyan/lhp/lms
TRELLIS_ORIGIN=/jiigan-hp/TRELLIS
TRELLIS_COMMIT=6b0d64751ad54d9c32d7b05fec482eb29178f56f
TRELLIS_SOURCE="$RUN_ROOT/baseline/TRELLIS-$TRELLIS_COMMIT"
TRELLIS_ENV="$LMS_ROOT/envs/trellis-eval"

read -r mount_target mount_type < <(findmnt -T "$RUN_ROOT" -n -o TARGET,FSTYPE)
[[ "$mount_target" == /jiigan-hp && "$mount_type" == hpvs_fs ]]
test -w "$RUN_ROOT"

source "$LMS_ROOT/.bashrc"
export http_proxy=http://127.0.0.1:7892
export https_proxy=http://127.0.0.1:7892
export HTTP_PROXY=http://127.0.0.1:7892
export HTTPS_PROXY=http://127.0.0.1:7892

if [[ ! -x "$TRELLIS_ENV/bin/python" ]]; then
    conda create -y --clone /vepfs_default/chanxueyan/hujingyu/envs/trellis_pami --prefix "$TRELLIS_ENV"
fi

if [[ ! -d "$TRELLIS_SOURCE/.git" ]]; then
    mkdir -p "$RUN_ROOT/baseline"
    git clone --no-hardlinks "$TRELLIS_ORIGIN" "$TRELLIS_SOURCE"
    git -C "$TRELLIS_SOURCE" checkout --detach "$TRELLIS_COMMIT"
    git -C "$TRELLIS_SOURCE" submodule update --init --recursive
fi

actual_commit=$(git -C "$TRELLIS_SOURCE" rev-parse HEAD)
[[ "$actual_commit" == "$TRELLIS_COMMIT" ]]
git -C "$TRELLIS_SOURCE" config core.fileMode false
git -C "$TRELLIS_SOURCE" diff --exit-code --ignore-submodules=none

"$TRELLIS_ENV/bin/python" - <<'PY'
import torch
import transformers
print("torch", torch.__version__)
print("transformers", transformers.__version__)
PY

printf '%s\n' "$TRELLIS_SOURCE" > "$RUN_ROOT/baseline/source_path.txt"
printf '%s\n' "$TRELLIS_ENV" > "$RUN_ROOT/baseline/environment_path.txt"
echo "Prepared clean TRELLIS runtime: $TRELLIS_SOURCE"
