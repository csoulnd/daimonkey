# 报告厅（Report Hall）

汇报报告的模块化框架：**网页展示 + PPT 展示**。报告内容与代码同仓版本化，
随时可回溯。报告素材（**原材料**）来自知识库（`/knowledge`）、站内榜单与新闻
数据、外部资料——知识库是材料来源，不是报告本身。

访问入口：`/reports`（站点导航「报告厅」）。

## 目录结构

```
reports/
├── README.md            # 本文档
├── registry.yaml        # ★ 报告清单（新增报告 = 建目录 + 登记一行）
├── hello-report/        # 示例报告（可删除）
│   └── report.md
└── {slug}/              # 每份报告一个目录
    ├── report.md        #   内容文件（三选一，见下表）
    ├── index.html       #   或：原样网页报告
    ├── deck.pptx        #   或：演示文稿
    └── assets…          #   图片等同目录资源（自动拷贝，相对路径引用即可）
```

## 内容形态

| 内容文件 | 展示方式 | 适用场景 |
|---|---|---|
| `.md` | 站内渲染（Markdown → 页面，与知识库同管线） | 笔记型、结构化文字报告 |
| `.html` / `.htm` | 原样网页：详情页 iframe 内嵌 + 「新窗口打开」 | 带自定义样式/脚本的完整 HTML 报告 |
| `.pptx` | 演示文稿原件下载（网页化在线预览为 Roadmap 项） | 正式 PPT 汇报 |
| `showcase.yaml` | **调研展示**：构建时从知识库快照导入 Markdown 生成交互式展示页 | 产品调研文档的网页化呈现（见 `agentcore-platforms/`） |

**调研展示（showcase）**：汇报型展示页。内容唯一来源是知识库里的研究文档，构建时导入、不在网站侧另写文案；事实/研究判断样式区分、来源与版本固定链接保留。落地与巡检规则见 `skills/product-showcase/SKILL.md`；引擎在 `collector/showcase.py`。展示侧不改源文档，内容问题记入报告的 `content-issues.md` 交回研究侧。

- **`showcase.yaml` 结构**：`docs`（源文档注册表）→ `chapters`（汇报章节：导语/要点卡/小节）→ `diagrams`（多张交互图）→ `overview`（开篇概览）→ `sources_folds`（折叠来源表）。小节支持 `table_filter`（对比表按产品行收窄）、`compact`（去掉由图承载的引导句）、`mode: flow`（图+步骤同屏闭环）、`mode: fold`（折叠块）；要点卡 `points` 每条带 `kind`（fact/judgment/plain）与 ★ 原文锚点。
- **讲稿页**：报告目录放 `notes.md`（逐章口播稿），构建时渲染到 `/notes/{slug}`——**登录可见**（serve.py 对 `/notes/` 前缀复用助手会话鉴权，未登录 401 提示页，响应带 `X-Robots-Tag: noindex`）；公开演示页不放入口。

## 新增一份报告

1. 建目录 `reports/{slug}/`，放入内容文件（`report.md` / `index.html` / `deck.pptx`
   任一，文件名可自定义并在清单 `file:` 指定）。
2. 在 `registry.yaml` 登记：

   ```yaml
   reports:
     - slug: my-report          # 唯一标识，URL 为 /reports/my-report
       title: 我的报告标题
       date: 2026-10-12         # YYYY-MM-DD，列表按此倒序
       file: report.md          # 选填；缺省按默认名单探测
       summary: 一句话摘要       # 选填
       tags: [标签1, 标签2]      # 选填
       materials:               # 选填：参考材料（知识库笔记/链接），详情页展示
         - /knowledge/note?path=agent/xxx.md
         - https://example.com/source
   ```

3. 运行一次建站发布：任意「更新」按钮，或 CLI `.venv/bin/python -m collector --build`。

## 行为说明

- **容错**：清单缺字段 / 内容文件缺失 / 扩展名不支持 / slug 重复 / 目录不存在
  → 该条跳过并打印警告，**不阻断建站**（报告是内容数据，格式错误只影响自身）。
- **资源**：报告目录内除 `.md` 外的文件（图片、pptx、原样 html 及其配套资源）
  在建站时原样拷贝到 `dist/reports/{slug}/`；Markdown 正文相对路径自动重写。
- **清理**：从清单移除的报告，其 dist 页面与资源在下次建站时自动清理。
- **安全性**：Markdown 渲染与知识库同规则（bleach 白名单清洗、图片懒加载）；
  HTML 报告为作者自备内容，同源内嵌（仅放可信内容）。

## 技术实现

| 环节 | 位置 |
|---|---|
| 加载/校验/渲染/资源拷贝 | `collector/reports.py` |
| 建站接线 | `collector/sitegen.py → reports.build(env, common)` |
| 模板 | `templates/reports_index.html`、`templates/report_detail.html` |
| 撰写技能（骨架） | `skills/report-writing/SKILL.md` |
| 测试 | `tests/test_reports.py` |

## Roadmap

- [ ] PPT 网页化在线预览（pptx → HTML 幻灯片）
- [ ] 报告与 DSH 工作流打通：主题 + 材料（知识库等）→ 初稿
- [ ] 报告目录（TOC）侧栏、全屏演示模式
- [ ] PDF 报告形态、报告内引用站内榜单/新闻数据的联动组件
