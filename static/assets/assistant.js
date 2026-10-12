/* 智能助手悬浮组件：全站宠物入口 + 按需登录 + 会话 + 工作流 + 用量（原生 JS） */
(function () {
  "use strict";

  var API = "/api/v1/assistant";
  var state = { authed: false, sessionId: null, running: false, pollTimer: null,
                open: false, renameTarget: null, deleteTarget: null };

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
    });
  }

  /* ---- 按需登录：需要权限时展示密码行，成功后重试原动作 ---- */
  function askPassword() {
    return new Promise(function (resolve) {
      var row = $("asst-login-row");
      var input = $("asst-password");
      row.hidden = false;
      input.value = "";
      input.focus();
      function done(value) {
        row.hidden = true;
        input.removeEventListener("keydown", onKey);
        resolve(value);
      }
      function onKey(ev) {
        if (ev.key === "Enter") {
          ev.preventDefault();
          var v = input.value.trim();
          if (v) done(v);
        } else if (ev.key === "Escape") {
          done(null);
        }
      }
      input.addEventListener("keydown", onKey);
      msg("此操作需要登录，请输入密码", true);
    });
  }

  function login(password) {
    return api("/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: password })
    }).then(function (res) {
      if (res.ok) {
        state.authed = true;
        var btn = $("asst-logout");
        if (btn) btn.hidden = false;
        return true;
      }
      msg(res.status === 429 ? "尝试次数过多，已临时锁定" : "密码错误", true);
      return false;
    });
  }

  /* 需要登录的动作包装：未登录 → 密码行 → 登录 → 重试一次 */
  function withAuth(action) {
    if (state.authed) return action();
    return askPassword().then(function (password) {
      if (!password) return null;
      return login(password).then(function (ok) { return ok ? action() : null; });
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
    ["asst-rename-row", "asst-delete-row"].forEach(function (id) {
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
      if (res.status === 401) { state.authed = false; finishRun(); msg("登录已过期，请重试", true); return; }
      if (res.status === 409) { finishRun(); msg("已有一个任务进行中", true); return; }
      if (!res.ok) { finishRun(); msg(res.body.error || ("HTTP " + res.status), true); return; }
      pollStatus();
    }).catch(function () {
      finishRun();
      msg("网络错误", true);
    });
  }

  function pollStatus() {
    stopPolling();
    state.pollTimer = setInterval(function () {
      api("/status").then(function (res) {
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
        $("asst-logout").hidden = true;
        renderMessages([]);
        refreshAll();
        msg("已退出登录（下次使用需重新输入密码）");
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
      if (state.authed) $("asst-logout").hidden = false;
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
