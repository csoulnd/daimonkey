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
