---
title: "工程提升计划:安装部署交付件与可搬迁性"
type: design
status: done
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

## 验收决议(2026-10-18)

需求方豁免正式搬迁/重启演练(D6 仅保留 smoke.sh 脚本本身供日后自愿执行);D1-D5 已由开发完成并随仓库合入。

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

## 完成状态(2026-10-17)

六项全部落地,生产服务已从 nohup 迁移到 systemd。换机流程收敛为:**旧机 `deploy/backup.sh` → 新机 `git clone` → `bootstrap.sh --restore` → 安全组放行 → `deploy/smoke.sh --all`**。

| 工单 | 状态 | 交付/证据 |
|---|---|---|
| D1 相对路径 | ✅ | `collector/config.py::resolve_data_path`;`db.save_raw` 只写相对路径;`scripts/migrate_relpath.py`;生产库 1104 条绝对路径已迁移(0 缺失);`verify.py` 新增库内路径/硬编码防回归 |
| D2 bootstrap | ✅ | `deploy/bootstrap.sh`(系统依赖/pi 1.3.0/venv/restore/迁移建库建站/userctl/systemd);幂等复跑通过;新目录恢复演练通过 |
| D3 进程托管 | ✅ | `deploy/daimonkey.service`(Restart=always、开机自启、journal、无采集定时器);生产 `enabled + active`,重启演练主进程更换且 20s 内 200 |
| D4 备份恢复 | ✅ | `deploy/backup.sh`:SQLite `.backup` 热备 + integrity_check、`data/` 关键内容、manifest/SHA256SUMS;实测 39MB/1067 文件,恢复后账号数一致、溯源完整 |
| D5 环境声明 | ✅ | `docs/deploy.md`(依赖矩阵/安装/换机/GitHub SSH key/排障);`check_env.sh` 增加 pi、Node≥18、sqlite3 CLI、GitHub SSH、systemd 单元、交付件检查 |
| D6 验收 | ✅ | `deploy/smoke.sh --all` 23/23 全绿（在线 9、搬迁 10、重启 4）;`verify.py` 42/42(含部署防回归);Python 117 测试 + node 测试全绿 |

实施中发现并修复的两个额外问题:

1. **bootstrap 工作目录**:从其他目录调用时 `python -m collector` 会误用调用者目录的代码/数据;已在脚本开头 `cd "$ROOT"` 并规范相对参数路径,由搬迁演练回归。
2. **依赖漂移**:新 venv 装到 Markdown 3.11.1 后中文 `**研究判断：**` 不再渲染为 strong;`requirements.txt` 改为精确版本锁定,升级需跑全量测试 + verify。

新增文件:`deploy/{bootstrap.sh,backup.sh,smoke.sh,daimonkey.service}`、`scripts/migrate_relpath.py`、`tests/test_deploy.py`、`docs/deploy.md`、`skills/deployment/SKILL.md`。运维后续只需按 `docs/deploy.md` 执行;GitHub SSH key 属环境事项,无 key 时知识库同步降级、站点不受影响。
