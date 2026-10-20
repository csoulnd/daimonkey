---
name: ops-agent
description: 站内运维 agent(ops)专家。适用场景: pi RPC 运行时、工具白名单与策略护栏、审批 broker、工具级审计、运维会话模式、服务降权与 sudoers/wrapper、opsctl 运维命令。
---

# 站内运维 agent 专家（ops-agent）

## 职责边界

- 主责：`collector/piagent.py::run_turn`（pi RPC）、`collector/ops.py`（策略 Python 侧）、
  `deploy/pi-ext/ops-guard.ts`（运行时拦截）、`deploy/pi-ext/ops-policy.json`（单一事实源）、
  `collector/assistant.py` 的 ops 会话/审批/事件缓冲、`scripts/serve.py` 的 ops 路由、
  `scripts/opsctl.py`、`deploy/sudoers.d/daimonkey`、服务降权。
- 协作：前端（`static/assets/assistant.js`、`templates/base.html`）与样式归 `ui-polish`；
  负载/内存/子进程纪律归 `load-optimization`；部署/换机归 `deployment`。
- 需求与设计蓝本：`docs/OPS-AGENT.md`（包含残余风险与上线顺序）；交付手册 `docs/deploy.md §11`。

## 安全铁律（事故换来的）

1. **先降权再开 ops**：`assistant.ops.enabled` 默认 `false`；只有
   `systemctl show daimonkey -p User` 不是 root、`opsctl deploy-check` 通过后才置 `true`。
   root 服务 + 工具 = 把 root shell 的审批面交给模型。
2. **fail-closed**：策略文件缺失/非法 → 扩展加载失败；路径越界/敏感路径 → block；
   需审批但无 UI → block；未知工具 → ask；审批超时/无人应答 → 拒绝；回调异常 → 拒绝。
3. **不以外部 `kill` 命令杀进程组**：本机实测 `subprocess.run(["kill","-9","-pid"])` 会卡住；
   统一 `os.killpg(pid, signal.SIGKILL)`（pi 用 `start_new_session=True`，pgid == pid）。
4. **秘钥只进 `data/`（600）**：策略把 `data/assistant.env`、`assistant_secret`、
   `update_password*`、`.ssh`、`.git/config`、`.pi` 列为 deny；`env`/`printenv`/`/proc/*/environ` 同样 deny。
5. **重启只能走固定 wrapper**：`/usr/local/bin/daimonkey-restart`（root:root 0755，
   `systemd-run --on-active` 延时）；sudoers 只授权这一条，禁止直接 `systemctl restart`。
6. **审计是旁路**：DB `assistant_tool_calls/assistant_approvals` + JSONL
   `data/ops-audit/YYYYMMDD.jsonl`；审计写失败不阻断工具，但拦截决策仍生效。

## runbook

```bash
cd <安装目录>
# 1) 单测（不联网、假 pi）
.venv/bin/python -m unittest tests.test_ops_agent -v

# 2) 策略自检（与 TS 同一份 JSON）
.venv/bin/python -c "from collector import ops; print(ops.load_policy()['version'])"

# 3) 降权部署（维护窗口；会 chown 整个仓库并重启服务）
sudo bash deploy/bootstrap.sh --run-as daimonkey --create-user
sudo -u daimonkey .venv/bin/python scripts/opsctl.py deploy-check
systemctl show daimonkey -p User -p Group --value

# 4) 开启 ops（registry.yaml → assistant.ops.enabled: true）后重启
sudo systemctl restart daimonkey

# 5) 线上最小 E2E（admin 账号，网页助手面板）
#    对话→运维，发“只回复 OK”，再发“运行 echo hello”，应出现工具卡片；
#    发“读取 data/assistant.env”，应被策略拒绝；发“git push --force”，应弹审批。
```

## 常见调整

| 需求 | 改哪里 |
|---|---|
| 允许/禁止某类命令 | `deploy/pi-ext/ops-policy.json` 的 `deny` / `safe` / `approval_required_always`（TS 与 Python 同时生效） |
| 可写目录/审批目录 | 同文件 `writable_dirs` / `approval_dirs`（最长前缀优先） |
| 工具清单/模型/超时 | `registry.yaml → assistant.ops`（`tools/model/thinking/timeout_s/approval_timeout_s`） |
| 前端工具卡片/审批样式 | `static/assets/style.css` 的 `.asst-tool*` / `.asst-approval*`（归 ui-polish） |
| 审计位置 | policy `audit_dir` + `assistant.ops.audit_dir`（默认 `data/ops-audit`） |
| 配额 | `assistant.ops.daily_limit / daily_tokens` |

## 排障

| 症状 | 处置 |
|---|---|
| 模式开关不出现 | `GET /api/v1/assistant/session` 看 `role=admin` 与 `ops_enabled`；registry 是否 true、是否重启过 |
| 发消息 403 | 非 admin 或 ops 关闭；`users.get_role` 检查角色 |
| 发消息 409 | 全局已有任务（并发 1）；等当前轮结束或 abort |
| 任务卡 running | 看 `data/update_status.json` 无关注；查 journal 的 `pi` 错误；`assistant.abort_run`；25min 看门狗不覆盖 ops，run_turn 自身 `timeout_s=900` |
| 工具全被拒 | 策略文件路径不对（按 cwd 找 `deploy/pi-ext/ops-policy.json`）；或 `ctx.hasUI=false`（RPC 未接审批） |
| 审批弹层不出现 | serve 未加载新代码（重启）；`/ops/runs/{id}` 的 `pending` 是否为空 |
| 扩展加载失败（pi 起不来） | `pi --mode rpc --no-session -e deploy/pi-ext/ops-guard.ts` 看 stderr；策略 JSON 非法会直接失败 |
| 降权后站点 502/权限错误 | `opsctl deploy-check`；属主/日志组；`deploy/ops-rollback.sh` |
| 知识库同步失败 | 降权后 `$ROOT/.ssh` 是否存在且属主为服务用户 |

## 决策记录

- 2026-10-20：pi 1.3.0 RPC（`--mode rpc`）作为运维运行时；每轮一个子进程 + `--session-id`
  持久会话，内存任务期 ~130MB、结束释放；关闭 MCP/skills/context-files，只保留显式工具白名单。
- 2026-10-20：策略单一事实源放 JSON，TS 扩展运行时拦截、Python 服务端二次校验与单测；
  两边都 fail-closed，避免“只靠前端/提示词”的假安全。
- 2026-10-20：审批走 pi 的 `ctx.ui.confirm → extension_ui_request → extension_ui_response`，
  前端审批弹层；无人在场一律拒绝。
- 2026-10-20：`os.killpg` 替代外部 `kill` 命令——后者在本机进程树下会卡住（单测踩到）。
- 2026-10-20：默认 `ops.enabled=false`，上线顺序固定为“降权 → deploy-check → 开开关”。
- 2026-10-20：审计双写（DB 结构化 + JSONL 追加）；DB 供页面/排查，JSONL 供离线取证。
