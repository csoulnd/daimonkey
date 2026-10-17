/* 智能助手悬浮组件：全站宠物入口 + 按需登录 + 会话 + 工作流 + 用量（原生 JS） */
(function () {
  "use strict";

  var API = "/api/v1/assistant";
  var state = { authed: false, user: null, sessionId: null, running: false,
                pollTimer: null, open: false, renameTarget: null, deleteTarget: null };

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

  /* 顶栏账号区:未登录=「登录」链接;登录后=头像(首字母)+下拉(用户名/退出) */
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
    avatar.textContent = name.charAt(0).toUpperCase();
    var menu = el("div", "avatar-menu");
    menu.hidden = true;
    var mi1 = el("div", "avatar-name", name);
    var mi2 = el("button", "avatar-out", "退出登录");
    mi2.type = "button";
    mi2.addEventListener("click", function () {
      api("/logout", { method: "POST" }).then(function () { location.href = "/"; });
    });
    menu.appendChild(mi1); menu.appendChild(mi2);
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
    stopPolling();
  }

  function refreshAll() {
    refreshSessions();
    refreshUsage();
    loadWorkflows();
  }

  /* ---- 会话 ---- */
  function refreshSessions() {
    api("/sessions").then(function (res) {
      if (res.status === 401) { state.authed = false; return; }
      if (!res.ok) return;
      var list = $("asst-session-list");
      list.textContent = "";
      var sessions = res.body.sessions || [];
      sessions.forEach(function (s) {
        var li = el("li", s.id === state.sessionId ? "on" : "");
        li.dataset.sid = String(s.id);  // selectSession 高亮同步依赖此标记
        var title = el("span", "asst-s-title", (s.title || ("会话 " + s.id)));
        title.title = s.title || "";
        li.appendChild(title);
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
    var generation = ++selectGeneration;
    api("/sessions/" + id + "/messages").then(function (res) {
      if (generation !== selectGeneration) return;  // 已切走，丢弃过期响应
      if (res.ok) renderMessages(res.body.messages || []);
    });
  }
  function newSession() {
    withAuth(function () {
      return api("/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "" })
      }).then(function (res) {
        if (res.ok) {
          state.sessionId = res.body.session_id;
          renderMessages([]);
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
        startRun("/chat", { session_id: sid, message: text });
      });
    });
  }

  function ensureSession() {
    if (state.sessionId) return Promise.resolve(state.sessionId);
    return api("/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "" })
    }).then(function (res) {
      if (!res.ok) { msg(res.body.error || "创建会话失败", true); return null; }
      state.sessionId = res.body.session_id;
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
            msg("网络中断，任务状态未知；网络恢复后可重新打开面板查看", true);
          }
          return;
        }
        failures = 0;
        if (!res.ok || res.body.state === "running") return;
        finishRun();
        if (res.body.state === "success") {
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

  /* ---- 用量（低调显示在面板底部） ---- */
  function refreshUsage() {
    api("/usage").then(function (res) {
      if (!res.ok) return;
      var box = $("asst-usage");
      box.textContent = "";
      var u = res.body.usage || {};
      ["interactive", "internal"].forEach(function (ch) {
        var v = u[ch];
        if (!v) return;
        var line = el("span", "asst-usage-line" +
          (v.limit && v.requests / v.limit >= 0.8 ? " warn" : ""));
        line.textContent = "今日" + (ch === "interactive" ? "助手" : "内部")
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
        showUser(null);
        $("asst-logout").hidden = true;
        renderMessages([]);
        refreshAll();
        msg("已退出登录（下次使用需重新输入账号密码）");
      });
    });
    $("asst-new").addEventListener("click", newSession);
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
    // 启动时静默探一次登录态（Cookie 7 天有效，多数时候直接就绪）
    api("/session").then(function (res) {
      state.authed = !!(res.ok && res.body.authed);
      if (state.authed) {
        showUser(res.body.user || null);
        $("asst-logout").hidden = false;
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
