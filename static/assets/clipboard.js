/* 云剪贴板（工作台）：两台设备间文本同步 + 小云盘。
 * IIFE 原生 JS（本站无构建链约定）：5 秒轮询全量刷新（页面可见时），
 * 删除/新增都以下一次轮询对齐——单用户数据量小，全量比增量简单可靠。 */
"use strict";
(function () {
  var API = "/api/v1/clipboard";
  var POLL_MS = 5000;

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }
  function fmtBytes(n) {
    if (n == null) return "";
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    if (n < 1073741824) return (n / 1048576).toFixed(1) + " MB";
    return (n / 1073741824).toFixed(2) + " GB";
  }
  function fmtTime(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return iso;
    function p(x) { return (x < 10 ? "0" : "") + x; }
    return p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }

  function msg(text, ok) {
    var el = $("clip-msg");
    if (!el) return;
    el.textContent = text || "";
    el.style.color = ok === false ? "var(--heat)" : "";
    if (text) {
      clearTimeout(msg._t);
      msg._t = setTimeout(function () { el.textContent = ""; }, 4000);
    }
  }

  function flash(btn, text) {
    var old = btn.textContent;
    btn.textContent = text;
    btn.disabled = true;
    setTimeout(function () { btn.textContent = old; btn.disabled = false; }, 1200);
  }

  /* ---- 渲染 ---- */

  function renderTexts(items) {
    var ul = $("clip-text-list");
    ul.innerHTML = items.length ? "" :
      '<li class="clip-empty dim">还没有文本。在一台设备存入，另一台约 5 秒内出现。</li>';
    items.forEach(function (it) {
      var li = document.createElement("li");
      li.className = "clip-item";
      var preview = it.content.length > 500 ? it.content.slice(0, 500) + "…" : it.content;
      li.innerHTML =
        '<div class="clip-item-body">' +
          '<pre class="clip-text"></pre>' +
          '<div class="dim clip-item-meta"></div>' +
        "</div>" +
        '<div class="clip-item-acts">' +
          '<button type="button" class="clip-copy">复制</button>' +
          '<button type="button" class="clip-del danger">删除</button>' +
        "</div>";
      li.querySelector(".clip-text").textContent = preview;
      li.querySelector(".clip-item-meta").textContent =
        fmtBytes(it.text_bytes) + " · " + fmtTime(it.created_at);
      li.querySelector(".clip-copy").addEventListener("click", function () {
        copyText(it.content, this);
      });
      li.querySelector(".clip-del").addEventListener("click", function () {
        removeItem(it.id, this);
      });
      ul.appendChild(li);
    });
  }

  function renderFiles(items) {
    var ul = $("clip-file-list");
    ul.innerHTML = items.length ? "" :
      '<li class="clip-empty dim">还没有文件。拖一个进来试试。</li>';
    items.forEach(function (it) {
      var li = document.createElement("li");
      li.className = "clip-item";
      li.innerHTML =
        '<div class="clip-item-body">' +
          '<a class="clip-file-name" href="' + API + "/files/" + it.id + '" download></a>' +
          '<div class="dim clip-item-meta"></div>' +
        "</div>" +
        '<div class="clip-item-acts">' +
          '<button type="button" class="clip-copy">下载</button>' +
          '<button type="button" class="clip-del danger">删除</button>' +
        "</div>";
      li.querySelector(".clip-file-name").textContent = it.file_name;
      li.querySelector(".clip-item-meta").textContent =
        fmtBytes(it.file_bytes) + " · " + fmtTime(it.created_at);
      li.querySelector(".clip-copy").addEventListener("click", function () {
        window.location.href = API + "/files/" + it.id;   // 与链接同源下载，走 Cookie 鉴权
      });
      li.querySelector(".clip-del").addEventListener("click", function () {
        removeItem(it.id, this);
      });
      ul.appendChild(li);
    });
  }

  function renderUsage(u) {
    if (!u) return;
    $("clip-usage-line").textContent =
      "文本 " + u.text_count + " 条 · 文件 " + u.file_count + " 个 · " +
      "云盘 " + fmtBytes(u.file_bytes) + " / " + fmtBytes(u.quota_bytes);
    var pct = u.quota_bytes ? Math.min(100, u.file_bytes / u.quota_bytes * 100) : 0;
    var bar = $("clip-quota-bar");
    bar.style.width = pct.toFixed(1) + "%";
    bar.classList.toggle("full", pct >= 95);
  }

  /* ---- 数据 ---- */

  function load() {
    fetch(API + "/items?limit=200", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { msg(data.error, false); return; }
        var items = data.items || [];
        renderTexts(items.filter(function (i) { return i.kind === "text"; }));
        renderFiles(items.filter(function (i) { return i.kind === "file"; }));
      })
      .catch(function () {});
    fetch(API + "/usage", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) { if (data.usage) renderUsage(data.usage); })
      .catch(function () {});
  }

  function saveText() {
    var input = $("clip-input");
    var content = input.value;
    if (!content.trim()) { msg("内容为空", false); return; }
    fetch(API + "/items", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: content })
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok) { msg(res.d.error || "存入失败", false); return; }
        input.value = "";
        msg("已存入，另一台设备约 5 秒内可见", true);
        load();
      })
      .catch(function () { msg("网络错误", false); });
  }

  function copyText(text, btn) {
    var done = function () { flash(btn, "已复制"); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
    } else {
      fallbackCopy(text);
      done();
    }
  }

  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (e) {}
    document.body.removeChild(ta);
  }

  function removeItem(id, btn) {
    btn.disabled = true;
    fetch(API + "/items/" + id, { method: "DELETE" })
      .then(function (r) {
        if (!r.ok) { btn.disabled = false; msg("删除失败", false); return; }
        load();
      })
      .catch(function () { btn.disabled = false; msg("网络错误", false); });
  }

  function uploadFile(file) {
    if (!file) return;
    var xhr = new XMLHttpRequest();          // XHR：上传进度 fetch 不可得
    var box = $("clip-progress"), bar = $("clip-progress-bar"), text = $("clip-progress-text");
    box.hidden = false;
    bar.style.width = "0%";
    text.textContent = "上传中 " + file.name + "（" + fmtBytes(file.size) + "）";
    xhr.upload.onprogress = function (e) {
      if (e.lengthComputable) {
        bar.style.width = (e.loaded / e.total * 100).toFixed(1) + "%";
      }
    };
    xhr.onload = function () {
      box.hidden = true;
      var d = {};
      try { d = JSON.parse(xhr.responseText); } catch (e) {}
      if (xhr.status === 201) {
        msg("已上传：" + file.name, true);
        load();
      } else {
        msg(d.error || "上传失败", false);
      }
    };
    xhr.onerror = function () { box.hidden = true; msg("网络错误", false); };
    xhr.open("POST", API + "/files");
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader("X-Filename", encodeURIComponent(file.name));
    xhr.send(file);
  }

  /* ---- 事件绑定 ---- */

  function bind() {
    $("clip-save").addEventListener("click", saveText);
    $("clip-input").addEventListener("keydown", function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") saveText();
    });
    $("clip-paste").addEventListener("click", function () {
      if (!navigator.clipboard || !navigator.clipboard.readText) {
        msg("此浏览器不支持读取剪贴板，请直接 Ctrl+V 粘贴到输入框", false);
        return;
      }
      navigator.clipboard.readText().then(function (t) {
        if (t) { $("clip-input").value = t; msg("已读取本机剪贴板，确认后点「存入」", true); }
        else { msg("本机剪贴板为空", false); }
      }).catch(function () {
        msg("读取被浏览器拒绝，请直接 Ctrl+V 粘贴到输入框", false);
      });
    });

    var fi = $("clip-file-input");
    $("clip-upload").addEventListener("click", function () { fi.click(); });
    fi.addEventListener("change", function () {
      if (fi.files && fi.files[0]) uploadFile(fi.files[0]);
      fi.value = "";
    });
    var drop = $("clip-drop");
    ["dragenter", "dragover"].forEach(function (ev) {
      drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("over"); });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("over"); });
    });
    drop.addEventListener("drop", function (e) {
      var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) uploadFile(f);
    });

    /* 轮询：页面可见才轮询；切回前台立即刷一次（设备 B 亮屏即同步） */
    setInterval(function () {
      if (document.visibilityState === "visible") load();
    }, POLL_MS);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") load();
    });
  }

  bind();
  load();
})();
