# 站内运维 Agent（ops agent）设计与实施计划

状态：已上线（2026-10-20）｜服务已降权、ops 已开启；待需求方验收
适用：站点安装目录（仓库根，InferTech 观察站）

## 1. 为什么改（现状审计）

### 1.1 现状事实

| 项 | 现状 | 文件 |
|---|---|---|
| 运行时 | `pi` 一次性子进程，`--no-tools --no-session --no-extensions --no-mcp` | `collector/piagent.py` |
| 工具 | 全禁；助手只能输出纯文本 | `piagent.py:54` |
| 会话 | 不落 pi 会话；多轮上下文由站点把最近 16 条消息拼进提示词 | `collector/assistant.py:269` |
| 界面 | 轮询 `/status`，没有流式输出，看不到工具调用 | `static/assets/assistant.js` |
| 安全边界 | 服务以 **root** 运行（`systemctl show daimonkey -p User` = root） | `deploy/daimonkey.service` |
| 账号 | 全部账号等权（管理员），无角色字段 | `collector/users.py` |
| 审计 | 只有 `assistant_runs` 汇总（时长/token），无工具级审计 | `collector/db.py` |

### 1.2 结论

以现状无法完成「只通过前端交互做运维」：没有工具 = 不能读日志、改代码、重启服务；没有会话 = 每次都要重新交代上下文；没有流式与审批 = 长任务不可见、危险操作不可控。同时**不能简单打开 `--tools`**：那等于把 root shell 交给一个会被网页内容提示注入的模型。

### 1.3 目标与非目标

目标（本期）：
1. 运维会话：pi 原生工具（read/write/edit/bash/find/grep/ls）+ 持久会话（`--session-id`）。
2. 流式前端：文本增量、工具调用卡片、结果折叠、停止按钮。
3. 危险操作前端审批（`ctx.ui.confirm` → Web 弹层），默认拒绝。
4. 管理员门控：只有 `role=admin` 账号可开运维会话；会话级 `mode=ops|chat`。
5. 策略护栏 + 审计：路径白名单/黑名单、命令分级（自动放行/需审批/直接拒绝）、工具调用上限、JSONL 与 DB 双审计。
6. 服务降权：专用 `daimonkey` 用户运行，禁止 root；重启走受控 wrapper。
7. 测试、部署文档、验收脚本全部落地。

非目标（后续）：
- 容器/VM 级强隔离（本期用独立系统用户 + 策略 + 审计；残余风险见 §7）。
- 多用户并发执行多个 agent（仍保持全局并发 1）。

## 2. 架构

```
浏览器 assistant.js
   │  POST /api/v1/assistant/ops/chat  → run_id
   │  GET  /api/v1/assistant/ops/runs/{run_id}?after=N   (600ms 轮询增量)
   │  POST .../approvals/{id} {allow}    （审批弹层）
   ▼
scripts/serve.py（登录/角色/配额/路由）
   ▼
collector/assistant.py（会话、锁、事件缓冲、审批 broker、审计）
   ▼
collector/piagent.py  run_turn()  → pi --mode rpc（每轮一个子进程，会话续接）
   ▼
pi（tools: read write edit bash find grep ls；-e ops-guard.ts）
   ├─ 工具调用事件 → on_event → 事件缓冲 → 前端
   ├─ 危险操作 → ctx.ui.confirm → extension_ui_request → 审批 broker → 前端
   └─ JSONL 审计 / DB assistant_tool_calls
```

每轮一个 pi 进程、会话状态落 `data/pi-sessions/`：任务期间内存 ~130MB，结束即释放，跨轮上下文由 pi 会话文件续接。

## 3. 数据模型（在线迁移，附加式）

```sql
ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin';      -- admin | member
ALTER TABLE assistant_sessions ADD COLUMN mode TEXT NOT NULL DEFAULT 'chat';  -- chat | ops
ALTER TABLE assistant_sessions ADD COLUMN pi_session TEXT;            -- pi --session-id
ALTER TABLE assistant_runs ADD COLUMN run_id TEXT;                    -- uuid，前端轮询键
ALTER TABLE assistant_runs ADD COLUMN mode TEXT;
ALTER TABLE assistant_runs ADD COLUMN reply TEXT;
ALTER TABLE assistant_runs ADD COLUMN tool_count INTEGER DEFAULT 0;

CREATE TABLE assistant_tool_calls (
  id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, session_id INTEGER,
  tool TEXT NOT NULL, args_json TEXT, decision TEXT,      -- allow | ask | deny | approved
  status TEXT,                                             -- running | ok | error | blocked
  output TEXT, duration_s REAL, created_at TEXT NOT NULL);
CREATE TABLE assistant_approvals (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, session_id INTEGER, owner TEXT,
  tool TEXT, command TEXT, status TEXT NOT NULL,           -- pending | allowed | denied | timeout
  created_at TEXT NOT NULL, decided_at TEXT);
```

## 4. 契约

### 4.1 piagent.run_turn

```python
@dataclass
class PiTurnResult:
    reply: str; finish_reason: str; tokens_in: int; tokens_out: int
    peak_rss_mb: float; duration_s: float; session_id: str | None
    tool_calls: list[dict]; aborted: bool

def run_turn(task, *, model, api_key_env="DEEPSEEK_API_KEY",
             system_prompt=None, thinking=None,
             tools="read,write,edit,bash,find,grep,ls",
             session_id=None, session_dir=None, cwd=None,
             timeout_s=900, approval_timeout_s=180,
             cancel_event=None, on_event=None, on_approval=None,
             extension_paths=None, max_tool_calls=60,
             pi_override=None) -> PiTurnResult
```

- `pi --mode rpc`，每轮一个进程；`session_id` 存在时带 `--session-id/--session-dir`，否则 `--no-session`。
- 事件：`message_update`（text_delta）、`tool_execution_start/end`、`extension_ui_request`、`agent_settled`。
- `on_approval(req) -> bool | str | None`：confirm→bool；select→选项字符串；input/editor→文本；超时/无回调 → 拒绝。
- 超时/取消：`kill -9 -<pgid>`；仍采样峰值 RSS。
- 测试必须 mock `subprocess.Popen`，禁止真实联网（`tests/__init__.py` 护栏）。

### 4.2 assistant.py

```python
start_ops_turn(session_id, message, user) -> run_id      # 权限/配额/锁/落库/起线程
run_status(run_id) -> {state, text, events, pending, error, tokens}
resolve_approval(run_id, approval_id, allow) -> bool
abort_run(run_id) -> bool
set_session_mode(session_id, mode, user) -> bool         # chat|ops，仅 admin
ops_enabled() -> bool                                    # registry 开关
```

事件（前端轮询增量）：
```json
{"seq":1,"type":"text","text":"..."}
{"seq":2,"type":"tool_start","tool":"bash","args":{"command":"..."}}
{"seq":3,"type":"tool_end","tool":"bash","status":"ok","output":"..."}
{"seq":4,"type":"approval","id":"...","tool":"bash","title":"...","command":"...","status":"pending"}
{"seq":5,"type":"approval_resolved","id":"...","allowed":true}
{"seq":6,"type":"done","reply":"...","tokens_in":1,"tokens_out":2}
```
缓冲上限：事件 500 条、文本 64KB、单条工具输出 4KB；run 结束后保留 10 分钟供轮询。

### 4.3 HTTP

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/assistant/session` | 追加 `role`、`ops_enabled` |
| POST | `/api/v1/assistant/sessions/{id}/mode` | `{mode:"ops"\|"chat"}`，admin |
| POST | `/api/v1/assistant/ops/chat` | `{session_id,message}` → `{run_id}`；403/409/429 |
| GET | `/api/v1/assistant/ops/runs/{run_id}?after=N` | 增量事件 + 状态 |
| POST | `/api/v1/assistant/ops/runs/{run_id}/approvals/{aid}` | `{allow:bool}` |
| POST | `/api/v1/assistant/ops/runs/{run_id}/abort` | 中止 |

### 4.4 策略文件（单一事实源）

`deploy/pi-ext/ops-policy.json`：`deny`（正则，直接拒绝）、`safe`（前缀，免审批）、`ask`（其余命令需审批）、`secret_paths`、`writable_dirs`、`approval_dirs`、`max_tool_calls`。Python 侧 `collector/ops.py` 加载同一文件做分类与测试；TS 扩展运行时读取。

## 5. pi 扩展 ops-guard.ts

- `tool_call` 钩子：
  - 路径类工具：解析绝对路径，必须位于仓库根内；`secret_paths` 拒绝；`approval_dirs` 需审批。
  - `bash`：`deny` → block；`safe` 前缀 → 放行；其余 → `ctx.ui.confirm`（RPC 转到前端）；无 UI 时拒绝。
  - 全局工具调用计数，超 `max_tool_calls` 直接 block。
- 审计：JSONL 追加 `data/ops-audit/YYYYMMDD.jsonl`（tool/args/decision/reason/ts）。
- 失败即拒绝（fail-closed）。

## 6. 部署（降权，2026-10-20 已执行）

1. 建系统用户 `daimonkey`（home 即仓库目录），`chown -R` 仓库/`.venv`/`data`/`dist`。
2. 迁移 GitHub SSH key 到仓库根 `.ssh/`（知识库同步依赖）。
3. `usermod -aG systemd-journal daimonkey`（读 journal）。
4. `/etc/sudoers.d/daimonkey` 只授权固定 wrapper：`/usr/local/bin/daimonkey-restart`（`systemd-run --on-active=3 systemctl restart daimonkey`，延时重启避免自杀式中断当前轮）。
5. unit `User=daimonkey`；`daemon-reload && restart`；验证站点/登录/知识库/更新按钮。
6. 回滚：unit 改回 `User=root` 重启（脚本 `deploy/ops-rollback.sh`）。

## 7. 残余风险（明确接受）

- 运维会话可由提示注入驱动：护栏是路径/命令策略 + 审批 + 审计，不是强沙箱；模型仍可能用允许的命令组合出意外结果。
- `DEEPSEEK_API_KEY` 在 pi 进程环境中，策略禁止 `env`/`/proc/*/environ` 读取，但无法 100% 防注入外带。
- 审批依赖人在场；无人在场时危险操作一律拒绝（fail-closed）。
- 后续：bubblewrap/容器隔离、MCP curated tools、网络出口限制。

## 8. 实施顺序

- [x] P0 审计（本文 §1）
- [x] P1 契约 + DB 迁移 + 角色 + 配置（2026-10-20）
- [x] P2 `piagent.run_turn`（RPC/会话/流式/审批/取消）+ 单测（2026-10-20）
- [x] P3 `ops-guard.ts` + `collector/ops.py` + 策略 JSON + 单测（2026-10-20）
- [x] P4 `assistant.py` / `serve.py` 集成（run 缓冲、审批 broker、权限门控）（2026-10-20）
- [x] P5 前端（流式、工具卡片、审批弹层、模式切换、停止）（2026-10-20）
- [x] P6 部署降权脚本 + opsctl + 文档（2026-10-20 已现场执行：User=daimonkey，deploy-check 15/15）
- [x] P7 全量测试 187 用例 + verify 全绿（2026-10-20）
- [x] P8 线上冒烟（2026-10-20：临时 admin；对话/工具/审批拒绝/停止链路通过；正式需求方验收待做）

## 9. 实施记录（2026-10-20）

| 项 | 落地 | 验证 |
|---|---|---|
| RPC 运行时 | `collector/piagent.py::run_turn`：pi `--mode rpc`，JSONL prompt/abort/extension_ui_response，事件转发、工具卡片、会话续接、超时/取消、峰值 RSS | `tests/test_ops_agent.py` 15 例；真实 pi 端到端跑通 bash/审批/拒绝 |
| 策略扩展 | `deploy/pi-ext/ops-guard.ts`：`tool_call` 钩子，路径 realpath 越界拦截、敏感路径、可写/审批目录、bash 分级、工具上限、JSONL 审计；fail-closed | 真实 pi：`echo hello` 放行、读 `data/assistant.env` 拒绝、`git push` 弹审批 |
| 策略一致性 | `collector/ops.py` 读同一份 `ops-policy.json`，服务端二次校验 + 单测；TS/Python 各自 fail-closed | 策略单测 5 例 |
| 会话/审批/审计 | `assistant.start_ops_turn/run_status/resolve_approval/abort_run`；`assistant_tool_calls`/`assistant_approvals`/`assistant_runs` 落库；事件缓冲 500 条 | 集成测 3 例（DB/审批/角色） |
| 前端 | 模式切换、流式文本、工具卡片（折叠）、审批弹层、停止、用量含 ops | `node --check` + 手工链路审读 |
| 安全默认 | `assistant.ops.enabled: false`；降权脚本/文档就绪后才置 true | 本文件 §6/§7 |
| 运维教训 | `subprocess.run(["kill","-9","-pid"])` 在本机进程树下会卡住；统一改 `os.killpg`（`run()` 同步修复） | 单测超时用例 |

### 已知残余风险（未变）

- 护栏不是沙箱：审批 + 路径/命令策略 + 审计，模型仍可能用允许的命令组合出意外结果；
  强隔离（容器/namespace/出口限制）列为后续。
- 审批依赖人在场；无人在场时危险操作一律拒绝（fail-closed）。
- `pi-sessions/` 与 `ops-audit/` 含运维上下文，按数据目录权限保护，不进 git。
