---
name: load-optimization
description: 本站负载优化专家。适用场景:内存/CPU 治理、常驻进程审计、agent 运行时选型、并发与限流规格、服务器高负载排查、新增依赖评估。
---

# 负载优化专家(load-optimization)

## 运行规格(一切上限的依据)

- 服务器:单机 3.3GB 内存,**无 swap**。
- 站点规格:**最多 10 人并发访问 / 20 人同时在线**。限流、并发上限、连接超时均按此规格设定(见 `scripts/serve.py`),规格变更时同步调整上限并在此记录。
- 原则:**无常驻 agent 进程**。AI 任务按需起进程、用完即退(PIAgent 一次性模式);任何新功能引入常驻内存前必须先过本专家的预算表。

## 内存预算表(2026-10-16 基线)

| 项 | 预算 | 说明 |
|---|---|---|
| serve.py(站点服务) | ≤80MB RSS | stdlib http.server + SQLite |
| AI 任务(按需) | ≤300MB,仅任务期间 | pi 子进程一次性执行,结束即释放 |
| 构建任务(--update/--build) | ≤150MB,仅运行期间 | 采集+评分+建站 |
| 系统 + 外部工具 | 其余 | journald、sshd、监控 agent 等非本站项 |

清理基线(2026-10-16):codex 遗留进程 4 个约 222MB 已清;DSH 常驻 runtime 187MB 已随运行时替换移除。可用内存从 ~1.84GB 提升约 400MB。

systemd 托管基线(2026-10-17):`daimonkey.service` 只跑 serve.py,systemd 自身开销可忽略;serve.py 启动实测 RSS 14.4MB(peak 14.9MB)、1 线程。`systemctl restart` 后主进程更换且 20 秒内恢复 200。

## 常驻进程白名单(审计 runbook)

```bash
ps -eo pid,ppid,rss,args --sort=-rss | grep -vE "grep|zcode|sshd|journald" | head -15
```

本站合法常驻进程**只有** `serve.py` 一个,由 systemd 单元 `daimonkey.service` 托管(`Restart=always`、开机自启、日志进 journal;不加采集定时器)。出现下列任一情况即为异常:

| 异常进程 | 含义 | 处置 |
|---|---|---|
| `deepseek-harness-sdk-runtime-*` | DSH 遗留(已弃用) | 确认无引用后 kill;若被拉起,排查 runtime.py 残留 |
| `codex app-server` / `codex proxy` | **外层 ZCode/codex CLI 环境的后端,不是本站进程** | 不要 kill:实测杀掉会自动重生,且可能影响当前编码会话;与本站内存账无关 |
| 多个 `python -m collector` | 更新进程堆积(应同时只有一个) | 查 `data/update_status.json` 与 serve.py 互斥锁 |
| `serve.py` 不在 systemd cgroup 下 | 旧的 nohup 裸跑残留 | `systemctl status daimonkey`;确认端口归属,停掉旧进程后重跑 `deploy/bootstrap.sh` |
| serve.py 线程数持续增长 | pi 采样线程泄漏 | `ls /proc/$(pgrep -f serve.py)/task \| wc -l`;piagent 采样线程 stop 后 2s 内必退出,持续增长即为 bug |

## PIAgent 运行时策略

- 站点 agent 一律走 **PIAgent 一次性进程模式**(`collector/piagent.py` 适配层):每次任务 spawn、结束退出,无空闲占用。
- 跨进程文件锁 `data/assistant.lock` 保持并发 1(护配额语义);超时与 token 上限见 `registry.yaml → assistant.execution`。
- 禁止引入新的常驻 SDK/runtime;若模型调用需要长驻(如流式),先在本专家记录内存实测再决策。

## 已知负载问题台账

| 问题 | 状态 | 备注 |
|---|---|---|
| DSH 常驻 187MB + _RssGuard 线程泄漏 | 已解决(2026-10-16 实测) | 运行时替换为 PIAgent;serve.py 重启后 1 线程起步、RSS 27MB;E2E 冒烟 pi 峰值 123MB 仅任务期间 |
| codex 进程 | 结论修正 | 属外层 ZCode/codex CLI 环境后端,非本站进程,kill 后自动重生;不再纳入本站内存账(见白名单表) |
| 整轮采集单个长事务锁库(可达 10 分钟) | 已解决(2026-10-17) | 逐源短事务+AI 评分移出主事务(自锁主因);另加更新子进程 25min 看门狗、慢请求(>5s)日志、前端轮询 15min 上限 | 影响:锁库期间 AI 评分/会话写入可能 busy;数据量小可接受,恶化时按源分事务 |
| dist/raw 逐 payload 整页(约 270MB)且每次全量重压 | 待决策 | 方案:raw 页改按需 JSON 渲染或 .gz 按内容哈希判断;动 dist 结构前先过 data-update 专家 |
| serve.py 子进程日志 fd 不显式关闭 | 低优先 | 每次更新泄漏一个 fd,量级极小 |
| 单测漏 mock 会起 pi 子进程联网、整轮挂起 | 已解决(2026-10-20) | `tests/__init__.py` 护栏:仅放行 loopback、未 patch 的 `assistant.run_internal` 直接报错、pbkdf2 降到 1000 轮;全量 169 用例 15s(原 >10min 挂死) |

## 常见调整

| 需求 | 改动点 |
|---|---|
| 调整限流/并发上限 | `scripts/serve.py` 限流常量(规格注释处),同步更新本文件规格段 |
| 更换 AI 运行时 | 只改 `collector/piagent.py`,保持 `run()` 接口语义,回归 tests/test_piagent.py |
| 调整内存预算 | 本文件预算表 + serve.py / registry 对应护栏值 |
| 调整服务托管 | 改 `deploy/daimonkey.service` 模板后重跑 `deploy/bootstrap.sh`(会把 `User/Host/Port` 渲染进 `/etc/systemd/system`) |
| 评估新依赖 | 记录体积与是否常驻,超预算须给出等量回收项;优先精确版本锁定(见 deployment 专家) |

## 排障

| 症状 | 排查 |
|---|---|
| 服务器内存持续走高 | 跑常驻白名单审计;`ls /proc/<serve_pid>/task \| wc -l` 查线程泄漏 |
| 站点卡顿 | `data/update_status.json` 是否 running(构建锁库);并发是否顶到限流上限 |
| AI 任务失败 | `assistant_runs` 表 status/error;pi 输出解析见 piagent.py 日志 |

## 决策记录

- 2026-10-16:弃用 deepseek-harness-sdk(常驻 187MB、守护线程越界风险),站内 agent 改用 Pi 一次性进程模式;codex 站外遗留进程清退(后发现属外层 ZCode 环境后端会重生,已从本站内存账移除)。规格定为 10 并发/20 在线。
- 2026-10-16(规格化防护落地):serve.py 实测——静态 120/min/IP、API 60/min/IP 令牌桶(超限 429+Retry-After)、全局在途上限 30、连接超时 30s、%00 路径 400、nosniff+CSP 安全头;压测 20 线程 120 请求全部成功(p50 5ms)、35 并发迸发全部 200,压测后 serve.py RSS 27MB/线程归零。同步修复 pipeline 首源失败 UnboundLocalError 与状态卡 running 问题。
- 2026-10-17(部署托管):serve.py 从 nohup 裸跑迁移到 systemd(`Restart=always`、开机自启、`KillMode=mixed` 连同更新子进程退出),服务仍只有 serve.py 一个常驻进程;bootstrap 幂等重跑不会重复起进程。未引入采集定时器,保持手动更新约定。依赖改为精确版本锁定,避免新机装到未验证行为。
- 2026-10-20(运维 agent 资源模型):ops 每轮一个 pi RPC 子进程（任务期峰值 ~130MB、结束即释放），会话文件落 `data/pi-sessions/`；全局执行锁仍为 1，审批等待不占 CPU。杀进程统一 `os.killpg`——外部 `kill -9 -pid` 在本机进程树下会卡死（已修 `run()` 与 `run_turn`）。
- 2026-10-20(单测轻量化):`test_build_survives_ai_failure` 只 mock 了 `ai.refresh_summaries_if_stale`,漏掉 `run_build` 第一站的 `workflows.generate_direction_digests`,用例真的起了 pi 子进程等 DeepSeek 回复,整轮测试挂起 >5 分钟;执行通道在超时清理进程时连累同机 `daimonkey.service` 被 SIGKILL(systemd 已自动恢复)。修复:补 mock 并断言两步都降级;`tests/__init__.py` 加护栏——外网连接只放行 loopback、未 patch 的 `assistant.run_internal` 立即抛错(不再起 pi)、pbkdf2 轮数降到 1000(只影响单测,哈希格式/校验不变),重型鉴权用例显式 `import tests` 加载护栏。结果:168→169 用例 15s 全绿。运维教训:长命令一律 `timeout` 包裹或 `setsid nohup` 后台跑,禁止让单命令顶到执行通道上限。

- 2026-10-21(HTTP/1.1 与探活)：serve.py 改 HTTP/1.1 keep-alive，省掉每请求重建 TCP；代价是响应必须自证帧边界——304 补 `Content-Length`、请求体未读完（413/429/503 提前返回等）补 `Connection: close` 防协议错位，空闲超时从 30s 收紧到 10s 以限制慢连接线程占用。另加 `/healthz`（只读短查询 + `dist/index.html` 探活，异常 503）和统一安全头（nosniff / frame / CSP），在 10 并发规格内不增加常驻内存。
