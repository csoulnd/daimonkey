---
name: directions
description: 三方向(Harness/FM/NV)频道化专家。适用场景:方向频道页、registry directions 配置、门户入口、Harness 收录分层(core/eco)、方向周总结、NV 频道与 MLPerf 榜。
---

# 三方向频道专家(directions)

## 职责边界

- 主责：`registry.yaml → directions`、`/dir/{方向}` 频道页、门户三方向入口、Harness tier 分层、方向周总结。
- 协作：模型榜单/AA 接入细节归 `model-eval`；周报 AI 工作流细节归 `news-digest`；模板样式归 `ui-polish`；采集事务与排障归 `data-update`。
- 需求蓝本：`docs/DESIGN-三方向.md`（1.4 默认决策表是唯一默认值来源）。

## 约束

1. **组织形式不硬编码**：方向、顺序、图标、关联榜单、新闻过滤、占位说明全部来自 `registry.directions`；模板只遍历配置。
2. 频道页固定三区：新闻流、榜单卡、每周总结；NV 允许 `placeholder: true` + 规划数据源清单。
3. Harness 产品必须带 `tier: core|eco`；核心榜只收能自主干活的产品，协议/框架/编排工具进生态榜，两榜不混排。
4. 旧路由 `/rankings/unified` 301 到 `/dir/harness`；全站 `/news` 与全站周报保留。
5. 改模板/静态资源后必须 `--build`；serve.py 改后 `systemctl restart daimonkey`。

## 已落地实现(2026-10-19)

- **M1 频道化**：`registry.directions` 是唯一组织来源（顺序/名称/图标/boards/news_tag/news_categories/placeholder/planned_sources）；sitegen 遍历配置生成 `/dir/{key}.html`，模板不出现方向名硬编码。旧 `/rankings/unified`、`/rankings/coding|general|claw` 301 到 `/dir/harness`，`/rankings/framework` 301 到 `/rankings/harness_eco`。
- **M2 Harness 分层**：产品 `tier: core|eco`；`harness_core` 为 `derived: true`，`derived_from: [coding, general, claw]`、`tier: core`，由评分层做二层分位；`harness_eco` 用 `select: {tier: eco}` 独立评分并 `folded: true`。分类源榜 `hidden: true`，不单独导航。联合榜已并入核心榜，旧 API `unified.json` 为只读别名。频道页「收录说明」含三问与迁移说明，内容全部来自 `direction.inclusion`。
- **M4 方向周总结**：`queries.news_digests(..., categories)`、`published_digest_summary(..., direction)` 参数化；`workflows._digest_task/_digest_store` 支持 direction；`generate_direction_digests()` 由 `run_build` 在主事务外调用，同方向同周已有总结则跳过，AI 失败只记 notes。频道页每周总结区展示已发布内容、该期周报链接与历期入口；方向周报页 `/dir/{key}/digest/{week}.html` 与全站 `/news/digest/{week}.html` 共用模板但互不串稿。
- **周次选择**：不指定 week 时取该方向“最近有新闻的 ISO 周”；数据滞后时页面标注“本周总结待更新”，不生成空周内容。
- **M5 FM 口径**：需求方决定 FM 榜以 AA Intelligence Index 为唯一排名口径，SuperCLUE 停用。`boards.model.select={origin: aa, status: active}`，AA 源自动同步同系列去重后的 Top 40 模型；旧 SuperCLUE 模型产品归档，页面保留历史指标并标注“仅存档回查”。详见 `skills/model-eval`。
- **M6 NV 接入**：`sources.mlperf` 取官方仓库 `summary.json`（v6.1），只收 closed/datacenter/Offline 的 LLM 结果，按 `Result/total_accelerators` 折算单卡 Tokens/s；`boards.nv_infer` 以 gpt-oss-120b 为主指标、DeepSeek-R1/Llama3.1-8B 为展示列；`directions.nv` 去掉占位、接入 `infra` 新闻；加速卡自动入池（origin=mlperf），掉榜归档。详见 `skills/nv-hardware`。

## runbook

```bash
cd <仓库根目录>
.venv/bin/python -m unittest discover -s tests
node tests/test_knowledge_search.cjs && node tests/test_knowledge_lazy.cjs
.venv/bin/python scripts/verify.py
.venv/bin/python -m collector --build          # 会自动补生成缺失的方向周总结
systemctl restart daimonkey                    # 仅当 serve.py 改动

# 方向周总结人工维护（AI 生成、人工可改）
.venv/bin/python scripts/digestctl.py list --direction harness
.venv/bin/python scripts/digestctl.py edit <id> --file /tmp/summary.txt --publish
.venv/bin/python scripts/digestctl.py generate --direction harness --force
```

## 排障

| 症状 | 处置 |
|---|---|
| 频道页板块为空 | 检查 directions.boards 的 board key 是否有 rankings 快照；NV 应有 nv_infer 当日快照，为空先查 MLPerf 源运行 notes |
| 方向新闻为空 | 检查 directions.news_categories 是否覆盖 news_items.category |
| 周总结不出现 | 检查 insights：`tags` 含 `direction:<key>`、`published_at` 为 ISO 周、`status=published`；再跑 `--build` |
| 周总结不生成 | 看 `--build` 返回 notes：无新闻范围/该周无新闻/已存在都会跳过；AI 失败会记 `[dir:<key>] 周总结跳过` |
| 周总结改完页面没变 | 修改的是 insights，需重新 `--build` 生成静态页 |
| 旧链接 404 | `/rankings/unified`、分类源榜应由 serve.py 返回 301；其余旧榜页按需加映射 |
| 核心/生态串榜 | 检查产品 `tier`、`boards.harness_eco.select`、`harness_core.derived_from`；重跑 `scoring.compute_all` |

## 决策记录

- 2026-10-19(M5)：需求方明确 FM 榜以 AA 总评分为准、放弃 SuperCLUE。模型池由 AA 自动同步，不再依赖 registry 手写模型名单；旧模型归档但保留页面，避免外链 404。
- 2026-10-19(M4)：方向总结采用“最近有新闻周”回退，不编造空周；方向总结默认生成即发布（配置 `assistant.ai.direction_digests.publish` 可改为草稿），全站周报保持草稿→发布。人工修改走 `scripts/digestctl.py`，不引入后台编辑器。
- 2026-10-19(M2)：按 DESIGN 1.4 默认 B 实施核心/生态分层；`harness_core` 继续使用原联合榜的二层分位算法，历史联合榜数据平滑过渡为只读别名；Browser Use 判定为框架（eco），其余 general/claw 消费级产品为 core。
- 2026-10-19(M1)：建立专家骨架；方向配置驱动频道页，NV 用 `placeholder + planned_sources` 表达“空页起步”。
- 2026-10-21(M6)：NV 从占位页升级为可用频道。`directions.nv` 挂 `nv_infer` 榜并接入 `infra` 新闻分类，占位标记移除；榜单成员、指标与排序全部来自 `sources.mlperf` 自动同步，模板仍不硬编码方向名。NV 数据源细节归 `skills/nv-hardware`。
