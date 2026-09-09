# aDSL 诊断 case 结果

- 生成时间：2026-08-30T20:05:21.581715+00:00
- Verification Status：ANALYZED
- 说明：这是环境相关的定向小样本诊断，不是论文完整 benchmark，也不支持统计性 SOTA 结论。

| Invocation | Logical case | Launcher | Run | Round | Approved | Finalization | Tokens | Seconds |
|---|---|---|---|---:|---|---|---:|---:|
| T01-main | T01 | completed | completed | 1 | True | image_critic_approved | 20316 | 690.601 |
| T01-one-round-control | T01 | completed | completed | 1 | False | round_limit_after_execution | 14754 | 76.41 |
| T02-bookshelf | T02 | completed | completed | 2 | True | image_critic_approved | 72780 | 260.031 |
| T03-radial-wheel | T03 | completed | completed | 1 | True | image_critic_approved | 19022 | 93.362 |
| T04-hollow-mug | T04 | completed | completed | 2 | True | code_critic_approved | 83554 | 296.796 |
| T05-patterned-desk | T05 | completed | completed | 1 | True | image_critic_approved | 20154 | 399.392 |
| I01-example-image | I01 | completed | completed | 2 | True | image_critic_approved | 63564 | 235.458 |
| I02-example-arti | I02 | completed | completed | 2 | True | image_critic_approved | 122624 | 473.045 |
| A01-cabinet | A01 | completed | completed | 1 | True | code_critic_approved | 49638 | 197.739 |
| A02-faucet | A02 | completed | completed | 2 | True | image_critic_approved | 78075 | 251.327 |
| E01-chair-slat-edit | E01 | completed | completed | 1 | True | image_critic_approved | 26230 | 69.973 |
| S01-living-room | S01 | completed | completed | 1 | True | image_critic_approved | 23426 | 438.111 |
| M01-base-motorcycle | M01 | completed | completed | 1 | True | image_critic_approved | 26871 | 161.294 |
| M01-cyberpunk-edit | M01 | completed | completed | 2 | True | image_critic_approved | 123800 | 256.868 |
| M01-cyberpunk-scratch | M01 | completed | completed | 2 | True | image_critic_approved | 91806 | 325.917 |

## 逻辑 case 预期

### T01 — text_structure

Exact component count, contact, centering, and even spacing.

- exactly four equal legs
- legs touch seat underside at four corners
- seat centered
- exactly three evenly spaced back slats
- no armrests

### T02 — text_structure

Primitive composition can express a paper-like bookshelf prompt.

- exactly four curved deep-red shelves
- top shelf smaller
- books or boxes present

### T03 — text_relations

Relational layout supports exact radial repetition and contact.

- hub and rim
- exactly sixteen evenly spaced spokes
- spokes contact hub and rim

### T04 — text_topology

DSL can express hollow forms and constructive geometry.

- open top
- visible wall thickness
- connected handle
- hollow interior
- watertight where appropriate

### T05 — text_appearance

DSL can satisfy patterned material and furniture structure requirements.

- diagonal black-white desktop pattern
- drawer and handle
- two side panels

### I01 — image_to_shape

Pure image-conditioned reconstruction works without textual expansion.

- recognizable match to asset/example.png
- no prompt details beyond image instruction

### I02 — image_to_articulation

Pure image-conditioned articulated reconstruction infers movable structure.

- recognizable match to asset/example-arti.png
- articulation represented in URDF
- joint semantics visually plausible

### A01 — articulation

Multiple revolute and prismatic joints have correct pivots, axes, limits, and motion.

- two hinged doors
- one prismatic drawer
- correct axes and limits
- non-colliding sampled motion

### A02 — articulation

Small control affordances can be articulated semantically.

- two rotating knobs
- one central lever
- three movable joints
- plausible pivots and limits

### E01 — edit_preservation

Editing changes requested count/spacing while preserving unrelated geometry.

- back slats change from three to five
- leg and seat geometry preserved
- no unrelated additions

### S01 — scene_relations

Agent and DSL generalize from objects to a multi-object relational scene.

- sofa against back wall
- coffee table in front
- TV on right facing sofa
- lamp on left
- rug under table and front sofa legs

### M01 — memory_edit

Persistent edit context helps a cyberpunk variant relative to scratch generation.

- base motorcycle recognizable
- edit preserves motorcycle identity
- cyberpunk additions are localized
- compare edit vs scratch rounds, tokens, and source diff
