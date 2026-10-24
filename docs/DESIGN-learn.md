---
title: "学习内容（LLM 推理技术基础）设计与规则"
type: design
status: draft
---

# 学习内容（LLM 推理技术基础）设计与规则

> 2026-10-22 草案。需求方已确认：主存储放站点仓库、网页内联编辑（登录后可用）、动态图先预渲染、发布不做门禁。
> 未决项以【默认】标注：按此实现，需求方可随时否决翻转。

## 0. 与上一版的关系

本文取代 2026-10-22 早间的两个决定，原因见决策记录：

1. **内容主存储**：由 `InferTechNote/learn/` 改为站点仓库 `content/learn/`。知识库只做单向归档备份，不参与站点渲染。
2. **发布门禁**：取消“未审不可见”。写完即公开；审核变为事后标记，编辑即时生效。

上一轮确定的其余部分继续有效：公开 `/learn` 一级入口、站点静态构建、零 Node 构建链、Markdown 为内容格式、复用现有 Markdown 渲染与 bleach 清洗。

## 0.1 实施状态（2026-10-23）

| 项 | 状态 |
|---|---|
| `/learn` 骨架：完整课程目录（左侧，含待发布）、文章页、单篇 TOC（右侧）、上下篇、首页入口 | ✅ 已上线 |
| 内容骨架 | 七卷 44 篇（导读 / 推理基础 / 基础模型 / 模型结构 / 算子加速 / 加速特性 / 实践），以 `series.yaml` 为准 | ✅ 2026-10-24 重排 |
| 《导读》首篇（`content/learn/inference/introduction.md`） | ✅ 已发布，站点公开 |
| 登录编辑器与单页重建 | ⏳ P1 |
| 审核标记 / 审核队列 | ⏳ P2 |
| 预渲染动画 / HBM-UB-Cube 交互组件 | ⏸ 暂缓（需求方 2026-10-23） |
| 知识库归档（含生成原子笔记） | ⏳ P2 |

## 1. 决策记录

| # | 决策 | 理由 |
|---|---|---|
| D1 | 学习内容主存储：`daimonkey/content/learn/` | 编辑器直接写本地文件、保存即重建；站点是产品核心，知识库只做备份 |
| D2 | 知识库：单向归档到 `InferTechNote/learn/`，不参与渲染 | “知识库只是备份”；避免双写与跨仓实时写入 |
| D3 | 入口：公开 `/learn` + 一级导航“推理基础” | 学习内容是公开门面；审核/编辑器登录可见 |
| D4 | 发布：写完即公开，不做审核门禁 | 需求方要“直接写完上传，实时修改” |
| D5 | 审核：事后标记 `待审/已审`，仅登录可见徽章与队列 | 保留留痕与可追溯，不阻塞发布 |
| D6 | 编辑：网页内联 Markdown 编辑器，登录后可用 | 需求方在页面上直接改文本 |
| D7 | 图表四层：Mermaid / SVG / 预渲染动画 / 交互 viz | 复杂结构图 + 运算过程 + 可讲解的交互 |
| D8 | 动态图暂缓；先做骨架与文章，HBM-UB-Cube 等有文章真正需要时再启动 | 需求方 2026-10-23：先不做这个主题 |
| D9 | 篇幅不设硬区间，由问题决定；超 8000 字须拆篇或说明 | 需求方：篇幅按内容来 |
| D10 | 公开索引只列未归档文章；归档篇保留 URL、退出索引 | 索引干净，旧链接不断 |

## 2. 目标与非目标

**目标**

- 一套可读、可扩展、可编辑的推理学习内容，覆盖从一次 decode 请求到推理系统的完整链路。
- 每篇文章可在网页上直接修改，保存后秒级生效。
- 复杂结构图、运算动画、交互演示都能在文章里稳定展示。
- 内容在站点仓库版本化；知识库定期收到只读归档。

**非目标（v1）**

- 不做训练/后训练/Agent 教程；不做 WYSIWYG；不做多作者协作流；不做评论/收藏；不做 CDN/外部图床。
- 不引入前端框架或 Node 构建链；不放开 Markdown 原始 HTML。
- 不把知识库当第二内容源（归档单向，站点永不读归档）。

## 3. 信息架构

```text
daimonkey（主）
├── content/learn/inference/       文章 + series.yaml + assets/ + viz spec
├── collector/learn.py             加载 / 校验 / 渲染 / 保存 / 单页重建
├── templates/learn_*.html         地图 / 文章 / 编辑器 / 审核队列
├── static/assets/learn.js         进度、编辑器、viz 懒加载
├── static/assets/viz.js           算子演示组件（按需加载）
└── data/learn-*.json(l)           审核、编辑审计、构建错误

InferTechNote（备份）
└── learn/inference/               归档镜像（Obsidian 可读，站点不渲染）
```

## 4. 内容模型

### 4.1 目录

```text
content/learn/inference/
├── series.yaml                # 完整课程目录（含规划章节）
├── ROADMAP.md                 # 章节核心问题与状态，不参与渲染
├── introduction.md
├── assets/
│   ├── 01-kv-layout.svg
│   ├── 01-kv-layout.png
│   └── src/
│       └── 01-kv-layout.drawio
```

未写出的章节不建空文件；只在 `series.yaml` 里登记为 `planned`。

### 4.2 series.yaml

**完整课程目录**与顺序的唯一来源：已发布章节只写 slug（标题取 frontmatter）；规划章节写 `planned: true` + `title`，在左侧目录显示“待发布”、不可点、没有 URL。

```yaml
id: inference
title: LLM 推理技术基础
description: 从一次 decode 请求到推理系统的技术地图
parts:
  - id: guide
    title: 导读
    chapters:
      - introduction
      - {slug: request-lifecycle, title: "推理全景：一次请求的一生", planned: true}
      - {slug: metrics-and-formulas, title: "指标与公式", planned: true}
  - id: foundations
    title: 基础
    chapters:
      - {slug: cost-model, title: "推理的代价模型", planned: true}
      - {slug: attention-kv-cache, title: "Attention 与 KV Cache", planned: true}
```

规则：

- 文件存在 → 已发布；`series.yaml` 只写 slug，不写 `title`/`planned`（写了会报错，避免标题双写）。
- 文件不存在 → 必须 `planned: true` + `title`；只出现在左侧目录，不渲染页面。
- 文章写好后：放进文件、去掉该条的 `planned`/`title`，左侧自动变成可点链接。
- 文件存在但未登记 → 构建失败；登记为 planned 但文件已存在 → 构建失败。
- 无重复 slug；分卷 id 唯一；顺序即左侧目录顺序。

### 4.3 frontmatter

```yaml
---
title: "KV Cache 与注意力变体"
type: guide            # guide | map | lab
domain: inference
status: draft          # draft | active | archived（draft/active 都公开）
one_liner: "KV Cache 是 decode 阶段显存占用的主项，形状由层数、KV head、head_dim 与 dtype 决定。"
prereq: [cost-model]   # 可选，前置章节 slug
related: [knowledge/infra/foundations/kv-cache.md]   # 可选，知识库备份里的相关笔记
sources: ["https://arxiv.org/abs/2309.06180"]
version: "vLLM 0.10.x / CANN 8.x"                    # 实现类必填
---
```

- `one_liner` ≤60 字，独立可读，卡片与搜索用。
- `related` 只在归档快照里存在该路径时渲染成链接，否则隐藏并告警；**不阻塞构建**。
- 审核字段不进 frontmatter；审核状态在 `data/learn-review.json`。
- 不新增 `tags`；检索维度由 `series`、分卷、`type` 承担。

### 4.4 文章骨架

```markdown
# 标题

> 一句话结论（比 one_liner 稍长，首段即结论）

## 前置与范围
## 正文（多个 H2/H3）
## 工程对照（可选：vLLM / Ascend）
## 自测
## 延伸
## 修订记录
```

- “相关笔记”卡片由构建根据 `related` 自动追加，正文不手写。
- 修订记录：日期 / 版本 / 改动 / 审核人。
- 篇幅由问题决定；超过 8000 字须拆篇或在此说明理由。

### 4.5 命名

- 路径 kebab-case；slug 稳定，改名视为新篇（旧 URL 归档或 301）。
- 文件名不带日期/版本/编号；章节号只在展示层生成。
- 图片：`NN-<subject>-<view>.svg` + `.png` 回退；源文件同 stem 放 `assets/src/`。
- 动画：`NN-<subject>-<view>.webp` 或 `.mp4`，poster 同名 `.png`。

## 5. 站点模型

### 5.1 路由

| 路由 | 内容 | 可见性 |
|---|---|---|
| `/learn` | 学习地图：分卷、章节卡、最近更新 | 公开 |
| `/learn/<series>/<slug>` | 文章页：左侧章节树、右侧 TOC、上/下篇 | 公开 |
| `/learn/edit/<series>/<slug>` | 内联编辑器 | 登录 |
| `/learn/review` | 审核队列（待审/已审/归档） | 登录 |
| `/learn/manifest.json` | 机读目录 | 公开 |
| `/api/v1/learn/article` | 保存文章（POST） | 登录 |
| `/api/v1/learn/review` | 标记审核状态（POST） | 登录 |

`/learn` 走 `_file` 的无扩展名回退：同时生成 `dist/learn/index.html` 与 `dist/learn.html`。
编辑器与审核页加入 `serve.py` 登录拦截，并设 noindex。
左侧固定展示整门课目录（全量，含 `planned` 待发布项，不可点）；右侧只展示当前文章的 H2–H3 目录。
学习页侧栏把全局导航收进“站点导航”折叠块（默认收起，展开状态用 localStorage 记忆），把垂直空间优先留给课程目录。

### 5.2 构建

- `collector/learn.py`：`load()` 读取 content、校验、算哈希；`build(env, common)` 渲染地图/文章/manifest；`rebuild(slug)` 供编辑器单页重建。
- `sitegen.build()` 在 `knowledge.build()` 之后调用 `learn.build()`；失败只影响 learn 输出，保留旧版并写 `data/learn-build-error.json`。
- 复用 `knowledge.render_markdown()` 的 Markdown/bleach/Mermaid/链接改写；`learn/` 不进 `displayable()`，`/knowledge` 行为不变。
- 静态资源：新增 `.svg` 复制；动画文件原样复制；`viz` spec 写入 `dist/learn/viz/`。

### 5.3 导航、搜索、SEO

- `base.html` 增加一级入口“推理基础”（放在三方向之后、个人工作台之前）；`learn_page` 控制当前态与左侧树。
- 已发布文章进入站内搜索索引，支持按系列/分卷筛选；编辑器、审核页不进索引。
- sitemap 自动收录公开文章；编辑器/审核页 noindex；文章页 Article JSON-LD。
- 首页增加“推理基础”入口卡（P1）。

## 6. 编辑与发布

### 6.1 权限

- **登录即可编辑**（`_assistant_user()` + 账号启用）；默认所有启用账号。
- `registry.yaml → learn.editors: []` 可收紧为白名单；空 = 所有登录账号。
- 保存/审核动作写审计日志，记录用户名。

### 6.2 编辑器

- `/learn/edit/<series>/<slug>`：服务端鉴权后返回编辑器页（noindex）。
- 元数据表单（title / one_liner / status / prereq / related / sources / version）+ 正文 textarea。
- 保存按钮 → `POST /api/v1/learn/article`；可选“预览”页签（不发布，仅编辑器内）。
- 不提供 WYSIWYG；不做在线图片上传（P2 再评估，先走 git）。

### 6.3 保存流程

1. 校验路径白名单（`content/learn/<series>/<slug>.md`）、大小 ≤200KB、frontmatter 必填、H1 存在、链接与 viz spec 合法。
2. 校验 `base_sha` 与磁盘当前文件一致；不一致返回 409 + 服务端内容，前端提示重载/合并。
3. 写临时文件 → 原子 rename；`rebuild(slug)` 渲染文章 + 索引 + manifest。
4. 渲染失败：回滚旧文件，返回 422 + 错误列表。
5. 成功：返回新 sha、页面 URL、警告（如 `related` 未命中）。
6. `git add <file>` + commit（10 秒去抖，合并连续保存）；push 后台可选（`learn.push`），失败只记日志。

### 6.4 审核标记

- `POST /api/v1/learn/review`：`{series, slug, action: reviewed|unreviewed, note}`。
- 记录 `{sha, reviewer, reviewed_at, note}` 到 `data/learn-review.json`，并 append `data/learn-review-log.jsonl`。
- 文章页徽章：当前 sha 与审核 sha 一致显示“已审 · 日期”，否则“待审”；**仅登录可见**。
- `/learn/review` 列出待审/已审/归档，可一键标记；不做通过/退回阻断。

### 6.5 回滚

- 每次保存都有 git commit；管理员可按 git 历史恢复（P3 做“历史版本”入口，v1 用命令行）。
- 审核记录保留历史 sha，不与内容绑定为门禁。

### 6.6 安全

- 仅登录；校验 `Origin`/`Sec-Fetch-Site` 同源；POST-only；沿用现有限流。
- 路径白名单，禁止 `..`、绝对路径、非 `content/learn/` 前缀。
- 内容仍经 bleach；原始 HTML 不放开；`viz`/`video` 由 builder 注入可信标记，不经 bleach。
- git 命令固定参数，不拼接用户输入；提交只包含目标文件。

## 7. 图表系统

### 7.1 四层

| 层 | 形式 | 用途 | 实现 |
|---|---|---|---|
| L0 | Mermaid | 流程、时序、状态机 | 现有懒加载 |
| L1 | SVG（PNG 回退） | 模型/系统结构图、InfraTech 风格复杂图 | `.svg` 作为 `<img>`；构建期校验 |
| L2 | WebP / MP4 | 搬运、流水线、tiling 动画 | `anim` 围栏 → builder 注入 `<img>`/`<video>` |
| L3 | 交互 `viz` | HBM→UB→Cube 步进/播放、双缓冲 | `viz` 围栏 → JSON spec + `viz.js` 组件 |

### 7.2 SVG 规范

- 只允许作为 `<img src>` 引用，不内联；构建期校验：无 `<script>`、无 `on*=`、无外部 `href`、无 `<foreignObject>`。
- 导出 2x 画布；源文件 `.drawio`/`.excalidraw` 与导出图同 stem。
- 图注写“结构参考 InfraTech（链接）”；不直接复制子模块位图。

### 7.3 预渲染动画

````markdown
```anim
{"src":"assets/hbm-ub-cube.webp","poster":"assets/hbm-ub-cube.png","caption":"HBM→UB→Cube 的一次 tile 计算","loop":true}
```
````

- WebP/APNG 走 `<img>`；MP4 走 `<video controls muted loop playsinline poster>`。
- 单个动画 ≤3MB；超出拆段或降分辨率；必须提供 poster。
- 生成脚本放 `assets/src/`；同一份 JSON spec 同时驱动 Python 预渲染与后续交互组件，避免两套逻辑。

### 7.4 交互 viz

````markdown
```viz
{"type":"hbm-ub-cube","tile":{"m":128,"n":128,"k":128},"ub_kb":192,
 "hbm_bw_gbs":1600,"cube_tflops":280,
 "steps":[{"name":"load A","from":"HBM","to":"UB","bytes":65536,"note":"异步搬运"},
          {"name":"cube","from":"UB","to":"Cube","flops":2097152,"note":"矩阵乘"}]}
```
````

- builder 校验 `type` 与字段，写 `dist/learn/viz/<slug>-<n>.json`，注入 `<figure class="viz" data-viz-type data-spec>` + `<noscript>` 回退图。
- `learn.js` 用 IntersectionObserver 懒加载 `viz.js`；`viz.js` 注册渲染器，首版只含 `hbm-ub-cube`。
- 控件：上一步/下一步/播放/速度；显示搬运字节、Cube 周期、当前 tile；键盘可操作；`prefers-reduced-motion` 只显示首帧与步进。
- 不引第三方库；SVG/Canvas 自绘；CSP 同源即可。

### 7.5 HBM-UB-Cube 组件（首版，暂缓）

> 2026-10-23 需求方决定先不做该主题；本节保留设计，等有文章需要动态算子图时再启动。

- 画面：HBM / UB（Unified Buffer）/ Cube 三区；数据块从 HBM 到 UB、Cube 计算、结果回写。
- 支持 tiling 与双缓冲：`load A`、`load B`、`cube`、`write back` 四类 step；高亮当前缓冲区与依赖。
- 计数器：搬运字节、Cube 周期、tile 进度；按 spec 的带宽/算力算 roofline 对比。
- 第一版只做步进 + 自动播放；不做参数编辑（参数改 spec 重存）。

### 7.6 InfraTech 参考规范

- 只参考其结构表达与信息密度，不直接引用仓库图片；重绘后在图注标“结构参考 InfraTech”。
- 若确认其许可证允许复用，才可复制并署名；默认按重绘处理。
- 复杂结构图优先 draw.io（`.drawio` 源文件）→ SVG + PNG；Excalidraw 亦可。

## 8. 知识库归档

- 目标仓库：`InferTechNote`，镜像目录 `learn/inference/`（与 `content/learn/inference/` 同构，含 assets）。
- 使用独立 clone：`data/archive-repo`（daimonkey 用户所有，SSH remote），不碰需求方的工作副本。
- `scripts/archive_learn.py [--push]`：pull → 镜像 → 生成 `learn/README.md` 索引 → commit →（可选）push。
- 单向：归档不回流；知识库中的手工修改会在下次归档时被覆盖，脚本先检测未提交改动并中止。
- 频率【默认】每月或手动；不随每次保存触发。
- **文章定稿后生成对应原子笔记**（`knowledge/inference/<slug>.md`），与文章镜像一起归档；笔记由 agent 生成、需求方审核后入库（2026-10-23 需求方决定）。
- `related` 链接指向备份里已有的笔记；站点不读归档。

## 9. 规则清单（v2）

- **R1 内容**：一篇一问题；结论先行；篇幅由内容决定（>8000 字须拆或说明）；证据分“已验证/推断/待验证”；源码固定 tag/commit。
- **R2 命名**：kebab-case；slug 稳定；图片/动画/源文件同 stem；`series.yaml` 是完整课程目录（规划章节标 planned）。
- **R3 Schema**：必填 `title/type/domain/status/one_liner`；`related` 可选；审核字段不进仓库；`domain: inference`。
- **R4 发布**：保存即公开；草稿/归档只影响展示与索引，不阻断；无门禁。
- **R5 审核**：事后标记 `待审/已审`；仅登录可见；记录 sha/人/时间/备注；改文自动回“待审”。
- **R6 编辑**：登录可用；路径白名单；`base_sha` 冲突检测；原子写；失败回滚；审计；自动 commit。
- **R7 图表**：Mermaid/SVG/动画/交互四层；SVG 校验；动画有 poster；viz 只收 JSON spec；复杂图重绘并署名。
- **R8 归档**：单向、独立 clone、先检测再覆盖、按需/每月；站点不读归档。
- **R9 工程**：复用现有渲染；零新依赖；单页重建；预算 ≤150KB（不含媒体）；测试与 verify 覆盖。
- **R10 协作**：交付附改动、验证输出、未决问题；不虚构来源；规则与代码冲突以代码为准。

## 10. 分期与验收

| 阶段 | 内容 | 状态 / 验收 |
|---|---|---|
| P0 | 本文档、`skills/learn-atlas/SKILL.md`、`content/learn/inference/series.yaml` + ROADMAP、加载器与校验、单篇模板、`scripts/verify_learn.py` | ✅ 2026-10-23（viz 原型随 HBM-UB-Cube 暂缓） |
| P1 | `/learn` 地图 + 文章页 + 登录编辑器 + 保存/重建/commit + 1 篇样板 | 🚧 地图、完整课程目录、文章页、《导读》已上线；编辑器与单页重建待做 |
| P2 | 审核标记/队列、搜索/SEO、归档脚本（含生成原子笔记）、复杂结构图 | 待做；HBM-UB-Cube 动态图暂缓 |
| P3 | 历史版本、阅读进度、图片上传、更多图表 | 待做 |
| P4 | 按 ROADMAP 逐章写、发布、迭代 | 持续；每篇有修订记录与审核标记 |

## 11. 风险

| 风险 | 应对 |
|---|---|
| 编辑即公开，错误短暂可见 | 发布前自检、保存前校验、git 快速回滚 |
| 编辑与 agent 同时改同一篇 | `base_sha` 冲突 + 文件锁 + 页面提示“正在编辑” |
| 编辑器被滥用 | 仅登录、路径白名单、大小限制、限流、审计 |
| 自动 commit 噪声 | 10 秒去抖合并；提交信息含用户名与 slug |
| 归档覆盖手工改动 | 归档前检测未提交改动，冲突即中止 |
| SVG/视频安全 | 只经 builder 注入；SVG 静态校验；不放开原始 HTML |
| 动态图拖慢首屏 | 懒加载、poster、单文件 ≤3MB、IntersectionObserver |
| 知识库与站点漂移 | 单向归档 + 每月 diff 检查；站点为准 |

## 12. 待定项

1. HBM-UB-Cube 首例参数：暂缓；启动时再定芯片型号（310P？）、UB 容量、tile 形状与是否双缓冲对比。
2. 编辑器权限：已定登录即可编辑（默认所有启用账号，`learn.editors` 可收紧）。
3. 归档频率：默认每月 + 手动。
4. 图片上传：P2 评估拖拽上传，还是继续走 git。
5. 归档原子笔记：已定生成（见 §8）。
