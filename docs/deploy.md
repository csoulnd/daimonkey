---
title: "部署与搬迁手册"
type: reference
status: active
---

# 部署与搬迁手册

本项目的交付件就是 **git 仓库本身**：新服务器 `git clone` 后执行 `deploy/bootstrap.sh`，不再另外打包。日常换机收敛为三条命令：**clone → bootstrap（可带备份恢复）→ 安全组放行端口**。

## 1. 前置依赖矩阵

| 项 | 要求 | 说明 / 自检 |
|---|---|---|
| 操作系统 | Ubuntu 24.04+ / Debian 12+，x86_64 | 更旧的系统 Python/Node 版本不足，需先升级 |
| Python | ≥ 3.11（含 venv/ensurepip） | `python3 --version`；bootstrap 会安装 `python3-venv` |
| Node.js | ≥ 18（含 npm） | `node -v`；pi 运行时依赖 |
| pi | `@earendil-works/pi-coding-agent@1.3.0`（锁定） | bootstrap 用 npm 全局安装，复跑时版本不符会重装 |
| 系统工具 | git、sqlite3、curl、tar、sha256sum | bootstrap 自动 apt 安装（`--skip-system-deps` 可跳过） |
| 端口 | TCP 8080（可改） | 云厂商安全组/防火墙需放行；服务默认监听 `0.0.0.0` |
| 磁盘 | ≥ 10GB 可用 | raw 归档、知识库快照、备份都在本机 |
| 内存 | 建议 3.3GB；最低 2GB | 规格 10 并发 / 20 在线，护栏见 `skills/load-optimization` |
| GitHub SSH key | 可选 | 知识库同步走 `git@github.com:...`；没有 key 时该功能失败但站点不受影响 |
| AI 密钥 | 可选 | `data/assistant.env` 内的 `DEEPSEEK_API_KEY`；缺失时助手/AI 复评降级 |
| systemd | 建议可用 | bootstrap 安装并 `enable --now`；容器内无 systemd 时用 `--no-service` 手动起 |

> 当前对外仅提供 HTTP（8080），未启用 HTTPS。

## 2. 首次安装（全新服务器）

```bash
git clone <仓库地址> <安装目录>
cd <安装目录>
sudo bash deploy/bootstrap.sh                      # 默认 0.0.0.0:8080
# 或指定端口/运行用户：
sudo bash deploy/bootstrap.sh --host 0.0.0.0 --port 8080 --run-as www-data
```

bootstrap 的七个步骤（每步幂等，可重复执行）：

1. 系统依赖：Python ≥3.11、Node ≥18、git、sqlite3、curl；缺什么装什么。
2. pi 运行时：锁定安装 `@earendil-works/pi-coding-agent@1.3.0`。
3. `.venv` + `pip install -r requirements.txt`。
4. 可选恢复：`--restore <backup.tar.gz>`（见第 4 节）。
5. 数据库：旧库自动跑 `scripts/migrate_relpath.py` → `--init-db`；`dist/` 不存在则 `--build`。
6. 安装 `/usr/local/bin/userctl`（包装器写入实际安装路径，任意目录可执行）。
7. 渲染并安装 `/etc/systemd/system/daimonkey.service`，`enable --now`，做 HTTP 健康检查。

完成后：

```bash
userctl add <用户名>                 # 交互式建号（≥8 位密码）
curl -I http://127.0.0.1:8080/       # 200
systemctl status daimonkey           # active (running)
journalctl -u daimonkey -f           # 实时日志
```

> 服务运行用户默认取 `sudo` 发起者（或当前用户）。bootstrap 会把 `data/`、`.venv/`、`dist/` 的属主改为该用户，确保服务可读写。

常用参数：

| 参数 | 作用 |
|---|---|
| `--host` / `--port` | 监听地址与端口 |
| `--run-as USER` | systemd `User=`（默认 sudo 发起者） |
| `--restore FILE` | 从备份恢复数据后建站 |
| `--force` | 覆盖非空 `data/`（旧目录改名为 `data.pre-restore-<时间>`） |
| `--rebuild` | 强制重建 `dist/` |
| `--no-service` | 不写 systemd、不启动（搬迁演练/调试） |
| `--bin-dir DIR` | `userctl` 安装目录（演练时指向临时目录） |
| `--skip-system-deps` | 跳过 apt（依赖已手工就绪） |

## 3. 服务管理

```bash
systemctl status daimonkey
systemctl restart daimonkey          # 重启（Restart=always，崩溃自动拉起）
systemctl stop daimonkey
journalctl -u daimonkey -n 100       # 最近日志
journalctl -u daimonkey --since "-10 min"
```

- 单元文件源：`deploy/daimonkey.service`（占位符由 bootstrap 渲染，目标 `/etc/systemd/system/daimonkey.service`）。
- 只托管常驻的 `serve.py`；**不加采集定时器**（产品约定：数据手动更新）。
- `git pull` 更新代码后需 `sudo systemctl restart daimonkey` 才会加载新代码；bootstrap 在单元无变化时不会重启服务（避免误杀进行中的更新）。先看 `GET /api/v1/update/status` 是否为 running。
- 更新子进程日志仍在 `data/logs/update-*.log`；看门狗 25 分钟超时。
- 无 systemd 的环境：`scripts/serve.sh --host 0.0.0.0 --port 8080` 前台运行。

## 4. 换服务器（备份 → 恢复闭环）

### 4.1 旧机备份

```bash
deploy/backup.sh                       # 默认输出到 ~/daimonkey-backups/
deploy/backup.sh --out /var/backups    # 指定目录
```

备份内容：

- SQLite 热备（`sqlite3 .backup`，含 WAL 中未落盘数据；`integrity_check` 校验）
- `data/` 关键内容：`assistant.env`、`assistant_secret`、`update_password.sha256`、`snapshots/`、`raw/`、`knowledge-repo/`、`knowledge-<用户>-repo/`、`knowledge_search*.json`、`update_status.json`
- `manifest.json`（格式版本、主机、git commit、行数统计）+ `SHA256SUMS`
- **排除**：`dist/`（可重建）、`data/cache/`（可再生）、`data/logs/`（诊断信息）
- 归档权限 600，内含账号哈希与密钥，按敏感文件保管

### 4.2 新机恢复

```bash
# 1. 新机拉代码
git clone <仓库地址> <安装目录> && cd <安装目录>

# 2. 恢复 + 安装 + 启动（备份需先 scp 到新机）
sudo bash deploy/bootstrap.sh --restore /path/to/daimonkey-backup-YYYYmmdd-HHMMSS.tar.gz

# 3. 安全组放行 TCP 8080；4. 验证
deploy/smoke.sh --all
```

恢复流程会：校验归档路径安全与 SHA256 → 非空 `data/` 需 `--force`（自动改名保留）→ 复制数据 → 跑相对路径迁移 → 建库 → 重建站点 → 启动服务。**旧备份里的绝对路径会被自动迁移**，无需手工 SQL。

### 4.3 GitHub SSH key 迁移（知识库同步）

知识库同步拉取 `git@github.com:csoulnd/InferTechNote.git`（以及各用户自配仓库）：

```bash
# 旧机
ls ~/.ssh/                        # id_ed25519 / id_rsa / known_hosts
# 新机（复制到服务运行用户的家目录，权限 600/700）
scp ~/.ssh/id_* ~/.ssh/known_hosts <新机>:~/.ssh/
chmod 700 ~/.ssh && chmod 600 ~/.ssh/id_* ~/.ssh/known_hosts
ssh -T git@github.com             # 看到 "successfully authenticated" 即可
```

没有 key 时：知识库同步报错、其他功能（榜单/新闻/账号/溯源）不受影响。HTTPS git 在某些网络被阻断，代码默认走 SSH；如需更换地址，用 `KNOWLEDGE_GIT`/`KNOWLEDGE_URL` 环境变量覆盖（见 `collector/knowledge.py`）。

## 5. 数据更新（保持手动）

```bash
scripts/update.sh                  # 全量：采集→评分→LRU→导出→建站
.venv/bin/python -m collector --collect-news
.venv/bin/python scripts/sync_knowledge.py
```

网页右上角「⟳ 更新」等价于全量更新（需登录账号）。**默认安装不启用 cron/定时器**，更新仍可手动触发；如需自动调度，用 `deploy/install-timers.sh` 安装分级 systemd timer（见 §12）。LRU 窗口见 `collector/registry.yaml → retention`。

## 6. 备份策略

- 手动：`deploy/backup.sh`；建议每次大更新后、换机前各做一次。
- 自动（可选）：`deploy/install-timers.sh` 会安装 `daimonkey-backup.timer`（每周一 06:00，写 `/var/lib/daimonkey-backups`，systemd 自动创建并归 `daimonkey` 用户）。安装脚本与调度细节见 §12。
- 恢复包必须与代码版本大致匹配（manifest 记录 git commit）；跨版本恢复后先跑 `deploy/smoke.sh`。

## 7. 自检与验收

```bash
bash scripts/check_env.sh                 # 环境/数据源/systemd/pi/SSH 自检，报告 env_report.md
.venv/bin/python scripts/verify.py        # 页面/溯源/体积 + 部署防回归（相对路径、硬编码、交付件）
deploy/smoke.sh                           # 在线检查（服务/账号/溯源/知识库/测试/verify）
deploy/smoke.sh --all                     # + 搬迁演练 + systemd 重启演练（D6 验收）
```

`smoke.sh --relocation` 会：备份 → 新目录 `bootstrap --restore --no-service`（临时端口）→ 临时实例全检（首页、账号数、登录、溯源、知识库、单测、verify）→ 清理。`--restart` 会 `systemctl restart` 并检查进程更换、服务自愈、journal 可查。

## 8. 可搬迁性约定（防回归）

- `raw_payloads.body_path` **只存相对 `DATA_DIR` 的路径**（`raw/<源>/<日期>/<hash>.json.gz`），读取时拼当前数据目录；旧绝对路径用 `scripts/migrate_relpath.py` 迁移（幂等）。
- 禁止在代码/文档中硬编码具体安装目录；`verify.py` 会扫描仓库文本文件（白名单仅 `docs/PLAN-deploy.md` 的历史记录）并在库内发现绝对路径时失败。
- `data/` 是唯一可变数据目录；`dist/` 可随时重建；部署脚本只写 `data/`、`dist/`、`.venv/` 与 systemd 单元。
- `dist/api/v1/raw` 只导出 `collector/registry.yaml → retention.raw_api_days`（默认 14 天）内的原始响应；全量清单在 `dist/api/v1/raw/index.json`（id/来源/时间/大小/是否导出），更早条目仍走 `dist/raw/<id>.html` 溯源页。
- 密钥只进 `data/`（600），不进 git、不进镜像、不进文档。

## 9. 排障

| 症状 | 排查 |
|---|---|
| bootstrap 报 Python < 3.11 | 升级到 Ubuntu 24.04+/Debian 12+，或手工装新版 Python 后加 `--skip-system-deps` |
| 报 Node < 18 | 升级 Node（≥18）后重跑；旧发行版 apt 源版本不足 |
| 端口被占用，服务起不来 | `ss -ltnp \| grep 8080`；常见为旧 `nohup serve.py`，停止后重跑 bootstrap |
| 服务 active 但页面 502/超时 | 等构建完成（`data/update_status.json`）；看 `journalctl -u daimonkey` |
| 知识库同步失败 | `ssh -T git@github.com`；确认服务用户有 key、remote 为 SSH |
| 恢复后溯源 404/断链 | 跑 `scripts/migrate_relpath.py`；`verify.py` 第 5 节会列出缺失文件 |
| 备份报 sqlite3 不可用 | 安装 `sqlite3` CLI；脚本自带 Python 备份 API 兜底 |
| 重启后未自启 | `systemctl is-enabled daimonkey`；未 enabled 则 `sudo systemctl enable --now daimonkey` |

## 10. 文件清单

| 文件 | 作用 |
|---|---|
| `deploy/bootstrap.sh` | 一条命令安装/恢复/托管 |
| `deploy/backup.sh` | 热备 + 数据打包（排除 dist/cache） |
| `deploy/smoke.sh` | 在线检查 + 搬迁/重启演练 |
| `deploy/daimonkey.service` | systemd 单元模板（bootstrap 渲染） |
| `deploy/pi-ext/ops-guard.ts` | 运维 agent 的 pi 工具策略扩展（拦截/审批/审计） |
| `deploy/pi-ext/ops-policy.json` | 路径/命令/审计策略单一事实源 |
| `deploy/sudoers.d/daimonkey` | 服务用户最小 sudo 授权（仅延时重启 wrapper） |
| `deploy/ops-rollback.sh` | 降权失败时回滚为 root 服务 |
| `deploy/daimonkey-news.timer/.service` | 每日新闻采集定时器与单元（可选，见 §12） |
| `deploy/daimonkey-update.timer/.service` | 每周全量更新定时器与单元（可选，见 §12） |
| `deploy/daimonkey-backup.timer/.service` | 每周备份定时器与单元（可选，见 §12） |
| `deploy/install-timers.sh` | 渲染并启用分级定时任务（支持 `--dry-run`） |
| `scripts/opsctl.py` | 运维 CLI：status/logs/restart/deploy-check |
| `scripts/migrate_relpath.py` | body_path 绝对→相对迁移（幂等） |
| `scripts/check_env.sh` | 环境与依赖自检 |
| `scripts/verify.py` | 站点验收 + 部署防回归 |

## 11. 服务降权与运维 agent（2026-10-20）

站内运维 agent（`docs/OPS-AGENT.md`）会让 pi 在本机执行 read/write/edit/bash。
**在 root 服务上开工具 = 把 root shell 的审批面交给模型**，因此上线顺序固定为：
先降权，再开 `assistant.ops.enabled`。

### 11.1 一条命令降权

```bash
cd <安装目录>
sudo bash deploy/bootstrap.sh --run-as daimonkey --create-user
```

这条命令会：

1. 创建系统用户 `daimonkey`（home=仓库目录，不重建目录）；
2. `chown -R daimonkey:daimonkey <安装目录>`（含 `.git/.venv/dist/data`）；
3. 若 `$ROOT/.ssh` 不存在，从 `/root/.ssh` 复制并收紧权限（知识库同步用）；
4. 安装 `/usr/local/bin/daimonkey-restart`（root:root 0755，只做 `systemd-run` 延时重启）；
5. 安装 `/etc/sudoers.d/daimonkey`（0440，`visudo -cf` 校验；只授权固定 wrapper）；
6. 把 `daimonkey` 加入 `systemd-journal` 组（`opsctl logs` 读日志）；
7. 单元模板渲染 `User=daimonkey` / `Group=daimonkey`，`daemon-reload` 后重启。

### 11.2 验收

```bash
sudo -u daimonkey .venv/bin/python scripts/opsctl.py deploy-check   # 服务用户/属主/wrapper/sudoers
systemctl show daimonkey -p User -p Group --value
sudo -u daimonkey .venv/bin/python scripts/opsctl.py status
sudo -u daimonkey .venv/bin/python scripts/opsctl.py logs -n 50
```

全部通过后，再把 `collector/registry.yaml → assistant.ops.enabled` 改为 `true`，
`sudo systemctl restart daimonkey` 使配置生效（服务启动时加载 registry）。
前端只有 `role=admin` 且 `ops_enabled=true` 才会出现「⚙ 运维」模式开关。

### 11.3 回滚

```bash
sudo bash deploy/ops-rollback.sh          # User 改回 root 并重启
# 同时把 assistant.ops.enabled 改回 false 并重启，避免 root 下暴露工具
```

数据/仓库属主保持 `daimonkey` 不影响 root 服务；如需完全还原再 `chown -R root:root`。

### 11.4 边界与注意

- 审批/路径黑名单/命令分级/审计是**护栏，不是沙箱**；残余风险见 `docs/OPS-AGENT.md §7`。
- 运维会话仍受 `assistant.ops.daily_limit / daily_tokens` 配额与全局并发 1 限制。
- `userctl` 以 root 运行会写 SQLite（可能新建 root 属主的 `-wal/-shm`）；降权后请用
  `sudo -u daimonkey userctl ...`，或事后 `chown daimonkey:daimonkey data/heat_index.sqlite*`。
- 运维 agent 只能通过 `daimonkey-restart` 延时重启；直接 `systemctl restart` 会中断当前轮。


## 12. 定时任务（systemd timer，2026-10-21）

> 2026-09-23 的「无自动调度」是当时的默认；需求方 2026-10-21 明确要求分级定时更新后，仓库提供**可选**的 systemd timer 交付件。`bootstrap.sh` 不做任何改动，是否启用由运维单独执行安装脚本决定。

### 12.1 单元清单

| 单元 | 频率（本地时区） | 命令 | 说明 |
|---|---|---|---|
| `daimonkey-news.timer/.service` | 每日 06:30 | `.venv/bin/python -m collector --collect-news` | 轻量新闻采集，不重建全站 |
| `daimonkey-update.timer/.service` | 每周一 07:30 | `.venv/bin/python -m collector --update` | 全量：采集 → 评分 → LRU → 导出 → 建站 |
| `daimonkey-backup.timer/.service` | 每周一 06:00 | `deploy/backup.sh --out /var/lib/daimonkey-backups` | SQLite 热备 + `data/` 关键内容 |

- 三份 service 全部 `User=daimonkey` / `Group=daimonkey`，`WorkingDirectory=__INSTALL_ROOT__`；
- 模板里的 `__INSTALL_ROOT__` 由安装脚本渲染为当前仓库绝对路径（与 `daimonkey.service` 同一约定），换机/改目录后重跑安装脚本即可；
- service 均为 `Type=oneshot` + `Restart=no`：失败只记一次失败，等下一个 timer 周期，不会重启风暴；
- timer 均 `Persistent=true`：关机错过的周期在下次启动后补跑一次（仅补最近一次）。

### 12.2 安装 / 停用

```bash
sudo deploy/install-timers.sh --dry-run   # 只渲染并打印命令，不写 /etc、不碰 systemd
sudo deploy/install-timers.sh             # 安装 + daemon-reload + enable --now 三个 timer
systemctl list-timers --all 'daimonkey-*'
journalctl -u daimonkey-news -u daimonkey-update -u daimonkey-backup
sudo systemctl disable --now daimonkey-news.timer daimonkey-update.timer daimonkey-backup.timer
```

前置条件：已跑过 `bootstrap.sh`（存在 `daimonkey` 用户、`.venv/` 与 `data/heat_index.sqlite`）。安装脚本会在缺失时给出 WARN，但不会替你建库。

### 12.3 注意

- 定时任务与网页「⟳ 更新」共用同一把更新锁：任一进行中，另一个触发返回 409/等待，不会并发写库。
- 备份目录由 systemd `StateDirectory=daimonkey-backups` 管理（`/var/lib/daimonkey-backups`，0700，属主 `daimonkey`）；恢复仍走 `bootstrap.sh --restore`。
- 不要手改 `/etc/systemd/system/` 下的单元副本：改仓库模板后重跑 `install-timers.sh`。
- `deploy/smoke.sh` 不检查 timer（自动调度属可选运维项）；验收用 `systemctl list-timers` 与 journal。

## 13. 站点基址与安全 Cookie（环境变量，2026-10-21）

canonical、`sitemap.xml`、`robots.txt` 里的绝对 URL 与 Cookie 的 `Secure` 属性由两个环境变量控制，
不写进 `registry.yaml`——配置跟着部署走：

| 变量 | 作用 | 取值与时机 |
|---|---|---|
| `DM_SITE_URL` | canonical / `sitemap.xml` / `robots.txt` 的站点基址（`collector/seo.py → site_base_url`） | **生产必须设置**，本站用公网 IP：`http://106.12.30.116:8080`（不带结尾斜杠、不带路径）。**未设置时输出 `http://<host>` 占位**，产物里会带「DM_SITE_URL 未设置」注释 |
| `DM_SECURE_COOKIES` | 登录/会话 Cookie 是否追加 `Secure`（`scripts/serve.py → secure_cookies_enabled`） | 仅在外部反向代理负责 HTTPS 时设为 `1`（`true/yes/on` 同义）；**当前仅 HTTP 阶段保持关闭**（不设置或 `0`），否则浏览器丢弃 Cookie、直接登录不上 |

两项都写在 `deploy/daimonkey.service` 的 `[Service]` 段 `Environment=` 上；模板里已留**注释示例**（当前 HTTP 阶段不启用）：

```ini
Environment=DM_SITE_URL=http://106.12.30.116:8080
# 仅外部反向代理负责 HTTPS 时需要，当前仅 HTTP 不启用：
# Environment=DM_SECURE_COOKIES=1
```

- `DM_SITE_URL` 在**构建期**读取：改后要重新 `--build`（或全量更新），dist 里的 canonical/sitemap/robots 才会更新；
- `DM_SECURE_COOKIES` 在**服务启动时**读取：改后 `systemctl restart daimonkey` 生效；
- 走 bootstrap 托管时改模板后重跑 `sudo bash deploy/bootstrap.sh --host <host> --port <port>`（会重新渲染 `/etc/systemd/system/daimonkey.service` 并 `daemon-reload`），不要手改 `/etc` 下的单元副本。

验收（在安装目录下）：

```bash
grep -o 'Sitemap: .*' dist/robots.txt            # 应为 DM_SITE_URL 的站点基址，不应出现 <host>
grep -i 'DM_SITE_URL 未设置' dist/robots.txt dist/sitemap.xml   # 应无输出（有输出=仍在使用占位）
```
