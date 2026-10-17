---
title: "工程提升计划:安装部署交付件与可搬迁性"
type: design
status: draft
---

# 部署交付件与可搬迁性改造计划

## 问题定义(2026-10-17 目录更名实战暴露)

"交付件换一台服务器就炸"的根因,按严重度:

| # | 雷点 | 现状 | 后果 |
|---|---|---|---|
| 1 | **溯源绝对路径** | raw_payloads.body_path 存 `/home/...` 绝对路径(1104 条,本次手工 SQL 迁移) | 换机/改目录即断溯源链 |
| 2 | **无安装交付件** | 新服务器需手工:venv/pip、npm 装 pi、建库、构建、装 userctl、nohup 起服务 | 不可复制,步骤靠记忆 |
| 3 | **进程无托管** | serve.py 用 nohup 裸跑 | 重启机器服务不来自启,崩了无人拉起 |
| 4 | **无备份/恢复** | 用户账号、密钥、知识库快照、溯源归档散在 data/,无打包工具 | 换机=丢数据 |
| 5 | **环境依赖未声明** | node/pi 版本、GitHub SSH key(知识库同步依赖)散落各文档 | 新机缺件运行时报错 |
| 6 | 文档硬编码路径 | 已修,但无防回归检查 | 未来改动再引入 |

## 改造方案(六项,按依赖排序)

### D1 路径自愈(治本,其他项的地基)
- `db.load_body` 等改为:**库内只存相对路径**(`raw/github/20260923/<hash>.json.gz`),读取时拼 `DATA_DIR`
- 一次性迁移:`scripts/migrate_relpath.py`(绝对→相对,幂等,可重复跑)
- 防回归:`verify.py` 增加检查——库内 body_path 不允许以 `/` 开头;仓库代码 grep 禁止 `/home/daimonkey` 硬编码(白名单:部署脚本模板)

### D2 安装交付件(bootstrap)
- 新增 `deploy/bootstrap.sh`,目标:**全新 Ubuntu/Debian 服务器一条命令拉起**
  1. 系统依赖:python3(≥3.11)、node(≥18)、git、sqlite3
  2. `npm i -g @earendil-works/pi-coding-agent@1.3.0`(版本锁定)
  3. venv + `pip install -r requirements.txt`
  4. `--init-db` + 首次构建(`--build`)
  5. 生成 `/usr/local/bin/userctl` 包装(按实际安装路径写入)
  6. 安装 systemd 单元(见 D3)
  7. 幂等:重复执行安全;`--restore <backup.tar.gz>` 走恢复流程(见 D4)
- 交付物=git 仓库本身(`git clone` 后跑 bootstrap),不再另打包

### D3 进程托管
- `deploy/daimonkey.service`(systemd):serve.py、`Restart=always`、开机自启、输出到 journal
- 保持"数据无自动调度"的产品约定:只托管服务进程,不加采集定时器
- bootstrap 自动 `systemctl enable --now`

### D4 备份与恢复(换机闭环)
- `deploy/backup.sh`:`sqlite3 .backup` 热备 + 打包 data/ 关键内容(主库、assistant.env/secret、知识库快照、raw 归档、update 状态),**排除** dist(可重建)与 cache
- 换机标准流程(写进 docs/deploy.md):
  `新机 git clone → bootstrap.sh --restore backup.tar.gz → 安全组放行 8080 → 验证`
- 附 GitHub SSH key 迁移说明(知识库同步依赖;无 key 则该功能降级,服务不炸)

### D5 环境声明与自检
- `docs/deploy.md`:前置依赖矩阵(OS/python/node/pi/git-ssh/端口)
- `scripts/check_env.sh` 扩展:检查 pi 可执行、node 版本、github SSH 可达(`ssh -T git@github.com`)

### D6 验收(定义"不炸")
- **搬迁演练**(在本机模拟):备份→新目录 clone+bootstrap --restore→全绿清单:
  服务 200、登录可用、账号在、溯源链抽验文件命中、知识库快照在、110 测试+verify 通过
- **重启演练**:systemd 重启服务自愈
- 以上两条作为 bootstrap 的自测脚本(`deploy/smoke.sh`)固化

## 工作量与顺序

D1(0.5d)→ D2+D3(1d)→ D4(0.5d)→ D5+D6(0.5d);合计约 2.5 个工作日,全部完成后"换服务器"收敛为三条命令。

## 任务派发(工单视图)

通用约定(每个工单都适用):先读根 [AGENTS.md](../AGENTS.md) 专家流程;`data/` 只读不改结构、`dist/` 不手改、密钥不进 git;改完跑 `unittest + verify.py` 全绿;完成沉淀回本文件与对应专家 skill。

| 工单 | 内容 | 可派发性 | 前置 | 验收标准 | 禁区 |
|---|---|---|---|---|---|
| D1 | 相对路径改造 + migrate_relpath.py + verify 防回归 | ★★★(需懂 db.py,建议留评审) | 无 | 库内 body_path 无 `/` 开头条目;溯源页抽验 200;测试全过 | 不得变更表结构语义,只动路径表达 |
| D2 | deploy/bootstrap.sh(含 --restore 钩子) | ★★★★★ 独立 | D1 | 全新目录模拟新机执行成功,幂等重跑安全 | 不动 collector/ 业务代码 |
| D3 | systemd 单元 + 开机自启 | ★★★★★ 独立 | 无 | 重启机器服务自愈;journal 可查日志 | 不加采集定时器(产品约定:手动更新) |
| D4 | backup.sh + 恢复流程 | ★★★★ | D2 | 备份→恢复→账号/溯源/知识库完整 | 备份必须排除 dist 与 cache |
| D5 | docs/deploy.md + check_env 扩展 | ★★★★★ 独立 | 无 | 新人按文档可独立完成部署 | — |
| D6 | smoke.sh 搬迁+重启演练 | ★★★★ | D1-D4 | 两条演练全绿并纳入 verify | — |

依赖关系:D1 → D2 → D4 → D6;D3/D5 可并行任意插空。单人全做约 2.5 天,两人并行(D1 一人,D2/D3/D5 一人)约 1.5 天。

**接手人所需访问**:服务器 SSH、github.com/csoulnd/daimonkey 仓库写权限;知识库同步功能的联调需 GitHub SSH key(没有则跳过该项联调)。
