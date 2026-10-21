/* 智能助手悬浮组件：全站宠物入口 + 会话 + 对话/运维 + 内置专家 + 用量（原生 JS）
 *
 * 本文件负责：
 *  - 顶栏账号头像（换表情：顶栏 use + 悬浮宠物 + 面板标题三处联动，持久化 dm-avatar）
 *  - 悬浮面板开合 / 拖拽改尺寸（localStorage 记忆）/ 恢复默认 / 大窗
 *  - 会话管理（新建 / 重命名 / 删除 / 列表）与移动端抽屉
 *  - 对话 / 运维模式切换（固定高度模式条，两种模式共用输入区）
 *  - 内置专家 / Skill 列表（popover，名称 + 说明，来自 /workflows）
 *
 * 安全/登录约束：助手内不再渲染登录表单；未登录时悬浮入口整体不可见、不可聚焦、
 * 不绑定点击（不存在「点宠物→提示登录」的路径），所有权限动作走 requireAuth() 防御。
 */
(function () {
  "use strict";

  var API = "/api/v1/assistant";
  var SIZE_KEY = "dm-asst-size";
  var AVATAR_KEY = "dm-avatar";
  var FONT_KEY = "asst-font-scale";

  var state = {
    authed: false, user: null, role: null, opsEnabled: false,
    sessionId: null, modes: {}, running: false,
    pollTimer: null, open: false, renameTarget: null, deleteTarget: null,
    rollback: null,
    /* 运维 run：run_id / 增量 seq / 并发令牌 / 流式与工具 DOM 引用 */
    runId: null, runSeq: 0, runToken: 0, opsTimer: null,
    streamEl: null, toolsEl: null, bubbleEl: null, toolList: [],
    pendingApproval: null, approvalBusy: false, doneReply: "",
    /* 头像（平静/开心/生气/戴帽子）与「大窗」前的尺寸 */
    avatar: "monkey", preMax: null, petBound: false
  };

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function on(id, evt, fn, opts) {
    var n = $(id);
    if (n) n.addEventListener(evt, fn, opts);
  }
  function msg(text, isErr) {
    var box = $("asst-msg");
    if (box) {
      box.textContent = text || "";
      box.title = text || "";
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

  /* ======================================================================
     头像：纯函数 nextAvatar() + 三处联动 applyAvatar()
     ====================================================================== */
  var AVATARS = ["monkey", "happy", "angry", "hat"];
  var AVATAR_LABELS = { monkey: "平静", happy: "开心", angry: "生气", hat: "戴帽子" };

  /* 纯函数（不依赖 DOM / localStorage）：未知值按第一个处理，保证永远有返回值 */
  function nextAvatar(cur) {
    var i = AVATARS.indexOf(cur);
    return AVATARS[(i + 1) % AVATARS.length];
  }
  function getAvatar() {
    try {
      var v = localStorage.getItem(AVATAR_KEY);
      if (AVATARS.indexOf(v) >= 0) return v;
    } catch (e) { /* 隐私模式 */ }
    return "monkey";
  }
  function saveAvatar(v) {
    try { localStorage.setItem(AVATAR_KEY, v); } catch (e) { /* 隐私模式 */ }
  }

  /* 宠物 / 面板标题的猴子脸：face=null 时回到当前头像对应的基础脸 */
  var _faceTimer = null;
  function petFace(face) {
    if (_faceTimer) { clearTimeout(_faceTimer); _faceTimer = null; }
    var base = state.avatar === "monkey" ? "#i-monkey" : "#i-monkey-" + state.avatar;
    var href = face ? "#i-monkey-" + face : base;
    ["asst-pet", "asst-panel-title"].forEach(function (id) {
      var wrap = $(id);
      if (!wrap) return;
      var use = wrap.querySelector("use");
      if (use) use.setAttribute("href", href);
      if (face) wrap.setAttribute("data-face", face);
      else wrap.removeAttribute("data-face");
    });
  }
  function petFaceThen(face, ms) {
    petFace(face);
    _faceTimer = setTimeout(function () { petFace(null); _faceTimer = null; }, ms);
  }

  /* 把「当前头像」应用到：顶栏 #i-ava-* + 悬浮宠物 + 面板标题 + 文字标签 */
  function applyAvatar(face) {
    if (AVATARS.indexOf(face) < 0) face = "monkey";
    state.avatar = face;
    var use = document.querySelector("#account-box .avatar use");
    if (use) use.setAttribute("href", "#i-ava-" + face);
    petFace(null);
    var label = $("asst-avatar-face");
    if (label) label.textContent = AVATAR_LABELS[face] || "";
  }
  function cycleAvatar() {
    var next = nextAvatar(getAvatar());
    saveAvatar(next);
    applyAvatar(next);
    msg("表情：" + (AVATAR_LABELS[next] || next));
    return next;
  }

  /* ======================================================================
     登录态（面板内不再有登录表单）
     ====================================================================== */
  /* 需要权限的动作统一包装：未登录连入口都不可见，这里只做防御性拦截 */
  function requireAuth(action) {
    if (!state.authed) return Promise.resolve(null);
    return action();
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

  /* 登出/登录失效后清掉面板里可能残留的上一账号数据（面板已隐藏，双保险） */
  function clearAssistantData() {
    ["asst-session-list", "asst-messages", "asst-usage", "asst-experts-list"]
      .forEach(function (id) {
        var n = $(id);
        if (n) n.textContent = "";
      });
    var count = $("asst-experts-count");
    if (count) count.textContent = "";
  }

  function showUser(name) {
    state.authed = !!name;
    state.user = name || null;
    if (!name) clearAssistantData();
    var label = $("asst-account-name");
    if (label) {
      label.textContent = name || "";
      label.hidden = !name;
    }
    renderAccount(name);
    applyAvatar(getAvatar());
    // 工作台入口显隐:localStorage 记录 + html.authed 类,
    // head 预载脚本在首帧前挂类,已登录刷新不再闪烁;探针失败自动摘除。
    // 悬浮宠物 #asst-pet 由 CSS 依据 html.authed 显隐；这里同步绑定/解绑入口。
    document.documentElement.classList.toggle("authed", !!name);
    setEntryEnabled(!!name);
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

  /* 顶栏账号区：只在 showUser() 时重建一次；document 级「点外面关闭」监听
     由 init() 统一安装一次，不再在 renderAccount 内重复 addEventListener。 */
  function renderAccount(name) {
    var old = $("account-box");
    if (old && old.parentNode) old.parentNode.removeChild(old);
    var loginLink = $("nav-login");
    if (loginLink) loginLink.style.display = name ? "none" : "";
    if (!name || !loginLink || !loginLink.parentNode) return;

    var box = el("span", "account-box");
    box.id = "account-box";

    var avatar = el("button", "avatar");
    avatar.type = "button";
    avatar.title = name;
    avatar.setAttribute("aria-label", "账号菜单：" + name);
    avatar.setAttribute("aria-haspopup", "true");
    avatar.setAttribute("aria-expanded", "false");
    avatar.innerHTML = '<svg aria-hidden="true"><use href="#i-ava-' + getAvatar() + '"/></svg>';

    var menu = el("div", "avatar-menu");
    menu.hidden = true;
    var mi1 = el("div", "avatar-name", name);
    var mi2 = el("button", "avatar-out", "换个表情");
    mi2.type = "button";
    mi2.title = "当前：" + (AVATAR_LABELS[getAvatar()] || "");
    var mi3 = el("button", "avatar-out", "退出登录");
    mi3.type = "button";

    avatar.addEventListener("click", function (ev) {
      ev.stopPropagation();
      menu.hidden = !menu.hidden;
      avatar.setAttribute("aria-expanded", menu.hidden ? "false" : "true");
    });
    mi2.addEventListener("click", function (ev) {
      ev.stopPropagation();       // 不让同一次点击冒泡到 document 把菜单提前关掉
      var next = cycleAvatar();   // 顶栏 + 宠物 + 面板标题 + localStorage 一起变
      mi2.title = "当前：" + (AVATAR_LABELS[next] || "");
      menu.hidden = true;         // 选完主动收起，避免「菜单卡住」
      avatar.setAttribute("aria-expanded", "false");
    });
    mi3.addEventListener("click", function (ev) {
      ev.stopPropagation();
      menu.hidden = true;
      api("/logout", { method: "POST" }).then(function () {
        state.sessionId = null;
        state.role = null;
        state.opsEnabled = false;
        state.modes = {};
        updateModeUI();
        showUser(null);          // 移除 html.authed → 宠物由 CSS 隐藏，面板同步关闭
        var lo = $("asst-logout");
        if (lo) lo.hidden = true;
      });
    });

    menu.appendChild(mi1);
    menu.appendChild(mi2);
    menu.appendChild(mi3);
    box.appendChild(avatar);
    box.appendChild(menu);
    loginLink.parentNode.insertBefore(box, loginLink);
  }

  /* ======================================================================
     会话模式：对话 / 运维（仅 admin 且 registry 开启 ops 时可用）
     ====================================================================== */
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
      if (res.status === 401) { handleAuthExpired(); return; }
      if (!res.ok) { msg(res.body.error || "切换模式失败", true); return; }
      state.modes[String(sid)] = mode;
      updateModeUI();
      msg(mode === "ops" ? "已切到运维模式（工具操作会请求审批）" : "已切到对话模式");
      refreshSessions();
    });
  }

  /* 模式条高度固定；这里只换按钮态 / 文案 / 输入框 placeholder，不改变外层尺寸 */
  function updateModeUI() {
    var wrap = $("asst-mode");
    var avail = opsAvailable();
    var mode = currentMode();
    if (wrap) {
      wrap.hidden = !avail;
      Array.prototype.forEach.call(
        wrap.querySelectorAll("button[data-mode]"), function (b) {
          var isOn = b.getAttribute("data-mode") === mode;
          b.classList.toggle("on", isOn);
          b.setAttribute("aria-pressed", isOn ? "true" : "false");
        });
    }
    var hint = $("asst-mode-hint");
    if (hint) {
      hint.textContent = avail
        ? (mode === "ops" ? "运维模式（工具操作会请求审批）" : "对话模式")
        : "对话模式";
    }
    var panel = $("asst-panel");
    if (panel) panel.setAttribute("data-mode", mode);
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

  /* ======================================================================
     面板尺寸：clamp / dvh 默认值在 CSS 里，JS 只负责拖拽后的像素记忆
     ====================================================================== */
  function isMobileSheet() {
    return (window.innerWidth || 1024) <= 720;
  }
  function sizeLimits() {
    var vw = window.innerWidth || 1024;
    var vh = window.innerHeight || 768;
    return {
      vw: vw, vh: vh,
      maxW: Math.max(280, vw - 32),
      maxH: Math.max(320, vh - 44)
    };
  }
  function clampSize(w, h) {
    var l = sizeLimits();
    return {
      w: Math.round(Math.min(Math.max(Number(w) || 300, 300), l.maxW)),
      h: Math.round(Math.min(Math.max(Number(h) || 360, 360), l.maxH))
    };
  }
  function readSavedSize() {
    try {
      var raw = localStorage.getItem(SIZE_KEY);
      if (!raw) return null;
      var o = JSON.parse(raw);
      if (!o || !isFinite(o.w) || !isFinite(o.h)) return null;
      return clampSize(o.w, o.h);
    } catch (e) { return null; }
  }
  function saveSize(size) {
    try { localStorage.setItem(SIZE_KEY, JSON.stringify(clampSize(size.w, size.h))); }
    catch (e) { /* 隐私模式 */ }
  }
  /* size=null 表示交回 CSS 的 clamp() 默认值 */
  function applySize(size) {
    var panel = $("asst-panel");
    if (!panel) return;
    if (isMobileSheet() || !size) {
      panel.style.removeProperty("--asst-w");
      panel.style.removeProperty("--asst-h");
      panel.removeAttribute("data-sized");
      return;
    }
    var c = clampSize(size.w, size.h);
    panel.style.setProperty("--asst-w", c.w + "px");
    panel.style.setProperty("--asst-h", c.h + "px");
    panel.setAttribute("data-sized", "1");
  }
  /* 面板实际宽度 < 380px 时收起副标题/用户名，避免标题与窗口按钮互相挤压 */
  function syncChrome() {
    var panel = $("asst-panel");
    if (!panel) return;
    var r = panel.getBoundingClientRect();
    var w = (r && r.width) || 0;
    panel.classList.toggle("compact", w > 0 && w < 380);
  }
  function currentPanelSize() {
    var panel = $("asst-panel");
    var r = panel ? panel.getBoundingClientRect() : null;
    if (r && r.width > 0 && r.height > 0) {
      return { w: Math.round(r.width), h: Math.round(r.height) };
    }
    var saved = readSavedSize();
    if (saved) return saved;
    var l = sizeLimits();
    return {
      w: Math.round(Math.min(480, Math.max(360, l.vw * 0.32))),
      h: Math.round(Math.min(760, Math.max(420, l.vh * 0.66)))
    };
  }
  function maxSize() {
    var l = sizeLimits();
    return {
      w: Math.min(l.maxW, Math.max(680, Math.round(l.vw * 0.58))),
      h: Math.min(l.maxH, Math.round(l.vh * 0.86))
    };
  }
  /* 打开面板 / 窗口 resize 时使用的尺寸：大窗优先，其次记忆值，否则 CSS 默认 */
  function preferredSize() {
    var panel = $("asst-panel");
    if (panel && panel.classList.contains("max") && !isMobileSheet()) return maxSize();
    return readSavedSize();
  }
  function resetSize() {
    try { localStorage.removeItem(SIZE_KEY); } catch (e) { /* 隐私模式 */ }
    state.preMax = null;
    var panel = $("asst-panel");
    if (panel) panel.classList.remove("max");
    var btn = $("asst-max");
    if (btn) {
      btn.setAttribute("aria-pressed", "false");
      btn.title = "大窗 / 小窗切换";
    }
    applySize(null);
    syncChrome();
    msg("已恢复默认尺寸");
  }
  function toggleMax() {
    if (isMobileSheet()) return;
    var panel = $("asst-panel");
    if (!panel) return;
    var on = !panel.classList.contains("max");
    if (on) {
      state.preMax = currentPanelSize();
      panel.classList.add("max");
      applySize(maxSize());
    } else {
      panel.classList.remove("max");
      applySize(state.preMax || readSavedSize());
      state.preMax = null;
    }
    syncChrome();
    var btn = $("asst-max");
    if (btn) {
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.title = on ? "切换回小窗" : "切换大窗";
    }
  }
  /* 左上角拖拽点：面板锚在右下，向左/向上拖 = 变宽/变高 */
  function initResize() {
    var grip = $("asst-resize");
    var panel = $("asst-panel");
    if (!grip || !panel) return;
    var drag = null;
    grip.addEventListener("pointerdown", function (ev) {
      if (isMobileSheet()) return;
      ev.preventDefault();
      var size = currentPanelSize();
      drag = { x: ev.clientX, y: ev.clientY, w: size.w, h: size.h };
      panel.classList.add("resizing");
      if (grip.setPointerCapture && ev.pointerId != null) {
        try { grip.setPointerCapture(ev.pointerId); } catch (e) { /* 忽略 */ }
      }
    });
    grip.addEventListener("pointermove", function (ev) {
      if (!drag) return;
      ev.preventDefault();
      applySize({ w: drag.w + (drag.x - ev.clientX), h: drag.h + (drag.y - ev.clientY) });
    });
    function finish(ev) {
      if (!drag) return;
      drag = null;
      panel.classList.remove("resizing");
      if (grip.releasePointerCapture && ev.pointerId != null) {
        try { grip.releasePointerCapture(ev.pointerId); } catch (e) { /* 忽略 */ }
      }
      if (!isMobileSheet()) {
        saveSize(currentPanelSize());
        state.preMax = null;   // 大窗里手动拖过尺寸后，再切回小窗以新尺寸为准
      }
      syncChrome();
    }
    grip.addEventListener("pointerup", finish);
    grip.addEventListener("pointercancel", finish);
    grip.addEventListener("dblclick", function (ev) { ev.preventDefault(); resetSize(); });
  }

  /* ======================================================================
     面板开合（未登录不开放）
     ====================================================================== */
  function petOpen(ev) {
    if (ev) ev.preventDefault();
    openPanel();
  }
  function petKey(ev) {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      openPanel();
    }
  }
  /* 登录态开关助手入口：未登录时宠物不可见/不可聚焦/不绑定任何点击；
     #asst-panel 同时 hidden + inert + aria-hidden，键盘 Tab/Esc 都进不去。 */
  function setEntryEnabled(enabled) {
    var pet = $("asst-pet");
    var panel = $("asst-panel");
    if (pet) {
      if (enabled) {
        pet.removeAttribute("inert");
        pet.removeAttribute("aria-hidden");
        pet.setAttribute("tabindex", "0");
        if (!state.petBound) {
          pet.addEventListener("click", petOpen);
          pet.addEventListener("keydown", petKey);
          state.petBound = true;
        }
      } else {
        pet.setAttribute("inert", "");
        pet.setAttribute("aria-hidden", "true");
        pet.setAttribute("tabindex", "-1");
        pet.setAttribute("aria-expanded", "false");
        pet.classList.remove("hide");
        if (state.petBound) {
          pet.removeEventListener("click", petOpen);
          pet.removeEventListener("keydown", petKey);
          state.petBound = false;
        }
      }
    }
    if (!enabled) {
      if (state.open) closePanel();
      if (panel) {
        panel.hidden = true;
        panel.setAttribute("inert", "");
        panel.setAttribute("aria-hidden", "true");
      }
      setDrawer(false);
      setExperts(false);
    }
  }

  function openPanel() {
    if (!state.authed) return;   // 未登录不存在「点宠物→提示登录」的路径
    state.open = true;
    var panel = $("asst-panel");
    var pet = $("asst-pet");
    if (panel) {
      panel.hidden = false;
      panel.removeAttribute("inert");
      panel.removeAttribute("aria-hidden");
    }
    if (pet) {
      pet.classList.add("hide");
      pet.setAttribute("aria-expanded", "true");
    }
    applySize(preferredSize());
    syncChrome();
    refreshAll();
    setTimeout(function () {
      var input = $("asst-input");
      if (input) input.focus();
    }, 30);
  }
  function closePanel() {
    state.open = false;
    var panel = $("asst-panel");
    var pet = $("asst-pet");
    if (panel) {
      panel.hidden = true;
      panel.setAttribute("inert", "");
      panel.setAttribute("aria-hidden", "true");
    }
    if (pet) {
      pet.classList.remove("hide");
      pet.setAttribute("aria-expanded", "false");
      if (state.authed) pet.focus();   // 键盘用户关闭面板后回到入口
    }
    setDrawer(false);
    setExperts(false);
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
    var send = $("asst-send");
    if (send) send.disabled = false;
  }

  /* ======================================================================
     会话管理
     ====================================================================== */
  /* Cookie 过期 / 伪造态：关闭面板、摘除 html.authed、解绑宠物入口 */
  function handleAuthExpired() {
    state.sessionId = null;
    state.role = null;
    state.opsEnabled = false;
    state.modes = {};
    updateModeUI();
    showUser(null);   // state.authed=false + 移除 html.authed + setEntryEnabled(false)
    var lo = $("asst-logout");
    if (lo) lo.hidden = true;
  }

  function refreshSessions() {
    api("/sessions").then(function (res) {
      if (res.status === 401) { handleAuthExpired(); return; }
      if (!res.ok) return;
      var list = $("asst-session-list");
      if (!list) return;
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
        list.appendChild(el("li", "asst-s-empty",
          "（暂无活跃会话，点上方「＋ 新会话」）"));
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
    setDrawer(false);
    var generation = ++selectGeneration;
    api("/sessions/" + id + "/messages").then(function (res) {
      if (generation !== selectGeneration) return;  // 已切走，丢弃过期响应
      if (res.ok) renderMessages(res.body.messages || []);
    });
  }
  function newSession() {
    return requireAuth(function () {
      var mode = currentMode();
      return api("/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "", mode: mode })
      }).then(function (res) {
        if (res.status === 401) { handleAuthExpired(); return; }
        if (res.ok) {
          state.sessionId = res.body.session_id;
          state.modes[String(state.sessionId)] = res.body.mode === "ops" ? "ops" : "chat";
          renderMessages([]);
          updateModeUI();
          refreshSessions();
          msg("已新建会话");
        } else { msg(res.body.error || "创建失败", true); }
      });
    });
  }
  /* ---- 面板内闭环：行内重命名 / 行内删除确认（不用浏览器原生弹窗，避免焦点抢占导致页面滚动） ---- */
  function anyInlineRowOpen() {
    return ["asst-rename-row", "asst-delete-row"]
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
    if (!row || !input) return;
    hideInlineRows();
    setDrawer(false);
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
    if (row) row.hidden = true;
    state.renameTarget = null;
    if (!sid || !input) return;
    var title = input.value.trim();
    if (!title) return;
    requireAuth(function () {
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
    if (!row) return;
    hideInlineRows();
    setDrawer(false);
    state.deleteTarget = session.id;
    row.hidden = false;
  }

  function submitDelete() {
    var row = $("asst-delete-row");
    var sid = state.deleteTarget;
    if (row) row.hidden = true;
    state.deleteTarget = null;
    if (!sid) return;
    requireAuth(function () {
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
    if (!box) return;
    box.textContent = "";
    if (!items.length) {
      box.appendChild(el("div", "asst-empty", "暂无消息，输入内容开始对话。"));
      return;
    }
    items.forEach(function (m) {
      var div = el("div", "asst-bubble " + (m.role === "user" ? "u" : "a"));
      if (m.workflow) div.appendChild(el("div", "asst-wf-tag", "⚙ " + m.workflow));
      div.appendChild(el("div", "asst-bubble-body", m.content == null ? "" : m.content));
      box.appendChild(div);
    });
    box.scrollTop = box.scrollHeight;
  }

  /* ======================================================================
     对话 + 轮询
     ====================================================================== */
  function sendMessage() {
    if (state.running) return;
    var input = $("asst-input");
    if (!input) return;
    var text = input.value.trim();
    if (!text) return;
    requireAuth(function () {
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
      if (res.status === 401) { handleAuthExpired(); return null; }
      if (!res.ok) { msg(res.body.error || "创建会话失败", true); return null; }
      state.sessionId = res.body.session_id;
      state.modes[String(state.sessionId)] = res.body.mode === "ops" ? "ops" : "chat";
      updateModeUI();
      refreshSessions();
      return state.sessionId;
    });
  }

  function runWorkflow(name, description) {
    if (state.running) { msg("已有任务执行中，完成后再试", true); return; }
    requireAuth(function () {
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
    var send = $("asst-send");
    if (send) send.disabled = true;
    petFace("thinking");
    msg("执行中…");
    api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (res) {
      if (res.status === 401) { failedRun("登录已过期，请重试"); handleAuthExpired(); return; }
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
    var send = $("asst-send");
    if (send) send.disabled = false;
  }

  /* ======================================================================
     运维轮（ops）：流式文本 + 工具卡片 + 审批 + 停止
     与 /chat 的区别：后端后台线程执行，前端按 run_id 增量轮询 events；
     每轮一个 pi 子进程，工具与审批事件由服务端缓冲（见 collector/assistant.py）。
     ====================================================================== */
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
    var send = $("asst-send");
    if (send) send.disabled = true;
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
      if (res.status === 401) { failedRun("登录已过期，请重试"); handleAuthExpired(); return; }
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
    var title = $("asst-approval-title");
    var cmd = $("asst-approval-cmd");
    if (title) title.textContent = " " + (pending.tool || "");
    if (cmd) cmd.textContent = pending.command || pending.title || "";
    box.hidden = false;
    state.approvalBusy = false;
    var allow = $("asst-approval-allow");
    var deny = $("asst-approval-deny");
    if (allow) allow.disabled = false;
    if (deny) deny.disabled = false;
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
    var allowBtn = $("asst-approval-allow");
    var denyBtn = $("asst-approval-deny");
    if (allowBtn) allowBtn.disabled = true;
    if (denyBtn) denyBtn.disabled = true;
    api("/ops/runs/" + state.runId + "/approvals/" + aid, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ allow: !!allow })
    }).then(function (res) {
      state.approvalBusy = false;
      if (!res.ok) {
        if (allowBtn) allowBtn.disabled = false;
        if (denyBtn) denyBtn.disabled = false;
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
    var btn = $("asst-stop");
    if (btn) btn.disabled = true;
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
    var send = $("asst-send");
    if (send) send.disabled = false;
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

  /* ======================================================================
     用量（低调显示在会话栏底部）
     ====================================================================== */
  function refreshUsage() {
    api("/usage").then(function (res) {
      if (!res.ok) return;
      var box = $("asst-usage");
      if (!box) return;
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

  /* ======================================================================
     内置专家 / Skill（popover，名称 + 说明；点击运行工作流）
     ====================================================================== */
  function renderExperts(workflows) {
    var list = $("asst-experts-list");
    var count = $("asst-experts-count");
    if (!list) return;
    list.textContent = "";
    if (count) count.textContent = "";
    if (!state.authed) return;
    if (!workflows.length) {
      list.appendChild(el("div", "asst-experts-empty", "暂无可用专家"));
      return;
    }
    if (count) count.textContent = String(workflows.length);
    workflows.forEach(function (w) {
      var btn = el("button", "asst-expert");
      btn.type = "button";
      btn.title = (w.description || w.name) + "\n点击运行";
      btn.appendChild(el("strong", "asst-expert-name", w.name));
      btn.appendChild(el("span", "asst-expert-desc", w.description || ""));
      btn.addEventListener("click", function () {
        setExperts(false);
        runWorkflow(w.name, w.description);
      });
      list.appendChild(btn);
    });
  }
  function loadWorkflows() {
    if (!state.authed) return;
    api("/workflows").then(function (res) {
      if (res.status === 401) { handleAuthExpired(); return; }
      renderExperts(res.ok ? (res.body.workflows || []) : []);
    });
  }
  function setExperts(open) {
    var list = $("asst-experts-list");
    var btn = $("asst-experts-toggle");
    if (!list || !btn) return;
    list.hidden = !open;
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open && !list.querySelector(".asst-expert")) loadWorkflows();
  }

  /* ======================================================================
     移动端会话抽屉
     ====================================================================== */
  function setDrawer(open) {
    var panel = $("asst-panel");
    var btn = $("asst-mobile-sessions");
    if (panel) panel.classList.toggle("drawer-open", !!open);
    if (btn) {
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      btn.setAttribute("aria-controls", "asst-side-col");
    }
  }

  /* ======================================================================
     初始化
     ====================================================================== */
  function init() {
    /* 宠物点击/键盘只在登录后由 setEntryEnabled() 绑定，未登录没有任何入口交互 */
    on("asst-min", "click", closePanel);

    /* 字号动态缩放（面板令牌 --asst-scale，记忆在 localStorage） */
    var FONT_STEPS = [0.85, 1, 1.15, 1.3];
    var fontScale = 1;
    try {
      var saved = parseFloat(localStorage.getItem(FONT_KEY));
      if (saved >= 0.8 && saved <= 1.4) fontScale = saved;
    } catch (e) { /* 隐私模式 */ }
    function applyFontScale() {
      var panel = $("asst-panel");
      if (panel) panel.style.setProperty("--asst-scale", String(fontScale));
      try { localStorage.setItem(FONT_KEY, String(fontScale)); } catch (e) { /* 忽略 */ }
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
    on("asst-font-minus", "click", function () { stepFontScale(-1); });
    on("asst-font-plus", "click", function () { stepFontScale(1); });
    on("asst-max", "click", toggleMax);
    on("asst-reset", "click", resetSize);

    /* 会话模式切换（对话 / 运维；仅 admin 且 registry 开启 ops 时显示） */
    on("asst-mode", "click", function (ev) {
      var btn = ev.target.closest ? ev.target.closest("button[data-mode]") : null;
      if (btn) setMode(btn.getAttribute("data-mode"));
    });

    on("asst-stop", "click", stopOpsRun);
    on("asst-approval-allow", "click", function () { resolveApproval(true); });
    on("asst-approval-deny", "click", function () { resolveApproval(false); });

    /* 行内重命名 / 删除确认（会话栏每项的 ✎/🗑 触发 openRename/openDelete） */
    on("asst-rename-ok", "click", submitRename);
    on("asst-rename-cancel", "click", function () {
      var row = $("asst-rename-row"); if (row) row.hidden = true;
    });
    on("asst-rename-input", "keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); submitRename(); }
      else if (ev.key === "Escape") {
        ev.stopPropagation();
        var row = $("asst-rename-row"); if (row) row.hidden = true;
      }
    });
    on("asst-delete-ok", "click", submitDelete);
    on("asst-delete-cancel", "click", function () {
      var row = $("asst-delete-row"); if (row) row.hidden = true;
    });

    on("asst-new", "click", newSession);
    on("asst-mobile-sessions", "click", function (ev) {
      ev.stopPropagation();
      var panel = $("asst-panel");
      setDrawer(!(panel && panel.classList.contains("drawer-open")));
    });
    on("asst-experts-toggle", "click", function (ev) {
      ev.stopPropagation();
      var list = $("asst-experts-list");
      setExperts(!!(list && list.hidden));
    });

    on("asst-form", "submit", function (ev) {
      ev.preventDefault();
      sendMessage();
    });
    on("asst-input", "keydown", function (ev) {
      if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
        ev.preventDefault();
        sendMessage();
      }
    });

    /* Esc 逐层退出：专家 popover → 移动端抽屉 → 行内操作行 → 面板 */
    document.addEventListener("keydown", function (ev) {
      if (ev.key !== "Escape" || !state.open) return;
      var list = $("asst-experts-list");
      if (list && !list.hidden) { setExperts(false); return; }
      var panel = $("asst-panel");
      if (panel && panel.classList.contains("drawer-open")) { setDrawer(false); return; }
      if (anyInlineRowOpen()) { hideInlineRows(); return; }
      closePanel();
    });

    /* document 级「点外面关闭」只装一次；菜单/专家/抽屉的开关由各自按钮决定 */
    document.addEventListener("click", function (ev) {
      var box = $("account-box");
      if (box) {
        var menu = box.querySelector(".avatar-menu");
        if (menu && !menu.hidden && !box.contains(ev.target)) {
          menu.hidden = true;
          var avatar = box.querySelector(".avatar");
          if (avatar) avatar.setAttribute("aria-expanded", "false");
        }
      }
      var experts = $("asst-experts");
      var list = $("asst-experts-list");
      if (experts && list && !list.hidden && !experts.contains(ev.target)) setExperts(false);
      var panelEl = $("asst-panel");
      if (panelEl && panelEl.classList.contains("drawer-open")) {
        var side = $("asst-side-col");
        var toggle = $("asst-mobile-sessions");
        if (side && !side.contains(ev.target) && !(toggle && toggle.contains(ev.target))) {
          setDrawer(false);
        }
      }
    });

    initResize();

    /* 首帧：本地缓存有登录态才接管顶栏并绑定宠物；否则立即进入「无入口」态。
       base.html 的内联脚本只画裸头像，这里同步补全，避免探针返回前点了没反应。 */
    var cachedUser = null;
    var cachedAuthed = false;
    try {
      cachedUser = localStorage.getItem("authuser");
      cachedAuthed = localStorage.getItem("authed") === "1";
    } catch (e) { /* 隐私模式 */ }
    if (cachedAuthed && cachedUser) {
      showUser(cachedUser);   // 乐观信任本地缓存，探针返回后再校正
      var cachedLogout = $("asst-logout");
      if (cachedLogout) cachedLogout.hidden = false;
    } else {
      showUser(null);         // 未登录：不绑定宠物、不请求会话，面板 hidden + inert
    }
    applyAvatar(getAvatar());
    updateModeUI();

    /* 启动时静默探一次登录态（Cookie 7 天有效，多数时候直接就绪）；
       走 loadSessionMeta 才能一并拿到 role/ops_enabled，决定是否显示运维模式。 */
    loadSessionMeta().then(function (body) {
      var authed = !!(body && body.authed);
      var lo = $("asst-logout");
      if (authed) {
        showUser(body.user || null);
        if (lo) lo.hidden = false;
        refreshAll();          // 已有 Cookie 时直接拉会话/用量/专家
      } else {
        showUser(null);        // 清除可能过期的缓存并关闭/隐藏入口
        if (lo) lo.hidden = true;
      }
    });

    /* 窗口尺寸变化：重算记忆尺寸；移动端交回 CSS 全屏 sheet */
    var resizeTimer = null;
    window.addEventListener("resize", function () {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        applySize(preferredSize());
        syncChrome();
        if (!isMobileSheet()) setDrawer(false);
      }, 120);
    });
  }

  /* 纯函数暴露给测试（不影响运行时） */
  try {
    window.__dmAssistant = {
      nextAvatar: nextAvatar,
      avatars: AVATARS.slice(),
      labels: AVATAR_LABELS,
      getAvatar: getAvatar,
      showUser: showUser
    };
  } catch (e) { /* 忽略 */ }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
