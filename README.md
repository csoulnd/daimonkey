# InferTech 观察站(daimonkey)

以推理技术为重心的技术观察站:**模型评测榜、智能体热度榜、AI 资讯**,以及登录后的**个人工作台**(报告厅、按用户隔离的个人知识库)。每个数字都能溯源到原始数据。品牌:[daimonkey「大马猴」](brand/daimonkey.md)。

## 功能一览

| 板块 | 入口 | 说明 |
|---|---|---|
| 门户主页 | `/` | 各板块 TOP 汇总,一屏看全站 |
| 模型评测 | `/rankings/model` | SuperCLUE 中文基准(月更),API 与开源一起排、开源另有分榜,链接 ModelScope/官网,叠加 AI 复评 |
| 智能体热度 | `/rankings/unified` | GitHub/npm/PyPI/应用商店等多源数据 + AI 复评的融合排名 |
| AI 资讯 | `/news` | 公开渠道聚合,提到榜上产品自动关联,每周周报 |
| 个人工作台 | 登录后侧栏 | 报告厅(`/reports`)、个人知识库(`/knowledge`,每个账号可配自己的 GitHub 仓库)、全站悬浮智能助手 |

匿名可浏览公开板块;工作台内容登录后可见。

## 快速开始

```bash
cd /home/agent-heat-index
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # 国内可加 -i 清华镜像

.venv/bin/python -m collector --init-db     # 首次建库
.venv/bin/python -m collector --update      # 采集→评分→导出→建站
.venv/bin/python scripts/serve.py --host 0.0.0.0   # http://<IP>:8080
```

无任何自动调度:全站手动更新(页面「⟳ 更新」按钮或 CLI),页面显著展示数据截至时间。对外访问需在云安全组放行 TCP 8080。

## 账号与权限

```bash
userctl add <用户名>          # 建号(交互输密码,≥8 位;任意目录可执行)
userctl passwd / list / disable / enable <用户名>
userctl repo <用户名> <github地址|->   # 配置该用户的知识库仓库(网页内也可配)
```

- 登录:右上角「登录」→ `/login`;登录后右上角头像菜单可退出
- 权限动作(数据更新/知识库同步/助手)登录即可用;首个账号创建后旧单一密码自动退役
- 知识库用户隔离:登录后知识库页顶部「我的知识库」卡配置自己的仓库,点同步生成专属页面;未配置走默认 [InferTechNote](https://github.com/csoulnd/InferTechNote)

## 常用命令

```bash
.venv/bin/python -m collector --update         # 全量更新(含模型榜源)
.venv/bin/python -m collector --collect-news   # 仅新闻(约 1 分钟)
.venv/bin/python scripts/fetch_superclue.py    # 模型榜月更(改 registry 报告地址后)
.venv/bin/python -m unittest discover -s tests # 测试
.venv/bin/python scripts/verify.py             # 站点验收
```

## 目录结构

```
collector/    核心 Python 包(采集/评分/建站/PIAgent 助手/账号)
scripts/      serve.py(服务+防护)/ userctl.py / fetch_superclue.py / sync_knowledge.py
templates/    页面模板   static/assets/  样式与前端 JS
skills/       专家文档   AGENTS.md  开发工作流铁律   PLAN.md  迭代计划
reports/      报告内容(进 git)
data/         SQLite/密钥/快照(不入库)   dist/  构建产物(不手改)
```

## 深入了解

- **开发约定与专家体系**:根 [AGENTS.md](AGENTS.md)——任何开发任务先选专家、结束沉淀回专家文档;专家索引在 `skills/`(负载优化/UI 美化/数据更新/新闻汇总/模型评测/个人工作台/报告撰写)
- **当前迭代计划**:[PLAN.md](PLAN.md)
- **评分口径**:站内各榜页面「评分说明」折叠块;AI 参与的环节在页面均有标注
- **助手运行时**:PIAgent 一次性进程(pi.dev),无常驻内存;配额与护栏见 `registry.yaml → assistant`
- **数据保留**:原始响应/时序/快照按 LRU 窗口滚动(`registry.yaml → retention`),溯源链永久不断

## 安全要点

- 密钥只进 `data/`(600 权限):`assistant.env`(DEEPSEEK_API_KEY);密码哈希存 SQLite(pbkdf2)
- 防护按 10 并发/20 在线规格:每 IP 限流、在途上限、连接超时、看门狗;详见 `skills/load-optimization`
- 数据抓取遵守源站限频与退避;每条数据保留原始响应可溯源
