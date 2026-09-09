# aDSL vs TRELLIS：7-case CLIP pilot

Verification Status: **ANALYZED**

本报告只描述 2026-09-02 完成的本地粗略实验，不是 aDSL 论文完整复现，也不验证论文的总体 SOTA 结论。

## 结论

- text-to-3D 的 5 个 case：aDSL 24.171，TRELLIS 25.201，aDSL 低 1.031 CLIP points。按实验前锁定的 ±2 工程观察线，可称为“粗略接近”。
- image-to-3D 的 2 个 case：aDSL 66.822，TRELLIS 70.719，aDSL 低 3.897 points；两个 case 都是 TRELLIS 更高。
- 视觉复核支持 image track 的结果：TRELLIS 的桌腿比例、柜体轮廓、抽屉和接缝更接近输入；aDSL 输出更 primitive。
- CLIP 不可靠测量 exact count、接触、拓扑和约束满足。T05 中 TRELLIS 更像常见办公桌，却增加侧柜/额外结构；aDSL 更贴合指定结构，但 CLIP 仍偏向 TRELLIS。

因此，当前证据只支持：

> aDSL 在结构明确的 text cases 上可以凭程序化几何取得接近 TRELLIS 的 CLIP；在当前两个纯图像外观复原 case 上则明显落后。

## Material Passport

- run root：/jiigan-hp/lms/aDSL/experiment/clip_trellis_pilot_20260902
- manifest：run root/manifest.json
- aDSL 输入：2026-08-30 审计中冻结的 5 个独立 text GLB 和 2 个 image-conditioned GLB
- TRELLIS 源码：clean commit 6b0d64751ad54d9c32d7b05fec482eb29178f56f
- TRELLIS text 权重：TRELLIS-text-xlarge-original
- TRELLIS image 权重：TRELLIS-image-large
- TRELLIS seed：1；每 case 一次；不做 prompt expansion；不自动 retry
- CLIP：本地 openai/clip-vit-large-patch14
- GPU：单卡 A800；TRELLIS、Eevee、CLIP 严格串行
- 成功情况：7/7 TRELLIS GLB、14/14 render groups、三个阶段 exit code 均为 0

## 公平渲染

两种方法全部通过同一个项目 renderer：

- Blender Eevee
- 8 views，方位间隔 45°
- elevation 15°
- 1024×1024
- 256 samples
- neutral material
- 相同 AABB center/max-extent normalization
- nominal white world color

注意：项目固定 Filmic/Medium High Contrast/exposure=-0.8。实测所有代表图的角像素为 RGB(134,134,134)，所以 nominal white world 在最终 PNG 中呈灰色。两侧设置一致，不影响方法间公平性，但不能称作纯白像素背景。

## 分数

主指标是 reference 与 8 个 render view 的 CLIP cosine×100 后取 mean；失败按 0 计入。

| Case | Track | aDSL | TRELLIS | aDSL−TRELLIS |
|---|---|---:|---:|---:|
| T01 | text | 25.164 | 25.878 | -0.714 |
| T02 | text | 22.277 | 23.637 | -1.360 |
| T03 | text | 22.739 | 24.344 | -1.604 |
| T04 | text | 27.874 | 27.748 | +0.125 |
| T05 | text | 22.799 | 24.399 | -1.600 |
| I01 | image | 70.015 | 74.400 | -4.385 |
| I02 | image | 63.629 | 67.037 | -3.409 |

| Track | Method | Matched mean | Shuffled-reference mean | Gap |
|---|---|---:|---:|---:|
| text | aDSL | 24.171 | 15.994 | +8.176 |
| text | TRELLIS | 25.201 | 16.652 | +8.550 |
| image | aDSL | 66.822 | 60.150 | +6.671 |
| image | TRELLIS | 70.719 | 62.437 | +8.282 |

两张输入图的 self-control 都约为 100。乱序对照证明评分链路有区分力，但 image track 只有两个同类家具，样本过小且错配分仍偏高。

## 视觉发现

- T01 chair：aDSL 的四腿和三条背横档更直接；TRELLIS 有较多座下细结构。两者 CLIP 很接近。
- T02 bookshelf：两者都有明显书架语义；TRELLIS 外观更自然，aDSL 的 curved front edge 仍不突出。
- T03 wheel：aDSL 的 16-spoke 程序结构清晰；TRELLIS 轮毂/轮圈更自然。CLIP 无法证明辐条恰好为 16。
- T04 mug：aDSL 略高 0.125，但两者都只是“看起来像 mug”；hollow interior、wall thickness 和 watertightness 仍需几何 verifier。
- T05 desk：TRELLIS 的常见办公桌先验提高语义分，却违背 exactly-one-drawer/two-side-panel 约束；这是 CLIP 与 constraint satisfaction 不一致的关键 case。
- I01 table：TRELLIS 更接近参考的桌面厚度、围板和腿部比例。
- I02 nightstand：TRELLIS 更接近完整柜体和抽屉面；aDSL 有粗糙横向构件，轮廓和接缝较弱。

## 限制

- ±2 是工程阈值，不是置信区间或等价检验。
- 只有一个 seed；不能估计 TRELLIS 的采样方差。
- text prompt 多为家具/日用品，覆盖面小。
- image track 只有两个家具 case；不能外推到 Toys4K。
- neutral material 有利于比较几何语义，但移除了纹理/颜色质量差异。
- CLIP 不覆盖 execution safety、代码质量、edit locality、articulation、printability 或物理有效性。
- 没有下载完整 Toys4K；只在数据盘保存公开 metadata 和冻结的 10-category subset 清单。

原始数值以 run root/clip/scores.json 为准，诊断图在 run root/analysis/。
