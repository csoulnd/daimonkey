---
name: data-update
description: 本站数据更新操作与排障。适用场景：热度/新闻数据过期需要更新、更新按钮不工作、更新失败排查、查看更新状态与日志。
---

# 数据更新（data-update）

## 更新方式（四选一）

1. **网页按钮**（推荐）：任意页面右上角「⟳ 更新」（全量）、新闻页「⟳ 仅更新新闻」（轻量，约 1 分钟）、知识库页「⟳ 同步知识库」（从 GitHub 同步代码仓，约 10~60 秒）。
   - 权限：已登录账号（Cookie 7 天有效）直接执行；未登录点按钮弹账号框（用户名+密码），登录一次本浏览器后续免密。账号用 `userctl add <用户名>` 在服务器任意目录创建。
2. **CLI**（服务器上）：`cd <仓库根目录> && .venv/bin/python -m collector --update`（或 `--collect-news` 仅新闻；`scripts/sync_knowledge.py` 仅知识库）。
3. **API**：`POST /api/v1/update`（或 `/update/news`、`/update/knowledge`），需账号登录 Cookie（`X-Update-Password` 头已移除）；状态查询 `GET /api/v1/update/status`（公开，含 `kind` 字段区分 full/news/knowledge）。
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
| 登录失败 | 账号体系：`scripts/userctl.py list` 看账号、`passwd <用户名>` 改密码；仅密码+无账号的过渡模式已随首个账号创建而失效 |
| 数据仍显示过期 | 全量更新后需等构建完成（状态变 success）才刷新；缓存策略已设 no-cache |
| 换机/改目录后溯源页打不开 | `raw_payloads.body_path` 只存相对路径；先跑 `.venv/bin/python scripts/migrate_relpath.py`，再用 `scripts/verify.py` 第 5 节核对文件可解析 |
| 新机更新行为与旧机不同 | 依赖已精确锁定；检查 `pip freeze` 与 `requirements.txt` 是否一致，重跑 `deploy/bootstrap.sh` |
| 更新长时间停在 running（>10 分钟） | 先看进程是否卡在 `read()`：`cat /proc/<pid>/wchan`、`ss -tnp \| grep <pid>`；2026-10-20 曾因 `Http.fetch` 透传 `timeout=None`（httpx 语义=禁用超时）卡在一条不断流的 TCP 上，已修复并加回归用例 `tests/test_sources.py::TestHttpTimeout` |

## 原则

- **无自动调度**是刻意设计（需求方 2026-09-23 指示），不要自作主张加 cron。
- 超过 48 小时未更新，页面自动挂横幅提醒。
- LRU 保留窗口在 `collector/registry.yaml → retention` 调整，`0` = 永久。
- **溯源路径可搬迁**（2026-10-17）：`body_path` 存 `raw/<源>/<日期>/<hash>.json.gz` 相对路径，读取拼 `DATA_DIR`；禁止写绝对路径。换机走 `deploy/backup.sh` + `deploy/bootstrap.sh --restore`，不要手工搬 `data/`。备份排除 `dist/`（可重建）与 `data/cache/`（可再生）。

## 决策记录

- 2026-10-21(原始响应窗口化)：`/api/v1/raw` 只导出 `retention.raw_api_days`（默认 14 天）窗口内的响应体，`raw/index.json` 保留全量清单（id/来源/时间/大小/是否导出），`dist/raw/*.html` 溯源页不删——既控 API 与 dist 体积，又不破坏已发布的外链。
- 2026-10-21(AI 摘要数字对账)：摘要里的统计数字以「板块最新快照」为唯一口径，生成后校验；对不上时降级为模板文案，不让模型给出的数字直接进页面。
- 2026-10-22(AI 摘要口径 v2.1)：摘要统一说「收录 N 个 / 入榜 M 条」，与榜单 KPI 同源；榜单评分 prompt 中「热度」改「公开采纳指标」。摘要 signature 的版本盐改为 `summary-v2.1-adoption-scope`，旧摘要因签名不匹配自然重生成，避免新口径下沿用旧文案。
