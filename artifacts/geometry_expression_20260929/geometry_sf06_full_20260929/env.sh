source /vepfs_default/chanxueyan/lhp/lms/aDSL/temp/geometry_expression_20260929/env.sh
export PYTHONPATH="$PYTHONPATH:/jiigan-hp/lms/aDSL/experiment/runtime/mujoco-py310:/vepfs_default/chanxueyan/lhp/lms/fea_runtime/gmsh-4.15.2/lib"
export LD_LIBRARY_PATH="/vepfs_default/chanxueyan/lhp/lms/fea_runtime/gmsh-4.15.2/lib:$LD_LIBRARY_PATH"
export ADSL_CCX_BIN=/vepfs_default/chanxueyan/lhp/lms/fea_runtime/calculix-2.23/ccx_2.23
export ADSL_RENDER_ENGINE=CYCLES ADSL_RENDER_WIDTH=512 ADSL_RENDER_HEIGHT=512 ADSL_RENDER_SAMPLES=32
export ADSL_RENDER_THREADS=4 PYTHONUNBUFFERED=1 OPENAI_AGENTS_DISABLE_TRACING=1
export CUDA_VISIBLE_DEVICES='' HIP_VISIBLE_DEVICES='' ROCR_VISIBLE_DEVICES='' LIBGL_ALWAYS_SOFTWARE=1
unset ADSL_GPU_RENDER_QUEUE
export LD_LIBRARY_PATH="/vepfs_default/chanxueyan/lhp/lms/fea_runtime/sysroot/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH"
