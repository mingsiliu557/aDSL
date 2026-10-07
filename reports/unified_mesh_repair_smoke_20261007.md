# 统一网格修复与去重：人工压力 smoke 实施报告

日期：2026-10-07。实现及离线验证已完成。

基准 `master@2836c3d1f49f5b43ba267ffa8840b8158417391d`；实际验证代码 `9593627108518d27a740ebe66ccfaa9fff4f5fd1`。隔离分支 `codex/unified-mesh-repair-smoke`，工作树 `/tmp/adsl_unified_mesh_repair_20261007`。原主工作区的未提交修改保留。

## 修改与提交

| 阶段 | 提交 | 实际修改 |
| --- | --- | --- |
| A | `fdb0267` | local_precision_repair 迁为 mesh_repair；六项输入修复集中；mesh_defects 归入 mesh_validity；更新 consumer imports，消除 validity→export_glb 依赖 |
| B | `cb16cd1` | checked repair 内部结果复用实际 rounded target solid／metrics／全局方向；公开四元组和 JSON 键不变；失败指标区分 source 与 target |
| C | `2c526a0` | 复用文件读回实体；延后显示；正常 N+2 写出；按文件投影最终状态；部分诊断按需生成；显示写出错误仍验证已有 STL |
| D | `9593627` | 删除 14 个退役 private Blender Boolean helper；迁移旧测试代码并静态核对；增加唯一人工 smoke 文件和统一入口文档 |

核心调用仍为 mesh_validity 的 normalize_blender_input / validate_mesh / checked_solid / target_mesh / validate_written_mesh。Mesh64 求值与公开几何 API 不变；输入 trial 原子提交，显示修复仅改显示副本。没有增加修复器注册框架、全局缓存、容差或几何重建依赖。

主要文件：adsl-core/core/export/{mesh_repair,mesh_validity,export_glb,export_assembly}.py、benchmark/scripts/reference_worker.py、adsl-core/core/docs/fixed_assembly.md、tests/test_mesh_repair_smoke.py。旧 Boolean／constructive／loft／Mesh64 测试仅迁移代码及 AST 核对，未执行历史工厂。三份旧 backend 诊断脚本只标注历史 SHA 依赖。

## 实际验证

| 阶段 | 固定配置 | PASS | FAIL | SKIP | 耗时 |
| --- | --- | ---: | ---: | ---: | ---: |
| A | I1–I8、K1–K3 | 11 | 0 | 0 | 2.66 s |
| B | T1–T10、N1–N5、K1 | 16 | 0 | 0 | 3.65 s |
| C | W1–W7 | 7 | 0 | 0 | 5.96 s |
| D | 完整且唯一 33 配置 | 33 | 0 | 0 | 9.79 s |

阶段重复的 K／W 配置不累加为独立样本。W3／W4 补充失败 Asset／parent 身份与临时对象计数断言后，定向 2 passed（3.77 s），再运行最终全部 33 配置。最终 33 配置中：12 个安全修复、11 个正确拒绝、3 个 K 正常控制、7 个 W 导出／反馈配置（2 个正常导出、5 个故障注入）。安全修复包含居中后可直接通过的业务转换；不把每次业务调用都写成实际几何编辑。

附加小检查：隔离子进程禁止 bpy/bmesh 仍能导入纯数组模块；reference_worker 仅加载模块和核对 import 映射，未调用资产导入器；生产代码不存在退役 14 函数定义／调用，mesh_validity 不再反向依赖 export_glb。

初次 T2 测试构造把 cast 位移误算成翻边操作位移；校正为“翻边位移 0、输出顶点等于实际 rounded 顶点、总误差仍受原预算约束”，没有更改算法或阈值。静态 worker 导入检查补入其 CLI 脚本目录后完成。正式逐阶段和最终 gate 的结果见原始日志。

## 33 配置预期／实际

| ID | 预期 | 实际 | 精度编辑次数 | 秒 | 关键证据 |
| --- | --- | --- | ---: | ---: | --- |
| I1 | 有效／受限修复 | PASS：安全修复 | 0 | 0.0139 | 共线 cap 重三角化；位置、面积、体积和材质不变 |
| I2 | 有效／受限修复 | PASS：安全修复 | 0 | 0.0051 | 完全重复共享边清理；位移为 0 |
| I3 | 有效／受限修复 | PASS：安全修复 | 0 | 0.0047 | 一 ULP 三边界裂缝闭合，位移 1.686e-7 mm |
| I4 | 拒绝 | PASS：正确拒绝 | 0 | 0.0020 | 真实缺面保留并拒绝；试修回滚 |
| I5 | 拒绝 | PASS：正确拒绝 | 0 | 0.0017 | 已显式三角化的退化面按现有限制拒绝 |
| I6 | 拒绝 | PASS：正确拒绝 | 0 | 0.0019 | 错误绕序拒绝，数据／对象数不变 |
| I7 | 拒绝 | PASS：正确拒绝 | 0 | 0.0025 | 0.001 间隙不扩大半径；拒绝 |
| I8 | 拒绝 | PASS：正确拒绝 | 0 | 0.0024 | 可焊裂缝＋真实孔洞，试修回滚 |
| T1 | 有效／受限修复 | PASS：安全修复 | 6 | 0.0210 | 6 次安全收缩；业务入口复用 target 实体／指标 |
| T2 | 有效／受限修复 | PASS：安全修复 | 1 | 0.0073 | 一次对角翻转；顶点不动、面数不变 |
| T3 | 有效／受限修复 | PASS：安全修复 | 1 | 0.0086 | 纯翻面进入修复；业务居中后直接转换也有效 |
| T4 | 有效／受限修复 | PASS：安全修复 | 7 | 0.0217 | 远离零面积区域的翻面也处理；两个组件保留 |
| T5 | 有效／受限修复 | PASS：安全修复 | 6 | 0.0242 | 6 次收缩；有方向内腔与材料体积保留 |
| T6 | 拒绝 | PASS：正确拒绝 | 0 | 0.0068 | link condition 阻止危险收缩 |
| T7 | 拒绝 | PASS：正确拒绝 | 0 | 0.0162 | 逐面材质边界保护，拒绝 |
| T8 | 拒绝 | PASS：正确拒绝 | 0 | 0.0063 | 跨材质翻边被拒绝 |
| T9 | 拒绝 | PASS：正确拒绝 | 0 | 0.0063 | 1e-9 预算不足；剩余翻面证据完整 |
| T10 | 拒绝 | PASS：正确拒绝 | 0 | 0.0157 | 真实 1e-9 间隙 cast 坍缩；不跨壳焊接 |
| K1 | 有效／受限修复 | PASS：正常控制 | 0 | 0.0101 | UNCHANGED；实体来自实际 rounded target，公开返回值兼容 |
| K2 | 有效／受限修复 | PASS：正常控制 | 0 | 0.0028 | 严格正面积薄面保留，数据不变 |
| K3 | 有效／受限修复 | PASS：正常控制 | 0 | 0.0078 | 0.2 mm 真实间隙与两个组件保留 |
| N1 | 有效／受限修复 | PASS：安全修复 | 6 | 0.0366 | 大平移不放大局部毫米预算 |
| N2 | 有效／受限修复 | PASS：安全修复 | 6 | 0.0365 | mm_per_unit=.001，同物理尺寸／预算 |
| N3 | 有效／受限修复 | PASS：安全修复 | 6 | 0.0368 | mm_per_unit=1000，同物理尺寸／预算 |
| N4 | 有效／受限修复 | PASS：安全修复 | 0 | 0.0043 | 刚性平移后局部微裂缝判据不变 |
| N5 | 拒绝 | PASS：正确拒绝 | 0 | 0.0024 | 当前映射焊接需位移 1.686e-4 mm 超硬上限，拒绝 |
| W1 | 有效／受限修复 | PASS：正常控制 | 0 | 1.8290 | 4 次 GLB＝N+2；单件／scene／exploded 实际读回有效 |
| W2 | 有效／受限修复 | PASS：正常控制 | 0 | 0.3375 | visual_only 顶层 NOT_EVALUATED，正常显示概要可用 |
| W3 | 注入导出／反馈 | PASS：注入反馈 | 0 | 0.0953 | B 求值注入失败；A STL 保留，部分预览不批准 |
| W4 | 注入导出／反馈 | PASS：注入反馈 | 0 | 0.4974 | A body 注入失败但 part mesh 留作诊断；新目录取消故障后恢复 |
| W5 | 注入导出／反馈 | PASS：注入反馈 | 0 | 0.3466 | 实际损坏 scene 文件；scene 引用失效，单件／STL 不受牵连 |
| W6 | 注入导出／反馈 | PASS：注入反馈 | 0 | 0.3276 | 实际损坏 exploded；scene／单件仍有效，无额外诊断 GLB |
| W7 | 注入导出／反馈 | PASS：注入反馈 | 0 | 0.3904 | 仅 B 显示转换失败，STL 有效；typed feedback → NO_PROPOSAL → NO_CHANGE |

输入重三角化／焊接记录在 attempted_measures／normalizations，表中的“精度编辑次数”仅统计显示收缩／翻边，不能用其 0 推断输入未处理。逐配置 before／after 指标、预算、位移上界、原始数组、拒绝 code／stage 和全部尝试，见完整 JSON；未测得的值保留 null。

## 导出状态与反馈证据

W1／W2 各自恰好写 A.glb、B.glb、scene.glb、exploded.glb，无额外诊断 GLB。STL 与各 GLB 独立读回。W5 真实 scene 文件损坏后，其发布引用清空，但单件 file_validation 仍为 PASS；W6 只拒绝 exploded，不生成额外诊断。W3／W4 确认失败 Asset、parts／bodies 构造数据和 parent 身份不变；故障点对象／mesh 数量不增，显示写出仅保留合法的 N 个 mesh 与 N 个 parent，无孤立 mesh。它们的有效 part mesh 保留供诊断，未完成的件不获得完整文件成功状态。W7 canonical／STL 不受显示失败牵连，显示诊断保留 part B、node、stage、output_role。

W7 使用现有 FixedAssembly 循环和 Engineering 证据适配器，外部执行器／评审／模型全部 stub：Engineering 收到带 B 区域的 typed findings，返回 NO_PROPOSAL；Coder 返回 NO_CHANGE 并正常停止。当前版本文件实际复制到 stub 工作区，source／manifest／文件 SHA／retained version 绑定核对；预置旧 GLB 不被用于批准。该项证明错误路由与停止，不证明真实 Agent 修模能力。

## 复现与证据入口

在本机现有环境中：

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=/tmp/adsl_unified_mesh_repair_imports:/tmp/adsl_unified_mesh_repair_20261007
export ADSL_TEST_FIXED_REAL=1
cd /tmp/adsl_unified_mesh_repair_20261007
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest --collect-only -q -p no:cacheprovider tests/test_mesh_repair_smoke.py
ADSL_MESH_SMOKE_EVIDENCE_DIR=/tmp/fresh_artificial_mesh_evidence /vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q -p no:cacheprovider tests/test_mesh_repair_smoke.py
```

其他环境安装该 checkout 的 core／agents 并检查模块路径后，用对应 Python 执行相同 pytest 命令。构造全部内联于唯一新测试文件；无需任何历史 fixture／案例／资产。

证据目录：[unified_mesh_repair_smoke_20261007/](unified_mesh_repair_smoke_20261007/)。其中 results.json 为摘要，raw_evidence.zip 包含完整阶段／逐配置记录、人工最小数组及本次 W 文件与 stub 流程证据；provenance.json 记录实际依赖、源码哈希和测试 SHA。

本轮没有历史物体 replay、下载、benchmark、真实 API、图片渲染或 Topology／Standing／Overhang／FEA checker 进程。所有 native 工作在 CPU 上。完整自交检测仍为 NOT_EVALUATED；报告范围是现有修复能力、拒绝边界和实际文件一致性，不是任意非法 mesh 可修或制造／站立批准。
