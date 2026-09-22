# EXACT + ASCII STL 修复与 SF02/SF03 全新生成

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run
- Origin Date: 2026-09-22
- Verification Status: 精度修复 VERIFIED；全新生成结果尚未完成
- Version Label: exact_ascii_stl_fresh_v1

## 修改及验证

生产变更只包括公共 Boolean 使用 EXACT，以及固定装配两个导出分支显式
`printed.export(..., file_type='stl_ascii')`。保留同一最终网格、打印平移、单位、材质、
checker 算法/容差、Image/Code 审核、Engineering/Coder 单一修补循环。
不删面、不补洞、不修改生成源码；最终 checker 仍读取实际落盘 STL，而非绕过它检查别的资产。

- 24 项针对性回归通过，12.56 秒：公共 Boolean 选择与异常处理、visual_only/导出行为、
  小面经打印平移后 ASCII STL 坐标逐值保留、正常和倾斜 T 支架真实导出。
- 此前失败的两个真实导出测试恢复 PASS；没有改期望或放宽阈值。
- 新导出的正常 T 支架实际 assembly_topology：2/2 打印件、1/1 接口 PASS。
  证据 `temp/exact_ascii_stl_topology_20260922/checkers/assembly_topology/report.json`。
- 本例恢复不代表 SF02/SF03 所有开口已修复，也不证明实物固定或制造成功。
- ASCII 文件比二进制大；本机支持高精度读取，不保证外部切片器/查看器不会再次降低精度。

验证命令与上一份报告相同，新增测试已包含在 `test_fixed_assembly_visual_only.py` 中；
pytest 本次输出目录为 `temp/exact_ascii_stl_tests_20260922/`，不覆盖失败记录。

## 新生成设置

- 两例：SF02、SF03，只启用 assembly_topology，非新增 w/wo 全套配对。
- 原始任务来自 `experiments/standing_fea_30/case_manifest.json`；未加入旧源码、模型或旧渲染。
- 使用现有 `ObjectWorkflow.generate`，Planner 自主分件/接口，Coder 生成完整新程序；
  保留 Image/Code Critic、Engineering 建议与隔离修补/retained 发布。
- 冻结：1 scene unit=1 mm、单侧余量0.2 mm；SF02整体90×90×150 mm，SF03整体90×80×180 mm。
- 每例5轮评估（初始+最多4次源码修补），不追加；API请求重试沿用配置，不新建候选循环。
- CLIProxy 既有独立 tmux `adsl_cliproxy_20260919`，模型 `gpt-5.6-sol`，max_retries=6。
  实验不启停代理、不改凭据；不做跨实验累计token记账，保留本次调用与成本。
- CPU CYCLES 512×512、32 samples；geometry执行120秒、render300秒，checker原有隔离预算不改。
- 使用项目代码盘 `temp/assembly_exact_ascii_fresh_20260922/`，每例独立目录，失败按现有分类保存/继续。
  共同流程故障仍暂停。旧批次、原始模型和旧补测保留，不把新独立生成与旧修补混为同一起点。

## 提交入口与证据

SF02 不在旧 `run.py` CLI 的三例白名单中，但已有 `prepare(cases=..., sizes=...)` / `main()` 支持这两例。
因此只用本次目录下的小包装调用已有入口，不改生产生成/实验框架。

```bash
bash experiments/tmux_session.sh adsl_exact_ascii_fresh_20260922 \
  /vepfs_default/chanxueyan/lhp/lms/aDSL \
  bash temp/assembly_exact_ascii_fresh_20260922/launch.sh
```

启动前通过既有 `prepare()` 冻结 batch/input；`submission.json`、实现快照/哈希和模型配置保存在新目录。
`batch.log` 与 tmux 同时保留输出；任务退出后回到可输入 shell。每例的 `input_audit.json`、
`stage_inputs/`、`api_calls/`、`assembly_versions.json`、候选图片/模型、checker报告和 `result.json`
由原入口保存。是否实际启动/完成以本次 `run_status.json` 和实时日志为准，本说明不预宣称生成成功。
