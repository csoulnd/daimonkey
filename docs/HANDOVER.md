---
title: "开发交接文档(三方向 M1-M4)"
type: reference
status: active
---

# 开发交接文档:三方向(Harness/FM/NV)开发

> 面向接手开发的工程师。目标:不依赖任何口头上下文,凭本文档+仓库内文档即可开工。
> 阅读顺序:本文 → [AGENTS.md](../AGENTS.md)(工作流铁律)→ [DESIGN-三方向.md](DESIGN-三方向.md)(需求与设计)→ 代码。

## 1. 项目现状速览

- **站点**:InferTech 观察站(品牌 daimonkey),门户+模型评测榜+智能体热度榜+AI 资讯+登录后个人工作台(报告厅/按用户隔离知识库/悬浮助手)。
- **位置**:服务器目录 `/home/daimonkey`;仓库 `github.com/csoulnd/daimonkey`(main);服务 `systemd daimonkey`(端口 8080,`systemctl restart daimonkey`)。
- **账号**:唯一账号 `dai`(需求方)。CLI `userctl add/passwd/repo/list`(任意目录)。
- **健康基线**:110 unittest + 2 node 测试全过;`scripts/verify.py` 33/33;serve RSS ~27MB/空闲线程归零。

## 2. 架构要点(动手前必读)

1. **静态站 + 本地服务**:Jinja2 模板(`templates/`)→ `collector/sitegen.py` 构建 → `dist/`;
   `scripts/serve.py` 服务 dist + 动态端点(登录/更新/助手/知识库搜索/防护)。
   **改模板/静态资源必须重建**:`.venv/bin/python -m collector --build`(资源指纹 ?v= 自动失效缓存)。
2. **registry.yaml 是心脏**:产品清单/榜单权重/数据源/新闻规则/助手配置全在此;
   三方向频道化就是给它加 `directions` 段(见 DESIGN 2.1),**组织形式不得硬编码进模板**。
3. **数据管线**:source(一源一模块,`collector/sources/`)→ metrics → `scoring.compute_all`
   (分位归一)→ rankings → sitegen/export。**三条铁律**(都是踩过的坑):
   - 逐源独立短事务,严禁整轮长事务锁库;
   - AI 调用(`ai.score_boards_safe`)在主事务之外执行;
   - 库内路径一律相对(`raw/...`),严禁绝对路径(verify 有防回归检查)。
4. **助手运行时**:PIAgent 一次性进程(`collector/piagent.py`,pi CLI 全局安装,`--no-tools`);
   密钥 `data/assistant.env`(DEEPSEEK)、`data/model_eval.env`(AA),均 600 不进 git。
5. **前端无构建链**:原生 JS(IIFE)。登录态显隐走 localStorage + head 预载 `html.authed` 类
   (防 FOUC 模式,见 base.html 头部与 assistant.js showUser),新功能照抄该模式。

## 3. 你的任务:三方向 M1-M4

设计与默认决策全在 **[DESIGN-三方向.md](DESIGN-三方向.md)**(六项默认值需求方未否决即按默认执行,
翻转任一项需同步该文档)。里程碑:

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M1 | 频道化骨架:registry `directions` 配置 + `/dir/{方向}` 频道页(新闻/榜单/周总结三区)+ 门户改三方向入口 + NV 空页 | 增删方向只改配置;旧路由跳转正确;NV 页有说明与数据源规划清单 |
| M2 | Harness 甄别重构:产品加 `tier: core\|eco`,核心榜/生态榜分层,现联合榜并入 | MCP/n8n 类不再与产品混排;迁移说明写入频道页"收录说明" |
| M3 | AA 接入:`collector/sources/aa.py`(httpx!见下表),与 SuperCLUE 双口径并列展示,标注来源 | 模型榜出现 AA 列;溯源 raw 归档;周更不超额 |
| M4 | 方向周总结:周报管线按 `news_tag` 参数化,AI 生成+人工可改 | 每频道有当周总结;全站周报保留 |

## 4. 数据通道实测结论(全部本机验证过)

| 通道 | 状态 | 关键事实 |
|---|---|---|
| SuperCLUE | ✅ 在用 | PDF→pypdf 抽图→pi 多模态读榜(分上下两半合并+完整性校验);月更改 registry base_url 跑 `scripts/fetch_superclue.py` |
| **AA** | ✅ 已通 | `GET https://artificialanalysis.ai/api/v2/language/models/free`,头 `x-api-key`,key 在 `data/model_eval.env`;**必须 httpx(10s),curl 在此线路必截断**;free 层 200 模型(指数 v4.3/编码/九域/定价/性能),限额 1000 次→周更 |
| OpenRouter | ✅ 可达未接 | `openrouter.ai/rankings`(使用量份额),数据端点需 spike;用途=定关注范围 |
| LMArena / HuggingFace / code-arena / decode.ai | ❌ 断连 | 不接;列可留位 |

## 5. 开发约定(AGENTS.md 摘要+血泪补充)

- **专家先行**:任务开始选/建 `skills/<专家>/SKILL.md`,结束把决策沉淀回去(仓库硬约定);
  计划文档:PLAN-deploy(部署,已完成)、DESIGN-三方向(你的)。
- **测试必须隔离 DB**:`patch.object(db, "DB_PATH", 临时文件)`——曾有测试把弱密码账号写进生产库的事故。
- 每阶段:测试+verify 全绿 → git commit(推送 main)→ 更新对应 skill 与 PLAN 状态。
- 禁区:`data/` 结构不动、`dist/` 不手改、密钥不进 git、绝对路径不进代码与库。
- 前端改动跑 `node --check`;登录态相关功能先看 base.html 头部预载脚本再动手。

## 6. 常用命令

```bash
cd /home/daimonkey
.venv/bin/python -m collector --build          # 重建站点(改模板/静态后)
.venv/bin/python -m collector --update         # 全量更新
.venv/bin/python -m unittest discover -s tests # 测试
.venv/bin/python scripts/verify.py             # 站点验收
systemctl restart daimonkey                    # 重启服务(serve.py 改动后)
userctl list                                   # 账号管理
deploy/backup.sh                               # 备份(动 data 前先跑)
```

## 7. 求助路径

排障先查 `skills/` 对应专家文档(负载优化/UI/数据更新/新闻/模型评测/工作台/报告撰写);
历史决策记录在各专家"决策记录"段与 git log。需求歧义时找需求方(只答产品问题,不答技术问题);
默认值翻转以 DESIGN 文档 1.4 表为准。
