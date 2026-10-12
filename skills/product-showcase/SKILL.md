---
name: product-showcase
description: 把 InferTechNote 产品调研文档接成报告厅展示的重复落地规则。适用场景：为调研产品新增/更新展示页（报告厅 showcase 类型）、调整架构图或章节映射、巡检展示与源文档的同步状态。
---

# 产品调研展示（product-showcase）

首个落地：`reports/agentcore-platforms/`（Amazon 首版，2026-10-12）。
本技能记录四个可复制规则——**文档映射、图示表达、来源保留、更新同步**。

## 职责边界（先记住）

- **InferTechNote = 研究内容唯一来源**；本代码仓只做呈现。展示侧**不修改**源文档。
- 内容在**构建时导入**（`collector/showcase.py` 读 `data/knowledge-repo/`），
  不在网站侧另写文案；页面不是独立调研，不补充未经原文证实的结论。
- 发现内容问题 → 记录到 `reports/{slug}/content-issues.md`（文件位置/问题/建议），
  交回研究侧；修订后经知识库同步 + 建站自动反映。
- 可基于源文档做**网络检索**核实，但新证据必须回到研究侧落文档后才进展示。

## 1. 文档映射（showcase.yaml）

- 报告目录 `reports/{slug}/` 含 `showcase.yaml` 即被识别为「展示」类型（无需 `file:`）。
- `products:` 列出全部产品，`status: ready|pending`；展示当前只渲染第一个 ready 的，
  pending 在产品胶囊区显示"待接入"（分阶段接入的可视化，见 stage_note）。
- `sections:` 按**标题文本**匹配源文档（`##` 或 `###`，引号/空格差异自动归一化）：
  - `mode: steps` → 有序列表抽为步骤切换器（联动图高亮）；
  - `mode: diagram` → 交互图代替原文 Mermaid（原文入口指向知识库）；
  - `mode: verify` → 待验证事项的独立样式区；
  - `auxiliary: true` → 辅助章节（定制/托管/费用），视觉降级、不抢主线；
  - 每个章节自动挂「知识库原文（带锚点）+ GitHub 版本固定链接」。
- **文档改标题时**：构建日志输出警告，页面该章节显示"文档已变更"提示——这是特性，
  不要吞掉；按警告更新 yaml 的 `heading` 即可。
- 组件说明 `desc_key` 引用源文档组件表行（构建时提取，单一来源）；表里没有的
  视图形节点用 `desc_note`（研究视图定位注释，页面会标注"非文档原文结论"）。

## 2. 图示表达（diagram.svg + 交互）

- 交互图是**呈现层研究视图**：必须与文档研究视图（Mermaid）语义一致；图注明确
  "依据公开职责重构，不表示厂商内部结构"。文档改动图结构时同步改 SVG（同步规则 4）。
- SVG 约定：节点 `<g class="sc-node" data-node="id">`，容器用 `sc-container`，
  治理类旁挂 `sc-side`；颜色只用 CSS 变量（随明暗主题）；不写内联色值。
- 交互：滚轮/按钮缩放、拖拽平移、点击/回车 → 组件说明面板、步骤切换联动
  `step_highlights` 高亮、图与整页两级的全屏模式。**交互服务于理解，不加装饰动画**。
- 长文本节点用 `<tspan>` 或拆行；中文 13.5px、副文案 11.5px（与站点令牌一致）。

## 3. 来源保留（不得省略）

- 证据基线（日期、复核记录、未实测声明）必须出现在页头，来自 `showcase.yaml →
  baseline`，措辞沿用文档。
- 章节内引用链接按规则重写：库内文档 → `/knowledge/notes/...`；库外路径（如
  `.agents/`）→ GitHub **版本固定**链接（commit 取自构建时的知识库快照）；
  外部来源原样保留。
- 页脚固定提供：摘要/详细文档（知识库）、GitHub 原文（固定 commit）、产品对照。
- 研究判断样式化区分（段首整段 `mark-judgment`；段中 `mark-judgment-inline`），
  待验证事项区独立边框——**样式只标注，不改文案**。

## 4. 更新同步（构建期闭环）

- 数据流：研究侧改文档 → push → 知识库「⟳ 同步知识库」按钮/`sync_knowledge.py`
  → 建站 → 展示页自动导入新内容。无需人工改展示。
- 页头显示**内容指纹**（源文档哈希）与快照 commit，可快速核对"页面 vs 文档"是否同版。
- 巡检清单（定期或接到报告时）：
  1. 构建日志有无 `[reports]` 展示警告（缺章节/缺组件表行/缺 SVG）；
  2. 指纹是否随最近一次知识库同步变化（未变=未同步或文档未改）；
  3. 源文档章节结构是否变动（新增子节想纳入 → 更新 yaml sections）；
  4. `content-issues.md` 的条目是否已被研究侧修订（修订后从文件移除）。

## 新增下一个产品（如阿里云）的步骤

1. 阅读该产品 summary/detail，确定主线章节（定位 → 架构 → 执行路径 → 设计取舍
   → 辅助 → 研究状态），据此写 `sections`；保留"事实/判断/待验证"的原文分层；
2. 按文档 Mermaid 研究视图重画 `diagram.svg`，组件表行键填 `desc_key`；
3. 同一 `showcase.yaml` 的 `products` 中把该产品提为 `status: ready`（或按对比
   结构拆两个 section 组）；对比章节用 `comparison.md` 作为 `detail_doc` 来源；
4. 走一遍巡检清单后提交；内容问题记入 `content-issues.md`。

## 参考

- 引擎：`collector/showcase.py`；集成：`collector/reports.py`（kind=showcase）
- 模板/交互：`templates/report_showcase.html`、`static/assets/showcase.js`
- 测试：`tests/test_showcase.py`（10 例）；示例：`reports/agentcore-platforms/`
