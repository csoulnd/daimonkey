/**
 * daimonkey 站内运维 agent 的策略扩展（pi `tool_call` 钩子）。
 *
 * 单一事实源：deploy/pi-ext/ops-policy.json（Python 侧 collector/ops.py 读同一份，
 * 供服务端二次校验与离线单测；本文件是运行时实际拦截点）。
 *
 * 安全原则：fail-closed。
 * - 策略文件缺失/非法 → 扩展加载失败，pi 直接起不来；
 * - 路径越界、敏感路径 → block；
 * - 需审批但无 UI（RPC 客户端没接审批）→ block；
 * - 未知工具/未知情况 → 审批，而不是放行；
 * - 审计写失败只记 stderr，不阻断（审计是旁路），但拦截决策本身仍然生效。
 *
 * 所有决策追加到 <repo>/data/ops-audit/YYYYMMDD.jsonl（0600），与
 * collector.assistant 的 DB 工具审计互为补充。
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import * as fs from "node:fs";
import * as path from "node:path";

// 说明：策略文件按「pi 的工作目录」(run_turn 固定传仓库根) + 相对路径定位；
// 环境变量 DAIMONKEY_OPS_POLICY 可覆盖（单测/临时演练）。扩展由 pi 的 jiti 加载，
// 不需要在仓库安装 npm 依赖；上面的 import type 在运行时会擦除。

type Policy = {
  version: number;
  repo_root: string;
  max_tool_calls: number;
  audit_dir?: string;
  secret_paths: string[];
  writable_dirs: string[];
  approval_dirs: string[];
  deny: string[];
  safe: string[];
  approval_required_always: string[];
};

type Decision = "allow" | "ask" | "deny";

const POLICY_PATH =
  process.env.DAIMONKEY_OPS_POLICY ||
  path.join(process.cwd(), "deploy", "pi-ext", "ops-policy.json");

function loadPolicy(): Policy {
  const raw = fs.readFileSync(POLICY_PATH, "utf8");
  const data = JSON.parse(raw) as Policy;
  if (!data || typeof data.version !== "number" || data.version < 1) {
    throw new Error(`ops-policy version 非法: ${POLICY_PATH}`);
  }
  if (typeof data.repo_root !== "string" || !data.repo_root.trim()) {
    throw new Error(`ops-policy repo_root 缺失: ${POLICY_PATH}`);
  }
  for (const key of [
    "secret_paths",
    "writable_dirs",
    "approval_dirs",
    "deny",
    "safe",
    "approval_required_always",
  ] as const) {
    if (!Array.isArray(data[key])) {
      throw new Error(`ops-policy 字段 ${key} 必须是数组: ${POLICY_PATH}`);
    }
  }
  if (!Number.isInteger(data.max_tool_calls) || data.max_tool_calls < 1) {
    throw new Error(`ops-policy max_tool_calls 必须是正整数: ${POLICY_PATH}`);
  }
  for (const pattern of data.deny) {
    try {
      new RegExp(pattern, "i");
    } catch (err) {
      throw new Error(`ops-policy deny 正则非法 ${pattern}: ${String(err)}`);
    }
  }
  return data;
}

const policy = loadPolicy();

// ---------------------------------------------------------------- 基础工具

function parts(text: string): string[] {
  return text
    .replace(/\\/g, "/")
    .replace(/^\.\//, "")
    .replace(/^\/+|\/+$/g, "")
    .split("/")
    .filter(Boolean);
}

function within(base: string, target: string): boolean {
  return target === base || target.startsWith(base + path.sep);
}

/** 对不存在的写目标也解析 realpath：向上找最近存在的祖先，再拼回剩余段。 */
function realpathLoose(target: string): string {
  let current = path.resolve(target);
  const rest: string[] = [];
  // 最多向上 40 层，防御异常输入
  for (let i = 0; i < 40; i += 1) {
    try {
      return path.join(fs.realpathSync(current), ...rest.reverse());
    } catch {
      const parent = path.dirname(current);
      if (parent === current) return path.resolve(target);
      rest.push(path.basename(current));
      current = parent;
    }
  }
  return path.resolve(target);
}

function secretHit(rel: string): string | null {
  const relParts = parts(rel).map((p) => p.toLowerCase());
  for (const entry of policy.secret_paths) {
    const needle = parts(entry).map((p) => p.toLowerCase());
    if (!needle.length || needle.length > relParts.length) continue;
    for (let i = 0; i + needle.length <= relParts.length; i += 1) {
      if (needle.every((p, j) => p === relParts[i + j])) return entry;
    }
  }
  return null;
}

function dirDepth(rel: string, dirs: string[]): { depth: number; entry: string } {
  let best = { depth: 0, entry: "" };
  for (const raw of dirs) {
    const entry = parts(raw).join("/");
    if (!entry) continue;
    if (rel === entry || rel.startsWith(entry + "/")) {
      const depth = parts(entry).length;
      if (depth > best.depth) best = { depth, entry };
    }
  }
  return best;
}

function resolveInside(raw: string): { rel?: string; error?: string } {
  if (typeof raw !== "string" || !raw.trim()) return { error: "路径为空" };
  const root = path.resolve(policy.repo_root);
  const rootReal = realpathLoose(root);
  const target = path.isAbsolute(raw) ? raw : path.join(root, raw);
  const lexical = path.resolve(target);
  if (!within(root, lexical)) return { error: "路径越界（.. 穿越仓库根）" };
  const real = realpathLoose(lexical);
  if (!within(rootReal, real)) return { error: "路径越界（符号链接指向仓库根之外）" };
  const rel = path.relative(rootReal, real).split(path.sep).join("/");
  return { rel: rel === "." ? "" : rel };
}

function classifyPath(raw: string, mode: "read" | "write"): [Decision, string] {
  const resolved = resolveInside(raw);
  if (resolved.error) return ["deny", resolved.error];
  const rel = resolved.rel ?? "";
  const hit = secretHit(rel);
  if (hit) return ["deny", `敏感路径禁止访问: ${hit}`];
  if (mode === "read") return ["allow", "仓库内只读访问"];

  const writable = dirDepth(rel, policy.writable_dirs);
  const approval = dirDepth(rel, policy.approval_dirs);
  if (writable.depth && writable.depth > approval.depth) {
    return ["allow", `可写目录: ${writable.entry}`];
  }
  if (approval.depth) return ["ask", `审批目录: ${approval.entry}`];
  if (writable.depth) return ["allow", `可写目录: ${writable.entry}`];
  return ["ask", "仓库内非白名单目录，需审批"];
}

// ---------------------------------------------------------------- 命令分级

function squash(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

function prefixMatch(segment: string, item: string): boolean {
  const seg = squash(segment);
  const needle = squash(item);
  return Boolean(needle) && (seg === needle || seg.startsWith(needle + " "));
}

/** 按未引用的 ; && || | 换行拆分；同时标记写入/替换/后台等特殊特征。 */
function splitCommand(command: string): { segments: string[]; special: boolean } {
  const segments: string[] = [];
  let buf: string[] = [];
  let quote: string | null = null;
  let special = false;
  let i = 0;
  while (i < command.length) {
    const ch = command[i];
    const next = command[i + 1] ?? "";
    if (quote === "'") {
      buf.push(ch);
      if (ch === "'") quote = null;
      i += 1;
      continue;
    }
    if (quote === '"') {
      if (ch === '"') {
        quote = null;
        buf.push(ch);
      } else if (ch === "\\" && next) {
        buf.push(ch + next);
        i += 1;
      } else {
        if ((ch === "$" && next === "(") || ch === "`") special = true;
        buf.push(ch);
      }
      i += 1;
      continue;
    }
    if (ch === "'" || ch === '"') {
      quote = ch;
      buf.push(ch);
      i += 1;
      continue;
    }
    if (ch === "\\" && next) {
      buf.push(ch + next);
      i += 2;
      continue;
    }
    if (ch === "$" && next === "(") {
      special = true;
      buf.push(ch + next);
      i += 2;
      continue;
    }
    if (ch === "`") {
      special = true;
      buf.push(ch);
      i += 1;
      continue;
    }
    if (ch === ">") {
      special = true;
      buf.push(ch);
      i += 1;
      continue;
    }
    if (ch === "<" && next === "(") {
      special = true;
      buf.push(ch);
      i += 1;
      continue;
    }
    if (ch === "&") {
      if (next === "&") {
        segments.push(buf.join(""));
        buf = [];
        i += 2;
        continue;
      }
      special = true;
      buf.push(ch);
      i += 1;
      continue;
    }
    if (ch === "|") {
      segments.push(buf.join(""));
      buf = [];
      i += next === "|" ? 2 : 1;
      continue;
    }
    if (ch === ";" || ch === "\n") {
      segments.push(buf.join(""));
      buf = [];
      i += 1;
      continue;
    }
    buf.push(ch);
    i += 1;
  }
  segments.push(buf.join(""));
  return { segments: segments.map((s) => s.trim()).filter(Boolean), special };
}

function hasTee(segment: string): boolean {
  return squash(segment)
    .split(" ")
    .map((token) => token.replace(/^['"]|['"]$/g, "").replace(/\/+$/, ""))
    .filter(Boolean)
    .some((token) => token.split("/").pop()?.toLowerCase() === "tee");
}

/** 仓库外绝对路径：需要人工确认（/dev/null、/tmp 例外）。 */
function hasOutsideAbsolute(command: string): boolean {
  const matches = command.match(/(?:^|[\s'"=])(\/[^\s'"]+)/g) ?? [];
  return matches.some((raw) => {
    const token = raw.replace(/^[\s'"=]+/, "");
    if (token === "/dev/null" || token.startsWith("/tmp/")) return false;
    return !(
      token === policy.repo_root ||
      token.startsWith(policy.repo_root + "/")
    );
  });
}

function classifyBash(command: unknown): [Decision, string] {
  if (typeof command !== "string" || !command.trim()) {
    return ["ask", "空命令，需人工确认"];
  }
  for (const pattern of policy.deny) {
    let match: RegExpExecArray | null = null;
    try {
      match = new RegExp(pattern, "i").exec(command);
    } catch {
      continue;
    }
    if (match) {
      return ["deny", `命中拒绝规则: ${pattern}（匹配 ${match[0].slice(0, 80)}）`];
    }
  }

  const { segments, special } = splitCommand(command);
  if (!segments.length) return ["ask", "命令无法解析，需人工确认"];

  for (const segment of segments) {
    for (const item of policy.approval_required_always) {
      if (prefixMatch(segment, item)) return ["ask", `需审批命令: ${item}`];
    }
  }
  if (special) return ["ask", "包含写入/替换/后台执行特征，需人工确认"];
  if (hasOutsideAbsolute(command)) {
    return ["ask", "命令包含仓库外绝对路径，需人工确认"];
  }
  for (const segment of segments) {
    if (hasTee(segment)) return ["ask", "命令包含 tee/输出写入，需人工确认"];
    if (!policy.safe.some((item) => prefixMatch(segment, item))) {
      return ["ask", `非安全命令: ${segment.slice(0, 120)}`];
    }
  }
  return ["allow", "全部为安全命令"];
}

// ---------------------------------------------------------------- 审计

function audit(entry: Record<string, unknown>): void {
  try {
    const dir = path.resolve(
      policy.repo_root,
      policy.audit_dir || "data/ops-audit",
    );
    fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
    const now = new Date();
    const day = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}${String(
      now.getDate(),
    ).padStart(2, "0")}`;
    fs.appendFileSync(path.join(dir, `${day}.jsonl`), JSON.stringify({
      ts: now.toISOString(),
      ...entry,
    }) + "\n", { mode: 0o600 });
  } catch (err) {
    process.stderr.write(`[ops-guard] 审计写入失败: ${String(err)}\n`);
  }
}

function summarizeInput(tool: string, input: unknown): string {
  if (!input || typeof input !== "object") return brief(input, 600);
  const obj = input as Record<string, unknown>;
  if (tool === "bash") return `命令：${brief(obj.command, 600)}`;
  if (obj.path) return `路径：${brief(obj.path, 300)}`;
  return brief(input, 600);
}

function brief(value: unknown, limit = 300): string {
  let text: string;
  if (typeof value === "string") text = value;
  else {
    try {
      text = JSON.stringify(value);
    } catch {
      text = String(value);
    }
  }
  text = (text ?? "").replace(/\s+/g, " ").trim();
  return text.length > limit ? text.slice(0, limit) + "…" : text;
}

function toolPath(event: { toolName: string; input?: Record<string, unknown> }): {
  path?: string;
  mode: "read" | "write";
} {
  const input = event.input ?? {};
  if (event.toolName === "write" || event.toolName === "edit") {
    return { path: String(input.path ?? ""), mode: "write" };
  }
  if (event.toolName === "read") {
    return { path: String(input.path ?? ""), mode: "read" };
  }
  if (
    event.toolName === "grep" ||
    event.toolName === "find" ||
    event.toolName === "ls"
  ) {
    return {
      path: input.path === undefined ? "" : String(input.path),
      mode: "read",
    };
  }
  return { path: undefined, mode: "read" };   // 未知工具 → 上层按 ask 处理
}

// ---------------------------------------------------------------- 扩展入口

export default function (pi: ExtensionAPI): void {
  let calls = 0;

  pi.on("tool_call", async (event, ctx) => {
    calls += 1;
    const base = {
      tool: event.toolName,
      args: brief((event as { input?: unknown }).input),
    };
    if (calls > policy.max_tool_calls) {
      audit({ ...base, decision: "blocked", reason: "工具调用数超过上限" });
      return { block: true, reason: `工具调用数超过上限（${policy.max_tool_calls}）` };
    }

    let decision: Decision;
    let reason: string;
    if (event.toolName === "bash") {
      [decision, reason] = classifyBash(
        (event as { input?: { command?: unknown } }).input?.command,
      );
    } else {
      const { path: target, mode } = toolPath(
        event as { toolName: string; input?: Record<string, unknown> },
      );
      if (target === undefined) {
        decision = "ask";
        reason = `未知工具 ${event.toolName}，需人工确认`;
      } else if (!target) {
        // grep/find/ls 省略 path 时默认仓库根，按只读处理
        decision = "allow";
        reason = "默认仓库根只读";
      } else {
        [decision, reason] = classifyPath(target, mode);
      }
    }

    if (decision === "deny") {
      audit({ ...base, decision: "deny", reason });
      if (ctx.hasUI) ctx.ui.notify(`已拒绝: ${reason}`, "warning");
      return { block: true, reason };
    }
    if (decision === "ask") {
      if (!ctx.hasUI) {
        audit({ ...base, decision: "blocked", reason: "需审批但无 UI（fail-closed）" });
        return { block: true, reason: "需要人工审批，但当前会话没有审批界面（fail-closed）" };
      }
      const allowed = await ctx.ui.confirm(
        `需要确认：${event.toolName}`,
        `${summarizeInput(event.toolName, (event as { input?: unknown }).input)}\n原因：${reason}`,
      );
      if (!allowed) {
        audit({ ...base, decision: "denied", reason });
        return { block: true, reason: "用户拒绝该操作" };
      }
      audit({ ...base, decision: "approved", reason });
      return undefined;
    }

    audit({ ...base, decision: "allow", reason });
    return undefined;
  });
}
