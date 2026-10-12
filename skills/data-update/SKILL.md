---
name: data-update
description: 本站数据更新操作与排障。适用场景：热度/新闻数据过期需要更新、更新按钮不工作、更新失败排查、查看更新状态与日志。
---

# 数据更新（data-update）

## 更新方式（四选一）

1. **网页按钮**（推荐）：任意页面右上角「⟳ 更新」（全量）、新闻页「⟳ 仅更新新闻」（轻量，约 1 分钟）、知识库页「⟳ 同步知识库」（从 GitHub 同步代码仓，约 10~60 秒）。
   - 权限：已登录智能助手（Cookie 7 天有效）直接执行；未登录点按钮弹密码框，输入一次即建立会话，本浏览器后续免密。
2. **CLI**（服务器上）：`cd /home/agent-heat-index && .venv/bin/python -m collector --update`（或 `--collect-news` 仅新闻；`scripts/sync_knowledge.py` 仅知识库）。
3. **API**：`POST /api/v1/update`（或 `/update/news`、`/update/knowledge`），带助手 Cookie 或 `X-Update-Password` 头；状态查询 `GET /api/v1/update/status`（公开，含 `kind` 字段区分 full/news/knowledge）。
4. 三种更新互斥：任一进行中，其他触发返回 409。

## 更新链路

`--update` = 采集（GitHub/npm/PyPI/App Store/VS Code，限频退避）→ 评分（分位归一化）→ LRU 清理 → 导出 JSON → 建站（dist/）。
采集通常 1-5 分钟；进行中再触发会返回 409。更新完成后页面自动刷新。

## 排障

| 症状 | 查哪里 |
|---|---|
| 按钮点了没反应 | `data/logs/serve.log`（serve 是否存活：`pgrep -af scripts/serve`）；浏览器控制台网络错误 |
| 更新失败/partial | `data/logs/update-*.log`（每次更新一个文件）；`data/update_status.json` 的 `state/error` |
| 知识库同步超时/失败 | 日志见 git 命令；确认 remote 是 SSH（`git -C data/knowledge-repo remote -v`，HTTPS 在本机被阻断，脚本会自动迁移） |
| GitHub 限流 | 日志中 403/429；配置 `GITHUB_TOKEN` 环境变量解除 60 次/时限制 |
| 密码错误 | 校验文件 `data/update_password.sha256`（600 权限）；改密码=重写该文件的 sha256 值 |
| 数据仍显示过期 | 全量更新后需等构建完成（状态变 success）才刷新；缓存策略已设 no-cache |

## 原则

- **无自动调度**是刻意设计（需求方 2026-09-23 指示），不要自作主张加 cron。
- 超过 48 小时未更新，页面自动挂横幅提醒。
- LRU 保留窗口在 `collector/registry.yaml → retention` 调整，`0` = 永久。
