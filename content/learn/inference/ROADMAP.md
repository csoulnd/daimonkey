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
