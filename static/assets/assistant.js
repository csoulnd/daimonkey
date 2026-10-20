/* 智能助手悬浮组件：全站宠物入口 + 按需登录 + 会话 + 工作流 + 用量（原生 JS） */
(function () {
  "use strict";

  var API = "/api/v1/assistant";
  var state = { authed: false, user: null, role: null, opsEnabled: false,
                sessionId: null, modes: {}, running: false,
                pollTimer: null, open: false, renameTarget: null, deleteTarget: null,
                rollback: null,
                /* 运维 run：run_id / 增量 seq / 并发令牌 / 流式与工具 DOM 引用 */
                runId: null, runSeq: 0, runToken: 0, opsTimer: null,
                streamEl: null, toolsEl: null, bubbleEl: null, toolList: [],
                pendingApproval: null, approvalBusy: false, doneReply: "" };

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function msg(text, isErr) {
    var box = $("asst-msg");
    if (box) {
      box.textContent = text || "";
      box.className = "asst-msg" + (isErr ? " err" : "");
    }
  }
  function fmtTokens(n) {
    return n >= 1000000 ? (n / 1000000).toFixed(1) + "M"
      : n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n);
  }
  function scrollMessages() {
    var box = $("asst-messages");
    if (box) box.scrollTop = box.scrollHeight;
  }

  /* ---- 会话模式：对话 / 运维（仅 admin 且 registry 开启 ops 时可用） ---- */
  function opsAvailable() {
    return state.role === "admin" && !!state.opsEnabled;
  }
  function currentMode() {
    if (!opsAvailable()) return "chat";
    var key = state.sessionId ? String(state.sessionId) : "new";
    return state.modes[key] === "ops" ? "ops" : "chat";
  }

  /* 切换当前会话模式；尚无会话时先记在 "new" 槽，建会话时带上。 */
  function setMode(mode) {
    if (!opsAvailable() || state.running) return;
    mode = mode === "ops" ? "ops" : "chat";
    if (!state.sessionId) {
      state.modes.new = mode;
      updateModeUI();
      return;
    }
    var sid = state.sessionId;
    api("/sessions/" + sid + "/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: mode })
    }).then(function (res) {
      if (!res.ok) { msg(res.body.error || "切换模式失败", true); return; }
      state.modes[String(sid)] = mode;
      updateModeUI();
      msg(mode === "ops" ? "已切到运维模式（工具操作会请求审批）" : "已切到对话模式");
      refreshSessions();
    });
  }
  function updateModeUI() {
    var wrap = $("asst-mode");
    if (!wrap) return;
    var avail = opsAvailable();
    var mode = currentMode();
    wrap.hidden = !avail;
    Array.prototype.forEach.call(
      wrap.querySelectorAll("button[data-mode]"), function (b) {
        var on = b.getAttribute("data-mode") === mode;
        b.classList.toggle("on", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
    var input = $("asst-input");
    if (input) {
      input.placeholder = (avail && mode === "ops")
        ? "输入运维指令，Ctrl+Enter 发送" : "输入消息，Ctrl+Enter 发送";
    }
  }
  function loadSessionMeta() {
    return api("/session").then(function (res) {
      var b = (res && res.body) || {};
      var ok = !!(res && res.ok && b.authed);
      state.role = ok ? (b.role || "member") : null;
      state.opsEnabled = ok ? !!b.ops_enabled : false;
      updateModeUI();
      return b;
    });
  }

  /* ---- 大马猴表情:切换宠物/面板标题的 symbol 与 data-face 动效 ---- */
  var _faceTimer = null;
  function petFace(face) {
    if (_faceTimer) { clearTimeout(_faceTimer); _faceTimer = null; }
    ["asst-pet", "asst-panel-title"].forEach(function (id) {
      var wrap = $(id);
      if (!wrap) return;
      var use = wrap.querySelector("use");
      if (use) use.setAttribute("href", face ? "#i-monkey-" + face : "#i-monkey");
      if (face) wrap.setAttribute("data-face", face);
      else wrap.removeAttribute("data-face");
    });
  }
  function petFaceThen(face, ms) {
    petFace(face);
    _faceTimer = setTimeout(function () { petFace(null); _faceTimer = null; }, ms);
  }

  function api(path, options) {
    return fetch(API + path, Object.assign({ credentials: "same-origin" },
      options || {})).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        return { ok: r.ok, status: r.status, body: body };
      });
    }).catch(function () {
      // 网络层失败(断网/服务重启):返回统一形状,调用方按非 2xx 处理
      return { ok: false, status: 0, body: {} };
    });
  }

  /* ---- 按需登录：需要权限时展示用户名+密码行，成功后重试原动作 ---- */
  function askPassword() {
    return new Promise(function (resolve) {
      var row = $("asst-login-row");
      var userInput = $("asst-user");
      var input = $("asst-password");
      row.hidden = false;
      userInput.value = "";
      input.value = "";
      if (!state.user) userInput.focus(); else input.focus();
      function done(value) {
        row.hidden = true;
        userInput.removeEventListener("keydown", onUserKey);
        input.removeEventListener("keydown", onKey);
        resolve(value);
      }
      function credentials() {
        return { username: userInput.value.trim(), password: input.value };
      }
      function onUserKey(ev) {
        if (ev.key === "Enter") {
          ev.preventDefault();
          input.focus();  // 用户名回车跳到密码框
        } else if (ev.key === "Escape") {
          done(null);
        }
      }
      function onKey(ev) {
        if (ev.key === "Enter") {
          ev.preventDefault();
          if (input.value) done(credentials());
        } else if (ev.key === "Escape") {
          done(null);
        }
      }
      userInput.addEventListener("keydown", onUserKey);
      input.addEventListener("keydown", onKey);
      msg("此操作需要登录，请输入用户名与密码", true);
    });
  }

  function showUser(name) {
    state.user = name || null;
    var label = $("asst-user-label");
    if (label) {
      label.textContent = name || "";
      label.hidden = !name;
    }
    renderAccount(name);
    // 工作台入口显隐:localStorage 记录 + html.authed 类,
    // head 预载脚本在首帧前挂类,已登录刷新不再闪烁;探针失败自动摘除。
    document.documentElement.classList.toggle("authed", !!name);
    try {
      if (name) {
        localStorage.setItem("authed", "1");
        localStorage.setItem("authuser", name);
      } else {
        localStorage.removeItem("authed");
        localStorage.removeItem("authuser");
      }
    } catch (e) { /* 隐私模式 */ }
  }

  /* 顶栏账号区:未登录=「登录」链接;登录后=猴子头像(可换表情)+下拉(用户名/换表情/退出) */
  var AVATARS = [["monkey", "平静"], ["happy", "开心"], ["angry", "生气"], ["hat", "戴帽子"]];
  function getAvatar() {
    try {
      var v = localStorage.getItem("dm-avatar");
      for (var i = 0; i < AVATARS.length; i++) if (AVATARS[i][0] === v) return v;
    } catch (e) { /* 隐私模式 */ }
    return "monkey";
  }
  function renderAccount(name) {
    var old = $("account-box");
    if (old) old.parentNode.removeChild(old);
    var loginLink = $("nav-login");
    if (loginLink) loginLink.style.display = name ? "none" : "";
    if (!name || !loginLink || !loginLink.parentNode) return;
    var box = el("span", "account-box");
    box.id = "account-box";
    var avatar = el("button", "avatar");
    avatar.type = "button";
    avatar.title = name;
    avatar.innerHTML = '<svg aria-hidden="true"><use href="#i-ava-' + getAvatar() + '"/></svg>';
    var menu = el("div", "avatar-menu");
    menu.hidden = true;
    var mi1 = el("div", "avatar-name", name);
    var mi2 = el("button", "avatar-out", "换个表情");
    mi2.type = "button";
    mi2.addEventListener("click", function (ev) {
      ev.stopPropagation();
      var cur = getAvatar(), i = 0;
      while (i < AVATARS.length && AVATARS[i][0] !== cur) i++;
      var next = AVATARS[(i + 1) % AVATARS.length][0];
      try { localStorage.setItem("dm-avatar", next); } catch (e) { /* 隐私模式 */ }
      var use = avatar.querySelector("use");
      if (use) use.setAttribute("href", "#i-ava-" + next);
    });
    var mi3 = el("button", "avatar-out", "退出登录");
    mi3.type = "button";
    mi3.addEventListener("click", function () {
      api("/logout", { method: "POST" }).then(function () { location.href = "/"; });
    });
    menu.appendChild(mi1); menu.appendChild(mi2); menu.appendChild(mi3);
    avatar.addEventListener("click", function (ev) {
      ev.stopPropagation();
      menu.hidden = !menu.hidden;
    });
    document.addEventListener("click", function () { menu.hidden = true; });
    box.appendChild(avatar); box.appendChild(menu);
    loginLink.parentNode.insertBefore(box, loginLink);
  }

  function login(credentials) {
    return api("/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(credentials)
    }).then(function (res) {
      if (res.ok) {
        state.authed = true;
        showUser(res.body.user || credentials.username);
        var btn = $("asst-logout");
        if (btn) btn.hidden = false;
        loadSessionMeta();  // 取 role / ops_enabled，决定是否显示模式切换
        return true;
      }
      if (res.status === 401) state.authed = false;  // 过期/伪造态自愈
      msg(res.status === 429 ? "尝试次数过多，已临时锁定" : "用户名或密码错误", true);
      return false;
    });
  }

  /* 需要登录的动作包装：未登录 → 登录行 → 登录 → 重试一次 */
  function withAuth(action) {
    if (state.authed) return action();
    return askPassword().then(function (credentials) {
      if (!credentials) return null;
      return login(credentials).then(function (ok) { return ok ? action() : null; });
    });
  }

  /* ---- 面板开合 ---- */
  function openPanel() {
    state.open = true;
    $("asst-panel").hidden = false;
    $("asst-pet").classList.add("hide");
    refreshAll();  // refreshSessions 在无当前会话时自动选中最近一个
    $("asst-input").focus();
  }
  function closePanel() {
    state.open = false;
    $("asst-panel").hidden = true;
    $("asst-pet").classList.remove("hide");
    abandonRun();  // 停止轮询并复位运行态；服务端任务继续，重开面板后可看历史
  }

  function refreshAll() {
    refreshSessions();
    refreshUsage();
    loadWorkflows();
  }

  /* 关闭面板：只停止前端轮询/复位 UI，服务端任务继续（重开可看历史） */
  function abandonRun() {
    stopPolling();
    stopOpsPoll();
    state.runToken += 1;          // 让晚到的轮询响应失效
    state.running = false;
    state.runId = null;
    state.bubbleEl = null;
    state.streamEl = null;
    state.toolsEl = null;
    state.toolList = [];
    state.rollback = null;
    hideApproval();
    setStopVisible(false);
    $("asst-send").disabled = false;
  }

  /* ---- 会话 ---- */
  function refreshSessions() {
    api("/sessions").then(function (res) {
      if (res.status === 401) { state.authed = false; return; }
      if (!res.ok) return;
      var list = $("asst-session-list");
      list.textContent = "";
      var sessions = res.body.sessions || [];
      var modes = {};
      sessions.forEach(function (s) {
        modes[String(s.id)] = s.mode === "ops" ? "ops" : "chat";
        var li = el("li", s.id === state.sessionId ? "on" : "");
        li.dataset.sid = String(s.id);  // selectSession 高亮同步依赖此标记
        var title = el("span", "asst-s-title", (s.title || ("会话 " + s.id)));
        title.title = s.title || "";
        li.appendChild(title);
        if (s.mode === "ops") {  // 运维会话标记
          var modeTag = el("span", "asst-s-mode", "⚙");
          modeTag.title = "运维会话";
          li.appendChild(modeTag);
        }
        var acts = el("span", "asst-s-acts");
        var btnRename = el("button", null, "✎");
        btnRename.type = "button";
        btnRename.title = "重命名";
        btnRename.onclick = function (ev) { ev.stopPropagation(); openRename(s); };
        var btnDel = el("button", null, "🗑");
        btnDel.type = "button";
        btnDel.title = "删除";
        btnDel.onclick = function (ev) { ev.stopPropagation(); openDelete(s); };
        acts.appendChild(btnRename);
        acts.appendChild(btnDel);
        li.appendChild(acts);
        li.onclick = function () { selectSession(s.id); };
        list.appendChild(li);
      });
      state.modes = modes;
      updateModeUI();
      if (!sessions.length) {
        var empty = el("li", "asst-s-empty", "（无活跃会话）");
        list.appendChild(empty);
        return;
      }
      // 当前会话被删除后自动切换到最近的一个
      if (!state.sessionId || !sessions.some(function (s) { return s.id === state.sessionId; })) {
        selectSession(sessions[0].id);
      }
    });
  }
  /* 会话切换：高亮同步 + 异步竞态防护（晚到的旧响应不再覆盖新内容） */
  var selectGeneration = 0;

  function highlightSession() {
    document.querySelectorAll("#asst-session-list li").forEach(function (li) {
      li.classList.toggle("on", li.dataset.sid === String(state.sessionId));
    });
  }

  function selectSession(id) {
    state.sessionId = id;
    highlightSession();
    updateModeUI();
    var generation = ++selectGeneration;
    api("/sessions/" + id + "/messages").then(function (res) {
      if (generation !== selectGeneration) return;  // 已切走，丢弃过期响应
      if (res.ok) renderMessages(res.body.messages || []);
    });
  }
  function newSession() {
    withAuth(function () {
      var mode = currentMode();
      return api("/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "", mode: mode })
      }).then(function (res) {
        if (res.ok) {
          state.sessionId = res.body.session_id;
          state.modes[String(state.sessionId)] = res.body.mode === "ops" ? "ops" : "chat";
          renderMessages([]);
          updateModeUI();
          refreshSessions();
        } else { msg(res.body.error || "创建失败", true); }
      });
    });
  }
  /* ---- 面板内闭环：行内重命名 / 行内删除确认（不用浏览器原生弹窗，避免焦点抢占导致页面滚动） ---- */
  function anyInlineRowOpen() {
    return ["asst-rename-row", "asst-delete-row", "asst-login-row"]
      .some(function (id) { var n = $(id); return n && !n.hidden; });
  }
  function hideInlineRows() {
    ["asst-rename-row", "asst-delete-row", "asst-login-row"].forEach(function (id) {
      var n = $(id); if (n) n.hidden = true;
    });
  }

  function openRename(session) {
    var row = $("asst-rename-row");
    var input = $("asst-rename-input");
    hideInlineRows();
    state.renameTarget = session.id;
    input.value = session.title || "";
    row.hidden = false;
    input.focus();
    input.select();
  }

  function submitRename() {
    var row = $("asst-rename-row");
    var input = $("asst-rename-input");
    var sid = state.renameTarget;
    row.hidden = true;
    state.renameTarget = null;
    if (!sid) return;
    var title = input.value.trim();
    if (!title) return;
    withAuth(function () {
      return api("/sessions/" + sid + "/rename", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title })
      }).then(function (res) {
        if (res.ok) { msg("已重命名"); refreshSessions(); }
        else { msg(res.body.error || "重命名失败", true); }
      });
    });
  }

  function openDelete(session) {
    var row = $("asst-delete-row");
    hideInlineRows();
    state.deleteTarget = session.id;
    row.hidden = false;
  }

  function submitDelete() {
    var row = $("asst-delete-row");
    var sid = state.deleteTarget;
    row.hidden = true;
    state.deleteTarget = null;
    if (!sid) return;
    withAuth(function () {
      return api("/sessions/" + sid, { method: "DELETE" }).then(function (res) {
        if (res.ok) {
          if (state.sessionId === sid) {
            state.sessionId = null;
            renderMessages([]);
          }
          refreshSessions();
          msg("会话已删除");
        } else { msg(res.body.error || "删除失败", true); }
      });
    });
  }

  function renderMessages(items) {
    var box = $("asst-messages");
    box.textContent = "";
    items.forEach(function (m) {
      var div = el("div", "asst-bubble " + (m.role === "user" ? "u" : "a"));
      if (m.workflow) div.appendChild(el("div", "asst-wf-tag", "⚙ " + m.workflow));
      div.appendChild(el("div", "asst-bubble-body", m.content == null ? "" : m.content));
      box.appendChild(div);
    });
    box.scrollTop = box.scrollHeight;
  }

  /* ---- 对话 + 轮询 ---- */
  function sendMessage() {
    if (state.running) return;
    var input = $("asst-input");
    var text = input.value.trim();
    if (!text) return;
    withAuth(function () {
      return ensureSession().then(function (sid) {
        if (!sid) return;
        var box = $("asst-messages");
        var bubble = el("div", "asst-bubble u");
        bubble.appendChild(el("div", "asst-bubble-body", text));
        box.appendChild(bubble);
        box.scrollTop = box.scrollHeight;
        input.value = "";
        msg("");
        // 发送失败(401/409/配额/网络):撤回气泡并还原输入,长文不丢
        var rollback = function () {
          if (bubble.parentNode) bubble.parentNode.removeChild(bubble);
          input.value = text;
        };
        state.rollback = rollback;
        if (currentMode() === "ops") startOpsRun(sid, text);
        else startRun("/chat", { session_id: sid, message: text });
      });
    });
  }

  function ensureSession() {
    if (state.sessionId) return Promise.resolve(state.sessionId);
    var mode = currentMode();
    return api("/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "", mode: mode })
    }).then(function (res) {
      if (!res.ok) { msg(res.body.error || "创建会话失败", true); return null; }
      state.sessionId = res.body.session_id;
      state.modes[String(state.sessionId)] = res.body.mode === "ops" ? "ops" : "chat";
      updateModeUI();
      refreshSessions();
      return state.sessionId;
    });
  }

  function runWorkflow(name, description) {
    if (state.running) return;
    withAuth(function () {
      var payload = {};
      if (name === "content_polish") {
        var text = window.prompt(description + "\n请输入要润色的文案：", "");
        if (!text) return null;
        payload = { text: text };
      }
      if (!window.confirm("运行工作流「" + name + "」？")) return null;
      return startRun("/workflows/" + encodeURIComponent(name), payload);
    });
  }

  function startRun(path, body) {
    state.running = true;
    $("asst-send").disabled = true;
    petFace("thinking");
    msg("执行中…");
    api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (res) {
      if (res.status === 401) { state.authed = false; failedRun("登录已过期，请重试"); return; }
      if (res.status === 409) { failedRun("已有一个任务进行中"); return; }
      if (!res.ok) { failedRun(res.body.error || ("HTTP " + res.status)); return; }
      pollStatus();
    }).catch(function () {
      failedRun("网络错误");
    });
  }

  /* 启动即失败:收尾 + 撤回乐观气泡并还原输入(仅聊天路径设置了 rollback) */
  function failedRun(text) {
    finishRun();
    stopOpsPoll();
    setStopVisible(false);
    if (state.bubbleEl && state.bubbleEl.parentNode) {
      state.bubbleEl.parentNode.removeChild(state.bubbleEl);
    }
    state.bubbleEl = null;
    petFaceThen("error", 2000);
    if (state.rollback) {
      try { state.rollback(); } catch (e) { /* 忽略 */ }
      state.rollback = null;
    }
    msg(text, true);
  }

  function pollStatus() {
    stopPolling();
    var failures = 0;
    state.pollTimer = setInterval(function () {
      api("/status").then(function (res) {
        if (res.status === 0) {
          // 网络错误:连续 10 次(约 20 秒)后放弃,避免按钮永久卡死
          if (++failures >= 10) {
            finishRun();
            petFaceThen("error", 2000);
            msg("网络中断，任务状态未知；网络恢复后可重新打开面板查看", true);
          }
          return;
        }
        failures = 0;
        if (!res.ok || res.body.state === "running") return;
        finishRun();
        if (res.body.state === "success") {
          petFaceThen("happy", 1600);
          msg("完成");
          if (res.body.workflow && res.body.reply) {
            var box = $("asst-messages");
            var bubble = el("div", "asst-bubble a");
            bubble.appendChild(el("div", "asst-wf-tag", "⚙ " + res.body.workflow));
            bubble.appendChild(el("div", "asst-bubble-body", res.body.reply));
            box.appendChild(bubble);
            box.scrollTop = box.scrollHeight;
          } else if (state.sessionId) {
            selectSession(state.sessionId);
          }
        } else {
          petFaceThen("error", 2000);
          msg(res.body.error || "执行失败", true);
        }
        refreshUsage();
      });
    }, 2000);
  }
  function stopPolling() {
    if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
  }
  function finishRun() {
    stopPolling();
    state.running = false;
    state.rollback = null;  // 任务已进入执行,乐观消息由服务端落库,不再回滚
    $("asst-send").disabled = false;
  }

  /* ---- 运维轮（ops）：流式文本 + 工具卡片 + 审批 + 停止 ----
   * 与 /chat 的区别：后端后台线程执行，前端按 run_id 增量轮询 events；
   * 每轮一个 pi 子进程，工具与审批事件由服务端缓冲（见 collector/assistant.py）。 */
  function setStopVisible(on) {
    var btn = $("asst-stop");
    if (btn) { btn.hidden = !on; btn.disabled = false; }
  }

  function startOpsRun(sid, text) {
    var token = ++state.runToken;
    state.running = true;
    state.runId = null;
    state.runSeq = 0;
    state.doneReply = "";
    state.pendingApproval = null;
    hideApproval();
    $("asst-send").disabled = true;
    setStopVisible(true);
    petFace("thinking");
    msg("运维任务执行中…");

    var box = $("asst-messages");
    var bubble = el("div", "asst-bubble a asst-ops");
    var tools = el("div", "asst-tools");
    var stream = el("div", "asst-bubble-body asst-stream", "");
    bubble.appendChild(tools);
    bubble.appendChild(stream);
    box.appendChild(bubble);
    scrollMessages();
    state.bubbleEl = bubble;
    state.toolsEl = tools;
    state.streamEl = stream;
    state.toolList = [];

    api("/ops/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sid, message: text })
    }).then(function (res) {
      if (token !== state.runToken) return;
      if (res.status === 401) { state.authed = false; failedRun("登录已过期，请重试"); return; }
      if (!res.ok) { failedRun(res.body.error || ("HTTP " + res.status)); return; }
      state.runId = res.body.run_id;
      pollOps(token);
    }).catch(function () {
      if (token === state.runToken) failedRun("网络错误");
    });
  }

  function stopOpsPoll() {
    if (state.opsTimer) { clearInterval(state.opsTimer); state.opsTimer = null; }
  }

  function pollOps(token) {
    stopOpsPoll();
    var failures = 0;
    state.opsTimer = setInterval(function () {
      if (token !== state.runToken || !state.runId) { stopOpsPoll(); return; }
      api("/ops/runs/" + state.runId + "?after=" + state.runSeq).then(function (res) {
        if (token !== state.runToken) return;
        if (res.status === 0) {
          if (++failures >= 10) {
            stopOpsPoll();
            finishOps({ state: "failed", error: "网络中断，任务状态未知" }, token);
          }
          return;
        }
        failures = 0;
        if (!res.ok) { finishOps({ state: "failed", error: res.body.error || ("HTTP " + res.status) }, token); return; }
        var body = res.body || {};
        (body.events || []).forEach(function (ev) {
          if (ev.seq && ev.seq > state.runSeq) state.runSeq = ev.seq;
          handleOpsEvent(ev);
        });
        if (typeof body.text === "string" && state.streamEl) {
          state.streamEl.textContent = body.text;
          scrollMessages();
        }
        if (body.pending) showApproval(body.pending);
        else hideApproval();
        if (body.state !== "running") finishOps(body, token);
      });
    }, 600);
  }

  function handleOpsEvent(ev) {
    if (ev.type === "tool_start") addToolCard(ev.tool || "?", ev.args, null);
    else if (ev.type === "tool_end") finishToolCard(ev.tool || "?", ev.status, ev.output, ev.duration_s);
    else if (ev.type === "approval") showApproval(ev);
    else if (ev.type === "approval_resolved") hideApproval();
    else if (ev.type === "done" && ev.reply && state.streamEl) {
      state.doneReply = ev.reply;
      state.streamEl.textContent = ev.reply;
      scrollMessages();
    }
  }

  function addToolCard(tool, args, output) {
    var wrap = el("details", "asst-tool");
    wrap.open = false;
    var sum = el("summary");
    sum.appendChild(el("span", "asst-tool-name", tool));
    var brief = String(args == null ? "" : args).replace(/\s+/g, " ").slice(0, 140);
    if (brief) sum.appendChild(el("code", "asst-tool-args", brief));
    wrap.appendChild(sum);
    var out = el("pre", "asst-tool-out", output || "执行中…");
    wrap.appendChild(out);
    if (state.toolsEl) state.toolsEl.appendChild(wrap);
    state.toolList.push({ tool: tool, node: wrap, out: out, done: false });
    scrollMessages();
  }

  function finishToolCard(tool, status, output, duration) {
    var item = null;
    for (var i = state.toolList.length - 1; i >= 0; i -= 1) {
      if (!state.toolList[i].done && state.toolList[i].tool === tool) { item = state.toolList[i]; break; }
    }
    if (!item) { addToolCard(tool, "", output); item = state.toolList[state.toolList.length - 1]; }
    item.done = true;
    item.node.classList.add(status === "error" ? "err" : "ok");
    var suffix = status === "error" ? "失败" : "完成";
    if (duration != null) suffix += " · " + duration + "s";
    var sum = item.node.querySelector("summary");
    if (sum) sum.appendChild(el("span", "asst-tool-state", suffix));
    item.out.textContent = output || "（无输出）";
    scrollMessages();
  }

  function showApproval(pending) {
    var box = $("asst-approval");
    if (!box || !pending) return;
    state.pendingApproval = pending;
    $("asst-approval-title").textContent = " " + (pending.tool || "");
    $("asst-approval-cmd").textContent = pending.command || pending.title || "";
    box.hidden = false;
    state.approvalBusy = false;
    $("asst-approval-allow").disabled = false;
    $("asst-approval-deny").disabled = false;
    scrollMessages();
  }

  function hideApproval() {
    var box = $("asst-approval");
    if (box) box.hidden = true;
    state.pendingApproval = null;
  }

  function resolveApproval(allow) {
    if (!state.runId || !state.pendingApproval || state.approvalBusy) return;
    var aid = state.pendingApproval.id;
    state.approvalBusy = true;
    $("asst-approval-allow").disabled = true;
    $("asst-approval-deny").disabled = true;
    api("/ops/runs/" + state.runId + "/approvals/" + aid, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ allow: !!allow })
    }).then(function (res) {
      state.approvalBusy = false;
      if (!res.ok) {
        $("asst-approval-allow").disabled = false;
        $("asst-approval-deny").disabled = false;
        msg(res.body.error || "审批失败", true);
        return;
      }
      hideApproval();
      msg(allow ? "已允许" : "已拒绝");
    });
  }

  function stopOpsRun() {
    if (!state.runId || !state.running) return;
    setStopVisible(true);
    $("asst-stop").disabled = true;
    msg("正在停止…");
    api("/ops/runs/" + state.runId + "/abort", { method: "POST" }).then(function (res) {
      if (!res.ok) msg(res.body.error || "停止失败", true);
    });
  }

  function finishOps(body, token) {
    if (token !== state.runToken) return;
    stopOpsPoll();
    state.running = false;
    state.runId = null;
    state.rollback = null;
    $("asst-send").disabled = false;
    setStopVisible(false);
    hideApproval();
    var err = body && body.error;
    if (err) {
      petFaceThen("error", 2000);
      msg(err, true);
    } else if (body && body.aborted) {
      petFaceThen("calm", 1600);
      msg("已停止");
    } else {
      petFaceThen("happy", 1600);
      var count = (body && body.tool_count) || (state.toolList || []).length;
      msg(count ? ("完成 · 工具 " + count + " 次") : "完成");
    }
    refreshUsage();
    refreshSessions();
  }

  /* ---- 用量（低调显示在面板底部） ---- */
  function refreshUsage() {
    api("/usage").then(function (res) {
      if (!res.ok) return;
      var box = $("asst-usage");
      box.textContent = "";
      var u = res.body.usage || {};
      [["interactive", "助手"], ["internal", "内部"], ["ops", "运维"]].forEach(function (pair) {
        var ch = pair[0], v = u[ch];
        if (!v) return;
        var line = el("span", "asst-usage-line" +
          (v.limit && v.requests / v.limit >= 0.8 ? " warn" : ""));
        line.textContent = "今日" + pair[1]
          + " " + v.requests + "/" + v.limit
          + " · " + fmtTokens(v.tokens) + "tok";
        box.appendChild(line);
      });
    });
  }

  /* ---- 工作流 ---- */
  function loadWorkflows() {
    api("/workflows").then(function (res) {
      if (!res.ok) return;
      var box = $("asst-workflows");
      box.textContent = "";
      (res.body.workflows || []).forEach(function (w) {
        var btn = el("button", "asst-wf-btn", w.name);
        btn.title = w.description;
        btn.addEventListener("click", function () { runWorkflow(w.name, w.description); });
        box.appendChild(btn);
      });
    });
  }

  /* ---- 初始化 ---- */
  function init() {
    var pet = $("asst-pet");
    pet.addEventListener("click", openPanel);
    pet.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openPanel(); }
    });
    $("asst-min").addEventListener("click", closePanel);
    /* 字号动态缩放（面板令牌 --asst-scale，记忆在 localStorage） */
    var FONT_KEY = "asst-font-scale";
    var FONT_STEPS = [0.85, 1, 1.15, 1.3];
    var fontScale = 1;
    try {
      var saved = parseFloat(localStorage.getItem(FONT_KEY));
      if (saved >= 0.8 && saved <= 1.4) fontScale = saved;
    } catch (e) { /* 隐私模式 */ }
    function applyFontScale() {
      $("asst-panel").style.setProperty("--asst-scale", String(fontScale));
      try { localStorage.setItem(FONT_KEY, String(fontScale)); } catch (e) { }
    }
    applyFontScale();
    function stepFontScale(delta) {
      var idx = FONT_STEPS.indexOf(fontScale);
      if (idx < 0) idx = FONT_STEPS.indexOf(1);
      idx = Math.min(FONT_STEPS.length - 1, Math.max(0, idx + delta));
      fontScale = FONT_STEPS[idx];
      applyFontScale();
      msg("字号 " + Math.round(fontScale * 100) + "%");
    }
    $("asst-font-minus").addEventListener("click", function () { stepFontScale(-1); });
    $("asst-font-plus").addEventListener("click", function () { stepFontScale(1); });
    $("asst-max").addEventListener("click", function () {
      var panel = $("asst-panel");
      var maxed = panel.classList.toggle("max");
      this.title = maxed ? "切换回小窗" : "切换大窗";
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key !== "Escape" || !state.open) return;
      if (anyInlineRowOpen()) { hideInlineRows(); return; }  // 先关行内框，再最小化
      closePanel();
    });
    $("asst-logout").addEventListener("click", function () {
      api("/logout", { method: "POST" }).then(function () {
        state.authed = false;
        state.sessionId = null;
        state.role = null;
        state.opsEnabled = false;
        state.modes = {};
        updateModeUI();
        showUser(null);
        $("asst-logout").hidden = true;
        renderMessages([]);
        refreshAll();
        msg("已退出登录（下次使用需重新输入账号密码）");
      });
    });
    $("asst-new").addEventListener("click", newSession);
    // 会话模式切换（对话 / 运维；仅 admin 且 registry 开启 ops 时显示）
    var modeWrap = $("asst-mode");
    if (modeWrap) {
      modeWrap.addEventListener("click", function (ev) {
        var btn = ev.target.closest ? ev.target.closest("button[data-mode]") : null;
        if (btn) setMode(btn.getAttribute("data-mode"));
      });
    }
    $("asst-stop").addEventListener("click", stopOpsRun);
    $("asst-approval-allow").addEventListener("click", function () { resolveApproval(true); });
    $("asst-approval-deny").addEventListener("click", function () { resolveApproval(false); });
    // 行内重命名 / 删除确认（侧栏每项的 ✎/🗑 触发 openRename/openDelete）
    $("asst-rename-ok").addEventListener("click", submitRename);
    $("asst-rename-cancel").addEventListener("click", function () {
      $("asst-rename-row").hidden = true;
    });
    $("asst-rename-input").addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); submitRename(); }
      else if (ev.key === "Escape") { ev.stopPropagation(); $("asst-rename-row").hidden = true; }
    });
    $("asst-delete-ok").addEventListener("click", submitDelete);
    $("asst-delete-cancel").addEventListener("click", function () {
      $("asst-delete-row").hidden = true;
    });
    $("asst-form").addEventListener("submit", function (ev) {
      ev.preventDefault();
      sendMessage();
    });
    $("asst-input").addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
        ev.preventDefault();
        sendMessage();
      }
    });
    // 启动时静默探一次登录态（Cookie 7 天有效，多数时候直接就绪）；
    // 走 loadSessionMeta 才能一并拿到 role/ops_enabled，决定是否显示运维模式。
    loadSessionMeta().then(function (body) {
      state.authed = !!(body && body.authed);
      if (state.authed) {
        showUser(body.user || null);
        $("asst-logout").hidden = false;
        refreshAll();          // 已有 Cookie 时直接拉会话/用量，省一次展开
      } else {
        // 过期/伪造态:清除首帧标记,工作台入口收回
        showUser(null);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
