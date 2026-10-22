/* 站内搜索：Ctrl/Cmd+K 打开，Esc 关闭，↑↓ 选择，Enter 跳转。
   原生 IIFE、无第三方依赖；首次打开时才 fetch 搜索索引（JSON 由构建期生成）。
   中文按字符 + 子串匹配，拉丁按词前缀/子串匹配；结果按产品/榜单/页面/新闻分组。 */
(function () {
  "use strict";

  var root = document.getElementById("search-root");
  if (!root) {
    root = document.createElement("div");
    root.id = "search-root";
    document.body.appendChild(root);
  }

  var INDEX_URL = root.getAttribute("data-index") || "/assets/search-index.json";
  var TYPE_ORDER = ["product", "ranking", "page", "news"];
  var TYPE_LABELS = { product: "产品", ranking: "榜单", page: "页面", news: "新闻" };
  var TYPE_BOOST = { product: 12, ranking: 9, page: 6, news: 4 };
  var PER_GROUP = 6;
  var MAX_RESULTS = 30;
  var SUGGESTS = ["智能体", "模型", "推理性能", "MCP", "Claude Code"];

  var overlay = null;
  var dialog = null;
  var input = null;
  var statusEl = null;
  var resultsEl = null;
  var footMeta = null;
  var flat = [];
  var active = -1;
  var opened = false;
  var loaded = false;
  var loading = false;
  var loadFailed = false;
  var loadPromise = null;
  var items = [];
  var indexMeta = { total: 0, generated_at: "" };
  var composing = false;
  var timer = null;
  var lastFocus = null;

  function $(sel) { return root.querySelector(sel); }

  function escRe(text) {
    return String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function norm(text) { return String(text == null ? "" : text).toLowerCase(); }

  function isCJKChar(ch) { return /[\u3400-\u9fff\uf900-\ufaff]/.test(ch); }

  function hasCJK(term) {
    for (var i = 0; i < term.length; i++) if (isCJKChar(term.charAt(i))) return true;
    return false;
  }

  function hasWordPrefix(text, term) {
    if (!term) return false;
    return new RegExp("(^|[^a-z0-9])" + escRe(term), "i").test(text);
  }

  function allCharsIn(term, text) {
    var seen = {};
    for (var i = 0; i < term.length; i++) {
      var ch = term.charAt(i);
      if (seen[ch]) continue;
      seen[ch] = 1;
      if (text.indexOf(ch) < 0) return false;
    }
    return true;
  }

  function parseTerms(query) {
    return norm(query).replace(/\s+/g, " ").trim().split(" ").filter(Boolean).slice(0, 6);
  }

  function scoreItem(item, terms, fullQuery) {
    var title = norm(item.title);
    var body = norm((item.title || "") + " " + (item.subtitle || "") + " " +
                    (item.keywords || "") + " " + (item.tag || "") + " " +
                    (item.direction || "") + " " + (item.board_name || "") + " " +
                    (item.category || "") + " " + (item.source || ""));
    var score = 0;
    for (var i = 0; i < terms.length; i++) {
      var term = terms[i];
      var hit = -1;
      if (hasCJK(term)) {
        if (title.indexOf(term) >= 0) hit = 145 + term.length * 8;
        else if (body.indexOf(term) >= 0) hit = 95 + term.length * 5;
        else if (allCharsIn(term, body)) hit = 34 + term.length * 2;
      } else {
        if (hasWordPrefix(title, term)) hit = 132;
        else if (title.indexOf(term) >= 0) hit = 92;
        else if (hasWordPrefix(body, term)) hit = 64;
        else if (body.indexOf(term) >= 0) hit = 42;
      }
      if (hit < 0) return -1;
      score += hit;
    }
    if (fullQuery) {
      if (title === fullQuery) score += 220;
      else if (title.indexOf(fullQuery) >= 0) score += 65;
    }
    score += TYPE_BOOST[item.type] || 0;
    return score;
  }

  function search(query) {
    var terms = parseTerms(query);
    if (!terms.length) return [];
    var full = norm(query).replace(/\s+/g, " ").trim();
    var found = [];
    for (var i = 0; i < items.length; i++) {
      var item = items[i];
      if (!item || !item.title || !item.url) continue;
      var score = scoreItem(item, terms, full);
      if (score >= 0) found.push({ item: item, score: score, order: i });
    }
    found.sort(function (a, b) {
      if (b.score !== a.score) return b.score - a.score;
      var ta = TYPE_ORDER.indexOf(a.item.type), tb = TYPE_ORDER.indexOf(b.item.type);
      if (ta !== tb) return ta - tb;
      return a.order - b.order;
    });
    return found;
  }

  function groupResults(found) {
    var groups = [];
    var buckets = {};
    for (var t = 0; t < TYPE_ORDER.length; t++) {
      buckets[TYPE_ORDER[t]] = [];
    }
    for (var i = 0; i < found.length; i++) {
      var type = found[i].item.type;
      if (!buckets[type]) buckets[type] = [];
      if (buckets[type].length < PER_GROUP) buckets[type].push(found[i].item);
    }
    for (var k = 0; k < TYPE_ORDER.length; k++) {
      var key = TYPE_ORDER[k];
      if (buckets[key] && buckets[key].length) groups.push({ type: key, items: buckets[key] });
    }
    // 兼容未来新增类型
    Object.keys(buckets).forEach(function (key) {
      if (TYPE_ORDER.indexOf(key) >= 0 || !buckets[key].length) return;
      groups.push({ type: key, items: buckets[key].slice(0, PER_GROUP) });
    });
    return groups;
  }

  function highlight(el, text, terms) {
    text = String(text == null ? "" : text);
    var useful = terms.filter(function (t) { return t && t.length; })
      .sort(function (a, b) { return b.length - a.length; });
    if (!useful.length || !text) { el.textContent = text; return; }
    var re = new RegExp("(" + useful.map(escRe).join("|") + ")", "gi");
    var last = 0, match;
    while ((match = re.exec(text)) !== null) {
      if (match.index > last) el.appendChild(document.createTextNode(text.slice(last, match.index)));
      var mark = document.createElement("mark");
      mark.textContent = match[0];
      el.appendChild(mark);
      last = match.index + match[0].length;
      if (match[0].length === 0) re.lastIndex++;
    }
    if (last < text.length) el.appendChild(document.createTextNode(text.slice(last)));
  }

  function metaLine(item) {
    if (item.type === "product") {
      var bits = [];
      var label = item.board_name || item.subtitle;
      if (label) bits.push(label);
      if (item.tier) bits.push(item.tier);
      return bits.filter(Boolean).join(" · ");
    }
    if (item.type === "ranking") return (item.snapshot_date || "") + " 快照";
    if (item.type === "news") {
      return [item.direction, item.date, item.source].filter(Boolean).join(" · ");
    }
    return "页面";
  }

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text || "";
  }

  function clearResults() {
    if (resultsEl) resultsEl.replaceChildren();
    flat = [];
    active = -1;
  }

  function showEmpty(kind, query) {
    clearResults();
    if (!resultsEl) return;
    var box = document.createElement("div");
    box.className = "ss-empty";
    var strong = document.createElement("strong");
    var hint = document.createElement("span");
    if (kind === "loading") {
      strong.textContent = "正在加载搜索索引…";
      hint.textContent = "首次打开才加载，之后使用浏览器缓存。";
    } else if (kind === "error") {
      strong.textContent = "搜索索引加载失败";
      hint.textContent = "请检查网络后重试，或刷新页面。";
    } else if (kind === "no-result") {
      strong.textContent = "没有找到匹配内容";
      hint.textContent = "试试产品名、榜单名或新闻关键词，例如：" + (query || "智能体");
    } else {
      strong.textContent = "搜索全站公开内容";
      hint.textContent = "产品、榜单、方向频道与新闻；私有区（报告厅/知识库/剪贴板）不进入索引。";
    }
    box.appendChild(strong);
    box.appendChild(hint);
    if (kind === "empty") {
      var sugg = document.createElement("div");
      sugg.className = "ss-suggests";
      SUGGESTS.forEach(function (text) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "ss-suggest";
        b.textContent = text;
        b.setAttribute("data-q", text);
        sugg.appendChild(b);
      });
      box.appendChild(sugg);
    }
    resultsEl.appendChild(box);
  }

  function setActive(index) {
    if (!flat.length) { active = -1; if (input) input.removeAttribute("aria-activedescendant"); return; }
    if (index < 0) index = flat.length - 1;
    if (index >= flat.length) index = 0;
    active = index;
    for (var i = 0; i < flat.length; i++) {
      var on = i === active;
      flat[i].classList.toggle("is-active", on);
      flat[i].setAttribute("aria-selected", on ? "true" : "false");
    }
    var el = flat[active];
    if (input) input.setAttribute("aria-activedescendant", el.id);
    if (el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
  }

  function navigate(item) {
    closeDialog();
    if (item.external) {
      window.open(item.url, "_blank", "noopener,noreferrer");
    } else {
      window.location.href = item.url;
    }
  }

  function render(query) {
    if (!resultsEl) return;
    clearResults();
    if (!loaded) {
      showEmpty(loadFailed ? "error" : "loading", query);
      setStatus(loadFailed ? "索引加载失败" : "正在加载搜索索引…");
      return;
    }
    var trimmed = String(query || "").trim();
    if (!trimmed) {
      showEmpty("empty", "");
      setStatus("共 " + indexMeta.total + " 条可搜索内容" +
                (indexMeta.generated_at ? " · 索引更新于 " + indexMeta.generated_at.slice(0, 10) : ""));
      return;
    }
    var found = search(trimmed);
    if (!found.length) {
      showEmpty("no-result", trimmed);
      setStatus("没有匹配结果");
      return;
    }
    var terms = parseTerms(trimmed);
    var groups = groupResults(found);
    var shown = 0;
    groups.forEach(function (group) {
      var head = document.createElement("div");
      head.className = "ss-group-head";
      var label = document.createElement("b");
      label.textContent = TYPE_LABELS[group.type] || group.type;
      var count = document.createElement("span");
      count.textContent = group.items.length + " 项";
      head.appendChild(label);
      head.appendChild(count);
      resultsEl.appendChild(head);
      group.items.forEach(function (item) {
        if (shown >= MAX_RESULTS) return;
        var a = document.createElement("a");
        a.className = "ss-item" + (item.external ? " is-external" : "");
        a.href = item.url;
        a.id = "ss-opt-" + flat.length;
        a.setAttribute("role", "option");
        a.setAttribute("aria-selected", "false");
        if (item.external) {
          a.target = "_blank";
          a.rel = "noopener noreferrer";
        }
        var main = document.createElement("span");
        main.className = "ss-item-main";
        var title = document.createElement("span");
        title.className = "ss-item-title";
        highlight(title, item.title, terms);
        var tag = document.createElement("span");
        tag.className = "ss-item-tag";
        tag.textContent = metaLine(item);
        main.appendChild(title);
        main.appendChild(tag);
        a.appendChild(main);
        if (item.subtitle) {
          var sub = document.createElement("span");
          sub.className = "ss-item-sub";
          highlight(sub, item.subtitle, terms);
          a.appendChild(sub);
        }
        a.addEventListener("click", function () {
          // 外链使用浏览器默认 target=_blank；内链默认跳转。关闭弹层即可。
          closeDialog();
        });
        resultsEl.appendChild(a);
        flat.push(a);
        shown++;
      });
    });
    setStatus("找到 " + found.length + " 条结果" + (found.length > shown ? " · 显示前 " + shown + " 条" : ""));
    setActive(0);
    if (input) input.setAttribute("aria-expanded", "true");
  }

  function scheduleRender() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () {
      timer = null;
      render(input ? input.value : "");
    }, 40);
  }

  function loadIndex() {
    if (loaded) return Promise.resolve();
    if (loadPromise) return loadPromise;
    loading = true;
    loadFailed = false;
    if (opened) render(input.value);
    loadPromise = fetch(INDEX_URL, { credentials: "same-origin", cache: "no-cache" })
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (data) {
        var list = data && Array.isArray(data.items) ? data.items : (Array.isArray(data) ? data : []);
        items = list.filter(function (it) { return it && it.title && it.url; });
        indexMeta.total = items.length;
        indexMeta.generated_at = (data && data.generated_at) || "";
        loaded = true;
        loading = false;
        if (opened) render(input.value);
      })
      .catch(function () {
        loading = false;
        loadFailed = true;
        loadPromise = null;
        if (opened) render(input.value);
      });
    return loadPromise;
  }

  function buildSkeleton() {
    overlay = document.createElement("div");
    overlay.className = "ss-overlay";
    overlay.hidden = true;
    overlay.innerHTML =
      '<div class="ss-dialog" role="dialog" aria-modal="true" aria-label="站内搜索">' +
        '<div class="ss-head">' +
          '<span class="ss-icon" aria-hidden="true"><svg class="ic"><use href="#i-search"/></svg></span>' +
          '<input class="ss-input" type="search" id="ss-input" role="combobox" ' +
            'aria-expanded="false" aria-controls="ss-results" aria-autocomplete="list" ' +
            'placeholder="搜索产品、榜单、方向频道与新闻…" autocomplete="off" ' +
            'autocapitalize="off" spellcheck="false">' +
          '<span class="ss-kbd"><kbd>Esc</kbd></span>' +
          '<button class="ss-close" type="button" aria-label="关闭搜索"><svg class="ic" aria-hidden="true"><use href="#i-close"/></svg> 关闭</button>' +
        '</div>' +
        '<div class="ss-status" id="ss-status" aria-live="polite"></div>' +
        '<div class="ss-results" id="ss-results" role="listbox" aria-label="搜索结果"></div>' +
        '<div class="ss-foot">' +
          '<span><kbd>↑</kbd><kbd>↓</kbd> 选择</span>' +
          '<span><kbd>Enter</kbd> 打开</span>' +
          '<span><kbd>Esc</kbd> 关闭</span>' +
          '<span class="ss-foot-right">Ctrl / ⌘ + K 随时唤起</span>' +
        '</div>' +
      '</div>';
    root.appendChild(overlay);
    dialog = overlay.querySelector(".ss-dialog");
    input = overlay.querySelector(".ss-input");
    statusEl = overlay.querySelector(".ss-status");
    resultsEl = overlay.querySelector(".ss-results");
    footMeta = overlay.querySelector(".ss-foot-right");

    overlay.addEventListener("mousedown", function (ev) {
      if (ev.target === overlay) closeDialog();
    });
    overlay.querySelector(".ss-close").addEventListener("click", closeDialog);
    resultsEl.addEventListener("click", function (ev) {
      var target = ev.target;
      var suggest = target && target.closest ? target.closest(".ss-suggest") : null;
      if (!suggest) return;
      var q = suggest.getAttribute("data-q") || "";
      input.value = q;
      render(q);
      input.focus();
    });
    input.addEventListener("compositionstart", function () { composing = true; });
    input.addEventListener("compositionend", function () { composing = false; scheduleRender(); });
    input.addEventListener("input", scheduleRender);
    input.addEventListener("keydown", function (ev) {
      if (ev.isComposing || composing) return;
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        setActive(active + 1);
      } else if (ev.key === "ArrowUp") {
        ev.preventDefault();
        setActive(active - 1);
      } else if (ev.key === "Enter") {
        if (active < 0 || !flat[active]) return;
        ev.preventDefault();
        var idx = Number(flat[active].id.replace("ss-opt-", ""));
        var data = visibleItems()[idx];
        if (data) navigate(data);
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        ev.stopPropagation();
        closeDialog();
      }
    });
  }

  var visibleCache = null;
  function visibleItems() {
    // 从当前渲染的链接反查索引条目，避免重复维护列表。
    if (!input) return [];
    var found = search(input.value);
    var out = [];
    var groups = groupResults(found);
    groups.forEach(function (g) {
      g.items.forEach(function (item) { if (out.length < MAX_RESULTS) out.push(item); });
    });
    return out;
  }

  function openDialog() {
    if (!overlay) buildSkeleton();
    if (opened) return;
    opened = true;
    lastFocus = document.activeElement;
    overlay.hidden = false;
    document.body.classList.add("ss-open");
    setExpanded(true);
    input.value = "";
    input.focus();
    if (!loaded) loadIndex();
    else render("");
  }

  function closeDialog() {
    if (!opened) return;
    opened = false;
    overlay.hidden = true;
    document.body.classList.remove("ss-open");
    setExpanded(false);
    if (lastFocus && lastFocus.focus && document.contains(lastFocus)) {
      try { lastFocus.focus(); } catch (e) { /* 忽略 */ }
    }
  }

  function setExpanded(on) {
    var buttons = document.querySelectorAll("#search-open, [data-search-open]");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute("aria-expanded", on ? "true" : "false");
    }
  }

  document.addEventListener("keydown", function (ev) {
    var key = (ev.key || "").toLowerCase();
    if ((ev.ctrlKey || ev.metaKey) && key === "k") {
      ev.preventDefault();
      if (opened) closeDialog(); else openDialog();
      return;
    }
    if (ev.key === "Escape" && opened) {
      ev.preventDefault();
      closeDialog();
    }
  });

  document.addEventListener("click", function (ev) {
    var target = ev.target;
    var btn = target && target.closest ? target.closest("#search-open, [data-search-open]") : null;
    if (!btn) return;
    ev.preventDefault();
    openDialog();
  });

  // 兼容脚本晚于按钮渲染、以及动态插入的入口。
  document.querySelectorAll("#search-open, [data-search-open]").forEach(function (btn) {
    btn.setAttribute("aria-haspopup", "dialog");
    btn.setAttribute("aria-controls", "search-root");
    btn.setAttribute("aria-expanded", "false");
  });
})();
