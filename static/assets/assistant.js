/* 智能助手前端：单用户登录 + 会话 + 对话 + 工作流 + 用量显示（原生 JS，无框架） */
(function () {
  "use strict";

  var API = "/api/v1/assistant";
  var state = { authed: false, sessionId: null, running: false, pollTimer: null };

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function esc(s) {
    return String(s == null ? "" : s);
  }
  function msg(text, isErr) {
    var box = $("asst-msg");
    box.textContent = text || "";
    box.className = "asst-msg" + (isErr ? " err" : "");
  }
  function fmtTokens(n) {
    return n >= 1000000 ? (n / 1000000).toFixed(1) + "M"
      : n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n);
  }

  function api(path, options) {
    return fetch(API + path, Object.assign({ credentials: "same-origin" },
      options || {})).then(function (r) {
      if (r.status === 401) { state.authed = false; showLogin(); throw new Error("未登录"); }
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (!r.ok) throw new Error(body.error || ("HTTP " + r.status));
        return body;
      });
    });
  }

  /* ---- 视图切换 ---- */
  function showLogin() {
    $("asst-login").hidden = false;
    $("asst-app").hidden = true;
    stopPolling();
  }
  function showApp() {
    $("asst-login").hidden = true;
    $("asst-app").hidden = false;
    refreshSessions();
    refreshUsage();
    loadWorkflows();
  }

  /* ---- 登录 / 登出 ---- */
  function doLogin() {
    var btn = $("asst-login-btn");
    var password = $("asst-password").value;
    if (!password) { $("asst-login-msg").textContent = "请输入密码"; return; }
    btn.disabled = true;
    fetch(API + "/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: password })
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (b) {
        return { ok: r.ok, status: r.status, body: b };
      });
    }).then(function (res) {
      btn.disabled = false;
      if (res.ok) {
        $("asst-password").value = "";
        $("asst-login-msg").textContent = "";
        state.authed = true;
        showApp();
      } else if (res.status === 429) {
        $("asst-login-msg").textContent = "尝试次数过多，已临时锁定，请稍后再试";
      } else {
        $("asst-login-msg").textContent = res.body.error || "密码错误";
      }
    }).catch(function () {
      btn.disabled = false;
      $("asst-login-msg").textContent = "网络错误，请重试";
    });
  }
  function doLogout() {
    api("/logout", { method: "POST" }).catch(function () { }).then(function () {
      state.authed = false;
      showLogin();
    });
  }

  /* ---- 会话 ---- */
  function refreshSessions() {
    api("/sessions").then(function (data) {
      var list = $("asst-sessions");
      list.textContent = "";
      (data.sessions || []).forEach(function (s) {
        var li = el("li", s.id === state.sessionId ? "on" : "");
        var title = el("span", "asst-s-title", esc(s.title || ("会话 " + s.id)));
        title.title = (s.title || "") + " · " + (s.updated_at || "");
        var del = el("button", "asst-s-del", "×");
        del.title = "删除会话";
        del.addEventListener("click", function (ev) {
          ev.stopPropagation();
          api("/sessions/" + s.id, { method: "DELETE" }).then(function () {
            if (state.sessionId === s.id) { state.sessionId = null; renderMessages([]); }
            refreshSessions();
          }).catch(function (e) { msg(e.message, true); });
        });
        li.appendChild(title);
        li.appendChild(del);
        li.addEventListener("click", function () { selectSession(s.id); });
        list.appendChild(li);
      });
      if (!state.sessionId && data.sessions && data.sessions.length) {
        selectSession(data.sessions[0].id);
      }
    }).catch(function (e) { if (state.authed) msg(e.message, true); });
  }
  function selectSession(id) {
    state.sessionId = id;
    document.querySelectorAll("#asst-sessions li").forEach(function (li) {
      li.classList.remove("on");
    });
    api("/sessions/" + id + "/messages").then(function (data) {
      renderMessages(data.messages || []);
      refreshSessions();
    }).catch(function (e) { msg(e.message, true); });
  }
  function newSession() {
    api("/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "" })
    }).then(function (data) {
      state.sessionId = data.session_id;
      renderMessages([]);
      refreshSessions();
    }).catch(function (e) { msg(e.message, true); });
  }

  function renderMessages(items) {
    var box = $("asst-messages");
    box.textContent = "";
    items.forEach(function (m) {
      var div = el("div", "asst-bubble " + (m.role === "user" ? "u" : "a"));
      if (m.workflow) div.appendChild(el("div", "asst-wf-tag", "工作流: " + m.workflow));
      div.appendChild(el("div", "asst-bubble-body", esc(m.content)));
      box.appendChild(div);
    });
    box.scrollTop = box.scrollHeight;
  }

  /* ---- 对话 + 轮询 ---- */
  function sendMessage() {
    if (state.running) return;
    if (!state.sessionId) { msg("先新建或选择一个会话", true); return; }
    var input = $("asst-input");
    var text = input.value.trim();
    if (!text) return;
    var box = $("asst-messages");
    var bubble = el("div", "asst-bubble u");
    bubble.appendChild(el("div", "asst-bubble-body", text));
    box.appendChild(bubble);
    box.scrollTop = box.scrollHeight;
    input.value = "";
    msg("");
    startRun("/chat", { session_id: state.sessionId, message: text });
  }

  function runWorkflow(name, description) {
    if (state.running) return;
    var payload = {};
    if (name === "content_polish") {
      var text = window.prompt(description + "\n请输入要润色的文案：", "");
      if (!text) return;
      payload = { text: text };
    }
    if (!window.confirm("运行工作流「" + name + "」？将消耗当日配额。")) return;
    startRun("/workflows/" + encodeURIComponent(name), payload);
  }

  function startRun(path, body) {
    state.running = true;
    $("asst-send").disabled = true;
    msg("执行中…（DSH agent loop，最长 5 分钟）");
    api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function () {
      pollStatus();
    }).catch(function (e) {
      finishRun();
      msg(e.message, true);
    });
  }

  function pollStatus() {
    stopPolling();
    state.pollTimer = setInterval(function () {
      api("/status").then(function (s) {
        if (s.state === "running") return;
        finishRun();
        if (s.state === "success") {
          msg("完成");
          if (s.workflow && s.reply) {
            // 工作流结果不落会话，直接回显到对话面板
            var box = $("asst-messages");
            var bubble = el("div", "asst-bubble a");
            bubble.appendChild(el("div", "asst-wf-tag", "工作流: " + s.workflow));
            bubble.appendChild(el("div", "asst-bubble-body", s.reply));
            box.appendChild(bubble);
            box.scrollTop = box.scrollHeight;
          } else if (state.sessionId) {
            selectSession(state.sessionId);
          }
        } else {
          msg(s.error || "执行失败", true);
        }
        refreshUsage();
      }).catch(function (e) {
        finishRun();
        msg(e.message, true);
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

  /* ---- 用量 ---- */
  function refreshUsage() {
    api("/usage").then(function (data) {
      var u = data.usage || {};
      var box = $("asst-usage");
      box.textContent = "";
      ["interactive", "internal"].forEach(function (ch) {
        var v = u[ch];
        if (!v) return;
        var ratio = v.limit ? v.requests / v.limit : 0;
        var line = el("div", "asst-usage-line" + (ratio >= 0.8 ? " warn" : ""));
        line.textContent = (ch === "interactive" ? "助手" : "内部")
          + " " + v.requests + "/" + v.limit + " 次 · "
          + fmtTokens(v.tokens) + "/" + fmtTokens(v.tokens_limit) + " tok";
        box.appendChild(line);
      });
    }).catch(function () { /* 用量失败不打扰 */ });
  }

  /* ---- 工作流列表 ---- */
  function loadWorkflows() {
    api("/workflows").then(function (data) {
      var box = $("asst-workflows");
      box.textContent = "";
      (data.workflows || []).forEach(function (w) {
        var btn = el("button", "asst-wf-btn", "⚙ " + w.name);
        btn.title = w.description;
        btn.addEventListener("click", function () { runWorkflow(w.name, w.description); });
        box.appendChild(btn);
      });
    }).catch(function () { });
  }

  /* ---- 初始化 ---- */
  function init() {
    $("asst-login-btn").addEventListener("click", doLogin);
    $("asst-password").addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") doLogin();
    });
    var logoutBtn = $("asst-logout");
    if (logoutBtn) logoutBtn.addEventListener("click", doLogout);
    $("asst-new").addEventListener("click", newSession);
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
    fetch(API + "/session", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (s) {
        state.authed = !!s.authed;
        if (state.authed) showApp(); else showLogin();
      }).catch(showLogin);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
