# LLM 推理技术基础 · 路线图

> 逐章清单、标题与顺序以 `series.yaml` 为准；本文件只记录每卷目标与发布状态，避免两处维护完整目录。

| 卷 | 目标 | 章节数 | 已发布 |
|---|---|---|---|
| 导读 | 通用前置：读法、知识库思路、概率与信息论、硬件、benchmark | 6 | 2 |
| 推理基础 | 一次推理的账本：生命周期、代价模型、KV Cache、数值格式 | 4 | 0 |
| 基础模型 | GLM、DeepSeek、Qwen、Kimi 的结构与推理影响 | 5 | 0 |
| 模型结构 | 从 token 到 logits：embedding、tokenizer、位置编码、attention、MoE、FFN/Norm、长上下文 | 8 | 0 |
| 算子加速 | 结构落到设备：GEMM、Grouped MatMul、FA、PA、mRoPE cache、MegaMoE、量化算子 | 8 | 0 |
| 加速特性 | 调度、KV/显存、量化、投机、并行通信、长上下文服务、框架生命周期、性能工程 | 9 | 0 |
| 实践 | 性能调优、精度定位、nano 项目 | 4 | 0 |
| **合计** | | **44** | **2** |

## 写作顺序（建议）

1. 先把“推理基础”四篇补齐，形成一条 decode 请求的完整账本；
2. 再写“模型结构 + 算子加速”的纵向链路：attention → FA/PA，MoE → Grouped MatMul/MegaMoE；
3. 系统卷在基础链路稳定后写，框架解剖放在最后，用一次请求串起前面所有机制；
4. 实践卷按真实调优/排障案例穿插，不追求先写完。

## 维护规则

- 新增文章：写 `.md`，把 `series.yaml` 里对应条目的 `planned: true` 和 `title` 去掉。
- 删除/合并：先归档旧文（`status: archived` + `superseded_by`），再从 `series.yaml` 移除，不直接删文件。
- 标题以 `series.yaml` 的 `title`（已发布则取 frontmatter）为准；本表不重复列章节标题。

### tensor-layout · 张量基础：形状、布局与数据类型

- 所属卷：数学与计算基础，承接 linear-algebra。
- 状态：作者已授权开写，v0.1 首稿完成，待作者评审。
- 正文或草稿路径：`docs/drafts/learn/tensor-layout.md`；尚未加入公开内容目录。
- 本篇目标：读懂张量各轴含义，推导常见运算的形状，并区分逻辑形状、底层存储与数据类型。
- 建议架构：①从 token 表示 [B,S,D] 建立问题；②张量、阶数、shape 与轴语义；③索引、切片及维度增减；④逐元素运算、广播、归约与批量矩阵乘法；⑤reshape 与轴置换的区别；⑥storage、stride、offset、连续性及视图/拷贝；⑦dtype、类型转换与逻辑存储字节数；⑧贯穿示例、最小实验与自测。
- 范围边界：以普通稠密 strided 张量为主；dtype 仅介绍用途、字节数与转换，位编码/舍入/数值范围留 numeric-formats；不展开完整 Attention、GPU 分块布局、量化实现与性能调优。
- 下一步：作者审阅八节首稿，确认布局深度、例子与篇幅后修订；保持 series 中 planned 不变。
- 已完成：八节正文、参考与修订、八道自测及答案；按 [B,S,D] → [B,S,H,d] → [B,H,S,d] 贯穿，补广播语义误用、reshape/转置对照、stride 与 offset 手算、视图/连续化及逻辑字节数边界。
- 待确认：待作者评审；API 来源已核对 PyTorch 2.14 官方文档，示例在 Python 3.12.14 / PyTorch 2.14.1 CPU 临时环境运行。临时环境提示未安装 NumPy，但示例不依赖 NumPy，七段全部通过。未新增站点依赖，未测 GPU 或性能。
- 已做验证 / 是否发布：提取草稿七段 Python 代码，各自在独立命名空间执行，全部断言通过；调用站点 `_render_markdown` 检查目录、六张表格、七段代码及 stride 公式文本；核对 frontmatter 与 planned 状态，`git diff --check` 通过。未构建、未发布。
- 更新时间：2026-10-24。
