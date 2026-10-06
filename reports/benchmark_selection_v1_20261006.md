# ABO / Toys4K 第一轮筛选总结

记录日期：2026-10-06，时间均为 UTC。状态：候选获取、预览和有限参考测量已完成；开发推荐待用户审核。

本轮取得 55 个元数据候选，52 个成功预览，10 个可靠材料实体，给出 ABO 10 + Toys4K 10 个开发推荐。推荐按可用指标取并集，不能解释为 20 个完整物理合格样本。尚未运行 Planner/Coder 生成对比、拆合收益实验或 FEA。

## 1. 代码、数据与完成时间

- 筛选工具位于隔离工作树 `/tmp/adsl_benchmark_selection_20261006`，分支 `feat/benchmark-selection-v1`，提交 `736d8d2e8d7843e054d9c6be229169b5b940fffd`。
- 复用的生产实现基线：`master@2a11b196fb7c09a72747fd8f6977b433f0428bf9`。各阶段缓存描述符另保存实际代码、配置和产物哈希。筛选工具当前不在主工作区的 `benchmark/` 下；本次总结不合并或推送代码。
- 全部原始资产、派生几何、图片、测量及日志保存在数据盘：`/jiigan-hp/lms/aDSL/benchmark/selection_v1/`。大型模型没有纳入 Git。
- ABO 按官方模型索引与商品元数据取得 GLB；Toys4K 按已有 CSV 编号和文件 SHA 使用非官方 Blender ZIP 镜像 `lihong-cs/3dgeneration_baseline@365b279da42ee1f6aa612319f0b95a616c05f70b`。镜像渠道不改变数据集身份，也不构成资产公开再分发授权。
- Toys ZIP 于 **11:57:19** 完整校验结束：31,576,633,609 bytes，SHA256 `e326255aa624a7a582ee052aed737e6d91d484d43a504e0d8f4bd51354b9ca00`。归档位于 `raw/archives/toys4k_blend_files.zip`，同目录保留 provenance；25 个选中实例已提取并核对源身份。
- 后续批次于 **12:50:44** 标记 `COMPLETED`，依据 `logs/revision_20261006/remaining_batch_status.json`。增量实施开始记录为 08:11:56，至该批次完成约 4 小时 39 分钟，含下载、实现、等待和处理，不是纯计算耗时，也不是首次筛选的总耗时。

## 2. 实际完成数量

| 项目 | ABO | Toys4K | 合计 |
|---|---:|---:|---:|
| 元数据候选 | 30 | 25 | 55 |
| 记录为已下载 | 30 | 25 | 55 |
| 当前源身份校验有效 | 29 | 25 | 54 |
| 预览成功 | 29 | 23 | 52 |
| 可靠材料实体 | 5 | 5 | 10 |
| 当前有效 overhang 测量 | 5 | 5 | 10 |
| 正常使用姿态 standing 有效且 PASS | 2 | 0 | 2 |
| 当前外观资格有效 | 12 | 7 | 19 |
| 开发推荐 | 10 | 10 | 20 |
| 用户确认开发样本 | 0 | 0 | 0 |

ABO 有 6 份测量报告，其中 5 份属于当前有效记录，另 1 份过期，不计入当前结果。两例壁挂物体的地面站立诊断 PASS 单列保存，不计入正常独立站立样本。

推荐 20 例中的指标覆盖为：ABO 外观 10 / overhang 4 / standing 2；Toys4K 外观 7 / overhang 4 / standing 0。不同指标可重叠。20 个推荐中有 3 个 Toys 目前仅获 overhang 资格，外观完整性仍待审核。

候选状态为 recommended_dev 20、excluded 2、needs_review 33。当前有效粗标签计数：complex_surface 15、standing_sensitive 17、grouping_tradeoff 18、multipart_contact 18；标签可重叠，未分类不表示没有挑战。

## 3. 本轮采用的协议与已完成验证

- 固定种子 20261006；更正 ABO 分类，三阶/两阶梯 `B07WDJRMPG`、`B07WMRJ68R` 保留记录并排除当前类别范围，`B082JGPBLQ` 改为柜类；增量补充 5 个 ABO，没有重建整份候选集。
- 原文件保留，派生副本统一最长边 150 mm，保存层级、缩放、使用姿态和清理记录。只复用保持几何的退化面/微裂缝处理，不任意补洞、加厚、删部件或加底座。
- 成功预览包含原材质与中性材质两套八视图（6 环绕 + 俯视 + 仰视）及固定输入图；CPU Cycles、512×512、32 samples，透明独立 PNG 不加文字，联系表使用白底 JPEG。逐帧记录实际相机参数。
- overhang 使用 Dapper 目标函数，α=0.3、Rvox=0.1，整件参考 N=1，统一冻结每例 h/V_ref，搜索 24 个轴对齐正旋转。PASS 表示测量完成有效，不表示无支撑或可直接打印；面积是 G 最佳姿态下的面积，并非独立面积最小值。
- standing 采用自由整体参考、统一密度 1240 kg/m³、重力 9.81 m/s²、5 秒观察、25°倾倒阈值、既有 rigid_flex 后端。当前筛选协议按观测中的峰值倾角判定，静止诊断另存；不固定接地部件，不验证生成模型的多接口保持力。
- 已有本轮实现验证记录：定向测试 52 passed / 2 skipped；启用原生且包含 Dapper 回归后 63 passed / 0 skipped；独立原生 smoke 7 项通过，含稳定体 PASS、倾倒反例 FAIL/74.1214°、24 朝向与缓存/坏文件隔离。不同集合不相加。本次只整理既有记录，没有重新运行测试。
- VLM 累计真实尝试 47 次、成功 43 次：ABO 37/36，Toys4K 10/7。本次增量新增 22 次；失败同 key 保留并缓存，累计上限 50，不自动扩额度。粗标签仍需要人工确认。

## 4. 20 个开发推荐

用途缩写：A=外观，O=overhang，S=正常独立站立。它们表示当前参考的评测资格，不是生成结果的验收结论。粗标签原值与逐项原因见 `recommended_dev20.jsonl`。

| 来源 | source_id | 类别 | 当前用途 | 推荐理由与限制 |
|---|---|---|---|---|
| ABO | B076V626RZ | 灯具 | A | 形状可辨、功能部件接触清楚；材料实体未可靠测得。 |
| ABO | B07BWJCZJW | 椅/凳 | A/O/S | 曲面软包与支撑结构可辨，当前两项物理参考测量有效。 |
| ABO | B07F3Y9BSC | 桌 | A | 曲线桌面与桌腿适合形状观察；孤立弯曲组件的归属需人工确认。 |
| ABO | B07S6WNZ7Y | 柜/书架 | A | 窄高柜体、门板与层板有分组讨论空间；尚无可靠实体测量。 |
| ABO | B075X2XZDD | 灯具 | A | 弯曲灯臂、细长支撑与截面变化；物理资格未确认。 |
| ABO | B07B4LZP9Q | 椅/凳 | A | 扶手、靠背、软垫和腿部外观清楚；连通材料参考未验证。 |
| ABO | B07QD6V6MP | 桌 | A | 细支撑与桌面/框架接合清楚；物理测量未知。 |
| ABO | B088HDFTSS | 柜/搁板 | A/O | 三角与曲边轮廓；壁挂搁板，独立站立不适用。 |
| ABO | B07QZ21DDV | 椅/凳 | A/O/S | 曲面软包及独立支撑清楚，正常姿态站立参考通过。 |
| ABO | B07GFWF2GM | 桌 | A/O | 面板/桌面具分组讨论空间；多材料岛限制整体站立资格。 |
| Toys4K | cat_057 | 猫 | A/O | 曲面角色完整可辨，overhang 有效；正常使用姿态待确认。 |
| Toys4K | cow_012 | 牛 | A | 躯干曲面、细腿与附属部位清楚；连通材料参考未验证。 |
| Toys4K | dinosaur_020 | 恐龙 | A | 长尾、细肢与脚部有形状挑战；实体与使用姿态待复核。 |
| Toys4K | dragon_007 | 龙 | A | 翅膀、尾部与躯干变化丰富；当前飞行姿态需要外部支撑。 |
| Toys4K | lion_003 | 狮子 | A | 鬃毛、躯干与肢尾相接可辨；可靠连通参考未验证。 |
| Toys4K | monkey_005 | 猴 | A | 头部、帽子、肢尾及细支撑有截面变化；物理资格未确认。 |
| Toys4K | robot_050 | 机器人 | A | 多个接触部位与窄脚支撑清楚；连通实体与站立待复核。 |
| Toys4K | bunny_004 | 兔 | O | 当前可靠实体与 overhang 测量有效；外观审核及站立适用性未确认。 |
| Toys4K | cat_055 | 猫 | O | 当前可靠实体与 overhang 测量有效；外观审核及站立适用性未确认。 |
| Toys4K | cow_006 | 牛 | O | 当前可靠实体与 overhang 测量有效；外观审核及站立适用性未确认。 |

Toys4K 共 25 例的 standing 限制：19 例自然使用姿态或独立站立适用性未确认，5 例未验证单一连通材料实体，1 例需外部支撑。5 个可靠 Toys 参考均还缺姿态/适用性确认，不能把 standing=0 写成全部不稳定。

## 5. 缺陷、失败与结论边界

当前几何缺陷统计为涉及案例数：开放边 40、非流形边 12、重复面 10、方向不一致边 6、零面积三角形 3。类别可能重叠，不能相加当成无效案例总数；这些记录也不等同于自动清理后的全部实体失败。

原始网格不满足实体计算要求，并不自动说明图片外观有缺陷，也不自动阻止 Agent 根据参考图重新构造有效模型。应分别处理：外观完整性、参考物理可测性、生成结果有效性。参考物理 unknown 时仍可做外观生成和生成模型自身检查，但不能输出伪造参考值或宣称已完成可靠物理对比。主体确实缺少关键部位的参考需人工复核。

- `ABO_B07QCQ1J7M` 当前源文件 SHA 与历史记录不符，原因未确认；保留原文件与历史记录，暂停其资格。
- `Toys4K_dinosaur_055` 渲染失败，`Toys4K_dinosaur_059` 导入失败；不把环境/执行故障直接写成几何或稳定性失败。
- 本轮没有真实拆合测量，grouping_tradeoff 是机会假设，multipart_contact 不是 GT 连接图；没有证明分组改进、生成方法优于 baseline 或多接口保持性能。
- 历史 `review/closeout.md/json`、`package_validation.json` 仍是上一轮数量和旧包记录；三个 Toys 推荐的 selection_note 仍残留“归档不可用”占位。当前事实以最新 `summary.json`、批次完成记录、当前资格字段与源/产物哈希为准。本次仅记录这些不一致，不修改筛选器、历史报告或原始候选。

## 6. 审核入口与后续边界

- [当前数量与指标统计](/jiigan-hp/lms/aDSL/benchmark/selection_v1/review/summary.json)
- [所有候选](/jiigan-hp/lms/aDSL/benchmark/selection_v1/manifests/candidates.jsonl) / [20 个推荐](/jiigan-hp/lms/aDSL/benchmark/selection_v1/manifests/recommended_dev20.jsonl)
- [ABO 联系表](/jiigan-hp/lms/aDSL/benchmark/selection_v1/review/ABO_contactsheet.jpg) / [Toys4K 联系表](/jiigan-hp/lms/aDSL/benchmark/selection_v1/review/Toys4K_contactsheet.jpg)
- [逐例审核 README](/jiigan-hp/lms/aDSL/benchmark/selection_v1/review/README.md)
- [审核 ZIP](/jiigan-hp/lms/aDSL/benchmark/selection_v1/selection_v1_review.zip)：305,316,517 bytes，约 305 MB，不含 31.58 GB 原始归档或大型模型。包内部分历史说明过期，使用时注意前述限制。
- 原始/派生/图片/测量/日志分别在数据根下 `raw/`、`derived/`、`previews/`、`measurements/`、`logs/`；配置与协议位于筛选工作树 `benchmark/configs/selection_v1.json`、`benchmark/selection_protocol.md`。

本轮在推荐审核处结束。需要用户确认图像完整性、正常使用姿态及开发名单；未自动扩展正式 100 例或启动生成对比。开发集及近重复资产族应在未来正式测试集冻结时排除。公开给生成方法的输入只含固定图、正常物体描述、尺度和使用姿态要求，参考网格、风险标签及参考测量保留在评测侧。
