/* 调研展示（/reports/{slug}）：多实例交互——每张图独立完成
   缩放/平移、组件说明、执行步骤联动；外加侧边目录滚动高亮与整页演示模式。
   交互服务于理解，无装饰性动画。原生 JS，无框架。 */
(function () {
  "use strict";

  /* ---- 每个 .sc-flow 实例：图 + （可选）步骤 + 组件面板，闭环交互 ---- */
  document.querySelectorAll(".sc-flow").forEach(function (flow) {
    var data = { components: [], step_highlights: {}, doc_url: "" };
    var dataEl = flow.querySelector(".sc-data");
    if (dataEl) {
      try { data = JSON.parse(dataEl.textContent); } catch (e) { /* 保底空数据 */ }
    }
    var comps = {};
    (data.components || []).forEach(function (c) { comps[c.id] = c; });

    var viewport = flow.querySelector(".sc-viewport");
    var inner = flow.querySelector(".sc-inner");
    var diagram = inner ? inner.querySelector("svg") : null;
    if (!viewport || !inner || !diagram) return;

    /* 缩放 / 平移（实例内独立状态） */
    var scale = 1, tx = 0, ty = 0;
    function applyTransform() {
      inner.style.transform = "translate(" + tx + "px," + ty + "px) scale(" + scale + ")";
    }
    function zoomBy(factor, cx, cy) {
      var next = Math.min(4, Math.max(0.5, scale * factor));
      if (cx !== undefined) {
        var rect = viewport.getBoundingClientRect();
        var px = cx - rect.left, py = cy - rect.top;
        tx = px - (px - tx) * (next / scale);
        ty = py - (py - ty) * (next / scale);
      }
      scale = next;
      applyTransform();
    }
    flow.querySelectorAll("[data-zoom]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var action = btn.dataset.zoom;
        if (action === "in") zoomBy(1.25);
        else if (action === "out") zoomBy(0.8);
        else if (action === "reset") { scale = 1; tx = 0; ty = 0; applyTransform(); }
        else if (action === "full") {
          // 全屏整个流程卡：图 + 步骤 + 组件面板一起进全屏，演示时可切步
          if (flow.requestFullscreen) {
            flow.requestFullscreen();
            setTimeout(function () { applyTransform(); }, 60);
          }
        }
      });
    });
    viewport.addEventListener("wheel", function (ev) {
      ev.preventDefault();
      zoomBy(ev.deltaY < 0 ? 1.12 : 0.89, ev.clientX, ev.clientY);
    }, { passive: false });
    var dragging = null;
    viewport.addEventListener("pointerdown", function (ev) {
      if (ev.target.closest("[data-zoom]")) return;
      dragging = { x: ev.clientX - tx, y: ev.clientY - ty };
      viewport.setPointerCapture(ev.pointerId);
      viewport.classList.add("dragging");
    });
    viewport.addEventListener("pointermove", function (ev) {
      if (!dragging) return;
      tx = ev.clientX - dragging.x;
      ty = ev.clientY - dragging.y;
      applyTransform();
    });
    ["pointerup", "pointercancel"].forEach(function (name) {
      viewport.addEventListener(name, function () {
        dragging = null;
        viewport.classList.remove("dragging");
      });
    });

    /* 组件说明面板（实例内） */
    var panelBody = flow.querySelector(".sc-panel-body");
    var panelHint = flow.querySelector(".sc-panel-hint");
    var panelTitle = flow.querySelector(".sc-panel-title");
    var panelDesc = flow.querySelector(".sc-panel-desc");
    var panelSrc = flow.querySelector(".sc-panel-src");

    function selectNode(nodeId) {
      var comp = comps[nodeId];
      if (!comp) return;
      diagram.querySelectorAll(".sc-node").forEach(function (g) {
        g.classList.toggle("selected", g.dataset.node === nodeId);
      });
      if (!panelBody) return;
      if (panelHint) panelHint.hidden = true;
      panelBody.hidden = false;
      panelTitle.textContent = comp.label || nodeId;
      if (comp.desc_html) {
        panelDesc.innerHTML = comp.desc_html;
        panelSrc.innerHTML = '说明引自调研文档对应表格 · <a href="' +
          (data.doc_url || "#sec-overview") + '">知识库原文</a>';
      } else {
        panelDesc.textContent = comp.desc_note || "（研究视图定位，详见文档）";
        panelSrc.textContent = "研究视图定位注释（非文档原文结论）";
      }
    }
    diagram.addEventListener("click", function (ev) {
      var g = ev.target.closest(".sc-node");
      if (g && g.dataset.node) selectNode(g.dataset.node);
    });
    diagram.querySelectorAll(".sc-node[data-node]").forEach(function (g) {
      g.setAttribute("tabindex", "0");
      g.setAttribute("role", "button");
      g.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          selectNode(g.dataset.node);
        }
      });
    });

    /* 执行步骤切换 → 同图节点高亮（图与步骤同屏，无需滚动） */
    var stepsWrap = flow.querySelector(".sc-steps");
    if (stepsWrap) {
      var bodies = stepsWrap.querySelectorAll("[data-step-body]");
      var dots = stepsWrap.querySelectorAll("[data-step]");
      var current = 0;
      function show(index) {
        current = Math.max(0, Math.min(bodies.length - 1, index));
        bodies.forEach(function (b, i) { b.classList.toggle("hidden", i !== current); });
        dots.forEach(function (d, i) { d.classList.toggle("on", i === current); });
        diagram.querySelectorAll(".sc-node").forEach(function (g) {
          g.classList.remove("step-active");
        });
        (data.step_highlights[String(current + 1)] || []).forEach(function (id) {
          var g = diagram.querySelector('.sc-node[data-node="' + id + '"]');
          if (g) g.classList.add("step-active");
        });
      }
      var prev = stepsWrap.querySelector("[data-step-prev]");
      var next = stepsWrap.querySelector("[data-step-next]");
      if (prev) prev.addEventListener("click", function () { show(current - 1); });
      if (next) next.addEventListener("click", function () { show(current + 1); });
      dots.forEach(function (dot) {
        dot.addEventListener("click", function () { show(parseInt(dot.dataset.step, 10)); });
      });
      if (bodies.length) show(0);
    }
  });

  /* ---- 侧边目录：滚动高亮（scrollspy） ---- */
  var tocLinks = {};
  document.querySelectorAll(".sc-toc a").forEach(function (a) {
    tocLinks[a.getAttribute("href")] = a;
  });
  if ("IntersectionObserver" in window) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        document.querySelectorAll(".sc-toc a.on").forEach(function (x) {
          x.classList.remove("on");
        });
        var link = tocLinks["#" + entry.target.id];
        if (link) link.classList.add("on");
      });
    }, { rootMargin: "-15% 0px -75% 0px" });
    document.querySelectorAll(".sc-main section[id]").forEach(function (s) {
      observer.observe(s);
    });
  }

  /* ---- 演示文稿网页版：翻页 / 全屏（键盘在下方全屏处理中统一接入） ---- */
  document.querySelectorAll("[data-slides]").forEach(function (wrap) {
    var bodies = wrap.querySelectorAll("[data-slide-body]");
    var dots = wrap.querySelectorAll("[data-slide]");
    var pos = wrap.querySelector(".sc-slides-pos b");
    var current = 0;
    function show(index) {
      current = Math.max(0, Math.min(bodies.length - 1, index));
      bodies.forEach(function (b, i) { b.classList.toggle("hidden", i !== current); });
      dots.forEach(function (d, i) { d.classList.toggle("on", i === current); });
      if (pos) pos.textContent = String(current + 1);
    }
    var prev = wrap.querySelector("[data-slide-prev]");
    var next = wrap.querySelector("[data-slide-next]");
    if (prev) prev.addEventListener("click", function () { show(current - 1); });
    if (next) next.addEventListener("click", function () { show(current + 1); });
    dots.forEach(function (d) {
      d.addEventListener("click", function () { show(parseInt(d.dataset.slide, 10)); });
    });
    var full = wrap.querySelector(".sc-slide-full");
    if (full && wrap.requestFullscreen) {
      full.addEventListener("click", function () { wrap.requestFullscreen(); });
    } else if (full) {
      full.hidden = true;
    }
    if (bodies.length) show(0);
  });

  /* ---- 全屏演示时的键盘导航：←/→ 翻页或切步、数字键直达（Esc 浏览器原生退出） ---- */
  document.addEventListener("keydown", function (ev) {
    var fs = document.fullscreenElement;
    if (!fs) return;
    var flow = fs.classList && fs.classList.contains("sc-flow")
      ? fs : (fs.closest ? fs.closest(".sc-flow") : null);
    if (flow) {
      var stepsWrap = flow.querySelector(".sc-steps");
      if (!stepsWrap) return;
      if (ev.key === "ArrowRight" || ev.key === "ArrowDown" || ev.key === " ") {
        ev.preventDefault();
        var next = stepsWrap.querySelector("[data-step-next]");
        if (next) next.click();
      } else if (ev.key === "ArrowLeft" || ev.key === "ArrowUp") {
        ev.preventDefault();
        var prev = stepsWrap.querySelector("[data-step-prev]");
        if (prev) prev.click();
      } else if (/^[1-9]$/.test(ev.key)) {
        var dot = stepsWrap.querySelector('[data-step="' + (parseInt(ev.key, 10) - 1) + '"]');
        if (dot) { ev.preventDefault(); dot.click(); }
      }
      return;
    }
    var slides = fs.classList && fs.classList.contains("sc-slides")
      ? fs : (fs.closest ? fs.closest(".sc-slides") : null);
    if (slides) {
      if (ev.key === "ArrowRight" || ev.key === "ArrowDown" || ev.key === " ") {
        ev.preventDefault();
        var n2 = slides.querySelector("[data-slide-next]");
        if (n2) n2.click();
      } else if (ev.key === "ArrowLeft" || ev.key === "ArrowUp") {
        ev.preventDefault();
        var p2 = slides.querySelector("[data-slide-prev]");
        if (p2) p2.click();
      } else if (/^[1-9]$/.test(ev.key)) {
        var d2 = slides.querySelector('[data-slide="' + (parseInt(ev.key, 10) - 1) + '"]');
        if (d2) { ev.preventDefault(); d2.click(); }
      }
    }
  });

  /* ---- 全屏演示模式 ---- */
  var presentBtn = document.getElementById("sc-present");
  var scMain = document.getElementById("sc-main");
  if (presentBtn && scMain && scMain.requestFullscreen) {
    presentBtn.addEventListener("click", function () { scMain.requestFullscreen(); });
  } else if (presentBtn) {
    presentBtn.hidden = true;
  }
})();
