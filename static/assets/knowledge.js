/* 知识库：分页全文搜索与可见图表懒加载，无第三方网络请求。 */
(function () {
  "use strict";
  var query = document.getElementById("kb-query");
  var group = document.getElementById("kb-group");
  var state = document.getElementById("kb-search-state");
  var results = document.getElementById("kb-results");
  if (query) {
    var params = new URLSearchParams(location.search);
    var pathFilter = params.get("path") || "";
    query.value = params.get("q") || "";
    group.value = params.get("group") || "";
    var generation = 0;
    var controller;
    var more = document.createElement("button");
    more.type = "button";
    more.className = "kb-load-more";
    more.textContent = "加载更多结果";
    more.hidden = true;
    results.after(more);

    function search(append) {
      var current = ++generation;
      if (controller) controller.abort();
      controller = new AbortController();
      var offset = append ? results.children.length : 0;
      var request = new URLSearchParams({ q: query.value.trim(), group: group.value,
        path: pathFilter, offset: String(offset), limit: "40" });
      state.textContent = "正在搜索…";
      more.disabled = true;
      fetch("/api/v1/knowledge/search?" + request, { signal: controller.signal, cache: "no-store" })
        .then(function (r) {
          if (!r.ok) throw new Error("search unavailable");
          return r.json();
        }).then(function (data) {
          if (current !== generation) return;
          if (!append) results.replaceChildren();
          var fragment = document.createDocumentFragment();
          data.results.forEach(function (n) {
            var a = document.createElement("a");
            a.className = "kb-result";
            a.href = n.url;
            var title = document.createElement("span");
            title.className = "kb-result-title";
            title.textContent = n.title;
            var path = document.createElement("span");
            path.className = "dim";
            path.textContent = n.group_name + " · " + n.path;
            a.append(title, path);
            if (n.preview) {
              var preview = document.createElement("span");
              preview.className = "kb-preview";
              preview.textContent = n.preview;
              a.append(preview);
            }
            fragment.append(a);
          });
          results.append(fragment);
          more.hidden = results.children.length >= data.total;
          more.disabled = false;
          state.textContent = (pathFilter ? "当前目录 " + pathFilter + " · " : "") +
            (data.total ? "找到 " + data.total + " 篇文档" : "没有匹配的文档，请换个关键词或分类");
          if (pathFilter) {
            var reset = document.createElement("a");
            reset.href = "/knowledge#kb-search";
            reset.textContent = " · 返回全部文档";
            state.append(reset);
          }
          var url = new URL(location.href);
          if (query.value.trim()) url.searchParams.set("q", query.value.trim()); else url.searchParams.delete("q");
          if (group.value) url.searchParams.set("group", group.value); else url.searchParams.delete("group");
          history.replaceState(null, "", url.pathname + url.search + url.hash);
        }).catch(function (error) {
          if (error.name === "AbortError" || current !== generation) return;
          more.disabled = false;
          state.textContent = "搜索暂时不可用，请重试；可继续通过左侧目录浏览。";
        });
    }
    var timer;
    query.addEventListener("input", function () {
      ++generation;
      if (controller) controller.abort();
      clearTimeout(timer);
      timer = setTimeout(function () { search(false); }, 250);
    });
    group.addEventListener("change", function () { clearTimeout(timer); search(false); });
    more.addEventListener("click", function () { search(true); });
    if (query.value || group.value || pathFilter) search(false);
  }

  var codes = document.querySelectorAll(".markdown-body pre > code.language-mermaid");
  if (!codes.length) return;
  var libraryPromise;
  var queue = Promise.resolve();

  function library() {
    if (!libraryPromise) libraryPromise = new Promise(function (resolve, reject) {
      var script = document.createElement("script");
      script.src = "/assets/mermaid-11.12.0.min.js";
      script.async = true;
      script.onload = function () {
        mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "dark",
          fontFamily: 'system-ui, "Microsoft YaHei", sans-serif', suppressErrorRendering: true });
        resolve(mermaid);
      };
      script.onerror = function () { libraryPromise = null; script.remove(); reject(new Error("图表组件加载失败")); };
      document.head.append(script);
    });
    return libraryPromise;
  }

  function schedule(item) {
    if (item.pending || item.done) return;
    item.pending = true;
    item.button.disabled = true;
    item.button.textContent = "图表加载中…";
    queue = queue.then(function () {
      return new Promise(function (resolve) { setTimeout(resolve, 0); });
    }).then(function () { return library(); }).then(function (engine) {
      return engine.render("kb-diagram-" + item.number, item.code.textContent);
    }).then(function (rendered) {
      item.figure.innerHTML = rendered.svg;
      item.done = true;
    }).catch(function () {
      item.button.textContent = "图表加载失败，点击重试";
      item.button.disabled = false;
    }).finally(function () { item.pending = false; });
  }

  var observer = typeof IntersectionObserver !== "undefined" ? new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        observer.unobserve(entry.target);
        schedule(entry.target.kbDiagram);
      }
    });
  }, { rootMargin: "120px 0px" }) : null;

  codes.forEach(function (code, i) {
    var pre = code.parentElement;
    var figure = document.createElement("div");
    figure.className = "kb-diagram";
    var button = document.createElement("button");
    button.type = "button";
    button.className = "kb-load-more";
    button.textContent = "加载图表";
    figure.append(button);
    var source = document.createElement("details");
    var caption = document.createElement("summary");
    caption.textContent = "查看图表源码";
    source.append(caption);
    pre.before(figure, source);
    source.append(pre);
    var item = { figure: figure, button: button, code: code, number: i, pending: false, done: false };
    figure.kbDiagram = item;
    button.addEventListener("click", function () { schedule(item); });
    if (observer) observer.observe(figure);
  });
})();

/* ---- 我的知识库配置(P2):加载/保存仓库地址 + 触发同步 ---- */
(function () {
  "use strict";
  function $(id) { return document.getElementById(id); }
  var box = $("kb-config");
  if (!box) return;
  fetch("/api/v1/assistant/session", { credentials: "same-origin" })
    .then(function (r) { return r.json(); })
    .then(function (s) {
      if (!s || !s.authed) return;
      box.hidden = false;
      return fetch("/api/v1/assistant/knowledge-repo", { credentials: "same-origin" });
    })
    .then(function (r) { if (!r) return; return r.json(); })
    .then(function (d) {
      if (!d) return;
      if (d.repo) $("kb-repo-input").value = d.repo;
    }).catch(function () { /* 忽略 */ });
  $("kb-repo-save").addEventListener("click", function () {
    var msg = $("kb-config-msg");
    msg.textContent = "保存中…";
    fetch("/api/v1/assistant/knowledge-repo", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo: $("kb-repo-input").value.trim() })
    }).then(function (r) { return r.json(); })
      .then(function (d) { msg.textContent = d.ok ? (d.note || "已保存") : (d.error || "保存失败"); })
      .catch(function () { msg.textContent = "网络错误"; });
  });
  $("kb-sync-mine").addEventListener("click", function () {
    var msg = $("kb-config-msg");
    msg.textContent = "同步已启动,完成后刷新页面…";
    fetch("/api/v1/update/knowledge", { method: "POST", credentials: "same-origin" })
      .then(function (r) {
        if (r.status === 401) { msg.textContent = "请先登录"; return; }
        var tick = 0;
        var timer = setInterval(function () {
          if (++tick > 120) { clearInterval(timer); msg.textContent = "同步耗时异常,请查看日志"; return; }
          fetch("/api/v1/update/status", { cache: "no-store" })
            .then(function (r2) { return r2.json(); })
            .then(function (st) {
              if (st.state === "running") return;
              clearInterval(timer);
              msg.textContent = st.state === "success" ? "同步完成,刷新中…" : ("同步失败:" + (st.error || st.state));
              if (st.state === "success") setTimeout(function () { location.reload(); }, 800);
            });
        }, 3000);
      })
      .catch(function () { msg.textContent = "网络错误"; });
  });
})();
