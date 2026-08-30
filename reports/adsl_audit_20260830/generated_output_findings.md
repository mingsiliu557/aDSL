# 生成物质量与 case 诊断

Verification Status：**ANALYZED**

视觉判断基于每个 workspace 的八视图 contact_sheet、参考图对照、source.py、URDF、GLB 拓扑和 Critic 日志。原始小数据保存在 temp/audit_20260830；机器可读摘要见 case_results.json。

## 运行总表与人工结论

| Invocation | 轮次 | 当前流程 approved | Tokens | Seconds | 人工诊断 |
|---|---:|---|---:|---:|---|
| T01-main | 1 | true / image | 20,316 | 690.601 | 4 腿、3 slats、无扶手均满足；重复结构清楚，但仍混用绝对坐标 |
| T01-one-round-control | 1 | false / critic skipped | 14,754 | 76.410 | 输出可用，却因最后一轮逻辑完全未审查；证明 round bug |
| T02-bookshelf | 2 | true / image | 72,780 | 260.031 | 4 shelves/书本成立；curved nosing 在图中不明显，4 个 CSG 网格有 41 个退化面 |
| T03-radial-wheel | 1 | true / image | 19,022 | 93.362 | 16 spokes 的源码结构和视觉都清楚，是 relational repetition 的强 case |
| T04-hollow-mug | 2 | true / code | 83,554 | 296.796 | 源码有真实 cavity；八视图看起来仍像封顶实心杯，handle 粗糙且 union 非 watertight |
| T05-patterned-desk | 1 | true / image | 20,154 | 399.392 | 结构满足；条纹由 13 个薄 Cube + boolean 模拟，只在斜俯视明显，材质表达很弱 |
| I01-example-image | 2 | true / image | 63,564 | 235.458 | 木桌可辨识；首轮非法 0. thirty；腿/围板/比例/木纹显著粗化 |
| I02-example-arti | 2 | true / image | 122,624 | 473.045 | 三抽屉床头柜可辨识并有 3 prismatic joints；首轮非法 0. forty；接缝/把手/材质粗糙 |
| A01-cabinet | 1 | true / code | 49,638 | 197.739 | 2 revolute + 1 prismatic 正确；原八视图全关闭，motion 并未被 workflow 观察 |
| A02-faucet | 2 | true / image | 78,075 | 251.327 | 3 revolute joints 正确；修复了 floating lever support，但整体更像粗糙工业塔，faucet 语义偏弱 |
| E01-chair-slat-edit | 1 | true / image | 26,230 | 69.973 | 3→5 slats，+6/-6 行；其他结构视觉保持，是最干净的局部编辑 |
| S01-living-room | 1 | true / image | 23,426 | 438.111 | 五项空间关系大致满足；3/8 视图被墙遮挡，场景 primitive 化严重 |
| M01-base-motorcycle | 1 | true / image | 26,871 | 161.294 | 基础摩托车可辨识 |
| M01-cyberpunk-edit | 2 | true / image | 123,800 | 256.868 | 保留基体但 cyberpunk 改动视觉很克制；成本最高，+99/-3 行 |
| M01-cyberpunk-scratch | 2 | true / image | 91,806 | 325.917 | 视觉更统一、更明显 cyberpunk；首轮非法 0. thirty，power-unit CSG 有拓扑问题 |

说明：Seconds 是各 invocation launcher 记录；T01-main 发生在 executor 性能修复前，包含共享盘/模块导入异常开销，不应用于模型速度横向比较。

## 结构与关系：有效，但不是自动可靠

### T01 chair

最终源码使用 grid_shapes 复制四腿、stack_shapes 复制三条横档，因此 exact count 很容易读出。seat/back/upright 的不少位置仍为手写数值，说明 aDSL 是“减少”而非消除坐标推理。

one-round control 生成同需求的有效资产，却没有任何 Critic JSON，run.json 为：

- status=completed
- approved=false
- critic_skipped=true
- finalization_reason=round_limit_after_execution

这是一条控制流反例，不是生成质量失败。

### T02 bookshelf

初轮把内容 place_on_axis 到整个 shelf；curved nosing 高于 board，AABB 支撑面把书抬空。Code Critic 能从代码定位这一点，第二轮改为 shelf.board，说明：

- Iterative repair 确实可修正关系错误。
- Relational helper 的正确性依赖选对语义 subpart。
- AABB 近似不会自动理解“装饰前沿不应当作为书本支撑面”。

最终视觉中四层和书本清楚，但“visibly curved front edge”仍不强：侧板遮挡、前沿颜色相同、固定低 elevation。GLB 中对应四个 board+nosing boolean union 全部 non-watertight，共 41 个 zero-area faces；当前 Agent 没有发现。

### T03 wheel

源码明确用 [reference.copy() for _ in range(16)] 和 radial_shapes(..., rotate_with_layout=True)。半径/长度使 spokes 与 hub/rim 重叠。这个 case 同时满足：

- exact count 可从代码确定；
- even angular layout 由 DSL operator 保证；
- 接触在视觉和参数上都成立。

它是本轮最接近论文“关系操作优于脆弱坐标”的正证据。

## CSG / hollow / appearance 的不足

### T04 mug

MugBody 用 outer cylinder - cavity cylinder，cavity 穿过顶部且在 z=.12 停止，程序确实表达了开口、壁厚和底面。第一轮 Image Critic 认为像封顶实心物；Code Critic读取代码后确认 cavity，但发现 handle 侵入内腔并要求修复。

第二轮由 Code Critic approved，问题是最终八视图仍无法看到杯内壁/底面；用户要求的 visible interior 没有视觉证据。人工看起来仍接近实心/封顶，C handle 由多段 cylinder/sphere union，外观有低多边形折段感。拓扑分析显示 vessel mesh watertight，但 handle union 非 watertight。

结论：程序语义比 render 更正确，但“程序正确”不等于“最终可见要求满足”，也不等于所有 CSG mesh 拓扑有效。

### T05 desk

对角条纹不是 texture/material，而是 13 条超薄旋转 Cube 与白色 slab 做 boolean_intersection。优点是仍可用 primitive DSL 表达；缺点是：

- 正面/低角度几乎看不到图案；
- 几何复杂度和 CSG 风险替代了纹理；
- 图案边缘、粗糙度、真实材质均缺失。

这解释了论文为何另接 SpaceControl/Trellis 处理 high-frequency detail：公开 primitive 主流程本身不提供该质量层。

## 图片重建：语义可辨，细节显著丢失

### I01 table

纯图片输入，没有文字扩写。Planner 正确识别为矩形木桌，最终轮廓、四腿、围板可辨。相对参考图的不足：

- desktop/apron 过厚、比例笨重；
- 腿为直而细的 primitive，参考图的轮廓/收分没有恢复；
- 只有统一棕色，没有木纹、粗糙度或真实光照材质；
- 首轮 source.py 出现 WOOD = (0. thirty if False else 0.30, ...)，compile 失败。

### I02 articulated nightstand

Planner 从单图推断三抽屉，并生成：

- top_drawer_slide / middle_drawer_slide / bottom_drawer_slide；
- 都是 prismatic；
- axis 0 -1 0；
- limit 0..0.35；
- initial 0。

这是强的结构/功能推断。视觉不足：

- 抽屉黑缝过大；
- 把手太细小；
- frame/leg 较 clunky；
- 无木纹，平面棕色；
- 首轮 BRASS 中出现 0. forty，compile 失败。

因此 image-to-articulation 的“结构可解释性”比“参考图保真”更强。

## Articulation：程序能动，原 Agent 不验证运动

### URDF 静态检查

| Case | Movable joints | 类型与方向 |
|---|---:|---|
| I02 | 3 | 三个 drawer prismatic，axis 0 -1 0，limit 0..0.35 |
| A01 | 3 | 左右 door revolute（相反 z 轴），drawer prismatic 0 -1 0 |
| A02 | 3 | 左右 knob revolute around z，central lever revolute around x |

### 85% pose probe

审计工具只修改运行时内存中的 Joint.initial，不改原 source.py：

| Case | 目标状态 | 观察 |
|---|---|---|
| I02 | 三抽屉均 0.2975 | 三层都向前拉出，仍与柜体对齐 |
| A01 | 两门均 1.632 rad；抽屉 0.3825 | 双门向两侧打开、上抽屉拉出，语义正确 |
| A02 | 两 knob 0.455 rad；lever 0.5 rad | 三个控制件均改变姿态并保持连接；小旋转在静态图中不够显著 |

三者 pose GLB 的 SHA-256 都不同于初始 GLB，各产生 8 张 Eevee PNG。联系图在：

- temp/audit_20260830/pose_probes/I02-example-arti/contact_sheet.jpg
- temp/audit_20260830/pose_probes/A01-cabinet/contact_sheet.jpg
- temp/audit_20260830/pose_probes/A02-faucet/contact_sheet.jpg

这个结果支持 joint API 与生成代码，而同时揭示 workflow 缺口：原 Critic 从未收到这些图，也没有轨迹碰撞结果。

## 编辑与 memory 对照

### E01：简单局部编辑成功

| 项目 | 数值 |
|---|---:|
| 源码 diff | +6 / -6 lines |
| unified diff | 33 lines |
| 最终源码 | 75 lines，2,361 bytes |
| requests | 6 |
| tokens | 26,230 |
| rounds | 1 |

视觉上 seat、四腿、rear uprights、整体位置保持，只有 back slats 由 3 变 5。此结果明确支持 localized program rewrite。

### M01：复杂风格 edit 是混合结果

| 指标 | Base | Edit with memory | Scratch |
|---|---:|---:|---:|
| rounds | 1 | 2 | 2 |
| seconds | 161.294 | 256.868 | 325.917 |
| requests | 4 | 15 | 11 |
| tokens | 26,871 | 123,800 | 91,806 |
| source lines | 161 | 257 | 154 |
| diff from base | - | +99/-3 | - |
| execution failure | 0 | 0 | 1 |

Edit 的确保存了原摩托车框架、两轮、座椅和前叉，wall time 比 scratch 少约 69 秒，也避开了 scratch 的首轮语法失败。但它：

- 使用更多模型请求和 tokens；
- 生成源码明显膨胀；
- neon/armor/instrument 改动在图上较细碎，不如 scratch 整体设计明显；
- 需要 2 轮，不是论文示例的 1 轮。

结论：memory reuse 对“保持身份”和 wall time 有帮助，但本轮不支持“更少 tokens/rounds 或更好视觉”的普遍结论。

## Scene：关系存在，观察协议失败

S01 源码和部分正面图可确认：

- sofa 靠 back wall；
- coffee table 在前；
- TV 在右并朝向 sofa；
- floor lamp 在左；
- rug 在 table 下并延伸到 sofa 前腿。

问题是完整房间墙体参与 object-centered orbit，导致约 3/8 视图几乎只有不透明墙面。Image Critic 仍在第一轮 approved，说明“用 ALL views”提示无法弥补视角本身无效。scene evaluator 应当隐藏前墙/天花、使用内部相机或按对象/关系生成定向证据。

## Topology 校正说明

glTF 为 hard normals/UV 常把一个空间顶点拆成多个 face-corner vertex。若直接用 trimesh.is_watertight，普通 cube 会被错误报告为六个断开的面。本报告先复制 geometry，再以 merge_tex=True、merge_norm=True 按空间位置焊接，然后统计：

- 普通 Cube/Cylinder case 焊接后恢复 watertight。
- 只剩 T02、T04 handle、M01 scratch power-unit 的异常。
- 因此这些不是 glTF 表示造成的统一假阳性，而是局部 CSG 输出问题。

## 生成质量的共性短板

1. **primitive 感明显**：圆弧、倒角、薄壁、把手、腿型都较机械。
2. **材质能力弱**：颜色多为 flat RGB，几乎没有 texture、roughness、normal detail。
3. **代码冗长且数字多**：复杂对象仍产生大量绝对坐标，关系 DSL 没覆盖全部形态设计。
4. **视觉批准偏乐观**：固定视角看不到的结构常由代码推断通过。
5. **执行成功不等于 mesh quality**：CSG 的 non-watertight/degenerate faces 不会阻止 publish。
6. **风格编辑易变成“加贴片”**：M01 edit 保留结构很好，但视觉变化弱于 scratch。
7. **场景与对象共用相机策略**：场景遮挡严重。
8. **重复语法污染**：数字被模型写成英文单词片段，说明 tool output 还需 compile gate。
