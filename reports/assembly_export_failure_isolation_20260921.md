# 固定装配导出反馈与单例隔离

基线：master `5472ee15bf11f06098938a8d920b66f701194e5f`。
本轮为本地代码修改及无 API 测试；未启动实验，未修改生成模型，未提交/推送。

## 修改范围

- `adsl-core/core/export/export_assembly.py`：bounds/pose 比较改为仅由回归测试显式开启
  `compare_placement=True`；正常 visual_only 路径只检查可读、部件 ID/齐全性、有限非空网格。
  同网格复用及单位/变换的实际导出实现不变，不再将数值比较当候选门槛。
  逐面比较仍只用于回归。旧 geometry 模式的实体/接口检查不在本次修改范围。
- 同文件记录具体 `part_id`、`stage`、`failure_kind`：显式空网格/遗漏/件内断开属于候选证据；
  保存/读取/最终序列化失败属于导出不可用；泛化异常原因未知，不猜几何根因或公共故障。
- `adsl-agents/fixed_assembly.py`：上述短反馈进入现有审核上下文与同一 Coder 请求；
  已定位候选问题沿原预算修补，独立 Image/topology 问题仍可处理。
  仅有导出不可用/未知导出异常时正常结束，不为解决文件问题盲改形状，不标合格。
- `experiments/fixed_assembly_prompt/run.py`：去掉 EXPORTED 前缀、单例 API/工具错误和
  泛化 FLOW_ERROR 的自动停批。当前版本证据与历史记录分开；不可读 manifest 仍保存错误结果。
  只有明确的冻结输入变更、实际请求契约违规、结果/源码版本不一致及已确认公共环境不可用停批。
  缺审计信息不等于已确认违规；未执行/未评估不等于 PASS。
- `run_paired.py`：复用同一公共故障判定；个例失败不阻止其他案例。

保留：原始资产、working/retained、共享修补预算、Image/Code 裁决、assembly_topology 测量及阈值。
未自动启用旧 topology、standing、FEA、overhang；未改 API 配置或重试。
旧实验结果、失败日志和既有冻结配置不重写；本轮没有重新判定旧资产是否通过。

## 验证

**108 passed in 6.76s**。复用相关测试和参数化用例，关键覆盖：

1. 空/缺失/断开部件：具体部件、阶段、证据到达原修补入口，原源码保留。
2. 文件不可读、保存失败、未知导出原因：不误判 PASS、不盲目修形状、不仅凭名称停批。
3. 同一保存文件数值摆放不同：正常路径不做比较，回归路径仍检出。
4. 旧候选错误不污染当前候选；本例失败后继续独立案例。
5. 明确公共故障、冻结输入变更和源码版本错配仍停止。
6. Topology 失败仍进入反馈，失败候选不覆盖 retained；既有网格复用/材质导出回归通过。

实际命令：

```sh
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q \
  tests/test_fixed_assembly_visual_only.py tests/test_fixed_assembly_empty_mesh.py \
  tests/test_fixed_assembly_recovery.py tests/test_fixed_assembly_prompt.py \
  tests/test_fixed_assembly_paired.py \
  tests/test_assembly_topology.py::test_batched_engineer_source_fallback_one_budget_and_publication \
  tests/test_assembly_topology.py::test_rejected_candidate_cannot_publish_its_checker_result \
  tests/test_fixed_assembly_exports.py \
  tests/test_fixed_assembly_diagnostics.py::test_next_edit_uses_working_candidate_not_original
```

使用 mock 模型/执行器/检测结果及小网格文件序列化，未调用真实 API 或重跑已有物体。
这证明的是上述控制流/错误分类，不证明 agent 能修好真实装配，也不解决上游 API 503。
工作区本身完全不可写时无法保证写出结果；本轮未尝试覆盖或恢复系统环境。
