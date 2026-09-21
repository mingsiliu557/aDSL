# 固定装配：统一反馈与文件工具恢复

日期：2026-09-21。实施基线：master `8cc8e4c31165399bbeb50a7c8268583aef40eb21`。
这是代码及无 API 验证报告，不是 SF13 新实验结果。既有删除/修改保持不动，旧实验账本未重置。

## 改动

- `adsl-agents/fixed_assembly.py`：仍在同一个循环内先执行/审核，再收集 topology；Engineering 无方案或失败时，不再直接结束有可信待修反馈的修补。每轮重新选择建议或通用回退提案，经原 `prepare_candidate()` 和 `_repair()` 一次执行。只剩无定位的未验证结果且外观/导出没有待修项时正常停止。预算及 retained 验收不变。
- `adsl-agents/assembly_topology.py`：仅修改反馈/Engineering adapter。已知报告字段规范为 workspace 内绝对路径；保存可用性、源哈希和版本角色。类/函数定位保留；不能解析的属性 scope 作为未确认线索，不冒充索引或自动扩大到整个模块。虚构 finding/source ID 的提案仍拒绝，但原始可信反馈可以继续交 Coder。无效提案/未解析正文不作为指令回传。
- `adsl-agents/tools/files.py`：缺文件、错误分页参数、错误/不存在的 JSON pointer 返回短错误并记录 `success=false`，由当前 Runner 内的模型自行纠正；不增加重试循环。越界仍拒绝，损坏 JSON、编码、权限/I/O 问题记录明确错误并抛出。精确 `apply_patch`、原子写入及编辑权限没有修改。
- `adsl-agents/service.py`：仅两处 `source_grounded` 判定，要求成功读取 assigned_source；失败读取、只读报告均不算已看源码。
- `experiments/fixed_assembly_prompt/verify_topology.py`：将原边界定位报告作为结构化 `evidence_files` 传入。原始定位始终标为 `initial_source_only`，修改后的 checker 结果才是当前事实。

特别澄清：工具的 workspace 从来没有变成候选目录。旧 SF13 中是模型拼接了错误的候选目录前缀；本次明确路径约定，不改变 `resolve()` 的边界。

证据路径转换只修改反馈副本，不修改已绑定版本哈希的 manifest/审核记录。缺少可选文件仅标 `UNAVAILABLE`，不抹掉内联测量。

## 验证

执行环境：`/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python`。

```sh
python -m pytest -q \
  tests/test_read_file_recovery.py tests/test_patch_recovery.py \
  tests/test_bounded_checker_inputs.py tests/test_generation_review_contract.py \
  tests/test_assembly_feedback_recovery.py tests/test_assembly_topology.py \
  tests/test_fixed_assembly_recovery.py tests/test_fixed_assembly_visual_only.py \
  tests/test_fixed_assembly_plan_revision.py
```

结果：110 passed，13.80 秒。

另核对普通修补与初始生成的三个直接回归入口：

```sh
python -m pytest -q \
  tests/test_overhang_candidate_isolation.py::test_normal_repair_still_requires_patch \
  tests/test_fixed_assembly.py::test_old_schema_and_prompt_unchanged \
  tests/test_fixed_assembly.py::test_actual_planner_and_initial_coder_inputs
```

结果：3 passed，2.27 秒。两组共 113 项通过；`git diff --check` 通过。

### 四个交付问题

| 问题 | 验证结果及边界 |
| --- | --- |
| 两路反馈是否进入同一个 Coder？ | 模拟 Image/Code、几何结果和模型回复，实际执行原 `_repair()`。Coder payload 同时包含本轮两项 topology finding 与 Code 待修项；仅一个候选、一个 MODEL_STARTED 预算记录。 |
| 路径/定位错误是否可恢复？ | **实际调用文件工具**：错误候选路径返回 READ_NOT_FOUND；随后按 payload 中根目录绝对路径读到诊断，读取 assigned_source，再由真实 apply_patch 修改隔离候选。失败/成功事件均落在 edit_outcome；属性 scope、提案解析失败、虚构 ID、空提案分别验证建议降级或可信反馈回退。 |
| 未验证、预算与原资产是否保留？ | 无位置的 INDETERMINATE 不触发盲修；外观通过不使 topology 自动合格；最后一次修改仍完成复查；真实 NO_CHANGE 保留原资产；完成后 resume 不重复调用/扣次；前轮提案不泄漏；发布源码与 checker 哈希匹配。 |
| 哪些是真实执行？ | 新增闭环测试的模型、几何及渲染为模拟；`_repair/read_file/apply_patch`、文件隔离/哈希/预算/发布代码真实执行。现有 topology 测试另包含本地小几何和短时子进程限时回归；没有导出/修改真实 SF13，没有调用 API、Blender、FEA 或历史批次。 |

## 保持不变与尚待验证

- `adsl-core/core/assembly_topology.py`、阈值、TabSlot、add_part/connect、正式网格复用、导出一致性检查和 Planner 均未改。
- `ToolEvent` / `resolve()`、候选控制器、验收策略没有另建或重写。未启用旧 topology、FEA、standing、overhang。
- 没有新实验；SF13 原有 14 条开口边/层板及接口未验证的历史结果仍成立，不能称网格已修好。
- 等用户明确授权下一次续测，才在新目录复用 SF13、给一次实际源码修补机会。API 用量汇总等不在本次范围。
