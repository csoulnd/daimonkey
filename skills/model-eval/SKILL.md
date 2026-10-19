---
name: model-eval
description: 模型评分榜单专家。适用场景:模型 benchmark 榜单的数据源接入、评分设计、模型官方链接维护、Podium/AA Index/LMArena/SuperCLUE 聚合指标引入。
---

# 模型评分榜单专家(model-eval)

## 需求方对齐结论(2026-10-17)

- **排名方式**:API 模型与开源模型**一起排**(一个综合榜);开源模型**另有一个分榜单**(同榜过滤视图,不重复计分)。
- **重点**:benchmark 评分排名本身(模型评测),**不关注**组合代理与推理加速。
- **模型条目**:链接到各模型官方网站/ModelScope/Hugging Face(本机 HF 断连,链接照常展示)。
- **聚合指标四件套**:Podium Score(综合能力)/ AA Index(数学·编程·推理)/ LMArena Elo(真实用户体验)/ SuperCLUE(中文权威)。
- **评分维度**参考模型论文常用基准(MMLU-Pro、GPQA、AIME/MATH、LiveCodeBench/SWE-bench、IFEval 等)。
- 参考站点 feelmeu.github.io/github-trending-tracker(需求方认为观感一般,但其模型榜四源与本站新闻源可参考)。

## 数据源通道实测(2026-10-17,本机)

| 源 | 可达性 | 结论/动作 |
|---|---|---|
| Artificial Analysis(Podium/AA Index/大部分 benchmark) | 站点通但 SPA+慢(~12s);**官方 API 需免费 key** | **主数据源**;需求方注册 key → 存 `data/model_eval.env`(`AA_API_KEY=`),源模块按 API 实测响应微调字段映射 |
| SuperCLUE | 通(0.4s)但深度 SPA,接口藏于 chunk(`/api/chores/` 404) | 备选:①有浏览器环境时用开发者工具网络面板找真实接口;②人工录入月报分数+报告链接溯源 |
| LMArena Elo | 直连/HF/raw.githubusercontent **全断** | 需代理或镜像通道;列先预留显示 — |
| Code Arena(参考站第 4 源) | 断连 | 暂不接 |
| ModelScope | 通(0.2s) | 开源模型官方链接优先 ModelScope |
| **OpenRouter Rankings**(openrouter.ai/rankings) | 通(3s) | **真实使用量榜(调用量份额/应用排名)**——2026-10-18 探测新增;FM 方向口径=使用量定范围+质量定排名,数据端点待 spike |
| LiveBench / OpenCompass | 通 | 二期备选基准源 |

## 已落地(2026-10-17,v1.6)

- SuperCLUE 链路定案并上线:**PDF 月报(18MB,raw 归档溯源)→ pypdf 抽总榜大图 → 纵切上下两半(12% 重叠)→ pi(deepseek-flash)读图合并输出严格 JSON → 完整性校验(≥26 条且含 GPT/Claude/Gemini,不足重试一次并按模型名并集合并)→ registry 精确匹配**;结果缓存 `data/cache/superclue_<报告期>.json` 月更不重复读。
- registry:20 个模型(9 开源权重)、`boards.model`(overlay 不并入 Agent 联合榜)、指标 sc_total+六维;页面 `/rankings/model` = 总榜一起排 + 开源分榜 + ModelScope/官网链接;AI 复评走 v1.5 范式。
- 月更操作:改 `sources.superclue.base_url` → `scripts/fetch_superclue.py`。
- 遗留:AA Index/Podium(等 AA key)、LMArena(等网络通道)。

## 原蓝图(AA 主源,待 key)

1. `registry.yaml → models`:模型清单(name/org/官方链接/modelscope 链接/开源标记/所属厂牌),不占 agent products。
2. `collector/sources/model_eval.py`:AA API 模式(有 key 全量拉指标,逐指标带 raw_payload 溯源)+ manual 模式(registry 录入值+来源 URL,SuperCLUE 月报人工摘录)。
3. 榜:board `model`(全量一起排)+ `model_open`(sitegen 过滤开源,同榜视图,复用 v1.5「算法分位归一+AI 复评 70/30+前10+AI 总结」范式)。
4. 页面 `/rankings/model`:模型名链官网/ModelScope;列=综合/算法/AI 复评 + 四聚合 + 精选 benchmark;开源 badge。
5. 方法论升 v1.6(模型榜口径),首页导航加「模型榜单」入口。

## 排障

| 症状 | 处置 |
|---|---|
| AA API 401/字段对不上 | key 与端点以 artificialanalysis.ai API 文档为准,先 curl 单模型验证再入库 |
| SuperCLUE 接口找不到 | 浏览器环境抓网络面板;或走人工月报录入(manual 模式) |
| 模型榜数据过期 | 指标带 collected_at,榜单页展示数据截至时间,同 agent 榜更新链路 |
