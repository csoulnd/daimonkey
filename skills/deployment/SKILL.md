---
name: deployment
description: 本站部署交付与可搬迁性专家。适用场景:新服务器安装、换机/迁移目录、备份与恢复、systemd 托管、端口/运行用户调整、依赖版本漂移、搬迁演练与部署自检。
---

# 部署交付专家(deployment)

## 职责边界

- 负责 `deploy/`（bootstrap / backup / smoke / systemd 单元）与 `scripts/migrate_relpath.py`、`scripts/check_env.sh`、`docs/deploy.md`。
- 负责"换一台服务器不炸"：路径可搬迁、依赖可复现、进程可托管、数据可备份恢复、结果可演练。
- 不负责采集/评分/建站业务逻辑；改 `collector/` 业务代码归 data-update / load-optimization。systemd 只托管 `serve.py`，不改产品"手动更新"约定。

## 交付件地图

| 文件 | 作用 |
|---|---|
| `deploy/bootstrap.sh` | 系统依赖 → pi 锁定版 → venv/pip → 可选 restore → 迁移/建库/构建 → userctl → systemd |
| `deploy/daimonkey.service` | systemd 模板；bootstrap 用 `sed` 渲染 `__INSTALL_ROOT__/__RUN_AS__/__HOST__/__PORT__` 后写入 `/etc/systemd/system/` |
| `deploy/backup.sh` | SQLite 热备 + `data/` 关键内容打包；排除 `dist/`、`data/cache/`、`data/logs/` |
| `deploy/smoke.sh` | 在线检查 + 搬迁演练 + 重启演练（`--all`） |
| `scripts/migrate_relpath.py` | `raw_payloads.body_path` 绝对 → 相对迁移，幂等 |
| `docs/deploy.md` | 面向新人的依赖矩阵、安装/换机/排障手册 |

## 约束（改部署件前先看）

1. **路径只在库里存相对形式**：`raw_payloads.body_path = raw/<源>/<日期>/<hash>.json.gz`；读取时用 `collector.config.resolve_data_path()` 拼当前 `DATA_DIR` 或调用方显式传入的数据目录（测试会 monkeypatch）。禁止把绝对路径写回库。
2. **bootstrap 必须先 `cd "$ROOT"`** 再执行任何 `python -m collector`；否则从其他目录调用会误用调用者目录的代码与数据（2026-10-17 搬迁演练实测踩坑）。
3. **备份排除 `dist/` 与 `data/cache/`**；`data/logs/` 也不打（诊断信息）。归档含密钥，权限 600，禁止进 git/公开目录。
4. **不动既有库表语义**：D1 只改路径表达，不改 DDL；迁移脚本可重复执行。
5. **不加数据采集定时器**：`deploy/daimonkey.service` 只跑 `serve.py`；自动备份 cron 属运维范畴，需在文档中单独说明。
6. **依赖锁定**：`requirements.txt` 采用精确版本；升级需全量测试 + verify。Markdown 必须避开 3.11.1（中文 `**研究判断：**` 解析回归）。
7. **systemd 单元的 `User=` 由 bootstrap 渲染**；不要手改 `/etc/systemd/system/daimonkey.service`，改模板后重跑 bootstrap。
8. **bootstrap 在单元未变化时不重启服务**（避免误杀进行中的更新）；`git pull` 后要 `systemctl restart daimonkey` 才加载新代码。重启前查 `update/status` 是否 running。
9. **修复脚本后同时更新 smoke 断言**：搬迁/重启演练是验收，不是可选步骤。

## Runbook

### 新机安装

```bash
git clone <仓库> <安装目录> && cd <安装目录>
sudo bash deploy/bootstrap.sh --host 0.0.0.0 --port 8080
systemctl status daimonkey
deploy/smoke.sh
```

### 换机恢复

```bash
# 旧机
deploy/backup.sh --out /var/backups
# 新机
git clone <仓库> <安装目录> && cd <安装目录>
sudo bash deploy/bootstrap.sh --restore /path/to/daimonkey-backup-*.tar.gz
sudo deploy/smoke.sh --all
```

恢复会自动跑 `migrate_relpath.py`（兼容旧备份里的绝对路径）。GitHub SSH key 迁移见 `docs/deploy.md §4.3`。

### 自检

```bash
bash scripts/check_env.sh
.venv/bin/python scripts/verify.py          # 含第 5 节部署防回归
deploy/smoke.sh --all                        # 在线 + 搬迁 + 重启
```

## 常见调整

| 需求 | 改动点 |
|---|---|
| 改端口/监听地址 | `sudo bash deploy/bootstrap.sh --port 8081 --host 127.0.0.1` 重跑 |
| 改运行用户 | `--run-as <用户>`；bootstrap 会 chown `data/ .venv/ dist/`，确保能读写 |
| 换 pi 版本 | `deploy/bootstrap.sh` 的 `PI_PKG/PI_VERSION` + 本文件/文档同步；跑助手回归 |
| 改备份范围 | `deploy/backup.sh` 的 copy 列表；保持排除 dist/cache/logs |
| 升级 Python 依赖 | 先改 `requirements.txt` 精确版本 → 全量测试 + verify → 重跑 bootstrap |
| 无 systemd 环境 | bootstrap 加 `--no-service`，`scripts/serve.sh` 前台跑；smoke 的 `--restart` 会失败，属预期 |

## 排障

| 症状 | 排查 |
|---|---|
| 新机 bootstrap 失败在 Python/Node 版本 | 需 Ubuntu 24.04+/Debian 12+（Python≥3.11、Node≥18）；旧系统先升级 |
| 端口被占、服务起不来 | `ss -ltnp \| grep <端口>`；常见旧 `nohup serve.py`，停掉后重跑 |
| 恢复后服务 500 / dist 不存在 | 看 bootstrap 日志第 5 步；确认 `cd "$ROOT"` 生效，必要时 `--rebuild` |
| 溯源 404 / 文件缺失 | `scripts/migrate_relpath.py --dry-run`；`verify.py` 第 5 节列出缺失文件 |
| 迁移后 body_path 仍绝对 | `SELECT COUNT(*) FROM raw_payloads WHERE body_path LIKE '/%'`；重跑迁移并查 WARN |
| 新机测试与旧机行为不一致 | 检查 `pip freeze` 是否与锁定版本一致（Markdown 3.11.1 曾改变中文加粗） |
| smoke 搬迁失败 | 保留现场 `deploy/smoke.sh --relocation --keep`，看 `/tmp/tmp.*/bootstrap.log`、`serve.log` |
| 演练后残留 18080 端口 | smoke 现在直接 `exec` 并在 trap 中杀进程；若历史残留 `kill <pid>` |
| journal 无日志 | `journalctl -u daimonkey -n 50`；确认 unit 的 `StandardOutput` 未被改 |

## 决策记录

- 2026-10-17: 落地 D1-D6。`body_path` 改存相对路径（1104 条迁移，0 缺失）；新增 `migrate_relpath.py`、bootstrap/backup/smoke、systemd 单元、`docs/deploy.md`；`verify.py` 增加相对路径/硬编码/交付件检查。生产 serve 从 nohup 迁移到 systemd，`Restart=always`、开机自启、journal 可查。
- 2026-10-17: 搬迁演练发现并修复 bootstrap 未 `cd` 到安装根导致 `python -m collector` 用错目录；同轮发现 fresh venv 装到 Markdown 3.11.1，中文 `**研究判断：**` 不再解析为 strong，遂锁定全部直接依赖（`requirements.txt` 精确版本）。
- 2026-10-17: 验收证据：117 Python 测试 + node 测试通过；`verify.py` 42/42；`smoke --all` 23/23（在线 9、搬迁 10、重启 4）；备份 39MB/1067 文件，恢复后账号数一致、溯源链完整。
