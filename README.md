# InferTech 观察站（InferTech Observatory,原智能体热度指数）

以推理技术为重心的技术观察站(daimonkey「大马猴」品牌):模型评测榜、智能体热度榜、AI 新闻,以及登录后的个人工作台(报告厅/个人知识库,按用户隔离),**每个数字都能溯源到原始 HTTP 响应**。当前迭代计划见 [PLAN.md](PLAN.md)。

- **联合榜单**（第一个功能）：跨编码 / 开源框架 / 通用 / Claw 四个分类榜的数据整合榜，产品按"自己赛道内的组内分位分"跨榜排序，每条标注来源榜与覆盖率。
- **AI 新闻汇总**（第三个功能，`/news`）：公开 RSS/API 源的领域新闻，确定性关键词分类（无 LLM）、URL 规范化去重、每条可溯源；提到榜上产品自动关联产品页与当前名次，每周自动生成周报。
- **模型评测榜**(`/rankings/model`,v1.6):SuperCLUE 中文大模型月报口径,**API 与开源模型一起排**,开源权重另有分榜;每模型链到 ModelScope/官网;指标=总分+六维(数学/科学/代码/智能体/指令/幻觉),整份月报 PDF 归档可溯源,榜单图由 AI 辅助识别(标注);复用 v1.5 算法70%+AI复评30%。月更:改 `registry → sources.superclue.base_url` 指向新月报后跑 `scripts/fetch_superclue.py`。AA Index/Podium/LMArena Elo 待数据通道。
- **报告厅**（第四个入口，`/reports`，登录后可见）：汇报报告模块化框架——网页报告（Markdown 站内渲染或原样 HTML 嵌入）在线阅读、PPT 原件下载；内容与代码同仓版本化（`reports/`），素材（知识库等）登记为参考材料。详见 [reports/README.md](reports/README.md)。
- **数据手动更新**：无任何自动调度。前端右上角「⟳ 更新数据」按钮（经本地服务触发）或命令行手动执行；新闻页另有「仅更新新闻」轻量按钮（约 1 分钟，不重采热度数据）。全站显著展示**数据截至时间**，超 48 小时未更新自动挂横幅。
- **历史数据 LRU**：原始响应体 30 天、指标时序 180 天、榜单快照 365 天、新闻条目 180 天、采集缓存 48 小时，到期自动清理（响应体清理后元数据与 SHA-256 永久保留，溯源链不断）。窗口在 `collector/registry.yaml → retention` 调整，`0` = 永久保留。

## 快速开始

## 个人知识库（第二个功能）

与智能体热度指数同级入口：`/knowledge`。以 `csoulnd/InferTechNote` 为唯一内容源，提供目录树、全文搜索、Markdown 文章、图片、Mermaid 图表、本文目录与版本固定的 GitHub 原文链接。仅展示仓库概览、`agent/`、`infra/` 和 `knowledge/`；`templates/` 与隐藏配置不展示，仓库原文件保留；外部子模块不自动导入。

快照位于 `data/knowledge-repo`。同步方式二选一：

- **网页按钮**：知识库页右上「⟳ 同步知识库」（登录态/密码同其他更新按钮，约 10~60 秒，不重采热度数据）；
- **CLI**：`.venv/bin/python scripts/sync_knowledge.py`

两条路径都会写更新状态（mode=knowledge）、随 commit 变化刷新知识库 AI 摘要、失败保留旧快照并记录错误。**服务端 git 拉取走 SSH**（`collector/knowledge.py → GIT_URL`，本机 HTTPS git 协议被阻断实测超时；旧 HTTPS 工作区首次同步时自动迁移 remote）。

普通 `collector --build` / 热度更新会保留并重建已有知识库快照，不自动拉取 GitHub。同步失败时保留旧快照。

## AI 新闻汇总（第三个功能）

与热度指数、知识库同级入口：`/news`。配置全部集中在 `registry.yaml → news`（源清单、分类关键词规则、HN 查询词、保留窗口）。

## 智能助手(第四个功能,`/assistant`)

PIAgent 驱动的多用户助手,**双通道、配额隔离**:

- **运行时**:[Pi](https://pi.dev)(`@earendil-works/pi-coding-agent`)**一次性进程模式**(`collector/piagent.py`):每次任务 spawn pi 子进程、`--no-tools` 全禁工具、用完即退(实测峰值 ~130MB 仅任务期间,空闲零内存);多轮上下文从 SQLite 消息表重组传入。
- **对外助手窗口**:全站悬浮宠物 🐱 点开即聊(面板支持最小化/大窗/会话重命名与删除),未登录浏览不受影响、权限动作按需登录;账号登录(见下),签名 Cookie 携带用户名(HttpOnly + SameSite=Strict,7 天),按用户名失败 5 次锁 10 分钟;每个账号有自己独立的会话列表。
- **内部通道**:站点进程按需调用(周报 AI 摘要 / 文案润色 / 优化建议),CLI:`.venv/bin/python -m collector --assistant news-digest`。
- **资源护栏**(`registry.yaml → assistant`):全局并发 1(跨进程文件锁 `data/assistant.lock`)、单次超时 300s(超时杀进程组)、每通道独立每日请求数 / token 硬顶;每次运行落 `assistant_runs` 审计表(含峰值 RSS 与精确 token 用量)。
- **安全边界**:AI 产出一律带标记(`insights.llm_generated=1` 或 `assistant_messages.workflow`),确定性管线(榜单评分 / 新闻分类)不受影响;API Key 放 `data/assistant.env`(600 权限,`DEEPSEEK_API_KEY=sk-...`)。
- **扩展**:新工作流 = `collector/workflows.py` 里一个 `WorkflowSpec` + 注册(build_task 组装提示词、store 决定产物去向)。
- 表结构:`assistant_sessions(含 owner) / assistant_messages / assistant_usage / assistant_runs / users`(附加式迁移,`db.ensure_assistant_tables` + `ensure_users_tables`)。

## 用户系统(账号,全部等权)

- 匿名可浏览全部内容;登录后(全站 🐱 面板或更新按钮)可:触发数据更新、同步知识库、助手聊天、访问讲稿页 `/notes`。**全部账号等权(管理员模型),无角色分层**。
- 账号只在服务器上手动创建:任意目录执行 `userctl add <用户名>`(子命令:add / passwd / list / disable / enable;密码 pbkdf2_hmac 20 万轮盐渍哈希,存 SQLite `users` 表)。
- Cookie 值 = `{username}.{expires}.{HMAC(secret, username.expires)}`,不可伪造、携带身份。
- **过渡**:尚无任何账号时,仅密码登录沿用旧 `data/update_password.sha256`(用户名留空);建号后旧密码路径自动失效。`X-Update-Password` 头路径已移除(防爆破绕过锁定)。

## 访问规格与防护(10 并发 / 20 在线)

按「最多 10 人并发访问、20 人同时在线」规格设定防护上限(`scripts/serve.py`,调整后同步 `skills/load-optimization`):

- 每 IP 令牌桶限流:静态 120 req/min、API 60 req/min,超限 429 + Retry-After;
- 全局在途请求上限 30,超限 503;连接读超时 30s;
- 所有响应带 `X-Content-Type-Options: nosniff`,HTML 带基础 CSP(同源脚本/样式);空字节路径直接 400。

## AI 接入（第五个功能，v1.4 方法论）

- **三板块 AI 摘要**：首页 / 新闻页 / 知识库顶部统一格式的 AI 摘要卡片（`🤖 AI 摘要` + 内容 + AI 生成标注 + 数据指纹）。随数据刷新自动生成、首次启用自动补齐；摘要带数据指纹，同一份数据不重复生成（不烧配额）。配置 `registry.yaml → assistant.ai.summaries`。
- **榜单 AI 分析分**：全量更新后按榜生成 AI 分析分（0-100 + 一句话理由），存 `ai_scores` 表、与规则分**平行**存储——默认仅展示（榜单 AI 列悬停看理由、产品页徽章），排序仍由规则分决定；`assistant.ai.scoring.ai_weight > 0` 时启用加权融合（需同步升级方法论版本）。配置 `assistant.ai.scoring`（top_n 每榜送审前 N 名）。
- **周报 AI 段**：周报页顶部展示该周已发布的 AI 总结（insights 表 `llm_generated=1` 且 `status='published'` 才渲染，草稿不上线）。
- **触发链路**：AI 评分在 `pipeline._collect()` 评分后（仅全量更新）；AI 摘要在建站前 `pipeline.run_build()`（覆盖全量更新 / 仅新闻更新 / 单独 `--build` 三条路径）。
- **降级原则**：AI 步骤失败（缺 Key / 配额 / 超时 / 解析失败）只记 notes、不影响更新状态，旧摘要与旧 AI 分继续渲染；`--update` 输出与 `update_status.json` 的 `ai` 字段可查简况。
- 表结构：`ai_summaries(page 主键) / ai_scores(board+date+product 主键)`（附加式迁移，`db.ensure_ai_tables`），随榜单快照窗口清理。
- 实现入口：`collector/ai.py`（指纹/编排/JSON 容错）、`collector/workflows.py` 的 `page_summary` 与 `board_ai_analysis`。

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

- 登录:右上角「登录」按钮 → `/login`;账号 `userctl add <用户名>` 创建;知识库按用户自配 GitHub 仓库(`userctl repo <用户名> <url>`,默认 InferTechNote);个人工作台登录后可见。

- 浏览(GET)公开:`http://<服务器IP>:8080`。**对外访问需在云控制台安全组放行 TCP 8080**(本机防火墙未启用,仅云安全组拦截)。
- 登录:**右上角「登录」按钮** → `/login` 页;账号用 `userctl add <用户名>` 创建(任意目录)。
- 知识库按用户隔离:登录后在知识库页顶部「我的知识库」卡直接配置自己的 GitHub 仓库(或 CLI `userctl repo`),点「同步我的知识库」生成专属页面;未配置走默认 InferTechNote。
- 个人工作台(报告厅/知识库/讲稿页)登录后可见;更新等权限动作登录后即可用。
- API 中 `POST /api/v1/update` 与 `POST /api/v1/update/news`、`/api/v1/update/knowledge` 需登录 Cookie(不再接受 `X-Update-Password` 头或 URL 参数)。`GET /api/v1/update/status` 公开。

## 品牌

[daimonkey「大马猴」](brand/daimonkey.md):极简猴头,表达好奇、聪明与探索。favicon/侧栏/助手宠物统一使用;炫彩版仅强调场景。

## 开发工作流(专家/skill 为核心)

任何开发任务开始前与结束后都必须经过专家(skill)的选择、创建或更新——见根 [AGENTS.md](AGENTS.md)。专家索引:`skills/`(负载优化、UI 美化、数据更新、新闻汇总、产品展示、报告撰写)。任务结束时把决策与踩坑沉淀回对应专家文档,没有更新专家的任务视为未完成。

## 目录结构

```
agent-heat-index/
├── AGENTS.md                   # ★ 仓库助手工作约定:专家工作流铁律 + 专家索引
├── skills/                     # ★ 专家文档(负载优化/UI 美化/数据更新/新闻汇总/产品展示/报告撰写)
├── collector/                  # 核心 Python 包
│   ├── registry.yaml           # ★ 运维核心：产品清单/榜单权重/LRU窗口/数据源开关/新闻源
│   ├── config.py  db.py  http.py       # 配置、SQLite 10 表、限频退避 HTTP 客户端
│   ├── sources/                # 一源一模块（github/npm/pypi/appstore_rss/appstore_lookup/vscode/news_rss/news_hn）
│   ├── news.py                 # 新闻核心：URL规范化/RSS·Atom解析/分类/产品匹配/周分组
│   ├── pipeline.py  scoring.py  retention.py
│   ├── export.py              # 静态 API 导出（/api/v1/*.json，契约冻结）
│   ├── queries.py             # export 与 sitegen 共享查询层
│   ├── sitegen.py  svgchart.py        # Jinja2 建站 + 构建期 SVG 图表
│   ├── piagent.py  users.py            # PIAgent 运行时适配 / 账号体系
│   ├── sources/superclue.py            # 模型评测榜数据源(PDF→读榜)
├── templates/  static/assets/ # 页面模板 / 样式与 app.js·news.js
├── scripts/                    # serve.py（服务+防护+看门狗）/ userctl.py（账号）/ fetch_superclue.py（模型榜首灌）/ sync_knowledge.py（--user 按用户）/ verify.py
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
动态端点（仅本地服务）：`POST /api/v1/update`、`POST /api/v1/update/news`、`POST /api/v1/update/knowledge`、`GET /api/v1/update/status`、`GET /api/v1/knowledge/search`；
助手端点（账号 Cookie，登录载荷 {username,password}）：`GET/POST /api/v1/assistant/session|login|logout`、`GET sessions|sessions/{id}/messages|status|usage|workflows`、`POST sessions|chat|workflows/{name}`、`DELETE sessions/{id}`。

## 扩展方式

| 需求 | 改动点 |
|---|---|
| 新增产品 | `registry.yaml → products` 加一条（无需改代码） |
| 新增榜单/调权重 | `registry.yaml → boards`（**必须**递增 `methodology.version` 并更新首页「评分方法」折叠块） |
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
`serve.py` 权限动作需账号登录，默认只绑 127.0.0.1；限流与并发上限按 10 并发/20 在线规格设定。
