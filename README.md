# 智能体热度指数（Agent Heat Index）

汇总第三方智能体（Agent）生态数据、**每个数字都能溯源到原始 HTTP 响应**的热度榜单站。

- **联合榜单**（第一个功能）：跨编码 / 开源框架 / 通用 / Claw 四个分类榜的数据整合榜，产品按"自己赛道内的组内分位分"跨榜排序，每条标注来源榜与覆盖率。
- **AI Agent 新闻汇总**（第三个功能，`/news`）：公开 RSS/API 源的领域新闻，确定性关键词分类（无 LLM）、URL 规范化去重、每条可溯源；提到榜上产品自动关联产品页与当前名次，每周自动生成周报。
- **数据手动更新**：无任何自动调度。前端右上角「⟳ 更新数据」按钮（经本地服务触发）或命令行手动执行；新闻页另有「仅更新新闻」轻量按钮（约 1 分钟，不重采热度数据）。全站显著展示**数据截至时间**，超 48 小时未更新自动挂横幅。
- **历史数据 LRU**：原始响应体 30 天、指标时序 180 天、榜单快照 365 天、新闻条目 180 天、采集缓存 48 小时，到期自动清理（响应体清理后元数据与 SHA-256 永久保留，溯源链不断）。窗口在 `collector/registry.yaml → retention` 调整，`0` = 永久保留。

## 快速开始

## 个人知识库（第二个功能）

与智能体热度指数同级入口：`/knowledge`。以 `csoulnd/InferTechNote` 为唯一内容源，提供目录树、全文搜索、Markdown 文章、图片、Mermaid 图表、本文目录与版本固定的 GitHub 原文链接。仅展示仓库概览、`agent/`、`infra/` 和 `knowledge/`；`templates/` 与隐藏配置不展示，仓库原文件保留；外部子模块不自动导入。

快照位于 `data/knowledge-repo`。手动同步并重建（不采集热度数据）：

```bash
.venv/bin/python scripts/sync_knowledge.py
```

普通 `collector --build` / 热度更新会保留并重建已有知识库快照，不自动拉取 GitHub；服务器无法直连 GitHub 时可传入已核对 commit 的 Git 工作区快照，再执行 `collector --build`。同步失败时保留旧快照。

## AI Agent 新闻汇总（第三个功能）

与热度指数、知识库同级入口：`/news`。配置全部集中在 `registry.yaml → news`（源清单、分类关键词规则、HN 查询词、保留窗口）。

- **数据源（2026-10-09 逐源实测可达）**：OpenAI News、TechCrunch AI、The Verge AI、MIT Tech Review AI、MarkTechPost、Simon Willison、Latent.Space、ArXiv API、量子位、InfoQ 中国 + Hacker News（Algolia API）。HF 博客 / Reddit / blog.google 超时、机器之心 RSS 失效、Anthropic 无 RSS、VentureBeat 429，均未纳入（复测可达后可在 registry 一行启用）。
- **七个领域分类**：模型与能力 / 框架与工具链 / 编码智能体 / 推理与基础设施 / 产品与落地 / 安全与治理 / 研究与论文；国内/海外为筛选维度而非独立分类。分类为 `keyword_rules` 关键词规则（先匹配先得）+ 源级默认分类兜底，无 LLM、可复现。
- **去重**：URL 规范化（剥 `utm_*`/fragment、统一大小写与末尾斜杠）后 sha256 唯一约束；仅入库最近 `lookback_days` 天发布的条目。
- **产品联动（V2）**：新闻标题/摘要按产品名、仓库名与 `news_aliases` 做词边界匹配（ASCII 短词如 `ai`/`go` 自动跳过防误报），命中即挂产品徽章与当前主榜名次，产品页反向展示「相关新闻」。
- **每周周报（V2）**：按 ISO 周自动生成 `/news/digest/{年}-W{周}.html`（本周全部条目 + 被提及最多的产品），随保留窗口滚动。
- **更新方式**：并入全量更新链路（`--update`），或独立轻量入口——新闻页「⟳ 仅更新新闻」按钮（`POST /api/v1/update/news`，同一密码）与 CLI `python -m collector --collect-news`。
- 每条新闻带 `raw_payload_id` 溯源链接；摘要仅存标题+来源+截断 280 字的简介，全文只在原始响应归档中保留。

### 网站性能

- 构建时预生成 HTML / JSON / JS / CSS / SVG 的 gzip 副本，服务按 `Accept-Encoding` 协商；兼容不支持压缩的客户端。
- 页面与 JSON 通过 ETag 校验缓存；带内容指纹的脚本和样式、固定版本的 Mermaid 使用一年不可变缓存。动态更新状态仍为 `no-store`。
- 全文索引保留于服务端 `data/knowledge_search.json`，通过 `GET /api/v1/knowledge/search` 分页查询（默认 40 条）。`/knowledge/search.json` 仅保留轻量文档目录，不再包含正文。
- Mermaid 在图表接近视口时才加载，逐张渲染并保留源码；不支持可见性检测的浏览器可点击加载。
- 验证：`python -m unittest discover -s tests`、`node tests/test_knowledge_search.cjs`、`node tests/test_knowledge_lazy.cjs`（新闻模块见 `tests/test_news.py`）。

### 热度功能启动

```bash
cd /home/agent-heat-index

# 依赖（已装好则跳过）：Python ≥3.11
python3 -m venv .venv
.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 1) 首次：初始化 + 全流程更新（采集→评分→LRU→导出→建站）
.venv/bin/python -m collector --init-db
.venv/bin/python -m collector --update        # 或 bash scripts/update.sh

# 2) 启动本地服务（含更新按钮后端）
.venv/bin/python scripts/serve.py --host 0.0.0.0    # http://<服务器IP>:8080
#   仅本机访问：去掉 --host 参数（默认 127.0.0.1）
```

常用命令：`--collect` 仅采集、`--build` 仅导出+建站、`--init-db` 仅建库。
测试：`.venv/bin/python -m unittest discover -s tests`
验收：`.venv/bin/python scripts/verify.py`（页面/结构/两跳溯源抽查/体积预算，更新后建议跑一次）
环境检查：`bash scripts/check_env.sh`（生成 env_report.md，规范第 11 章要求）

## 访问与令牌

- 浏览（GET）公开：`http://<服务器IP>:8080`。**对外访问需在云控制台安全组放行 TCP 8080**（本机防火墙未启用，仅云安全组拦截）。
- 「更新数据」按钮受密码保护：每次更新输入密码，浏览器不保存密码。服务端校验值存于 `data/update_password.sha256`，文件权限应为 600。
- API 中 `POST /api/v1/update` 与 `POST /api/v1/update/news` 需带 `X-Update-Password` 头；不再接受旧令牌或 URL 参数。`GET /api/v1/update/status` 公开。

## 目录结构

```
agent-heat-index/
├── collector/                  # 核心 Python 包
│   ├── registry.yaml           # ★ 运维核心：产品清单/榜单权重/LRU窗口/数据源开关/新闻源
│   ├── config.py  db.py  http.py       # 配置、SQLite 10 表、限频退避 HTTP 客户端
│   ├── sources/                # 一源一模块（github/npm/pypi/appstore_rss/appstore_lookup/vscode/news_rss/news_hn）
│   ├── news.py                 # 新闻核心：URL规范化/RSS·Atom解析/分类/产品匹配/周分组
│   ├── pipeline.py  scoring.py  retention.py
│   ├── export.py              # 静态 API 导出（/api/v1/*.json，契约冻结）
│   ├── queries.py             # export 与 sitegen 共享查询层
│   ├── sitegen.py  svgchart.py        # Jinja2 建站 + 构建期 SVG 图表
├── templates/  static/assets/ # 页面模板 / 样式与 app.js·news.js
├── scripts/                    # serve.py（本地服务+更新接口）/ update.sh / verify.py / check_env.sh
├── tests/                      # 单元测试（评分/解析/LRU/新闻）
├── data/                       # SQLite 主库 / raw 响应归档 / 快照 / 日志 / 缓存
└── dist/                       # 生成产物：HTML + /api/v1/*.json（可直接部署任意静态服务）
```

## 数据与评分

- 数据源（本机 2026-09-23 实测可达的 6 个）：GitHub REST API、npm Downloads、
  PyPI(pypistats)、App Store 榜单 RSS、Apple lookup、VS Code Marketplace。
  HuggingFace / Docker Hub 本机不可达，已在 registry 中置 `enabled: false`。
- 评分：分类内**分位数归一化**（方向感知）+ **双分**（热度=存量，动量=增长）；
  缺失赋中性分位 0.5 并输出按生效权重计算的**覆盖率**，热度覆盖率 <50% 进**观察区**。
  联合榜只取主分类综合分，再按主分类做第二次分位排名。国产榜分为开源与应用两条赛道：
  开源榜使用 GitHub Stars/Forks、30 天提交和 Stars 增量；应用榜使用中国区 App Store 评分数与免费总榜名次。
  详见站点 `/methodology`。
- **GitHub 未认证限流（60 次/时）的应对**：同请求 6 小时响应缓存（含溯源 id）；
  四轮采集按重要性降级（仓库信息→30天提交→贡献者→30天发布），配额不足整轮跳过并在
  `/sources` 与日志公示。配置 `GITHUB_TOKEN` 环境变量可解除限制。
- **pypistats 限流的应对**：3 秒请求间隔 + [5,15] 短退避 + 300 秒单源时间预算；
  全线 429 时放弃剩余包、下轮自动补齐，不会拖垮整轮更新。

## API（静态 JSON，契约冻结）

`GET /api/v1/boards.json` · `/rankings/{board}.json`（board ∈ unified/coding/framework/general/claw/domestic/domestic_app）
· `/rankings/{board}/history.json` · `/products/{slug}.json`（含相关新闻） · `/raw/{id}.json`
· `/insights.json` · `/news.json`（最近 200 条 + 分类计数） · `/meta.json`；
动态端点（仅本地服务）：`POST /api/v1/update`、`POST /api/v1/update/news`、`GET /api/v1/update/status`、`GET /api/v1/knowledge/search`。

## 扩展方式

| 需求 | 改动点 |
|---|---|
| 新增产品 | `registry.yaml → products` 加一条（无需改代码） |
| 新增榜单/调权重 | `registry.yaml → boards`（**必须**递增 `methodology.version` 并更新 /methodology 版本表） |
| 启用/禁用数据源 | `registry.yaml → sources` 的 `enabled` |
| 新增数据源类型 | `collector/sources/` 写一个模块（实现 collect，返回带 raw_payload_id 的指标）+ 在 `SOURCE_REGISTRY` 注册 + registry 登记 |
| 调整历史保留窗口 | `registry.yaml → retention` |

## 与开发规范的偏差（按需求方 2026-09-23 指示）

| 规范原文 | 实际实现 | 原因 |
|---|---|---|
| systemd timer / CI 定时采集 | 无自动调度，手动更新按钮 + CLI | "数据暂时不自动更新" |
| raw_payloads.body_path NOT NULL | 可空 + body_evicted_at | LRU 清理响应体但保留溯源元数据 |
| Astro SSG 前端 | Jinja2 模板 + 原生 JS | 无代码仓、纯本地部署；避免 Node 构建链与内存开销；API 契约已冻结，未来可无损换 Astro/FastAPI |
| metrics 唯一约束 | 去掉 board 列 | 指标值与所属榜无关（一产品多榜共享同一指标） |
| 无联合榜单 | 新增 unified 榜 | "做数据整合，给出联合榜单"（第一个功能） |

## 安全与合规

不抓 Google Play、不用 headless 浏览器、不抓声明禁止抓取的站点；
每主机请求间隔 ≥1s，429/5xx 指数退避（5/15/45s），遵守 Retry-After。
`serve.py` 更新接口需密码验证，默认只绑 127.0.0.1。
