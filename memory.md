# aDSL 工作记忆

更新时间：2026-08-30（UTC）

本文是滚动的当前摘要，不是追加式日志。修改项目、环境或实验状态后，应替换过期内容。

## 当前分支与目标

- 仓库：/vepfs_default/chanxueyan/lhp/lms/aDSL
- 当前分支：api-native-development
- 原始公开代码基线：e1742e3
- 当前目标：在不引入 Codex Exec transport 的前提下，用本机 Stepcode OpenAI-compatible API 运行原 Agent 流程；保持原 aDSL/Eevee 设计，并审计公开代码、Agent 缺陷和生成物质量。
- master 保留此前 Codex CLI + Stepcode 双后端适配历史；当前分支不使用 Codex Exec。
- 本轮论文导向审计状态为 ANALYZED，不是 VERIFIED，也不是完整论文复现。
- 不向远程 push，除非用户明确要求。

## 本机路径与存储规则

- Python 环境：/vepfs_default/chanxueyan/lhp/lms/envs/adsl
- 当前小 case：仓库内 temp/；仅本地 Git exclude，不提交。
- 审计 case 根：temp/audit_20260830/
- 旧 temp 清理前清单：reports/adsl_audit_20260830/prior_temp_inventory.json
- 保留旧证据：temp/prior_evidence/
- GPU 手册：gpu_server_operation_manual.md；仅本地 exclude，禁止提交。
- /jiigan-hp 未作为当前依赖。数据盘恢复后必须先用 findmnt -T 和写测试确认真实 mount/FSTYPE，再考虑移动；不要因为目录可 cd 就判断健康。
- 当前工作全部可用 CPU 完成，未申请 GPU。若以后确需 GPU，严格按 gpu_server_operation_manual.md 从 tmux 内用 volc ml_devinstance launch 申请，不使用 Slurm。

## 用户级依赖规则

本机是 root，但本项目缺库时不能污染系统环境：

- 用户级 runtime 放在 /vepfs_default/chanxueyan/lhp/lms 下。
- 需要持久环境变量时写 /vepfs_default/chanxueyan/lhp/lms/.bashrc。
- 运行前 source /vepfs_default/chanxueyan/lhp/lms/.bashrc。
- 下载遇到网络问题可使用 127.0.0.1:7892 代理；直连能用时无需强制代理。
- 不把 API key、auth 文件或 credential 输出写入 repo、temp 日志、报告或 Git。

## Native Stepcode API 适配

当前 profile：

- adsl-agents/configs/llm/stepcode-gpt-5.6-sol.yaml
- model：gpt-5.6-sol
- API：OpenAI-compatible Responses
- credential.stepcode：true
- trust_env：false，防止 localhost gateway 被代理劫持

credential 行为：

- adsl-agents/utils/config.py 以 argv-only subprocess 调用 stepcode config get apiKey。
- adsl-run 未传 --model-config 时默认使用 Stepcode profile；原 OpenRouter profile 仍可显式选择。
- 只在内存提取唯一 ak-/sk- token。
- command 缺失、超时、非零退出或模糊输出全部 fail closed。
- ModelProfile repr 隐藏 api_key。
- runtime_config.json 只保存 credential_source=stepcode 等非秘密字段。
- 当前分支没有 Codex provider/profile/transport。

## 原项目 Eevee 与 CPU 环境

原项目默认渲染就是：

- BLENDER_EEVEE
- 1024×1024
- 256 samples
- 8 views
- 15° elevation

它不要求 GPU。先前失败是本机缺少 libEGL.so.1。

已完成用户级修复：

- EGL/Mesa deb 只解压到 /vepfs_default/chanxueyan/lhp/lms/adsl_runtime/egl
- .bashrc 已加入 ADSL_EGL_ROOT、LD_LIBRARY_PATH、LIBGL_DRIVERS_PATH、__EGL_VENDOR_LIBRARY_DIRS
- 修改前备份：/vepfs_default/chanxueyan/lhp/lms/.bashrc.before-adsl-egl-20260830
- bash -n 已通过
- bpy 4.0.0 使用 Mesa surfaceless EGL 在 CPU 上成功渲染
- 日志先出现 EGL_NOT_INITIALIZED，随后出现 fallback to surfaceless EGL rendering 属于预期 fallback；以最终 exit code 和 PNG 为准

本轮实验使用 env override：

- ADSL_RENDER_ENGINE=BLENDER_EEVEE
- ADSL_RENDER_WIDTH=512
- ADSL_RENDER_HEIGHT=512
- ADSL_RENDER_SAMPLES=16
- ADSL_ASSET_EXECUTOR_TIMEOUT_SECONDS=900

项目默认值仍保持 Eevee/1024/256/300 秒。

/tmp/adsl_site_cache/bpy 是本轮加速用的易失缓存，机器或 /tmp 清理后可消失，不能当持久依赖。

## 已实现的运行兼容修复

- Coder prompt 的 import 修正为 from adsl.core import *，与论文和 DSL 文档一致。
- render.py 支持 engine/width/height/samples 环境覆盖，默认不变。
- execution.py 子进程使用独立 process group；timeout/中断时有界 TERM → KILL。
- execution.py 直接执行同目录 asset_executor.py，不再用 python -m adsl.agents.utils.asset_executor，避免资产子进程无意义导入整个 Agent/OpenAI stack。
- 同一 source smoke 在 /tmp bpy cache 下从 308.634 s 降到 3.727 s。
- service.py 发布 scene.joint_states.json 和 render/meta.json，并记录有效 runtime 配置。
- .gitignore 不再忽略整个 tests/。
- tests 覆盖 config、credential 安全、execution cleanup/direct executor、prompt、publish 和 service config。

## 2026-08-30 诊断审计

Manifest：

- experiments/adsl_audit_20260830/case_manifest.json
- 12 个逻辑 case
- 15 次 invocation
- retry_policy=none；每个 invocation 最多启动一次
- Agent 内部 Debugger/repair 是被测流程的一部分

case 范围：

- exact count/contact chair
- curved bookshelf
- 16-spoke radial wheel
- hollow mug
- patterned desk
- pure image table
- pure image articulated nightstand
- two articulated text cases
- localized chair edit
- living-room relational scene
- motorcycle base/edit/scratch memory comparison

总体：

- 15/15 最终生成 GLB/URDF/PNG
- 14/15 被当前 workflow 标记 approved
- 118 model requests
- 836,614 total tokens
- invocation elapsed 求和 4,226.324 s
- 3 次首轮执行失败，全部为同类非法数字词语法，随后被 Debugger 修复
- session DB 合计 53,882,880 bytes，原始内容有 195 次 data:image occurrence
- 未申请 GPU

详细报告：

- reports/adsl_audit_20260830/README.md
- reports/adsl_audit_20260830/paper_claim_matrix.md
- reports/adsl_audit_20260830/code_agent_findings.md
- reports/adsl_audit_20260830/generated_output_findings.md
- reports/adsl_audit_20260830/case_results.json
- reports/adsl_audit_20260830/case_results.md

## 关键正面结果

- T01：4 条等长腿、3 条背横档、无扶手满足；grid/stack 对 repeated structure 有效。
- T03：源码明确用 radial_shapes 生成 16 根辐条并接触 hub/rim，是关系 DSL 的强证据。
- E01：chair 3→5 slats，只 +6/-6 行，其他几何保持，支持 localized edit。
- I02/A01/A02：URDF 都有精确 3 个 movable joints，语义名、轴和 limits 基本合理。
- pose probe 将三组关节设到 85% 行程后重新导出/渲染，抽屉、门、knob/lever 都实际移动且保持连接。
- T02/A02 的真实布局缺陷能在下一轮被 Critic/repair 修复，说明 loop 有实际价值。

pose probe：

- experiments/adsl_audit_20260830/render_pose_probe.py
- temp/audit_20260830/pose_probes/
- I02 drawers：0.2975
- A01 doors：1.632 rad；drawer：0.3825
- A02 knobs：0.455 rad；lever：0.5

## 关键流程/代码缺陷

1. asset_executor.py 用 runpy.run_path 不受限执行模型源码；子进程不是安全 sandbox。当前最高优先级。
2. service.py 最后一轮执行成功后直接 round_limit_after_execution，不运行 Critic；max_rounds=1 恒定 critic_skipped。
3. Image Critic approved 时跳过 Code Critic，与论文“Code Critic final adjudicator”不一致。
4. Planner relations/checklist 只是 list[str]，没有 typed/executable constraints。
5. 没有 deterministic count/contact/alignment/collision/topology/joint-motion gate。
6. articulated 主流程只渲染 Joint.initial；Code Critic prompt 要求非零 pose，但没有对应工具或图。
7. 固定 8×15° object orbit 看不到 mug interior，并让 living-room 约 3/8 视图被墙遮挡。
8. Code Critic prompt 要求 MUST TRUST THE CODE LOGIC，可能把源码意图误当最终几何/视觉事实。
9. 没有 AST/compile preflight；I01、I02、M01 scratch 首轮出现 0. thirty / 0. forty。
10. bounds.py 明确存在 transformed/boolean AABB 近似；T02 已触发 floating books。
11. SQLite selective context 不会物理删除旧 render data URL。
12. 生成代码仍大量使用绝对数字；复杂外观 primitive/flat-color 感明显。
13. 公开主流程没有论文的 SpaceControl/Trellis 高保真阶段。
14. ObjectWorkflow 单体且相机/视图/URDF 硬编码，难做 verifier 注入和 ablation。
15. 缺 Draco 时 Blender 打 ERROR 但 GLB 仍成功，日志容易误判。

## 生成物主要不足

- T02：curved nosing 视觉不明显；四个 nosing CSG 非 watertight，共 41 zero-area faces。
- T04：源码有真实 hollow vessel，但八视图看起来仍接近封顶实心；C-handle 粗糙且 union 非 watertight。
- T05：黑白条纹用 13 个薄 Cube + boolean 模拟，不是真正 texture/material。
- I01/I02：语义可辨识，但比例、腿型、把手、木纹、接缝和曲面细节明显弱于参考。
- A01：原八视图全关闭；motion 是审计 pose probe 才验证的。
- A02：关节正确，但外形更像粗糙工业塔，faucet 识别度一般。
- S01：空间关系大致成立，场景相机严重被墙遮挡。
- M01 edit：保存基体且 wall time 比 scratch 少，但 tokens/requests 更多、源码膨胀、视觉 cyberpunk 更弱。
- 拓扑统计必须先 merge_vertices(merge_tex=True, merge_norm=True)，否则 glTF hard-normal/UV 顶点拆分会把普通 cube 误报为非 watertight。

## 论文复现边界

论文使用：

- Gemini 3 Pro，temperature=1
- R=10
- 1024×1024
- 200 text prompts
- Toys4K 30 image cases
- 120 ablation prompts
- CLIP/VQA/FID/Execution Success
- baselines 和 38 人 user study
- SpaceControl/Trellis high-fidelity application

本轮均未完整复现，因此不能宣称论文 SOTA、100% execution success、CLIP/VQA/FID 或人类偏好已验证。

## 常用复查命令

先加载用户级 Eevee 库：

~~~bash
source /vepfs_default/chanxueyan/lhp/lms/.bashrc
~~~

重新分析既有 case：

~~~bash
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python   experiments/adsl_audit_20260830/analyze_cases.py
~~~

测试：

~~~bash
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q
~~~

Git 提交前：

- git diff --check
- py_compile 修改过的 Python 文件
- 全量 pytest
- secret scan，不能输出或提交真实 ak-/sk- token
- 确认 temp/ 和 gpu_server_operation_manual.md 不在 staged files
- 只本地 commit，不 push

## 建议的下一阶段修复顺序

1. 生成代码 sandbox + AST allowlist。
2. 修复 round off-by-one；每次成功 execution 都执行固定 verifier chain。
3. 增加 syntax preflight。
4. typed constraints + deterministic count/contact/topology/collision verifier。
5. joint lower/mid/upper pose sampling。
6. container/scene/articulation 的 asset-aware camera policy。
7. session 图片 artifact 外置与 prune/compact。
8. texture/material 或明确接入 high-fidelity generator。
9. 拆分 ObjectWorkflow 并加入 benchmark/ablation runner。

## 2026-08-31 Eevee / GPU 渲染核查

证据边界：

- 论文 Appendix A 只明确 8 个视角、45° 方位间隔、固定 15° 仰角、1024×1024、neutral materials/environment lighting；论文全文没有出现 Eevee 或 GPU，也没有报告 GPU 型号。
- 官方 sig-pku/aDSL 源码在 adsl-core/tools/render.py 中硬编码 init_render_engine("BLENDER_EEVEE", ...)，但没有 CUDA、OptiX 或 CPU/GPU device 选择。
- 因此可以确定官方渲染引擎是 Blender Eevee；“作者实验明确指定 GPU Eevee”不能由论文直接证明。标准 Eevee 的常规硬件路径是图形 GPU，Mesa software surfaceless 是本机登录节点的兼容 fallback。

本机适配与实测：

- /vepfs_default/chanxueyan/lhp/lms/.bashrc 已改为自动分流：检测到真实 NVIDIA GPU 行且存在 10_nvidia.json 时，保留用户态 GLVND libEGL loader 并选择 NVIDIA EGL vendor；否则选择 Mesa DRI/vendor。
- 备份：/vepfs_default/chanxueyan/lhp/lms/.bashrc.before-adsl-gpu-eevee-20260831
- 不能只用 nvidia-smi -L 的退出码判断 GPU：本站登录节点在 0 张 GPU 时也可能返回 0；必须匹配 ^GPU 行。
- 第一次 A800 smoke 误把整个用户态 EGL 路径移除，因 GPU image 缺少 libEGL.so.1 而 abort。正确做法是保留 user-local GLVND loader，只切换 vendor 到 /usr/share/glvnd/egl_vendor.d/10_nvidia.json。
- 修复后单卡 A800（driver 535.129.03）按 BLENDER_EEVEE、8 views、15°、1024×1024、256 samples 成功生成 8/8 PNG 和 meta.json，成功哨兵及 manager RC 均为 0。
- 可复查 worker：experiments/adsl_gpu_eevee_smoke.sh
- 可重建证据：temp/gpu_eevee_smoke_20260831_retry/ 与 temp/gpu_eevee_smoke_20260831_retry.log
- Eevee 单进程使用一张图形 GPU；双卡不会自动加速一个 render job。多个独立 case 并发时才考虑双卡。
- 后续若要求论文常规硬件路径，必须在按手册申请的 volc GPU worker 内 source lms/.bashrc 后运行；直接在登录节点运行仍会走 Mesa CPU fallback。

同条件速度基准（同一 GLB，BLENDER_EEVEE，8 views，15°，1024×1024，256 samples）：

- NVIDIA A800 的 Blender frame time：首视角 8.73 s（含首次 GPU/scene 初始化），后续 0.47–0.85 s；8 视角日志合计 12.44 s。
- Mesa CPU 首视角实测 96.76 s；第二视角进行到 151/256 samples 时为 55.15 s，速率与首视角一致。为避免额外约 11 分钟占用，得到完整首视角后终止其余视角。
- 按实测速率估算 CPU 8 视角约 774 s（12.9 min），与 GPU 12.44 s 相比，纯渲染阶段约 62×；warm-view 约 100–200×。具体倍数会随几何、材质、分辨率和 samples 改变。
- 新申请 volc worker 还有排队、容器和 bpy 初始化开销，所以一次性小任务的端到端加速低于 62×；批量 case 或复用 GPU worker 更划算。
- nvidia-smi pmon 在该容器中没有显示 Eevee 图形进程且产生无效日志/尾部等待，已从 smoke worker 移除；NVIDIA backend 通过强制 10_nvidia.json 且渲染成功来验证。

## 2026-08-31 持久化串行 GPU 渲染队列

目标：多个 agent/case 可以同时提交，但 Blender 渲染必须严格串行；不再为每个 case 单独创建/关闭 tmux 和申请 volc worker。

实现：

- 队列核心：adsl-core/tools/gpu_render_queue.py，只依赖 Python 标准库。
- 唯一接入点：adsl-agents/utils/asset_executor.py。text、image、articulated、edit 和每一轮 critic 前的 render 都经过此处。
- 设置 ADSL_GPU_RENDER_QUEUE 后只走队列；worker 不在线或 job 失败会显式失败，不回退到登录节点 Mesa CPU。
- 提交端通过原子 JSON 文件进入 FIFO；worker 用排他锁保证同一 queue 只有一个消费者，固定 concurrency=1。
- 每个 job 都启动一个全新的前台 Blender 子进程；必须等其完整退出后才检查下一项。
- worker 记录启动显存基线。Blender 退出后显存须在 60 s 内回到 baseline + 256 MiB；否则当前 job 失败、worker 状态变为 blocked，并保留后续 pending job，绝不继续提交导致 OOM。
- 单 job 默认 hard timeout 900 s；杀整个进程组；不自动 retry。
- 客户端等待默认 3600 s，asset executor 外层默认 3660 s；执行 manifest 记录 render_backend 和 render_job_id。
- worker restart 会把遗留 running job 标为 failed，不会猜测性重跑。
- graceful stop 只让当前 Blender 完成，pending job 留待下一次 worker；不可把 kill tmux 当正常停止方式。

控制脚本：experiments/gpu_render_queue/

- activate.sh：给 agent/service 注入 queue、Eevee、1024×1024、256 samples 和 timeout。
- control.sh：start / wait / status / stop。
- manager.sh：普通登录节点 tmux 内只申请一次 volc，默认单卡 A800，12 h allocation hard timeout。
- worker.sh：GPU worker 内运行串行 queue daemon；默认空闲 2 h 自动退出释放申请。
- README.md：完整操作与故障 marker。

卡数决策：当前 Eevee 路径单 job 使用一张图形 GPU，且为了 OOM 隔离固定单通道，因此默认 ml.pni2l.3xlarge 单卡 A800。双卡 flavor 不会让同一个 case 更快；除非以后明确实现两个彼此隔离的 queue lane，否则不申请双卡。

验证：

- 修改过的 Python 已通过 py_compile；四个 shell 脚本已通过 bash -n。
- 纯队列测试在 /tmp 隔离副本通过 3/3：第二个 job 在第一个进程退出后才开始、hard timeout 不重试、显存不回落时 worker fail-closed 且第二项保持 pending。

## 2026-09-02 aDSL vs TRELLIS 粗略 CLIP pilot

目的与证据边界：

- 这里只回答“当前 7 个既有 case 的 CLIP 大概效果是否接近 TRELLIS”，不是论文完整复现。
- text/image 两个 track 分开统计，不能混合；±2 CLIP points 只是工程观察线，不是统计等价检验。
- 没有跑论文的 200 text prompts、30 Toys4K、VQA/FID、用户研究或多随机种子。
- 最终 verification_status=ANALYZED 只适用于本地 7-case pilot。

实现入口：

- experiments/clip_trellis_pilot/build_manifest.py
- experiments/clip_trellis_pilot/prepare_toys4k_subset.py
- experiments/clip_trellis_pilot/run_trellis.py
- experiments/clip_trellis_pilot/render_batch.py
- experiments/clip_trellis_pilot/score_clip.py
- experiments/clip_trellis_pilot/gpu_worker.sh
- experiments/clip_trellis_pilot/launch.sh
- tests/test_clip_trellis_pilot.py

数据与环境：

- run root：/jiigan-hp/lms/aDSL/experiment/clip_trellis_pilot_20260902（约 1.3 GiB）。
- manifest：上述目录的 manifest.json；CLIP 结果在 clip/scores.json、scores.csv、report.md。
- 干净 TRELLIS baseline：上述目录 baseline/TRELLIS-6b0d64751ad54d9c32d7b05fec482eb29178f56f。
- TRELLIS commit：6b0d64751ad54d9c32d7b05fec482eb29178f56f；没有使用 /jiigan-hp/TRELLIS 里的工作树源码。
- text 权重：TRELLIS-text-xlarge-original；image 权重：TRELLIS-image-large；CLIP：本地 clip-vit-large-patch14。
- trellis-eval 环境实际是指向 /vepfs_default/chanxueyan/hujingyu/envs/trellis_pami 的 symlink；直接复制 60k+ 文件到 lms 太慢，未完成副本保留为 trellis-eval.incomplete-20260902T0415Z。
- rembg 的 u2net.onnx 缓存在 /jiigan-hp/lms/aDSL/experiment/model_cache/rembg/u2net.onnx，MD5=60024c5c889badc19c04ad937298a77b。

Toys4K 最小下载：

- 通过 127.0.0.1:7892 只下载约 3 MB 的公开 Toys4k.csv 元数据到数据盘。
- 元数据 SHA256=8609e8d3cb9affd85d0d443de59831bf241a716554d8f140e4eb209b2bde26b8。
- seed=260817975 冻结 10 个不同类别：banana、bottle、cells_battery、chair、key、motorcycle、pear、pencil、phone、sandwich。
- 官方 raw Toys4K 需要授权表单，因此没有下载完整 archive，也没有把仅有 metadata 的对象冒充已完成 image-to-3D case。
- 工具已经支持在以后拿到授权 archive 时只抽取这 10 个成员。

锁定协议：

- 5 个 text case（T01-T05）和 2 个 image case（I01-I02），复用原始 prompt/input image。
- TRELLIS 每 case seed=1、一次生成、不做 prompt expansion、不自动 retry。
- 两侧用同一个项目 Eevee renderer：8 views、45° 方位间隔、15° 仰角、1024×1024、256 samples、neutral material、同一 AABB normalization。
- manifest 中 background=white 指 world color 设置；项目 Filmic + exposure=-0.8 后实际角像素是 RGB(134,134,134)，即输出视觉为灰背景。两侧仍公平，但不能描述为纯白像素背景。
- CLIP 指标为 reference 与 8 个 view 的 cosine×100 后取 mean；失败按 0 进入主聚合。

正式运行结果：

- 单卡 A800 worker-hldpl；TRELLIS、Blender、CLIP 三阶段严格串行，三个 exit code 均为 0，结束后 worker 自动释放。
- 7/7 TRELLIS GLB 成功；14/14 aDSL/TRELLIS render group 成功，每组 8 张图。
- text：aDSL 24.171，TRELLIS 25.201，delta=-1.031；按预设 ±2 工程线属于 close_within_margin。
- image：aDSL 66.822，TRELLIS 70.719，delta=-3.897；TRELLIS 更高。
- text 逐 case delta（aDSL−TRELLIS）：T01 -0.714、T02 -1.360、T03 -1.604、T04 +0.125、T05 -1.600。
- image 逐 case delta：I01 -4.385、I02 -3.409；两个 case 方向一致。
- 乱序 reference 对照均值：text aDSL/TRELLIS=15.994/16.652，低于 matched 24.171/25.201；image=60.150/62.437，低于 matched 66.822/70.719。
- image self-control 约 100，说明 pipeline 数值正常；但 image shuffled gap 较小，也说明这两个同属家具的极小样本区分力有限。

视觉复核与结论：

- text 轨可以说“CLIP 粗略接近”，不能说 aDSL 在总体质量上等价 TRELLIS。
- T01/T03 体现 aDSL 的程序化关系优势：四腿/三横档、16 spokes 等精确结构清晰；CLIP 本身并不可靠计数。
- T05 是典型反例：TRELLIS 视觉更像常见办公桌，但出现侧柜/额外结构，违背“exactly one drawer + two side panels”；aDSL 更守结构约束，CLIP 却仍偏向 TRELLIS。
- I01/I02 输入图并排复核中，TRELLIS 的桌腿比例、柜体轮廓、抽屉和接缝更接近参考；aDSL 更 primitive、比例和细节更弱，与 image CLIP 差距一致。
- T04 两边都是可识别 mug，aDSL 略高；但 hollow interior、wall thickness、watertightness 不能由 CLIP 证明。
- 因此当前证据支持：aDSL 在结构明确的 text cases 上能用程序化几何取得接近的 CLIP，同时更易满足 exact constraints；在纯图像外观复原上明显落后 TRELLIS。

本次运行踩坑：

- GPU 容器中 /jiigan-hp 的 fstype 是 hpvs_fs.fsx-tos，不是登录节点显示的 hpvs_fs；preflight 必须接受 hpvs_fs*，否则会误报挂载盘不可用。
- rembg 默认会从 GitHub 下载 u2net 并 30 s timeout；必须提前经 7892 下载到数据盘并设置 U2NET_HOME。
- aria2c 对大单文件用 8 connections 明显比单连接 curl 稳定；下载完成必须校验官方 MD5。
- bash 的 ERR trap 会在预期的 partial stage return code 上提前终止；需用 if command; then rc=0; else rc=$?; fi 捕获，之后再决定是否继续 render/score。
- 数据盘不可靠保留 executable bit；baseline clone 设置 core.fileMode=false，脚本通过 bash 显式运行。
- apply_patch/view_image 的 bwrap 在本机因 unprivileged user namespace 禁用而失败；代码修改只能在明确 diff 审核后临时用 git apply，图片诊断通过 ImageMagick + base64 读取。

## 2026-09-02 实验归档入口

- 总入口：/jiigan-hp/lms/aDSL/experiment/clip_trellis_pilot_20260902/EXPERIMENT.md。
- 仓库副本：reports/adsl_experiment_archive_20260902.md。
- 总入口同时归档第一阶段 12 个逻辑 case/15 次调用的 Agent、代码、生成物缺陷，以及第二阶段 7-case TRELLIS/CLIP 对比。
- 第一阶段详细 Markdown 原文位于数据盘 archive/initial_case_audit_20260830/，包含 overview、agent logic、code findings、generated output findings、paper claim matrix 和 case results。
- 第二阶段原始分数、逐视角路径和对照实验仍以 clip/scores.json 为机器可读真值。
- 数据盘副本已逐文件 cmp 验证与仓库报告一致。

## 2026-09-03 Final Standing Stability（01）验证

- 范围：只验证成品站立稳定性；未实现或接入 checker，未修改任何现有 GLB、URDF 或 source.py，也未进入 progressive build、FEA 或 support 分析。
- 实验入口：experiments/final_standing_stability/analyze.py；说明：experiments/final_standing_stability/README.md；测试：tests/test_final_standing_stability.py。
- 正式结果：/jiigan-hp/lms/aDSL/experiment/physics_analysis/01_final_standing_stability/；机器可读真值为 results.json 和 summary.csv；仓库报告副本为 analysis_archive/final_standing_stability_analysis.md。
- 输入：audit_20260830 的 15 个 case、19 个姿态。运行前后 45 个 scene.glb、scene.urdf、source.py 的 SHA256 清单逐字一致。
- 方法：URDF collision geometry 的最低接触凸包；均匀实体与均匀薄壳两种质心；带符号稳定裕度、归一化裕度、最小倾倒角和 16 向临界水平加速度；MuJoCo 自由沉降、16 向阶梯水平力及 0.05 m/s 小冲量交叉复核。
- MuJoCo 3.12.0 使用 CPU，不申请 GPU；因代码盘 pip 安装触发 quota，额外 runtime 位于 /jiigan-hp/lms/aDSL/experiment/runtime/mujoco-py310。lms/.bashrc 定义 ADSL_MUJOCO_PYTHONPATH 和 adsl_mujoco_python，不全局覆盖 PYTHONPATH；修改前备份为 .bashrc.before-adsl-mujoco-20260903。
- 覆盖：排除 S01 后，18/18 姿态都有 MuJoCo 结果。初始姿态分布为 2 UNSTABLE、6 STABLE_CANDIDATE、6 INDETERMINATE、1 EXCLUDED。
- 用户倾倒判据（2026-09-03 更新）：MuJoCo 自由沉降、水平力和小冲量均以运行期间最大倾斜角严格大于 25° 为倒下；恰好 25° 不算。自由沉降和冲量观察窗口均为 5 秒。
- 25° 正式结果：18/18 非排除姿态在自由沉降中都未倒下；最大沉降倾角是 A01 initial 的 0.639°。因此结构 verdict 与自然倒下结果必须分别解读。
- 结构筛查失败：A01-cabinet 的最低平面实体/壳体质心均在初始支撑区外，但 MuJoCo 中仅摇摆 0.639° 后形成额外接触，按 25° 规则没有倒下；T03-radial-wheel 支撑面积为 0，在完全对称自由沉降下未自行倒，但约 0.2×自重水平力会使其超过 25°。
- 稳定候选：A02、E01、I01、I02、T01-main、T01-one-round-control；I02 三抽屉同时打开到 85% 上限后壳体裕度降至 0.0257、MuJoCo 最小观测倾倒力降至 0.2 倍自重，应视为最弱稳定候选。
- 不确定：三个 motorcycle 的约 0.44–0.46 平底支撑可能是网格离散化伪影；T02、T04、T05 与 M01 scratch 缺少可靠实体质心。MuJoCo 凸包代理不能消除该不确定性。
- 退化 mesh 处理：少于 4 顶点或共面的零体积分量不送入 MuJoCo，显式丢弃并计数；M01 scratch/T02/T05 分别为 9/14/2。第一次包含三项代理构建失败的输出完整保留为 01_final_standing_stability_attempt_01_degenerate_mesh_failures。
- 旧的“峰值 30° 或最终 15°”正式输出保留为 01_final_standing_stability_threshold30_peak15_final；canonical 01_final_standing_stability 已全部替换为严格 >25° 结果。
- 证据边界：原 URDF 没有 mass、density、inertia；1000 kg/m³ 和摩擦系数 2.0 均为统一实验假设。结果状态为 ANALYZED，不是工程认证或真实材料稳定性 VERIFIED。
- 验证：项目 tests/ 为 39/39 通过（含 5 个 Final Standing Stability 测试，其中 25.0°/刚超过 25.0° 边界已锁定），正式进程 exit code 0，结果报告副本 cmp 一致。
- 测试路径踩坑：裸跑 pytest -q 会沿仓库 temp 数据盘入口收集 runtime 内 MuJoCo、PyOpenGL、NumPy 的第三方自测并在 collection 失败；有效项目测试命令必须限定为 pytest -q tests。本轮未修改既有 pytest 配置。

## 2026-09-04 Progressive Build Stability（02）验证

- 新总入口：physics_analysis.md，统一记录 01 成品站立和 02 打印过程稳定性；02 仓库报告副本为 analysis_archive/progressive_build_stability_analysis.md。
- 02 入口：experiments/progressive_build_stability/analyze.py；说明：同目录 README.md；测试：tests/test_progressive_build_stability.py。
- 本阶段按用户确认只判断整体倾倒，不使用 FEA；弯曲、屈曲、层间开裂、热变形和喷嘴载荷均在范围外。
- 方法：authored URDF Z-up，从 global min-z 起以总高度 1% 为 Δh，固定 100 个高度；额外加入 collision geometry 出现/完成事件点。每层与 z<=h 半空间求交并优先封口。
- 每个 partial 用 CPU MuJoCo 沉降 5 秒；运行期间最大倾角严格 >25°才算倒下。所谓 unbonded free rigid body 不是高空自由落体，只用 0.002 场景单位间隙建立地面接触。
- 正式结果：/jiigan-hp/lms/aDSL/experiment/physics_analysis/02_progressive_build_stability/。15 个 case 中排除带整块房间地板的 S01；14 个 initial-pose 模型的 1400/1400 固定 Δh 均完成动态仿真，0 个点超过 25°。
- 固定 Δh 外共 149 个几何事件点；A02 6 个、I02 1 个、T03 1 个近零厚度 birth event 无法构造非零质量 MuJoCo body，明确保留为 unavailable，未计成稳定。
- 峰值倾角为 T03 3.356°、A01 0.639°、A02 0.085°，其余小于 0.08°。几何预筛仍将 A01 的 114/115 点和 T03 的 101/101 点判为不稳定。
- 关键边界：无扰动数值模型可能停在完全对称的不稳定平衡；因此只能说“按当前无扰动 >25° 判据未观察到倒下”，不能宣称实际打印稳定。下一步若测现实扰动，应预先冻结统一小冲量协议并与自然沉降分栏。
- 底板粘附只输出重力倾覆力矩/自重作为附加解释；无实测粘附强度时不作 pass/fail，也不改变逐 Δh 主结论。
- 依赖与资源：复用 01 的 Trimesh/Manifold 与数据盘 MuJoCo 3.12 runtime；全部 CPU 完成，不申请 GPU。partial STL 只在 /tmp 临时生成并在每 case 后清理。
- 验证：02 与 01 限定测试合计 9/9 通过；正式运行 exit code 0；1400 个固定 Δh 动态覆盖完整。


## 2026-09-04 Load-Bearing Structural Performance（03）验证

- 入口：experiments/load_bearing_structural_performance/analyze.py；配置：case_config.json；说明：README.md；测试：tests/test_load_bearing_structural_performance.py。
- 正式结果：/jiigan-hp/lms/aDSL/experiment/physics_analysis/03_load_bearing_structural_performance/；仓库报告副本：analysis_archive/load_bearing_structural_performance_analysis.md。
- 工具链：官方 Gmsh 4.15.2 SDK + 官方 CalculiX 2.23 源码。官方 ccx 预编译包依赖本机没有的 libgfortran.so.4，因此使用 GCC/GFortran 11、用户级 Ubuntu SPOOLES/ARPACK/BLAS/LAPACK 在 /vepfs_default/chanxueyan/lhp/lms/fea_runtime 编译 2.23；没有系统安装。
- lms/.bashrc 修改前备份为 .bashrc.before-adsl-fea-20260904；它 source experiments/load_bearing_structural_performance/activate.sh，提供 ADSL_CCX_BIN、adsl_gmsh、adsl_fea_python。wrapper 局部注入 LD_LIBRARY_PATH/PYTHONPATH，不全局污染现有环境。
- 官方 archive SHA256：ccx_2.23.tar.bz2=35d426fed5eb164fbbaaafa20819d13d22e30bc2b9bc9c6c4ed957e9468355dd；gmsh SDK=2dbd68d033f99b05789554bf4db6d47f4108dd36b0b36d4a50935df0ceb9e772。
- 解析基准：C3D10 悬臂梁位移误差 0.276%；固定—自由 Euler 柱临界载荷误差 0.340%，benchmark status=PASSED。
- 14-case 结果：4 SOLVED、7 INVALID_LOAD_PATH、3 NOT_MESHABLE；solver failure=0。4 个 SOLVED 为 E01、I01、T01-main、T01-control，全部 coarse/medium/fine 和 self-weight/functional 完成，4/4 通过 medium→fine 收敛门槛。
- 细网格 8/8 条载荷轨通过本轮代理阈值 U/L≤1%、名义屈服 FoS≥2、线性屈曲因子≥2；最柔的是 E01 functional 5.848 mm（0.650% 高度），其次 T01-main 5.587 mm（0.621%）。这是各向同性 PLA、全固定底面、线弹性代理，不是打印安全认证。
- 载荷路径缺陷：A01/A02/I02 的运动件虽有 URDF joint，但无 FEA 连接刚度/tie/contact；motorcycle、T03、T05 有 18–85 个断开体；M01 scratch/T02/T04 不能形成封闭实体。pipeline fail closed，没有体素补洞或静默粘合。
- 共享盘踩坑：CalculiX 会 delete/reopen DAT；直接以 jiigan-hp 为 cwd 报 openfile could not delete file。attempt-01 完整归档；正式 run_ccx 对每个 job 用 /tmp TemporaryDirectory，退出后解析、复制全部产物到数据盘并立即清理，避免共享盘语义和 /tmp 累积占满。
- 资源：全部 CPU，无 GPU。canonical 输出约 564 MiB，含 26 个成功 CalculiX job（24 case load/mesh + 2 benchmarks）。

## 2026-09-04 Support Requirement & Critical-Surface-Aware Support（04）验证

- 入口：experiments/support_requirement_critical_surfaces/analyze.py；配置：case_config.json 与 fff_profile.ini；说明：README.md；测试：tests/test_support_requirement_critical_surfaces.py。
- 正式结果：/jiigan-hp/lms/aDSL/experiment/physics_analysis/04_support_requirement_critical_surfaces/；仓库报告副本：analysis_archive/support_requirement_critical_surfaces_analysis.md。
- 方法：45°向下三角面 overhang 预筛 + PrusaSlicer G-code bridge/support/interface；interface footprint 与 support 起长 footprint 映射回原 collision face，输出 face IDs、面积、质心、bounds 和彩色 PLY。
- contact 是 0.2 mm top/bottom Z gap 内的名义承托区，不是严格 mesh 相交；关键面按 collision regex + face selector 显式配置，可靠表面重叠 >0.01 mm² 即 VIOLATION。
- profile：0.4 mm nozzle、0.2 mm layer、PLA、45°、everywhere rectilinear support、3 interface layers；原始 Z-up/initial state。第03项语义尺度保留元数据，实际切片最长边封顶 180 mm。
- 合成基准 PASSED；14/14 案例 ANALYZED，14/14 都生成 support。关键面为 3 VIOLATION、4 PASS、7 INDETERMINATE。
- 违规定位：A01 拉手 109.200 mm²；A02 aerator/knobs/lever 523.541 mm²；I02 三层抽屉拉手 253.287 mm²。E01/I01/T01-main/T01-control PASS。
- T02 候选关键面重叠 1204.717 mm²，但其与其余6个单一 GLB 案例没有可靠三角面语义来源，因此保持 INDETERMINATE。
- 网格缺陷：M01 scratch、T02、T04、T05 至少一个 non-watertight collision part；T02 有41个退化面，M01 scratch 有2个。PrusaSlicer 隐式 repair 不等于源模型通过质量检查。
- 源完整性：127个 URDF/GLB/STL/OBJ/source.py 运行前后 SHA256 一致。全部 CPU，无 GPU。
- runtime：本机无 Flatpak且官方 2.8.1 AppImage不兼容，使用 lms/tools/prusaslicer/2.4.0 下用户态解包的 Ubuntu Jammy 包；未系统安装。.bashrc 已备份为 .bashrc.before-adsl-support-20260904，并提供 adsl_prusaslicer/adsl_support_python。
- 编辑踩坑：本机 unprivileged user namespace 禁用导致 apply_patch 的 bwrap 间歇失败；一次 Python 行清理误将真实换行写成字面 `\n`，已恢复并重跑7/7单测。修正版14-case JSON完成后 Markdown 定位行曾有键引用错误，仅报告生成失败；已修复并从 canonical results.json 无需重切片地重建报告。

## 2026-09-04 Codex CLI 恢复与 01 易倒样例扩展

- 当前分支重新接回 `adsl-agents/providers/codex_cli.py` 和 `codex_codec.py`，默认
  profile 为 `codex-cli-gpt-5.6-sol.yaml`。Stepcode 不再是默认路径；OpenAI-compatible
  profile 只在显式指定时保留兼容。
- transport 使用已登录的 `codex exec --ephemeral --ignore-user-config --ignore-rules
  --sandbox read-only --json --output-schema`；aDSL 仍负责 Agents SDK session、受限工具、
  source 执行、critic/refinement 与渲染。真实结构化 smoke 成功，完整测试 76/76 通过。
- `pyproject.toml` 增加 `testpaths=["tests"]`，避免 `temp` 数据盘软链接使 pytest
  误收集 `runtime/mujoco-py310` 内第三方自测。
- CAP3D/MARVEL 只下载 caption CSV，正式位于
  `/jiigan-hp/lms/aDSL/datasets/prompt_sources/`，按数据集与不可变 revision 分层；
  未下载完整 ShapeNet/ABO 网格。selector 对四个 CSV 强制校验 SHA256。
- 实验根：
  `/jiigan-hp/lms/aDSL/experiment/physics_analysis/01_unstable_case_validation_20260904/`。
  16 个 prompt 已冻结为 8 initial + 8 reserve，使用 CAP3D 与 MARVEL level-2 原始 caption，
  不做稳定性 prompt engineering。论文未公开 200 条 prompt IDs，本实验只能标
  same-source/different-sample。
- 生成前发现横向 rocket 不适合 standing test；在任何模型生成和结果观察前改为
  ShapeNet standing/tower loudspeaker，并在 manifest 记录 pre-run amendment。
- 01 controls：稳定组 2/2 自由沉降最大倾角 0°；易倒组 3/3 严格超过 25°，峰值
  133.891°、179.990°、179.974°。control 不计入 aDSL failure rate。
- GPU queue 现直接固定到
  `/jiigan-hp/lms/aDSL/experiment/gpu_render_queue`；manager/worker 要求精确目录、
  mount target=/jiigan-hp、fstype=`hpvs_fs*`。第一次申请因旧 realpath 白名单在申请前
  退出；修复后的单卡 run `20260904T154103Z` 仍 pending。
- 无人值守 tmux `adsl_standing_cases` 已提交：等待 `adsl_gpu_renderer` live 后严格
  串行生成 U01–U08，每 case 两轮，不自动跑 reserve。日志与 marker 位于实验根：
  `generation_manager.log`、`generation_status_initial.json`、
  `INITIAL_GENERATION_SUCCESS` / `INITIAL_GENERATION_FAILED.exit_code`。
- 本账号另有两个从 2026-08-19 起停在 root 提示符的旧 GPU 会话：
  `gpu:4.0` 单卡、`gpualloc:1.0` 双卡。未获授权，未退出、未复用；可能占用配额。
- 后续：先检查生成 marker；完成后运行 01 analyzer 到 `generated_analysis/`。只有
  initial 8 个自然沉降全部不超过 25° 时才生成已冻结 U09–U16 reserve；最后运行
  `report_unstable_cases.py` 并归档。

### 2026-09-06 重提交 attempt_02

- attempt_01 在等待 43000 秒后先于 GPU 分配完成而退出，未生成任何 case；原日志和
  marker 已保留为 `generation_manager_attempt_01.log` 与
  `INITIAL_GENERATION_attempt_01_FAILED.exit_code`。
- 新 GPU run：`gpu_render_queue/runs/20260906T044309Z`；tmux：
  `adsl_gpu_renderer`。单卡 A800，allocation timeout=172800 秒（48 小时）。
- 新生成 tmux：`adsl_standing_cases`；日志：
  `generation_manager_attempt_02.log`；等待 timeout=200000 秒，长于 GPU allocation
  timeout，GPU manager 若提前失败则 control.sh 仍会立即 fail closed。
- attempt_02 成功/失败 marker：
  `INITIAL_GENERATION_attempt_02_SUCCESS` /
  `INITIAL_GENERATION_attempt_02_FAILED.exit_code`。提交时两个 tmux 均存活，GPU 状态
  为 Worker pending。

## 2026-09-06 阶段实验 presentation 归档

- 新增根目录 `presentation.md`：Marp-compatible 中文阶段汇报，覆盖 Agent/代码审计、
  7-case aDSL–TRELLIS CLIP pilot、01/02 稳定性、03 CalculiX FEA、04 PrusaSlicer
  support/critical-surface 分析，以及 U01–U08 attempt_02 pending 状态。
- 新增 `presentation_assets/`（约 14 MiB）：15 个 aDSL GLB、7 个 TRELLIS GLB、
  5 个稳定/失稳 control GLB、3 个 articulated URDF/关节状态、15 份审计渲染、5 张
  对比图和3张定量图。资源均从数据盘 canonical experiment 复制，原始 JSON/CSV/log/
  G-code/FRD 仍只保留在数据盘。
- 图表由 `presentation_assets/generate_figures.py` 生成，使用 aDSL 环境已有 Pillow，
  未安装新依赖。presentation 共 67 个本地链接，检查结果 missing=0；图表脚本通过
  `py_compile`，三张 PNG 均为 1600×900。
- 结论边界在 presentation 中单独标注：14/15 是 Agent 自审批而非真实成功率；CLIP
  pilot 不是统计等价检验；01/02 为无扰动刚体代理；03/04 不是工程或制造认证；
  U01–U08 尚未生成，不能提前写入 failure rate。
- 根据用户要求补充物理检测环境图：
  `presentation_assets/renders/simulation/` 下新增 01 MuJoCo control 初态/5秒终态、
  02 T03 四个 partial-build 高度、03 E01 实际 C3D10 fine mesh + FRD 位移/应力场、
  04 A02 `surface_classes.ply` support/contact 分类共4张 PNG，并分别插入 presentation。
- 可复现入口为 `presentation_assets/generate_simulation_snapshots.py`。脚本只读数据盘
  冻结的 XML/URDF/INP/FRD/PLY，输出展示 PNG，不修改原模型或分析结果；MuJoCo 使用
  项目 `.bashrc` 的 EGL 配置，CalculiX 图直接解析 actual fine deck/FRD，PrusaSlicer 图
  保留正式 PLY 的面颜色。03 图明确区分 FRD nodal-extrapolated 4.29 MPa 与 canonical
  DAT 3.963 MPa 的后处理口径。

## 2026-09-06 CPU stability pilot and geometry-feedback loop

- GPU worker remained pending, so U01 and U05 were generated with local CPU Blender EEVEE at 512x512, 64 samples, max_rounds=1. Both produced GLB, URDF, and eight renders.
- The unchanged Final Standing Stability checker found U01 upright (peak 0.002 degrees, verdict INDETERMINATE) and U05 naturally tipped (peak 97.140 degrees, final 96.504 degrees, support area 0, 16/16 impulse directions tipped, verdict UNSTABLE/high).
- U07 failed on the shared data disk with sqlite3.OperationalError: no such table: main.agent_messages; run.json remained status=running. This exposes both shared-filesystem SQLite fragility and incomplete exception-path state finalization.
- Important path pitfall: project temp is a symlink to /jiigan-hp/lms/aDSL/experiment, so it is not suitable when the goal is to avoid shared-disk SQLite. The first U05 feedback edit there received the intended plinth patch but exited 135 (SIGBUS) before render/critic.
- With explicit user authorization, the U05 edit was rerun in the real project-local local_experiment directory. The checker output was supplied verbatim as edit feedback. The agent added a concentric Cylinder plinth (radius 0.26, height 0.04).
- The local edit used the original model profile and critic settings: gpt-5.6-sol/high, max_rounds=2, 8 views, 1024x1024, 256 samples, local Blender EEVEE. Image Critic approved after round 1; Code Critic did not run because the current workflow short-circuits on Image Critic approval.
- Unchanged MuJoCo recheck: support area 0.2110, peak tilt 0.008346 degrees, final tilt 0.002314 degrees, 0/16 impulse directions tipped, minimum observed F/W 0.35. Natural fall changed true to false, but composite verdict remains INDETERMINATE/low due mesh-contact and mass-model uncertainty.
- This is an externally orchestrated checker-to-text-feedback-to-edit-to-checker feasibility result, not an automatic ObjectWorkflow gate and not a success-rate estimate.
- presentation.md, simulation figures, model assets, machine-readable evidence, and adsl_presentation_bundle_20260906.zip were updated. The ZIP now also includes analysis_archive, reports, and physics_analysis.md so report links are self-contained.
- The presentation now documents the exact external feedback handoff, ObjectWorkflow planner/coder/critic sequence, and byte-matching source diff. The portable evidence directory includes baseline/revised source.py, plan.json, runtime_config.json, and the coder response containing its single apply_patch call.
- Added an eight-view appearance audit. The upper lamp geometry is unchanged, but the radius-0.26 plinth is locally conspicuous: its diameter is 44.4 percent greater than the radius-0.18 sphere's diameter, although its 0.04 height is only about 2.4 percent of the 1.65 scene height. Conclusion: object identity retained, non-invisible base-fidelity tradeoff.


## 2026-09-06 通用 Engineering checker 与 FEA 自动反馈闭环

- `ObjectWorkflow` 新增通用 `CheckerSpec` / `CheckerResult` 协议、外部进程执行器和
  `object-engineering-critic`。checker 可重复注册；每轮结果固定写入
  `rounds/round_XX/checkers/<name>/result.json`，原始求解证据保留在同目录。
- required checker 的 `ERROR` 按基础设施故障停止，不把 solver/slicer 缺失伪装成几何
  缺陷；`FAIL/INDETERMINATE` 才交 Engineering Critic。配置 checker 后，只有外观和
  所有 required checker 均 PASS 才发布，最后一轮不再无审查发布。
- CLI 新增可重复 `--checker-config` 和 edit-only `--check-first`。`--check-first`
  跳过 initial edit patch，使 round 1 字节一致地检查输入源码；resume 会从
  `runtime_config.json` 恢复 checker specs。
- `experiments/workflow_checkers/run.py` 将现有 MuJoCo standing、CalculiX/Gmsh FEA、
  PrusaSlicer support 分析适配到统一协议；spec 可声明用户态
  PYTHONPATH/LD_LIBRARY_PATH prepend，无需 root 安装或 shell wrapper。
- 新增 `tests/test_workflow_checkers.py`；aDSL 环境完整回归 84/84 通过。一次误用
  ttrv 环境导致 collection 缺包，改用明确的
  `/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python` 后通过。
- FEA baseline：0.9 m 四腿薄椅，isotropic PLA proxy，底面全固定，座面 -1000 N、
  椅背 +300 N。模型 exact union 后 watertight/单 component，coarse/medium/fine 均
  求解并收敛；不是断连或 solver failure。
- Round 1 为真实 FAIL：U=39.728 mm，U/H=4.414%，17.571 MPa，FoS=2.846，
  buckling=9.061，hotspot=[0.26675,0.23675,0.48121] m。Image Critic approved。
- Engineering Critic 把热点映射到后立柱/座面连接；Coder 唯一 patch 是将两根后立柱
  从 22×22 mm 改为 22×40 mm，并将 y-center 0.245→0.236 以保持外缘不变。
- Round 2 仍 FAIL 但显著改善：U=19.522 mm，U/H=2.169%，9.613 MPa，FoS=5.201，
  buckling=9.107，mesh convergence 继续 PASS。位移下降 50.86%，应力下降 45.29%。
- 用户确认“有改善即可”，在 round 3 FEA 完成前主动停止。冻结结果严格选择 round 2；
  round 3 仅有第二次 patch/渲染、没有完整 FEA，不能用于性能结论。原始 run.json 因
  KeyboardInterrupt 仍显示 running，这是另一个 exception/cancellation 状态收尾缺陷。
- 同一 round 2 椅子上另外实跑通 standing spec（PASS，1/1 state 未超过25°）与 support spec（PASS，关键面无 nominal contact overlap）；证明三份适配器均通过通用 runner，而非只做 schema 单测。
- 两轮 Image Critic 虽均 approved，却都在 observations 中误称 seat thickened；源码 byte diff 证明座面没改，实际是后立柱。此结果继续证明 visual critic 不能描述/验证精确结构修改。
- 八个匹配 Eevee 视图 RGB MAE 平均 0.1416%，差值>10/255 像素平均 0.600%；源码和
  包围盒均证明变化局部，但像素差不是感知质量指标。
- 完整三网格 INP/DAT/FRD 保存在
  `local_experiment/fea_checker_loop_20260906/selected_improvement/evidence/`
  （约248 MiB）。portable presentation 只复制标准 JSON、源码、Critic 记录、16 张
  渲染、两份 GLB 和实际 FRD 场对比 PNG。


## 2026-09-06 Progressive-build checker 自动反馈闭环

- 通用 checker adapter 新增 `progressive` 模式和固定 spec/config：authored Z-up、
  Δh=总高1%并加入 geometry birth/complete events、密度1000 kg/m³、摩擦2.0、
  无底板粘附、每层 MuJoCo 自然沉降5秒，峰值倾角严格>25°为 FAIL。
- adapter fail closed：非空 partial 没有 MuJoCo 结果记 ERROR，clip failure 记
  INDETERMINATE；Agent feedback 只保留首次和最坏失败层，同时附 COM、signed/
  normalized margin、support area 和 active geometry names。
- 新增2项 adapter 回归测试；progressive/workflow 定向测试 14/14 通过。
- 三个手写 aDSL 诊断 fixture 都满足 final standing PASS / progressive FAIL：
  C01 39/107（first 47%，peak34.574°），C02 46/107（first36%，peak30.968°），
  C03 45/107（first39%，peak31.944°）。它们用于隔离机制，不计入自然prompt失败率。
- C01 进入真实 Codex CLI Engineering checker loop。Round1 checker 将首次/最坏高度、
  COM越界与 `narrow_base/central_stem/right_display_shelf` 传给 Engineering Critic；
  Coder唯一patch是在base后新增
  `stability_foot = Cube((0.44,0.28,0.02), center=(0.08,0,0.01))`。
- Round2同协议0/108失稳，peak0.0908°，signed margin +0.1345，support area0.1232，
  progressive PASS；独立成品standing peak0.0337°也PASS。流程在round2以
  `appearance_and_required_checkers_approved` 正常完成。
- 独立 PrusaSlicer复核前后均 `support_required=true`，overhang area同为
  3147.968 mm²；低foot只解决整体倾覆，不解决右悬臂过悬。Slicer是逐层G-code/
  support规划，不是逐道沉积热力学仿真。
- 八视图RGB MAE 0.169%，差值>10/255像素占0.682%；修改局部但底部foot可见。
- 新的Image Critic可证伪错误：baseline round1源码没有foot，它却声称已看到
  low-profile support extension。结合FEA椅子误称seat thickened，说明Image Critic
  不能审计精确结构修改；必须以source diff和checker为准。
- 完整记录：
  `presentation_assets/results/progressive_checker_loop/EXPERIMENT.md`；
  仿真图：`presentation_assets/renders/simulation/02b_mujoco_progressive_checker_loop.png`。

- 方法修正：真实FFF progressive主判据应由PrusaSlicer逐层G-code驱动，累计model+support+brim质量、COM、底板连通footprint与所需粘附强度；当前无粘附MuJoCo Δh降级为脱粘极端stress test，不再解释为真实打印过程主模拟。

## 2026-09-06 Standing GPU 正式批次终止

- `adsl_gpu_renderer` 与 `adsl_standing_cases` 从 04:43 UTC 起持续约 6 小时仅报告
  `Worker pending`，没有生成新的 U01–U08 正式样本；已有结论来自 CPU pilot U01/U05、
  U05 standing checker 闭环、3 个手写 unstable controls 和 progressive C01 闭环。
- 经用户确认 standing 阶段已经完成后，主动停止这两个 tmux 会话，并终止 tmux 退出后
  遗留的精确 pending allocation 进程。GPU run
  `/jiigan-hp/lms/aDSL/experiment/gpu_render_queue/runs/20260906T044309Z/`
  标记为 `MANAGER_ABORTED`；standing attempt_02 记录为失败/取消，不计入统计。
- `presentation.md` 已将“仍在 GPU 队列中”改为“长期 pending 后主动终止”；没有新模型或
  新 checker 结果可加入。后续 overhang prompt pilot 使用 CPU，不再新申请 GPU。
## 2026-09-06 Overhang checker-feedback pilot

- Standing/progressive experiments are complete; the six-hour pending GPU standing batch was terminated with no new U01–U08 outputs.
- Overhang pilot uses CPU only and paper-aligned CAP3D/MARVEL captions. Exact paper prompt IDs are not public.
- O01 mug feedback added two local 50-degree underside chamfers at the handle mounts. PrusaSlicer nominal support contact fell from 278.361 to 225.243 mm² (-19.08%) while print AABB stayed fixed; geometric overhang rose from 3087.424 to 3119.069 mm² (+1.02%).
- User selected an improvement-oriented stopping rule for this demonstration, so O01 is archived as measurable improvement but strict FAIL, not as a complete solution.
- O02–O04 baselines completed and all required support; O05–O08 expansion was stopped to avoid unnecessary CPU/API use.
- Canonical presentation evidence: `presentation_assets/results/overhang_checker_loop/EXPERIMENT.md`, paired GLBs, and `presentation_assets/renders/comparison/overhang-O01-four-view-before-after.png`.
- Flow pitfall: raw support-region triangle ID lists inflate the agent context; preserve them in raw artifacts but summarize object, face count, area, centroid, and bounds in future feedback payloads.

## 2026-09-06：StepCode LMS 版本隔离与 `lms_proxy`

- StepCode 上游从 2026-09-06 起拒绝 `1.2.48`，最低要求为 `1.2.70`；已验证 `1.2.79` 的模型列表和 `/v1/responses` 均返回 HTTP 200，`gpt-5.6-sol` 可用。
- 升级首次失败不是网络问题，而是 `/tmp` 的 6 GiB tmpfs 已满；将 `TMPDIR` 指到项目盘后升级成功。
- 全局 `/root/.stepcode/current` 已恢复指向 `1.2.48`；root 下的 `1.2.79` 副本已删除。
- LMS 新版位于 `/vepfs_default/chanxueyan/lhp/lms/stepcode/versions/1.2.79`，凭据仍从 `/root/.stepcode/config.json` 内存读取，没有复制到 LMS 或仓库。
- 未 source 的干净 shell：`stepcode --version` 为 `1.2.48`，且没有 `lms_proxy`。
- 使用新版前执行 `source /vepfs_default/chanxueyan/lhp/lms/.bashrc`；此后 `stepcode --version` 为 `1.2.79`，且 `ADSL_STEPCODE_BIN` 指向 LMS wrapper。
- 代理流程：`lms_proxy start`、`lms_proxy health`、任务结束后 `lms_proxy stop`。LMS 私有代理监听 `127.0.0.1:44949`，启动时会停止全局旧 Supervisor 代理以释放端口。
- localhost 健康检查必须绕过环境代理（`curl --noproxy "*" ...`）。直接 kill 全局代理会因 Supervisor 的 `autorestart=true` 被重新拉起，应使用 Supervisor stop；LMS 私有代理则由 `lms_proxy` 管理。
- 当前验收结束后，全局代理和 LMS 私有代理均保持停止，`44949` 无监听。

## 2026-09-09：物理 checker 代码同步主分支

- 将站立稳定性、渐进打印稳定性、CalculiX FEA 和 PrusaSlicer overhang/support
  checker 统一接入 aDSL 的 execute–critic–refine 工作流；required checker 参与最终
  发布判定，`FAIL`/`INDETERMINATE` 进入 Engineering Critic，`ERROR` 按基础设施故障
  fail closed。
- 保留 `--check-first` 原始 round-1 基线、结构化反馈、checker 配置不可被 Coder 修改、
  GPU Eevee 串行队列及 Codex CLI 默认传输；StepCode HTTP 后端仍可显式选择。
- 物理 checker 主提交为 `fbf8915`。合并 `origin/master` 时保留其
  `codex_api_plan.md` 与 `temp/` 忽略规则，并合并 README 的 StepCode、GPU 和 checker
  使用说明。
- 合并后在 aDSL Python 3.10 环境运行完整测试：`89 passed`。测试时清除机器级
  `ADSL_STEPCODE_BIN`，避免主机私有 wrapper 改变配置单测的假设。
- PDF、ZIP、`local_experiment/`、`presentation_assets/`、`presentation.md`、`temp`
  和未接入正式队列的 `vram_guard.py` 均保留为本地实验材料，不进入 Git 提交。

## 2026-09-09：Checker v2、源码定位与确定性候选修复

- 按 `Physics_Checker_Unification_and_Source_Repair_Plan.md` 完成 protocol-v2
  findings、AnalysisContext、AST+运行时 SourceIndex、坐标感知定位、结构化
  RepairProposal、隔离候选执行和确定性接受/回退策略；旧 protocol-v1 保持兼容。
- 每轮新增 `analysis_context.json`、`source_index.json`、`findings.json`、
  `localization.json`、`repair_proposals.json`；候选保存全部 checker、图像评审和
  `decision.json`，`repair_history.jsonl` 防止重复尝试。
- 真实 `standing_u05` StepCode+GPU+MuJoCo 闭环完成：基线 peak/final tilt 为
  97.1569°/96.9103°（FAIL）；Agent 只在 `SphericalBase` 增加半径0.15、高0.06、
  底面位于z=0的居中低矮圆柱脚；候选降至0.3904°/0.2251°（PASS）。
- 确定性策略确认 AST 范围合法、required checker 无回归、图像 critic approved，
  因此接受候选。8视角平均归一化RGB MAE为0.000476，变化像素约0.246%；变化局部，
  但这些视觉指标不等于功能证明。
- 完整实验在 `local_experiment/physics_checker_unification_20260909_u05_local/`；
  实施报告为 `reports/physics_checker_unification_20260909.md`。完整回归104项通过。
- 踩坑：SourceIndex 必须把自定义 Asset 实例关联到构造类源码；Engineering Critic
  的自由字典不能使用 SDK strict schema；StepCode瞬时502/503需要有限重试；GPU共享盘
  heartbeat需短grace并将真实丢卡标成基础设施错误；Code Critic不读源码不能算通过。
- 挂载盘上的 `sessions.sqlite3` 曾出现 `disk I/O error`；复制到项目盘后
  `PRAGMA integrity_check=ok` 并成功resume。后续活跃workspace/SQLite/小文件放项目盘，
  完成后的大体积render/solver证据再归档到 `/jiigan-hp/lms/aDSL/experiment/`。
- 本轮结束后 LMS StepCode proxy 已停止；A800 GPU renderer worker 保持运行，避免卡被
  回收。MuJoCo结果仍是固定代理条件下筛查，不是真实打印或安全认证。
