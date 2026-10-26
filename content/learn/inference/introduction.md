---
title: "导读"
type: guide
domain: inference
status: active
one_liner: "从模型架构到算子执行：八卷内容、软硬件架构图与三条学习路线。"
prereq: []
related: []
sources: ["https://www.hiascend.com/", "https://docs.vllm.ai/en/v0.32.0/design/arch_overview/"]
version: ""
---

# 导读

> LLM 大模型入门基础教程：从基础模型架构到最终的算子执行，逐步构建推理加速知识地图。每个层次都用尽量直观的可视化拆解，从基础公式入手，配合源码食用更佳。

## 整体架构

本知识库共八卷，以下是各卷收录的内容：

| 卷 | 回答的问题 | 主要内容 |
|---|---|---|
| [导读](/learn#part-guide) | 这套教程怎么读 | 教程目标、知识体系、学习路线与知识库核心思路 |
| [数学与计算基础](/learn#part-prerequisites) | 理解推理需要哪些基础 | 线性代数、张量、概率、信息论、数值格式、硬件基础与推理软件栈 |
| [模型结构](/learn#part-architecture) | 模型由哪些计算组成 | Tokenizer、词嵌入与输出头、位置编码、注意力变体、FFN/Norm、MoE、长上下文结构 |
| [模型家族概览](/learn#part-families) | 认识常见模型的规模与结构 | GLM、DeepSeek、Qwen、Kimi 的代表型号、参数规模与常见结构 |
| [推理基础](/learn#part-foundations) | 模型如何生成，代价是多少 | Prefill、Decode、采样、FLOPs/显存与 Roofline、KV Cache 基础 |
| [算子加速](/learn#part-operators) | 模型计算如何高效执行 | GEMM、Grouped MatMul、FlashAttention、PagedAttention、量化算子，以及 mRoPE 缓存、MegaMoE 进阶案例 |
| [推理系统与优化](/learn#part-systems) | 框架如何组织计算与资源 | 编译与执行、调度、显存与 KV 管理、量化、投机解码、并行通信、长上下文服务、框架解剖与性能工程 |
| [实践](/learn#part-practice) | 如何测量、调优与定位问题 | 推理 Benchmark、性能调优、精度定位与 nano 项目 |

一个记忆方法：**模型结构决定要算什么，算子决定怎么算得快，系统决定什么时候算、在哪里算、和谁一起算。**

```learn-diagram
inference-stack
```

上图辅助展示推理软硬件之间的关系；本教程的内容组织以上方卷表为主。

## 学习建议

按目标选一条路线，不必从头读到尾。

**路线 A · 系统入门**：先补必要的数学与计算基础，理解模型结构，通过模型家族概览认识真实模型，再学习推理过程；随后进入系统与框架章节，按需补算子。

**路线 B · 算子与性能**：从模型结构和推理代价出发，重点学习算子加速，再结合系统机制与实践看懂 kernel 和 profiler。

**路线 C · 问题驱动**：带着显存、吞吐、延迟或精度问题查阅对应章节，遇到不熟悉的机制再回补前置知识。

## 写在最后

这套教程会随着学习和实践持续补充。希望它既能帮助你建立从模型结构到算子执行的整体认识，也能在遇到具体问题时，成为一份方便回查的参考。

接下来，可以从[课程目录](/learn)选择适合自己的章节；想了解内容如何组织与沉淀，可以先读[《知识库核心思路》](/learn/inference/knowledge-base-method)。

如果发现错误、有值得补充的内容，或想交流推理优化，欢迎通过以下方式联系我：

- [**网站仓库** *csoulnd/daimonkey* 查看源码、反馈问题](https://github.com/csoulnd/daimonkey){: .learn-contact }
- [**知识库** *csoulnd/InferTechNote* 查阅笔记、交流补充](https://github.com/csoulnd/InferTechNote){: .learn-contact }
- [**邮件联系** *daidaicurry@foxmail.com* 交流想法与实践经验](mailto:daidaicurry@foxmail.com){: .learn-contact }

## 参考与修订

架构分层参考[昇腾软硬件平台](https://www.hiascend.com/)，框架职责参考[vLLM 架构说明](https://docs.vllm.ai/en/v0.32.0/design/arch_overview/)，按本教程的推理范围重新组织。

| 日期 | 版本 | 改动 |
|---|---|---|
| 2026-10-23 | v0.1 | 初稿：五卷结构、读法、写作约定 |
| 2026-10-24 | v0.2–v0.6 | 重排七卷，补学习路线，将写作约定移入《知识库核心思路》 |
| 2026-10-24 | v0.7 | 增加静态软硬件全景；数学与计算基础独立成卷，模型结构前置；三条路线改为文字 |
| 2026-10-24 | v0.8 | 按当前目录同步八卷表；精简架构图为六层静态主干；优化结尾与联系入口 |
