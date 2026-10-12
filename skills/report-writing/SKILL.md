---
name: report-writing
description: 报告厅报告撰写技能（骨架，待完善）。基于主题与材料撰写汇报报告，产出网页（Markdown/HTML）或 PPT 形态并发布到 /reports。适用场景：用户提出"写一份关于 X 的报告/汇报"。
---

# 报告撰写（report-writing）

> **状态：骨架。** 流程与模板先立框架，后续逐步完善（见文末 Roadmap）。
> 报告发布的工程细节见 `reports/README.md`；本文件只管"怎么写"。

## 输入

| 输入 | 必填 | 说明 |
|---|---|---|
| 主题 | ✅ | 报告要回答的问题 / 汇报的主题 |
| 材料 | 选填 | 知识库笔记（`data/knowledge-repo/` 本地文件或 /knowledge 链接）、站内榜单/新闻数据、外部链接、粘贴文本 |
| 目标形态 | 选填 | `web`（Markdown 或 HTML）或 `ppt`，默认 web |
| 受众与场合 | 选填 | 决定详略、语气与篇幅 |
| 篇幅/时长 | 选填 | 网页报告页数与 PPT 页数的指引 |

**材料的主要来源是知识库**（`/knowledge`），它是报告的原材料而非报告本身；
引用素材请登记到 `reports/registry.yaml` 的 `materials:` 字段。

## 流程（占位，待完善）

1. **消化材料**：通读指定材料，提取事实、数据、可引用的观点；（TODO：材料清单模板）
2. **定提纲**：结论先行，列出 3-5 个支撑论点及其证据来源；（TODO：提纲模板）
3. **成稿**：按提纲撰写；数字必带来源，观点与事实分离；
4. **审校**：事实核对、逻辑连贯、篇幅检查；（TODO：审校 checklist）
5. **出品与发布**：
   - web（Markdown）：`reports/{slug}/report.md`；
   - web（HTML）：`reports/{slug}/index.html`（原样网页，可自带样式）；
   - ppt：`reports/{slug}/deck.pptx`；
   - 在 `reports/registry.yaml` 登记（slug/title/date/file/summary/tags/materials）；
   - 运行一次建站发布（任意更新按钮或 `--build`）。

## 写作规范（占位）

- 所有数字必须可溯源（知识库原文/站内 API/外部链接）；
- 结论先行，一页一个要点；
- 术语首次出现给出解释；受众为非技术背景时避免未解释的缩写。

## 与 DSH 工作流的关系（待打通）

规划：新增 `collector/workflows.py` 工作流（如 `report_draft`），输入主题+材料路径，
输出 Markdown 初稿存为草稿，人工确认后放入 `reports/` 发布——沿用"AI 产物草稿态、
人工确认上线"的安全边界。

## Roadmap

- [ ] 材料消化清单模板（读什么、提取什么）
- [ ] 提纲模板（结论先行结构）
- [ ] 审校 checklist（数字溯源/逻辑/篇幅/术语）
- [ ] DSH 工作流 `report_draft`：主题+材料 → 初稿草稿
- [ ] PPT 模板规范（页数节奏、母版要求）
