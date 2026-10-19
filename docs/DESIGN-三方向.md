---
title: "三方向(Harness/FM/NV)需求分析与设计"
type: design
status: active
---

# 三方向需求分析与设计

> 需求来源:需求方 2026-10-17 三轮口述(见 ROADMAP-三方向.md 澄清记录)。
> 未决项以【默认】标注=按此实现,需求方可随时否决翻转。

## 实施状态(2026-10-19)

| 里程碑 | 状态 | 提交 | 验收证据 |
|---|---|---|---|
| M1 频道化骨架 | ✅ 已上线 | `80c524f` | `/dir/{harness,fm,nv}` 三区页、门户三入口、NV 空页、旧路由 301；137 tests，verify 58/58 |
| M2 Harness 甄别重构 | ✅ 已上线 | `557d79c` | 产品 `tier: core|eco`；`harness_core`/`harness_eco` 不混排；联合榜并入核心榜；收录三问上频道页；144 tests，verify 69/69 |
| M3 AA 接入 | ✅ 已上线 | `d031ed4` | AA 免费 API 4 页 704 模型、逐页 raw 溯源、7 天缓存；153 tests，verify 74/74 |
| M4 方向周总结 | ✅ 已上线 | `cbc3955` | W42 harness/fm 周总结已发布；方向周报页 `4` 期/方向；`digestctl` 人工编辑；167 tests，verify 83/83 |
| M5 FM 切换 AA 总评分口径 | ✅ 已上线 | 本次提交 | 需求方明确“按照 AA 为准，放弃 SuperCLUE，模型太老了”；模型榜 Top 40 自动入池、按 Intelligence Index 排序；SuperCLUE 停用；168 tests，verify 83/83 |

实施补充与偏差:

- **AA 主口径（2026-10-19 需求方决定）**：FM 榜以 AA Intelligence Index 总评分作为唯一排名口径；模型池由 AA 免费 API 自动同步（同系列去重取最高分版本，前 40），不再依赖 registry 手写名单。SuperCLUE 源停用，模型榜不再展示 sc_* 列；旧 SuperCLUE 模型产品归档，页面保留历史指标并标注“仅存档回查”。
- **AA 免费 API 字段**：设计 2.3 预设的 MMLU-Pro/GPQA/MATH/LiveCodeBench 不在免费接口中；榜单主线使用 AA Intelligence Index，编码/智能体指数作展示列，另存六类域指数。所有数值逐格带 `aa` 来源与 raw 溯源。
- **产品同步字段**：products 新增 `origin`（registry/aa）与 `aa_slug`；模型榜用 `select: {origin: aa, status: active}` 选品，掉出 Top 40 的 AA 模型自动归档。
- **方向新闻**：`directions.news_tag` 作为周总结分组标识；实际新闻过滤使用新增的 `news_categories` 列表（一个方向可覆盖多个分类，如 harness=coding+framework）。
- **核心榜派生**：`harness_core` 由隐藏分类源榜 `coding/general/claw` 派生，只取 `tier=core` 的主分类条目；`harness_eco` 按 `tier=eco` 独立评分。旧 `/api/v1/rankings/unified.json` 保留只读兼容别名。
- **周总结周次**：自动生成取“该方向最近有新闻的 ISO 周”；当新闻数据滞后、当前周无条目时，频道展示最近一期并标注“本周总结待更新”，不伪造当周内容。
- **人工可改**：`scripts/digestctl.py list/edit/publish/generate`；方向总结默认生成即发布（`assistant.ai.direction_digests.publish`），全站周报仍保持 AI 草稿→人工发布。

## 一、需求分析

### 1.1 用户与场景
- 读者(匿名):快速看懂 AI 三个层面"现在什么值得关注";
- 注册用户:在三方向下持续跟踪,并在个人工作台沉淀自己的内容;
- 需求方(产品):只看产品表现,不关心方法论细节;**榜单可信度=生命线**。

### 1.2 功能需求(按方向)

| 方向 | 需求 | 来源与状态 |
|---|---|---|
| Harness | 收录对象**严格甄别**(产品维度;协议/编排工具不得混入);新闻+榜单+每周总结 | 已确认问题,口径待定(1.4-D) |
| FM | 以 Artificial Analysis 为**唯一排名口径**,取数并标注来源;新闻+榜单+每周总结 | 已确认;2026-10-19 明确放弃 SuperCLUE |
| NV | **空页起步**,逐步添加;新闻+榜单+每周总结 | 已确认 |
| 横切 | 三方向统一组织形式**不写死**,频道化、配置驱动;门户聚合 | 已确认 |

### 1.3 非功能需求
- 可信:每个数字可溯源;AI 参与环节有标注;收录有标准可查;
- 可演进:方向/板块/页面结构由配置定义,调整组织形式不改代码骨架;
- 工程约束:静态站+serve.py 现架构不动;新数据源遵守限频退避。

### 1.4 关键决策(未决项给默认)

| # | 决策 | 选项 | 【默认】与理由 |
|---|---|---|---|
| D-口径 | Harness 榜收录标准 | A 严口径 / B 核心榜+生态榜分层 / C 分组不总排 | **B**——保信息量又不失可信:核心榜只收"能自主干活的产品",协议/框架/工具入生态榜平行展示,互不混排 |
| D-联合榜 | 现有联合榜去留 | 保留 / 并入 Harness 频道 / 下线 | **并入**:新口径下它就是 Harness 核心榜前身,历史数据平滑迁移 |
| D-AA列 | AA 取数范围 | 指数/基准分/价格/吞吐延迟 | **先指数+基准分**(榜单主线),价格与吞吐二期 |
| D-FM口径 | FM 榜排名依据 | AA 总评分 / SuperCLUE / 双口径并列 | **AA 总评分**(2026-10-19 需求方决定):放弃 SuperCLUE,模型池自动更新,避免榜单模型过旧 |
| D-架构 | 门户信息架构 | 三方向频道入口 / 维持现状 | **频道化**:门户四卡改为三方向入口+资讯;现导航"热度榜单"过渡为 Harness 频道 |
| D-周报 | 每周总结生成 | AI 生成 / 人工 / 混合 | **AI 生成+人工可改**:复用现周报与 AI 摘要管线,按方向分组 |
| D-NV页 | 空页内容 | 仅说明 / 说明+规划数据源 | **说明+规划中的数据源清单** |

## 二、设计

### 2.1 信息架构(配置驱动,不写死)

```
registry.yaml 新增:
directions:                       # 方向=频道,顺序即门户入口顺序
  harness: {name: 智能体, icon: i-index, boards: [harness_core, harness_eco], news_tag: harness}
  fm:      {name: 模型, icon: i-method, boards: [model], news_tag: fm}
  nv:      {name: 硬件, icon: ?,      boards: [], news_tag: nv, placeholder: true}
```
- 页面:`/dir/{方向}` 频道页(新闻流+榜单卡+每周总结三区);门户入口由 directions 顺序生成;
- 增删方向/换组织=改 registry,模板只认配置(**组织形式探索期的硬约束**);
- 旧路由 301:热度榜→harness 频道,模型榜保留。

### 2.2 榜单可信度机制(Harness 重构核心)

1. **收录标准(写进 registry 注释与频道页"收录说明")**:
   核心榜三问——面向用户可用?具备自主执行/多步编排?有可采的公开使用数据?
   协议/库/编排工具 → 生态榜(同数据管线,平行展示,不混排)。
2. **registry 产品清单加字段**:`tier: core|eco`,入榜需在 PR/变更说明中对照三问;
3. **迁移**:现 coding→core;framework 全部+MCP 等→eco;general/claw 按产品逐个判;
4. 展示:频道页核心榜为主表,生态榜折叠区。

### 2.3 FM 数据接入(AA,唯一口径)

- 适配层 `collector/sources/aa.py`:官方免费 API(AA_API_KEY,data/model_eval.env);
  逐页 raw 归档+页面"来源:Artificial Analysis"标注;7 天缓存控制免费额度。
- 模型池:AA 同系列去重(去掉 Max/High/Low、Reasoning、Fallback 等变体)后按
  Intelligence Index 取前 40,自动写入 products(origin=aa),掉出榜单的自动归档。
- 排名:**AA Intelligence Index 总评分**为 heat;AA Coding/Agentic 指数作展示列;
  免费 API 不含 MMLU-Pro/GPQA/MATH/LiveCodeBench,不虚构这些列。
- SuperCLUE:2026-10-19 需求方决定停用;历史 raw 与 sc_* 指标保留,旧模型页面
  标注"仅存档回查",不再参与模型榜。

### 2.4 每周总结(三方向复用)

- 现周报管线参数化:按 `news_tag` 过滤各方向条目 → 各频道周总结(AI 生成,insights 表
  加 direction 维度);门户保留全站周报。

### 2.5 NV 空页

- `/dir/nv`:方向说明+规划数据源清单(MLPerf/官方规格/云定价,标注"接入中");
  三件套骨架就位,板块空态提示。

### 2.6 里程碑(建议)

M1 频道化骨架+方向配置(含 NV 空页)→ M2 Harness 甄别重构与迁移 → M3 AA 接入
→ M4 方向周总结 → M5 FM 切换 AA 总评分口径。每步独立可上线。
