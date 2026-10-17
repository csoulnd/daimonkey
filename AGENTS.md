---
title: "仓库助手工作约定(专家/skill 为核心)"
type: reference
domain: repository
status: active
---

# 仓库助手工作约定(专家/skill 为核心)

本文件适用于整个仓库,是所有开发工作的入口约定。**任何开发任务开始前与结束后都必须经过专家(skill)的选择、创建或更新**——这是本仓库的组织核心,不是可选项。

## 仓库定位

InferTech 观察站(原智能体热度指数):以推理技术为重心,采集 Agent 生态数据、构建可溯源热度榜单,静态建站(`dist/`)+ 本地服务(`scripts/serve.py`)。技术栈为 Python 3.11+ / Jinja2 / 原生 JS / SQLite,无 Node 前端构建链。目标运行规格:**最多 10 人并发访问 / 20 人同时在线**(单机 3.3GB 内存),所有性能与安全上限按此规格设定。

## 专家工作流铁律(每个任务必经)

1. **开始前**:在下表选择负责该领域的专家,读其 `SKILL.md` 再动手;没有覆盖该领域的专家时,先按现有格式新建一个(哪怕初稿只有骨架),再开始任务。
2. **执行中**:遵循该专家的 runbook 与约束;发现专家文档与代码现实不符时,以代码为准并在结束时修正文档。
3. **结束后**:把本次任务产生的新知识沉淀回对应专家——决策及理由、踩过的坑、新的验证命令。**没有更新专家的任务视为未完成。**
4. 跨领域任务:选定一个主责专家,其余专家只在被触及时追加备注,不重复记录。

## 当前迭代计划

见 [PLAN.md](PLAN.md)——任务按表推进,完成后更新状态,不得丢弃。

## 专家索引(skills/)

| 专家 | 路径 | 适用场景 |
|---|---|---|
| 负载优化专家 | [skills/load-optimization/SKILL.md](skills/load-optimization/SKILL.md) | 内存/CPU 治理、进程审计、运行时选型、并发规格、限流 |
| UI 美化专家 | [skills/ui-polish/SKILL.md](skills/ui-polish/SKILL.md) | 页面视觉、设计令牌、图标系统、多模态截图美化循环 |
| 数据更新专家 | [skills/data-update/SKILL.md](skills/data-update/SKILL.md) | 数据更新链路、采集排障、更新按钮与状态 |
| 新闻汇总专家 | [skills/news-digest/SKILL.md](skills/news-digest/SKILL.md) | 新闻源、分类规则、周报、AI 摘要工作流 |
| 产品展示专家 | [skills/product-showcase/SKILL.md](skills/product-showcase/SKILL.md) | showcase 报告引擎与模板 |
| 报告撰写专家 | [skills/report-writing/SKILL.md](skills/report-writing/SKILL.md) | 报告厅内容撰写与登记 |
| 模型评测专家 | [skills/model-eval/SKILL.md](skills/model-eval/SKILL.md) | 模型 benchmark 榜单:数据源接入/评分设计/官方链接 |
| 个人工作台专家 | [skills/workspace/SKILL.md](skills/workspace/SKILL.md) | 工作台分区/用户隔离/自配知识库仓库 |

专家文档格式:frontmatter 仅 `name` + `description`(含"适用场景:"触发语);正文按 职责边界 → runbook/约束 → 常见调整表 → 排障表 → 决策记录(带日期)组织。

## 目录与内容归属

| 路径 | 内容 | 主责专家 |
|---|---|---|
| `collector/` | 核心 Python 包(采集/评分/建站/助手) | data-update、load-optimization |
| `scripts/` | serve.py / userctl / verify / 同步脚本 | load-optimization、data-update |
| `templates/` `static/assets/` | 页面模板 / 样式与 JS | ui-polish |
| `skills/` | 专家文档(本约定的主体) | 全部 |
| `reports/` | 报告内容(进 git) | report-writing、product-showcase |
| `tests/` | 单元测试(unittest + node) | 被测模块的专家 |
| `data/` | SQLite / 密钥 / 快照(**不入库**) | — |
| `dist/` | 构建产物(**不手改**,重建生成) | — |

## 修改纪律

- 先 `git status --short`,保留用户已有修改;`dist/`、`data/` 不入库、不手改。
- 密钥与密码只进 `data/`(600 权限),禁止写入代码、registry 或文档。
- 改模板/静态资源后必须 `.venv/bin/python -m collector --build` 重建才能生效。
- 新增依赖需在专家文档记录理由与体积影响;本仓库克制依赖(见 load-optimization 预算表)。

## 验证与交付

```bash
cd /home/agent-heat-index
.venv/bin/python -m unittest discover -s tests          # Python 全量
node tests/test_knowledge_search.cjs && node tests/test_knowledge_lazy.cjs
.venv/bin/python scripts/verify.py                      # 页面/结构/体积预算(更新后建议)
```

交付时说明:改动的文件、对应专家的更新内容、实际执行过的验证;只报告真实跑过的命令。
