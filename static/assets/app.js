/* 手动更新按钮 + 热度/动量视图切换 + 侧栏开合（无框架，原生 JS） */
(function () {
  "use strict";

  /* ---- 左侧侧栏：开合 + 记忆 + 当前页高亮 ---- */
  var SIDE_KEY = "side";
  function setSide(open) {
    document.documentElement.classList.toggle("no-side", !open);
    try { localStorage.setItem(SIDE_KEY, open ? "open" : "closed"); } catch (e) { /* 隐私模式 */ }
  }
  var btnClose = document.getElementById("side-close");
  var btnOpen = document.getElementById("side-open");
  if (btnClose) btnClose.addEventListener("click", function () { setSide(false); });
  if (btnOpen) btnOpen.addEventListener("click", function () { setSide(true); });
  var here = location.pathname.replace(/\/+$/, "") || "/";
  document.querySelectorAll(".tree a").forEach(function (a) {
    var dp = a.dataset.path || "";
    var on = false;
    if (dp === "/") {
      // 热度首页是除新闻/知识库/报告厅/助手外所有页面的归属（榜单/产品/方法论/数据源…）
      on = here !== "/news" && here !== "/knowledge" && here !== "/reports" && here !== "/assistant"
        && !here.startsWith("/news/") && !here.startsWith("/knowledge/")
        && !here.startsWith("/reports/") && !here.startsWith("/assistant/");
    } else {
      on = dp === here || here.startsWith(dp + "/");
    }
    if (on) a.classList.add("on");
  });

  /* ---- 榜单页视图切换 ---- */
  document.querySelectorAll(".view-toggle .tg").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll(".view-toggle .tg").forEach(function (b) {
        b.classList.remove("active");
      });
      btn.classList.add("active");
      var heat = document.getElementById("tbl-heat");
      var mom = document.getElementById("tbl-mom");
      if (!heat || !mom) return;
      heat.classList.toggle("hidden", btn.dataset.view !== "heat");
      mom.classList.toggle("hidden", btn.dataset.view !== "mom");
    });
  });

  /* ---- 主题切换（明/暗，记忆 + 跟随系统初始值已在 base.html 预设） ---- */
  var btnTheme = document.getElementById("btn-theme");
  if (btnTheme) {
    btnTheme.addEventListener("click", function () {
      var root = document.documentElement;
      var toLight = root.dataset.theme !== "light";
      if (toLight) root.dataset.theme = "light";
      else delete root.dataset.theme;
      try { localStorage.setItem("theme", toLight ? "light" : "dark"); } catch (e) { }
    });
  }

  /* ---- 手动更新（POST /api/v1/update + 轮询状态，成功后刷新页面）---- */
  // 清除旧令牌；更新按钮与智能助手共用登录态（Cookie 7 天），
  // 未登录时才弹密码框，输入的密码同时用于建立会话。
  try {
    sessionStorage.removeItem("utoken");
    var oldUrl = new URL(location.href);
    oldUrl.searchParams.delete("utoken");
    history.replaceState(null, "", oldUrl.pathname + oldUrl.search + oldUrl.hash);
  } catch (e) { /* 隐私模式等场景忽略 */ }

  function askPassword() {
    return new Promise(function (resolve) {
      var dialog = document.createElement("dialog");
      dialog.setAttribute("aria-label", "输入密码");
      dialog.className = "pwd-dialog";
      dialog.innerHTML = '<form><h3 style="margin-top:0">需要权限</h3><label>密码<input type="password" name="password" required autocomplete="off" style="display:block;margin:12px 0;padding:10px;font-size:16px"></label><div style="display:flex;gap:12px;justify-content:flex-end"><button type="button">取消</button><button type="submit">确定</button></div></form>';
      document.body.appendChild(dialog);
      var finished = false;
      function finish(value) {
        if (finished) return;
        finished = true;
        dialog.close();
        dialog.remove();
        resolve(value);
      }
      dialog.querySelector("form").addEventListener("submit", function (e) {
        e.preventDefault();
        finish(dialog.querySelector("input").value);
      });
      dialog.querySelector('button[type="button"]').addEventListener("click", function () { finish(null); });
      dialog.addEventListener("cancel", function (e) { e.preventDefault(); finish(null); });
      dialog.showModal();
      dialog.querySelector("input").focus();
    });
  }

  /* 用密码建立助手会话（一次登录，本浏览器获得完全权限） */
  function establishSession(password) {
    return fetch("/api/v1/assistant/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: password })
    }).then(function (r) { return r.ok; }).catch(function () { return false; });
  }

  function sessionAuthed() {
    return fetch("/api/v1/assistant/session", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (s) { return !!s.authed; })
      .catch(function () { return false; });
  }

  function poll(btn, orig) {
    var timer = setInterval(function () {
      fetch("/api/v1/update/status", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (s) {
          if (s.state === "running") {
            btn.textContent = "⟳ 更新中…";
            return;
          }
          clearInterval(timer);
          if (s.state === "success" || s.state === "partial") {
            location.reload();
          } else {
            btn.disabled = false;
            btn.textContent = orig;
            var detail = s.build_error || s.error;
            alert("更新结束：" + s.state + (detail ? "\n" + detail : "\n详见 data/logs/"));
          }
        })
        .catch(function () { /* 瞬时网络问题，下个周期重试 */ });
    }, 2500);
  }

  function trigger(btn, endpoint, password) {
    var orig = btn.textContent;
    var headers = password ? { "X-Update-Password": password } : {};
    fetch(endpoint, { method: "POST", headers: headers, credentials: "same-origin" })
      .then(function (res) {
        if (res.status === 401) {
          alert("密码错误或登录已过期，请重新点击并输入密码。");
          return;
        }
        if (!res.ok && res.status !== 409) throw new Error("HTTP " + res.status);
        btn.disabled = true;
        poll(btn, orig);
      })
      .catch(function () {
        alert("更新接口不可用。\n\n请先启动本地服务：python3 scripts/serve.py\n"
              + "或在项目目录手动执行：python3 -m collector --update，完成后刷新页面。");
      });
  }

  function bindUpdate(btn, endpoint) {
    btn.addEventListener("click", function () {
      fetch("/api/v1/update/status", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (st) {
          if (st.state === "running") {
            btn.disabled = true;
            poll(btn, btn.textContent);
            return;
          }
          sessionAuthed().then(function (authed) {
            if (authed) { trigger(btn, endpoint, null); return; }
            askPassword().then(function (password) {
              if (password === null || password === "") return;
              establishSession(password).then(function () { trigger(btn, endpoint, null); });
            });
          });
        })
        .catch(function () {
          alert("更新接口不可用。\n\n请先启动本地服务：python3 scripts/serve.py\n"
                + "或在项目目录手动执行：python3 -m collector --update，完成后刷新页面。");
        });
    });
  }

  var btnMain = document.getElementById("btn-update");
  if (btnMain) bindUpdate(btnMain, "/api/v1/update");
  var btnNews = document.getElementById("btn-news-update");
  if (btnNews) bindUpdate(btnNews, "/api/v1/update/news");
  var btnKb = document.getElementById("btn-kb-update");
  if (btnKb) bindUpdate(btnKb, "/api/v1/update/knowledge");
})();
