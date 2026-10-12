# 内容问题记录（交回研究侧处理）

范围：本次展示接入（Amazon 首版 + 全量阅读）时发现的源文档问题。
处理约定：**展示侧不改动源文档**；以下问题请研究侧确认后修订 InferTechNote 仓库，
下次知识库同步 + 建站后本页自动反映。

| # | 文件位置 | 问题 | 建议 |
|---|---|---|---|
| 1 | `agent/investigations/products/alibaba-agentcore/detail.md` 第 177 行（参考来源表前） | 写"全部来源查阅于 2026-10-10"，但文档开头（第 6 行）说明 2026-10-12 复核了 A1；两处日期语义不一致 | 改为"其余来源查阅于 2026-10-10；A1 于 2026-10-12 复核"，或在来源表 A1 行加复核日期 |
| 2 | `agent/investigations/products/alibaba-agentcore/detail.md` 第 94 行 | "不同 Session 使用独立工作目录，同时同 Agent 可用 `shared/` 共享文件"——"同时同 Agent"疑为笔误，语义不清 | 改为"同一 Agent 可用 `shared/` 共享文件"或"同时，同一 Agent 可用…" |
| 3 | `agent/investigations/products/amazon-agentcore/summary.md` 第 26 行（组件表末行） | 行键"Observability / Evaluations 等"含"等"字：作为展示层组件匹配键不稳定（本次接入用模糊匹配兜住，但后续文档改动容易失配） | 表行键保持稳定名词（去掉"等"，具体范围放描述列） |
| 4 | `agent/investigations/products/amazon-agentcore/detail.md` 第 30 行 | "Runtime 的框架中立性不能套用到 Harness 的编排引擎选择上。[B1][B2]"——"框架中立性"属研究判断，但整句以引用官方来源的形式呈现，事实与判断的边界在句内不清晰 | 拆分：官方文档支持的事实（Harness 使用 Strands）保留引用；"不能套用"的判断加"研究判断"标记 |

## 未发现问题的部分（供参考）

- 证据基线、查阅日期、来源表结构与"事实／研究判断／待验证"分层在本次接入的 Amazon 两篇文档中完整可用，展示层直接沿用原文标注。
- `comparison.md` 七项软件职责框架明确声明"不是厂商官方七层"，与 `README.md` 表述一致。
