---
name: news-digest
description: 本站新闻汇总与周报。适用场景：调整新闻源、分类规则、周报生成、AI 摘要工作流、新闻数据问题排查。
---

# 新闻汇总（news-digest）

## 功能位置

- 页面 `/news`（最近条目 + 分类筛选 + 产品联动徽章）；全站周报 `/news/digest/{年}-W{周}.html`；
- 三方向周报：`/dir/{harness,fm}/digest/{年}-W{周}.html`，与全站周报共用模板、按 `directions.news_categories` 过滤；
- 频道页每周总结区展示已发布的方向总结（`insights.tags` 含 `direction:<news_tag>`）；
- 数据源与规则全部在 `collector/registry.yaml → news`：源清单（`sources`）、分类关键词（`keyword_rules`）、HN 查询词、`lookback_days`、`digest_weeks`。

## 确定性管线（无 LLM，可复现——这是核心原则）

抓取 RSS/API → URL 规范化（剥 utm/fragment）+ sha256 去重 → 关键词规则分类（先匹配先得 + 源级默认兜底）→ 产品名词边界匹配挂徽章 → 按 ISO 周分组生成周报。
每条新闻带 `raw_payload_id` 溯源链接；摘要只存标题+来源+280 字截断。

## AI 摘要（智能助手内部工作流，可选增强）

- **三板块 AI 摘要（v1.4）**：首页/新闻/知识库顶部卡片，随 `--build`（含全部更新链路）按数据指纹自动生成，实现见 `collector/ai.py → refresh_summaries_if_stale`，落 `ai_summaries` 表；失败保留旧摘要并在 notes 记原因。
- **榜单 AI 分（v1.4）**：全量更新后 `ai.score_boards` 逐榜调用 `board_ai_analysis`（严格 JSON、解析失败重试一次），落 `ai_scores` 表，**与规则分平行、默认不影响排序**；开关 `registry.yaml → assistant.ai.scoring`。
- **周报 AI 总结段**：周报页展示该周 `insights` 表已发布（`llm_generated=1, status='published'`）的总结；草稿不上线。
- **方向周总结（M4）**：`--build` 时 `workflows.generate_direction_digests()` 按 `directions.news_categories` 过滤本周条目，逐方向调用 `news_digest_summary`；未指定周次时取该方向最近有新闻的 ISO 周（数据滞后不编造空周）。同方向同周已有总结则跳过；AI 失败只记 notes。方向总结默认生成即发布（`assistant.ai.direction_digests.publish`，可改 false 走草稿），标签 `news-digest,ai,direction:<key>`。
- **人工可改**：`.venv/bin/python scripts/digestctl.py list|edit|publish|generate`；编辑后需再 `--build` 才能更新静态页。全站周报仍保持 AI 草稿→人工发布。
- 手动触发全站周报总结：CLI `.venv/bin/python -m collector --assistant news-digest`，或智能助手面板工作流按钮 `news_digest_summary`；
- 依赖：`data/assistant.env` 里的 `DEEPSEEK_API_KEY`（600 权限）；AI 工作流走 internal 通道配额（每日 50 次 / 100 万 token）。
- **降级原则**：AI 步骤任何失败（含意外异常）只记 notes、不阻断采集/建站、不影响更新状态——这是硬约束，新增 AI 工作流必须遵守。

## 常见调整

| 需求 | 改动 |
|---|---|
| 新增/禁用新闻源 | `registry.yaml → news.sources` 的 `enabled`（源失效时先 curl 实测再禁） |
| 调整分类 | `keyword_rules` 关键词列表（确定性规则，改完可复现） |
| 周报保留周数 | `news.digest_weeks` |
| 新闻保留窗口 | `retention.news_days` |
| 方向新闻范围 | `directions.<key>.news_categories`（周报与频道新闻流共用） |
| 方向总结开关/发布态 | `assistant.ai.direction_digests.enabled/publish` |

## 排障

- 某源长期 0 条：先 `curl -sI <rss地址>` 实测可达性（历史上机器之心 RSS 失效、VentureBeat 429 被移除过）；
- 更新日志：`data/logs/update-*.log` 搜 source_key；
- AI 摘要失败：查 `assistant_runs` 表（status/error）与助手面板错误提示；配额用尽会明确报 429。
- 方向总结不出现：查 `insights` 的 tags（`direction:<key>`）、`published_at`、`status`；确认 `news_categories` 有当周条目；改完跑 `--build`。
- 方向周报与全站周报串稿：`published_digest_summary` 已按 direction 隔离；检查 tags 是否混写。
