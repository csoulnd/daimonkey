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
      // 热度首页是除新闻/知识库外所有页面的归属（榜单/产品/方法论/数据源…）
      on = here !== "/news" && here !== "/knowledge"
        && !here.startsWith("/news/") && !here.startsWith("/knowledge/");
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

  /* ---- 手动更新（POST /api/v1/update + 轮询状态，成功后刷新页面）---- */
  // 清除旧令牌；每次发起更新均输入密码，不在浏览器中保存。
  try {
    sessionStorage.removeItem("utoken");
    var oldUrl = new URL(location.href);
    oldUrl.searchParams.delete("utoken");
    history.replaceState(null, "", oldUrl.pathname + oldUrl.search + oldUrl.hash);
  } catch (e) { /* 隐私模式等场景忽略 */ }

  function askPassword() {
    return new Promise(function (resolve) {
      var dialog = document.createElement("dialog");
      dialog.setAttribute("aria-label", "输入更新密码");
      dialog.style.cssText = "border:1px solid #444;border-radius:12px;padding:24px;background:#161b22;color:#eee;max-width:90vw";
      dialog.innerHTML = '<form><h3 style="margin-top:0">更新数据</h3><label>更新密码<input type="password" name="password" required autocomplete="off" style="display:block;margin:12px 0;padding:10px;font-size:16px"></label><div style="display:flex;gap:12px;justify-content:flex-end"><button type="button">取消</button><button type="submit">开始更新</button></div></form>';
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
            alert("更新结束：" + s.state + (s.build_error ? "\n" + s.build_error : "\n详见 data/logs/"));
          }
        })
        .catch(function () { /* 瞬时网络问题，下个周期重试 */ });
    }, 2500);
  }

  function trigger(btn, endpoint, password) {
    var orig = btn.textContent;
    fetch(endpoint, { method: "POST", headers: { "X-Update-Password": password } })
      .then(function (res) {
        if (res.status === 401) {
          alert("更新密码错误，请重新点击更新并输入密码。");
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
          askPassword().then(function (password) {
            if (password !== null) trigger(btn, endpoint, password);
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
})();
