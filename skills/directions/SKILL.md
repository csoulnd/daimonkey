---
name: directions
description: 三方向(Harness/FM/NV)频道化专家。适用场景:方向频道页、registry directions 配置、门户入口、Harness 收录分层(core/eco)、方向周总结、NV 占位页。
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

## runbook

```bash
cd <仓库根目录>
.venv/bin/python -m unittest discover -s tests
.venv/bin/python scripts/verify.py
.venv/bin/python -m collector --build
systemctl restart daimonkey   # 仅当 serve.py 改动
```

## 排障

| 症状 | 处置 |
|---|---|
| 频道页板块为空 | 检查 directions.boards 的 board key 是否有 rankings 快照；NV 属预期空态 |
| 方向新闻为空 | 检查 directions.news_categories 是否覆盖 news_items.category |
| 周总结不出现 | 检查 insights：`tags` 含 `direction:<key>`、`published_at` 为 ISO 周、`status=published` |
| 旧链接 404 | `/rankings/unified` 应由 serve.py 返回 301；其余旧榜页按需加映射 |

## 决策记录

- 2026-10-19：建立专家骨架；按 DESIGN 1.4 默认 B（核心榜+生态榜）实现 Harness 分层，方向配置驱动频道页。
