---
name: model-eval
description: 模型评分榜单专家。适用场景:模型 benchmark 榜单的数据源接入、评分设计、模型官方链接维护、Podium/AA Index/LMArena/SuperCLUE 聚合指标引入。
---

# 模型评分榜单专家(model-eval)

## 当前口径(2026-10-19 起)

- **FM 榜唯一排名口径 = Artificial Analysis Intelligence Index 总评分**。需求方明确“按照 AA 为准，放弃 SuperCLUE，模型太老了”。
- 模型池自动同步：AA 同系列去重（去掉 Max/High/Low、Reasoning、Fallback 等变体）取最高分版本，按总评分前 40 入榜；新模型自动出现，掉出榜单自动归档。
- SuperCLUE 源已停用，模型榜不再有 `sc_*` 列；旧模型产品页面保留历史指标并标注“仅存档回查”。

## 需求方对齐结论(2026-10-17,部分已被上节取代)

- **排名方式**:API 模型与开源模型**一起排**(一个综合榜);开源模型**另有一个分榜单**(同榜过滤视图,不重复计分)。
- **重点**:benchmark 评分排名本身(模型评测),**不关注**组合代理与推理加速。
- **模型条目**:链接到各模型官方网站/ModelScope/Hugging Face(本机 HF 断连,链接照常展示)。
- **聚合指标四件套**:Podium Score(综合能力)/ AA Index(数学·编程·推理)/ LMArena Elo(真实用户体验)/ SuperCLUE(中文权威)。
- **评分维度**参考模型论文常用基准(MMLU-Pro、GPQA、AIME/MATH、LiveCodeBench/SWE-bench、IFEval 等)。
- 参考站点 feelmeu.github.io/github-trending-tracker(需求方认为观感一般,但其模型榜四源与本站新闻源可参考)。

## 数据源通道实测(2026-10-17,本机)

| 源 | 可达性 | 结论/动作 |
|---|---|---|
| Artificial Analysis(Podium/AA Index/大部分 benchmark) | ✅ 已接入(2026-10-19) | **主数据源**;免费 API 分页 200/页×4=704 模型;**必须 httpx**(curl 截断),单页偶发 30~60s,源内超时 75s;key 在 `data/model_eval.env` |
| SuperCLUE | 已停用(2026-10-19) | 需求方决定改用 AA 总评分；历史月报 raw 与 sc_* 指标保留，源开关 `enabled: false` |
| LMArena Elo | 直连/HF/raw.githubusercontent **全断** | 需代理或镜像通道;列先预留显示 — |
| Code Arena(参考站第 4 源) | 断连 | 暂不接 |
| ModelScope | 通(0.2s) | 开源模型官方链接优先 ModelScope |
| **OpenRouter Rankings**(openrouter.ai/rankings) | 通(3s) | **真实使用量榜(调用量份额/应用排名)**——2026-10-18 探测新增;FM 方向口径=使用量定范围+质量定排名,数据端点待 spike |
| LiveBench / OpenCompass | 通 | 二期备选基准源 |

## 历史:SuperCLUE 链路(2026-10-17,2026-10-19 停用)

SuperCLUE 曾以“PDF 月报 → pypdf 抽总榜图 → pi 多模态读榜 → registry 精确匹配”的方式上线，提供 `sc_total` + 六维指标。2026-10-19 需求方决定 FM 榜改用 AA 总评分后，源开关已关闭；代码、raw 归档与历史指标保留，便于回查，但不再参与排名或模型榜展示。

## AA 接入落地(2026-10-19,M3/M5)

- 源模块 `collector/sources/aa.py`;`GET https://artificialanalysis.ai/api/v2/language/models/free` + `x-api-key`。
- 实测:`tier=free`、`intelligence_index_version=4.3`、分页 `total_pages=4`(200/页,约 704~705 模型);返回九类指数 + pricing/performance,**不含 MMLU-Pro/GPQA/MATH/LiveCodeBench**——榜单主线用 Intelligence Index,编码/智能体指数作参考列，不虚构基准。
- 取数策略:每页原始 JSON 逐页 `save_raw` 归档(指标 raw_id 指向所属页);`data/cache/aa_models.json` 7 天有效,有效期内零请求(4 次/周),避免 1000 次免费额度被重复更新耗尽。**强制刷新=删除该缓存文件**。
- 模型池(`select_universe`):按 `family_key` 去掉括号变体与 Max/High/Low、Reasoning、Fallback 等词，同系列取 Intelligence Index 最高版本，再按总评分取前 `sources.aa.top_n`(默认 40)。
- 产品同步(`sync_products`):选中模型写入 `products`，字段 `origin='aa'`、`aa_slug=<AA slug>`、`board_tags=["model"]`、`status='active'`；已有 registry 模型优先复用（按 `aa_slug` 或 slug）；掉出 Top 40 的 AA 模型与旧 SuperCLUE 模型置 `status='archived'`（页面保留）。
- 榜单:`boards.model.select={origin: aa, status: active}`，`heat={aa_intelligence: 1.0}`，`display_metrics=[aa_intelligence, aa_coding, aa_agentic]`；模型页不再展示 SuperCLUE 列/旧开源分榜/官方入口。
- 溯源:每格 AA 数值带 `aa` 来源标签与 `/raw/{id}`；raw 是 AA 免费 API 的整页原始 JSON。
- 验证:`tests/test_aa.py`(解析/去重入池/产品同步/归档/缓存/密钥缺失)；verify `2e` 检查 40 个 AA 入池、heat 口径、来源溯源、SuperCLUE 停用。

## 实现位置

- `collector/sources/aa.py`：取数、去重选池、产品同步、指标产出。
- `collector/registry.yaml`：`sources.aa`（top_n/cache_days）、`sources.superclue.enabled=false`、`boards.model`。
- `collector/db.py` / `pipeline.py`：`products.origin/aa_slug` 附加迁移；采集后按 registry+AA 产品重算榜单。
- `templates/rankings.html`：模型榜 AA 总评分说明，模型页隐藏动量视图。

## 排障

| 症状 | 处置 |
|---|---|
| AA API 401/字段对不上 | 先确认 `data/model_eval.env` 的 AA_API_KEY;调试**必须用 httpx**(curl 会截断),打印响应键而非 key |
| 模型榜没有新模型 | AA 缓存 7 天内不会请求；删除 `data/cache/aa_models.json` 后跑一次全量更新。再查 `products.origin='aa' AND status='active'` 是否有 40 条 |
| 模型榜模型仍旧/过少 | 查 `boards.model.select` 与 `top_n`；确认 AA 源 collect 日志里的“入榜 N 个模型”；旧 SuperCLUE 页面归档属预期 |
| AA 7 天内不更新 | 预期行为(周更缓存);确需刷新时删除 `data/cache/aa_models.json` 再跑更新 |
| 模型页还想看 SuperCLUE | 已按需求方决定停用；历史数据只在旧模型产品页存档，不回榜 |
| 模型榜数据过期 | AA 指标带 collected_at，榜单页显示数据截至时间；缓存刷新后需重跑评分/建站 |

## 决策记录

- 2026-10-19(M5):需求方明确“按照 AA 为准，放弃 SuperCLUE，模型太老了”。FM 榜改为 AA Intelligence Index 唯一排名口径；模型池自动 Top 40（同系列去重），SuperCLUE 源停用、旧模型归档。AA 编码/智能体指数只作参考列。
- 2026-10-19(M3):AA 一期只接免费 API 实际可得的九类指数，不虚构 MMLU/GPQA 等列；免费额度用 7 天缓存保护，周更 4 次请求。
- 2026-10-22(FM 榜列与排序)：排序列以 AA Intelligence Index 为唯一主序，编码/Agent 指数降为参考列，表下标注评判标准与数据截至时间；理由是多指标混排会让名次不可解释，参考列只回答“哪方面强”，不参与排序。
