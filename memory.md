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
