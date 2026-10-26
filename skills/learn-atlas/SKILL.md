---
name: learn-atlas
description: 学习内容（/learn，LLM 推理技术基础）的内容骨架、Markdown 渲染、登录编辑与知识库归档。适用场景：新增或修改学习文章、调整 series.yaml/章节顺序、修改 /learn 页面与样式、排查学习内容构建失败、把文章归档到 InferTechNote。
---

# 学习内容专家（/learn）

## 职责边界

- 负责 `content/learn/**`、`collector/learn.py`、`templates/learn_*.html`、`templates/_learn_tree.html`，以及 `static/assets/style.css` 的 `.learn-*` 段。
- 不负责 `/knowledge` 的渲染（[knowledge.py](../../collector/knowledge.py)）、报告厅与榜单。
- 内容以站点仓库为唯一事实源；InferTechNote 只做单向归档，站点永不读归档。

## Runbook

1. **新增文章**：在 `content/learn/inference/` 写 `<slug>.md`，frontmatter 必填 `title/type/domain/status/one_liner`；把 slug 加进 `series.yaml` 对应卷；更新 `ROADMAP.md` 状态。
2. **校验**：`sudo -u daimonkey -H .venv/bin/python -m unittest tests.test_learn`；`learn.load_all()` 会检查目录一一对应、prereq 顺序、one_liner 长度、domain/slug。
3. **构建**：`sudo -u daimonkey -H .venv/bin/python -c "from collector import sitegen; sitegen.build()"`（只建站，不跑 AI/采集）；或按仓库常规 `-m collector --build`。
4. **验收**：`/learn`、`/learn/inference/<slug>` 返回 200；左侧树、上下篇、TOC 正常；`scripts/verify.py` 全绿。
5. **发布**：保存即公开；审核是事后标记（P2）；改文后重新构建即可。
6. **归档**：文章定稿后生成对应原子笔记，与文章一起归档到 InferTechNote（P2 脚本，当前手动）。

## 约束

- slug 稳定；改名视为新篇，旧 URL 归档或 301。
- `series.yaml` 是完整课程目录：已发布只写 slug；规划章节写 `planned: true` + `title`，左侧显示“待发布”、不可点。
- `one_liner` ≤60 字，独立可读；`domain` 固定 `inference`。
- 学习页链接用 `/learn/<series>/<slug>`；知识库链接用 `/knowledge/notes/...` 或 `../knowledge/...`（构建期按 `kb_known` 改写）。
- 不放开 Markdown 原始 HTML；`viz`/`video` 将来由 builder 注入可信标记。
- 作者撰写的教程目标、卷表和章节结构是内容主线；软硬件架构图只辅助展示。图表/路线优化不得连带重写目标或正文；涉及扩大范围时先保留原稿并给独立对照版本供作者评估。
- 构建失败保留旧 `/learn` 输出，错误写 `data/learn-build-error.json`，不阻断榜单页。

## 常见调整

| 需求 | 落点 |
|---|---|
| 新增/调整章节顺序 | `content/learn/inference/series.yaml` |
| 改文章 | `content/learn/inference/<slug>.md` |
| 改列表/文章布局 | `templates/learn_index.html`、`learn_article.html`、`_learn_tree.html` |
| 改样式 | `static/assets/style.css` 的 `/* 学习内容 */` 段 |
| 改导航/侧栏 | `templates/base.html` 的 `learn_page` 分支 |
| 改校验规则 | `collector/learn.py` 的 `load_all()` |
| 首页入口 | `templates/index.html` 的「推理基础」卡 |

## 排障

| 症状 | 检查 |
|---|---|
| `/learn` 404 | `dist/learn.html` 是否存在；构建日志是否有 learn 异常 |
| 文章不在导航 | 是否已加入 `series.yaml`；`status` 是否 `archived` |
| 构建报 one_liner | 超过 60 字，或 frontmatter 缺字段 |
| 相对链接 404 | slug 是否在 `series.yaml`；资源是否在 `assets/` |
| 知识库链接失效 | `related` 路径是否在知识库快照中；缺失只告警不阻塞 |

## 决策记录

- 2026-10-22：主存储由知识库仓库改为站点 `content/learn/`；取消“未审不可见”门禁；知识库改为单向归档。
- 2026-10-23：`/learn` 骨架 +《导读》上线；HBM-UB-Cube 动态图暂缓，先做内容骨架与后续文章。
- 2026-10-23：文章写完后生成原子笔记归档（需求方决定），不只是镜像文章。
- 2026-10-24：接手核对：课程已重排为七卷 44 篇，已有《导读》《知识库核心思路》两篇，其余 42 篇待发布；最新内容状态以 `series.yaml` 与 `ROADMAP.md` 为准，设计文档实施表尚未列出第二篇。路线图建议先补请求生命周期、代价模型、KV Cache、数值格式四篇。服务器仓库属 `daimonkey` 用户，root SSH 检查 Git 时应使用 `sudo -u daimonkey git ...`，无需修改全局 safe.directory。服务器实际验证 `sudo -u daimonkey .venv/bin/python -m unittest tests.test_learn`：7 项通过；`/learn` HTTP 200。
- 2026-10-24（优化评估，建议尚未实施）：导读四图目前都是 Mermaid；`static/assets/knowledge.js` 在每次页面加载后按可见性调用 `engine.render()`，未缓存 SVG，且初始化固定 `theme: "dark"`。建议导读采用构建时输出的 HTML/CSS 分层架构与路线卡片，复杂连线图用预制 SVG，避免读者访问时执行图布局。应区分“技术栈架构”和“课程顺序”：前者覆盖硬件、OS/驱动、硬件使能（运行时/编译/算子/通信）、模型执行与推理框架；模型结构及权重作为输入，性能/精度验证贯穿各层。参考昇腾官网 https://www.hiascend.com/ 的硬件使能/AI 框架/推理引擎职责划分，避免把不同生态画成严格一一对应的调用栈。课程建议优先补软硬件栈与张量基础、理顺 tokenizer/embedding 与 dense FFN/MoE 前置关系，并将具体模型和特殊算子作为通用原理的案例。知识库建议明确学习篇、专题笔记与实验记录的不同职责；文章镜像承担备份，派生笔记承担检索，不把派生笔记当成备份。正式改动前以用户后续反馈确定范围。
- 2026-10-24（内容与静态架构落地）：按用户反馈独立“数学与计算基础”卷，保留并补齐线性代数、张量、概率、信息论；模型结构前置于推理基础，总计八卷 48 篇规划、2 篇已发布。`request-lifecycle` 保留 slug，聚焦 Prefill/Decode/采样；框架调度与执行器路径留在系统卷。导读删三张路线图，改文字；首图采用固定 `learn-diagram` / `inference-stack` 标记，由 `learn._render_diagrams()` 在 Markdown 清洗后注入白名单模板，未知标记保留代码，禁止动态模板路径。知识库核心重写为学习篇、专题笔记与实验记录，区分备份和提炼，并明确证据及版本关系。目录入口和页脚同步文案。本轮无新增项目依赖。
- 2026-10-24（本地验证）：建立 Python 3.12 虚拟环境并安装既有 requirements 锁定依赖；首次构建需先 `python -m collector --init-db`，然后 `python -m collector --build`。`.venv/bin/python -m unittest discover -s tests` 446 项通过；学习模块 8 项通过；`scripts/verify_learn.py` 通过；两项 knowledge Node 测试通过。`scripts/verify.py --base http://127.0.0.1:18081` 因本地空采集库缺少榜单/产品/新闻而中止，不能记为全站验收通过。浏览器验证明暗主题、1440/880/390px 布局及架构图跳转；880/390px 无横向溢出，导读未加载 Mermaid。服务器上传被自动审批拦截，当前成果仅在本地，待用户明确允许上传后再部署验证。
- 2026-10-24（服务器发布完成）：用户明确授权直接修改服务器后，重新核对远端工作区干净、版本一致且无更新任务运行；先在独立临时副本运行 446 项 Python 测试（1 项跳过）、内容链接校验和两项 Node 回归，均通过。按逐文件 SHA-256 校验确认线上源文件未被并行修改，备份涉及源文件后发布，执行 `sudo -u daimonkey -H env DM_SITE_URL=http://106.12.30.116:8080 .venv/bin/python -m collector --build`；生产 `scripts/verify.py` 143/143 通过。源文件备份目录名 `backups/learn-20261024-152215`；未改服务进程代码，无需重启。公网浏览器访问被策略拒绝，未取得线上截图；视觉证据来自此前本地同版明暗/窄屏检查。
- 2026-10-24（作者主线与对照评审）：用户指出上一轮导读重写改变了其教程目标与卷表组织，明确“卷表为主，架构图仅展示”。本轮新增 `docs/reviews/learn-introduction-20261024/` 原稿、最小改动版、上轮重写版快照；正式导读和课程目录保持本轮开始时的版本。最小改动版逐字保留首图前的目标/卷表与结尾，只替首图及图注和路线段落；独立 `/design/learn-introduction` 默认 A 版，可切 B 版，原始 Markdown 可展开；不进入课程目录/sitemap。原稿七卷和当前八卷的差异在评审页外层说明，待作者评估后再统一。8 项学习测试、原文范围检查、构建、noindex/sitemap 检查和主题脚本语法检查通过；本轮浏览器访问被授权策略拒绝，未做新的视觉验收。
- 2026-10-24（对照页上线验收）：独立对照页已发布，正式导读源文件 SHA-256 保持 `7cb3b399027497ba98686010fe10b39a6b1d08e3376b7733db7ed9fba3d84c20`。服务器学习模块 8 项测试及全站 `scripts/verify.py` 143/143 通过；备份目录名 `backups/learn-compare-20261024-153425`。发布新增文档目录时须先创建父目录并设置所属用户；首次尝试因父目录缺失触发源文件恢复，补齐后发布成功。

- 2026-10-24（作者确认 A 版）：用户明确选择对照页 A。正式 `introduction.md` 逐字采用 `minimal-change.md`，保留作者的教程目标、卷表、标题结构及结尾，仅更新图/图注与学习路线；B 版与原稿继续留作对照。此次不调整课程目录，不擅自统一原稿七卷表述和现目录八卷的差异。
- 2026-10-24（A 版发布验证）：本地学习模块 8 项测试、内容链接校验、A 版逐字一致性与原文范围检查通过；本地及服务器均执行 `-m collector --build`。服务器全站验收 143/143 通过，旧版备份目录名 `backups/learn-adopt-a-20261024-153948`。对照页已标注正式采用 A，原始快照和 B 版继续保留。
- 2026-10-24（导读按作者反馈精修）：卷表的卷名、顺序、内容范围与 `series.yaml` 八卷同步，导语及 one_liner 改为八卷；作者的教程目标及三条路线保持原样。架构图仅作无链接静态展示，保留六层主干，在推理框架上方设“模型定义与资源”（结构/权重/配置及 Tokenizer 配套资源），删除原图标题、模型输入条、跨层验证、层编号与课程跳转。结尾更新阅读建议及交流文字，三个联系入口保留 Markdown href，以内置 `attr_list` 扩展的 `.learn-contact` 类配合 CSS 卡片样式呈现，无新增依赖；bleach 仍清洗事件及内联样式。A/B 原稿正文快照保留，评审页标明正式页已继续迭代及图示复用当前样式。
- 2026-10-24（精修发布验证）：本地及服务器学习模块 8 项测试、`scripts/verify_learn.py` 通过；实际执行两端 `-m collector --build`，服务器全站 `scripts/verify.py` 143/143 通过。额外核对八卷名称/顺序/锚点与 series 完全一致、图内无链接且资源层在框架之上、删除项不再出现、三处联系卡片及 mailto 正确、作者目标逐字保留；attr_list 允许样式类但事件/内联样式仍被清洗。服务器源文件备份目录名 `backups/learn-refine-20261024-155211`。

- 2026-10-24（恢复 B 版参考与修订）：用户澄清要保留的是此前 B 版的修订记录和索引内容。正式导读恢复 B 的“参考与修订”段：昇腾/vLLM 参考链接、合并的 v0.2–v0.6 记录及 v0.7；保留现有 v0.8。frontmatter.sources 同步参考链接，页内目录随标题重建。范围检查确认该段之前的正文（含作者目标、卷表、图、路线及联系方式）逐字不变。
- 2026-10-24（参考与修订发布验证）：本地 8 项学习测试、内容链接校验、构建及生成 HTML 中参考链接/修订版本/目录锚点检查通过；服务器重建及全站验收 143/143 通过。源文件备份目录名 `backups/learn-refs-20261024-155522`。

- 2026-10-24（模型家族概览前置）：作者确认该卷仅简要介绍常见参数规模与结构，因此移至模型结构之后、推理基础之前；统一卷名“模型家族概览”，保留所有 part id/slug。同步导读卷表、路线、ROADMAP 与设计文档；仅借鉴 LLM Atlas 的总览＋家族页组织形式，具体数据仍须核对官方来源。未调整推理基础卷内顺序，未触碰线性代数草稿和接续记录。

- 2026-10-24（张量篇架构讨论）：作者要求讨论架构后再写；已定位 tensor-layout，提纲留存 ROADMAP，未创建正文或变更 planned。建议围绕轴语义→形状运算→reshape/置换→stride/连续性→dtype 展开，明确稠密 strided 张量适用范围；数据类型篇幅限于用途、字节数与转换，数值编码和误差留 numeric-formats。讨论确认前不将建议视为定稿。

- 2026-10-24（张量篇首稿）：作者授权开写后完成 docs/drafts/learn/tensor-layout.md v0.1，保留 planned。围绕稠密 strided 张量区分轴数/矩阵秩、shape/轴语义、reshape/换轴、视图/拷贝、dtype/累加精度和逻辑字节数/实际存储；连续性简化公式须排除长度 0/1 轴，非连续不等于不能 view，contiguous 不保证独立存储。七段示例在仓库外临时 Python 3.12.14 / PyTorch 2.14.1 CPU 环境逐段通过，不给站点新增 torch 依赖（本次 wheel 约 127 MB）。站点渲染检查覆盖六张表、七段代码、TOC 和公式文本；仅草稿无须构建或发布。

- 2026-10-25（张量篇提交）：按作者要求提交并推送张量篇草稿及本篇接续/专家记录；共享文档采用局部暂存，避免把此前课程页面等未提交改动带入。保持草稿和待发布状态，不触发站点构建或部署。

- 2026-10-26（张量篇转正式发布）：linear-algebra 与 tensor-layout 转入 `content/learn/inference/`，`series.yaml` 移除 planned；数学与计算基础卷已发布 2 篇，全系列 48 篇中已发布 4 篇。`docs/drafts/learn/tensor-layout.md` 保留为逐字写作记录，ROADMAP 的已发布计数同步修正；导读对照页、静态架构图与相关技能记录一并入库。
