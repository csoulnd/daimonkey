---
title: "导读"
type: guide
domain: inference
status: active
one_liner: "先看推理软硬件全景，再沿数学、模型结构、推理过程与系统优化逐层学习。"
prereq: []
related: []
sources: ["https://www.hiascend.com/", "https://docs.vllm.ai/en/v0.32.0/design/arch_overview/"]
version: ""
---

# 导读

> 这套教程研究一个已经训练好的模型如何执行，以及怎样在精度要求下改善延迟、吞吐和资源占用。先用一张全景图定位，再从必要的数学与模型结构走向推理系统。

## 大模型推理的软硬件全景

从下往上看，硬件提供计算、存储与互联，操作系统与驱动管理资源，硬件使能层提供运行时、编译和算子能力，推理框架把它们组织成可处理请求的系统。

```learn-diagram
inference-stack
```

这是一张职责地图：实际产品可能覆盖多层，也可能直接调用底层算子。CPU 既可独立推理，也常承担加速器系统的主机工作；GPU、NPU 与 TPU 是常见的加速器生态入口，分类并非严格互斥。图中的软件只作代表，具体组合取决于硬件与版本兼容性。

模型结构决定计算和状态，算子实现决定执行效率，推理框架负责调度与资源管理。后文以 GPU 与昇腾 NPU 为主要工程对照，CPU、TPU 用于说明共性与差异。

## 学习顺序

主线是 **数学与计算基础 → 模型结构 → 推理基础 → 模型家族与架构实例 → 算子加速 → 推理系统与优化 → 实践**。完整章节见[课程目录](/learn)。

必要的线性代数、概率与信息论集中在[数学与计算基础](/learn#part-prerequisites)：先掌握矩阵乘法、张量形状、条件概率与 softmax 所需概念；交叉熵、KL 等内容在遇到精度评测时回查。已有基础可以跳读，不必反复从头学。

先读[模型结构](/learn#part-architecture)，理解 Attention、FFN 与输出概率，再进入[推理基础](/learn#part-foundations)，讨论 Prefill、Decode、KV Cache 和计算代价。这时讲的是框架共通的生成过程；具体请求如何排队、分配缓存和调用执行器，留到[推理系统与优化](/learn#part-systems)中的框架解剖。

**系统入门**沿主线读到推理基础，再进入系统卷；**算子与性能**在理解模型结构和推理代价后，重点读算子卷；**问题驱动**则从显存、吞吐、延迟或精度问题进入实践，按需回查相关机制。

## 内容与交流

[《知识库核心思路》](/learn/inference/knowledge-base-method)说明学习篇、专题笔记和实验记录如何关联。内容会持续修订，欢迎反馈错误或交流推理优化。

- 网站仓库：[csoulnd/daimonkey](https://github.com/csoulnd/daimonkey)
- 知识库：[csoulnd/InferTechNote](https://github.com/csoulnd/InferTechNote)
- 邮箱：[daidaicurry@foxmail.com](mailto:daidaicurry@foxmail.com)

## 参考与修订

架构分层参考[昇腾软硬件平台](https://www.hiascend.com/)，框架职责参考[vLLM 架构说明](https://docs.vllm.ai/en/v0.32.0/design/arch_overview/)，按本教程的推理范围重新组织。

| 日期 | 版本 | 改动 |
|---|---|---|
| 2026-10-23 | v0.1 | 初稿：五卷结构、读法、写作约定 |
| 2026-10-24 | v0.2–v0.6 | 重排七卷，补学习路线，将写作约定移入《知识库核心思路》 |
| 2026-10-24 | v0.7 | 增加静态软硬件全景；数学与计算基础独立成卷，模型结构前置；三条路线改为文字 |
