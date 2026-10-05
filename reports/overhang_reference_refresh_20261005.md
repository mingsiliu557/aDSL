# Overhang 参考更新重测修复

基线：`feat/loft@3c800fb5285600d39eac5a16e82d79b9640b2575`；隔离 checkout `/tmp/adsl_loft_20261004`。仅修改 `adsl-agents/fixed_assembly.py` 和对应分件选择回归，不修改几何、评分公式或 checker 必需性。

## 原因与改动

大象完整运行中，必要主体修复触发 partition reference 更新。随后 Overhang 重测复用了该候选已经存在的 `checkers/assembly_overhang`。`run_checker` 在创建输出目录时抛出 `FileExistsError`，尚未开始重测；错误处理覆盖了 `result.json`，旧参考下的 `report.json` 仍为 PASS。

重测现在使用候选内独立的 `partition_reference_refresh/checkers/assembly_overhang`，保留原测量、打印资产及 physics 输入。新运行被绑定到当前版本，刷新目录的所有产物纳入版本文件哈希。参考建立失败也独立写出 INDETERMINATE，不覆盖旧证据，不发布旧参考的打印 layout。

Overhang 仍是原配置中的非必需检查；没有把优化评分改为制造批准条件。原完整运行记录的 `approved=true` 仅表示当时外观、导出及必需 Topology/Standing 检查通过，不能解释为 Overhang 成功。历史完成记录保留。

## 定向验证

环境：`source /vepfs_default/chanxueyan/lhp/lms/aDSL/temp/elephant_full_no_fea_20261005/env.sh`。

```bash
python -m pytest -q -p no:cacheprovider tests/test_partition_selection.py
python -m pytest -q -p no:cacheprovider tests/test_partition_feedback.py tests/test_assembly_physics.py
```

- 选择/恢复回归：28 passed，14.14 s。模型与 checker 状态使用 mock，评分算术及循环分支是真实代码；mock 的目录创建已改为严格拒绝复用，以覆盖本次实际错误。验证新旧报告隔离、失败保留证据、新参考和最终打印包一致、快照哈希及 completed resume。
- 反馈/物理适配回归：18 passed，5 skipped，33.88 s。跳过项是未启用的原生 Standing、Blender 和 FEA 用例，不计为通过。

## 当前大象的独立真实复测

输出目录：`/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/elephant_overhang_refresh_20261005/`。其中 `recheck.py` 为可复核的一次运行脚本，`provenance.json` 固定源码、manifest、新参考、实际 checker 配置和历史证据哈希；`summary.json` 保存实际测量、推荐 STL 读回与原记录未改写检查。

- 输入来自完整运行 `elephant_full_no_fea_20261005` 的 `attempt_0001`，复用现有导出，不调用模型或重新生成。
- source SHA256：`b03b080be2af9a2cf398676d47d96db3916e46e48e08016c4e1f5f25df03e85b`。
- manifest SHA256：`896344eb2c2a3e9a8be6404909a31f940916b570b99d66a68f94a5bf1b5c7c22`。
- 新 reference SHA256：`3e8b4bc1feb4e77b049602624fd3d417bfb7c9617f6bf5b54a98bca1b481340f`。
- 仅运行真实 Overhang 子进程，CPU；FEA、其他 checker、Engineering/Coder 均未调用。原完成 book/result 不改写，复测不等于新增一次完整 Agent 优化闭环。

实际结果：**PASS**，真实 checker 耗时 **78.665 s**；N=1，h=10.865300750732423 mm，V=785，G=301，O=484。

推荐打印包已通过现有 `publish_print_layout` 校验 source/reference/mesh hash 后输出到 `print_parts/attempt_0001_recheck/`。独立读回 STL，床面最低 Z=0，重新计算 G=301，与报告一致；STL SHA256 为 `a949706ff47877a248cd5bdd94264033f13bad1ea83cd90c8a03e22a60417001`。复测前后所保护的旧 source、manifest、checker 目录、reference、completed book/result 的哈希全部未变。

旧参考下该候选曾测得 V=759、G=303、O=456。参考与网格尺寸已经变化，不能把 456→484 宣称为分件优化收益；本次没有改变模型或分件，仅补齐新参考的有效测量和打印输出。
