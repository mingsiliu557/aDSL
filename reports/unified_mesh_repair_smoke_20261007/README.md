# 人工网格 smoke 原始证据

- `results.json`：33 个固定配置的测量摘要。
- `provenance.json`：实际测试代码 SHA、模块路径、依赖与源码 SHA。
- `collect.log`：完整且唯一的 33 个 ID。
- `A/B/C/D.log`、`W_resource_gate.log`：逐阶段原生日志；阶段间复用 ID，不累加为独立样本。
- `D_static.log`：退役函数、依赖方向与静态测试迁移检查。
- `raw_evidence.zip`：逐配置完整 JSON、阶段日志、内联人工工厂，以及本次 W 配置实际写出的 STL/GLB/manifest/工作流 stub 证据。

危险缺陷被拒绝是该配置的预期成功，不是修复成功。W 的 Agent 与下游工作均为 stub；没有真实渲染图片或物理 checker 结果。文件状态描述人工几何及写出文件，不构成制造／站立批准。全部自交检查记录为 `NOT_EVALUATED`。

压缩包 SHA256：`7c186f0e529ce00a8096b4051f4f635033f74cf1744b49baf0b26f1ba22317bb`
