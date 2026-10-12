/* 调研展示（/reports/{slug}）：架构图缩放平移 + 组件说明 + 执行步骤联动 + 全屏演示
   交互服务于理解，无装饰性动画。原生 JS，无框架。 */
(function () {
  "use strict";

  var dataEl = document.getElementById("sc-data");
  var data = { step_highlights: {}, components: [] };
  if (dataEl) {
    try { data = JSON.parse(dataEl.textContent); } catch (e) { /* 保底空数据 */ }
  }
  var components = {};
  (data.components || []).forEach(function (c) { components[c.id] = c; });
  var stepHighlights = data.step_highlights || {};

  /* ---- 架构图：缩放 / 平移 / 全屏 ---- */
  var viewport = document.getElementById("sc-viewport");
  var inner = document.getElementById("sc-diagram-inner");
  var scale = 1, tx = 0, ty = 0;

  function applyTransform() {
    if (inner) {
      inner.style.transform = "translate(" + tx + "px," + ty + "px) scale(" + scale + ")";
    }
  }
  function zoomBy(factor, cx, cy) {
    var next = Math.min(4, Math.max(0.5, scale * factor));
    if (cx !== undefined && viewport) {
      var rect = viewport.getBoundingClientRect();
      var px = cx - rect.left, py = cy - rect.top;
      tx = px - (px - tx) * (next / scale);
      ty = py - (py - ty) * (next / scale);
    }
    scale = next;
    applyTransform();
  }
  function resetView() { scale = 1; tx = 0; ty = 0; applyTransform(); }

  if (viewport && inner) {
    document.querySelectorAll("[data-zoom]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var action = btn.dataset.zoom;
        if (action === "in") zoomBy(1.25);
        else if (action === "out") zoomBy(0.8);
        else if (action === "reset") resetView();
        else if (action === "full") {
          var target = viewport.closest(".sc-diagram-wrap") || viewport;
          if (target.requestFullscreen) target.requestFullscreen();
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
  }

  /* ---- 组件说明面板 ---- */
  var panelBody = document.getElementById("sc-panel-body");
  var panelTitle = document.getElementById("sc-panel-title");
  var panelDesc = document.getElementById("sc-panel-desc");
  var panelSrc = document.getElementById("sc-panel-src");
  var panelHint = document.querySelector(".sc-panel-hint");

  function selectNode(nodeId) {
    if (!components[nodeId]) return;
    var comp = components[nodeId];
    document.querySelectorAll("#sc-diagram .sc-node").forEach(function (g) {
      g.classList.toggle("selected", g.dataset.node === nodeId);
    });
    if (!panelBody) return;
    if (panelHint) panelHint.hidden = true;
    panelBody.hidden = false;
    panelTitle.textContent = comp.label || nodeId;
    if (comp.desc_html) {
      panelDesc.innerHTML = comp.desc_html;
      panelSrc.innerHTML = '说明引自调研文档组件表 · <a href="' +
        (data.doc_url || "#sec-relations") + '">在知识库查看文档</a>';
    } else {
      panelDesc.textContent = comp.desc_note || "（研究视图定位，详见文档）";
      panelSrc.textContent = "研究视图定位注释（非文档原文结论）";
    }
  }

  var diagram = document.getElementById("sc-diagram");
  if (diagram) {
    diagram.addEventListener("click", function (ev) {
      var node = ev.target.closest(".sc-node");
      if (node && node.dataset.node) selectNode(node.dataset.node);
    });
    // 键盘可达：节点 tabindex + Enter
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
  }

  /* ---- 执行步骤切换（联动架构图节点高亮）---- */
  function clearStepHighlight() {
    document.querySelectorAll("#sc-diagram .sc-node").forEach(function (g) {
      g.classList.remove("step-active");
    });
  }
  function highlightStep(stepIndex) {
    clearStepHighlight();
    var nodes = stepHighlights[String(stepIndex + 1)] || [];
    nodes.forEach(function (id) {
      var g = document.querySelector('#sc-diagram .sc-node[data-node="' + id + '"]');
      if (g) g.classList.add("step-active");
    });
  }

  document.querySelectorAll("[data-steps]").forEach(function (wrap) {
    var bodies = wrap.querySelectorAll("[data-step-body]");
    var dots = wrap.querySelectorAll("[data-step]");
    var current = 0;
    function show(index) {
      current = Math.max(0, Math.min(bodies.length - 1, index));
      bodies.forEach(function (b, i) { b.classList.toggle("hidden", i !== current); });
      dots.forEach(function (d, i) { d.classList.toggle("on", i === current); });
      highlightStep(current);
    }
    var prev = wrap.querySelector("[data-step-prev]");
    var next = wrap.querySelector("[data-step-next]");
    if (prev) prev.addEventListener("click", function () { show(current - 1); });
    if (next) next.addEventListener("click", function () { show(current + 1); });
    dots.forEach(function (dot) {
      dot.addEventListener("click", function () { show(parseInt(dot.dataset.step, 10)); });
    });
    if (bodies.length) show(0);
  });

  /* ---- 全屏演示模式 ---- */
  var presentBtn = document.getElementById("sc-present");
  var scMain = document.getElementById("sc-main");
  if (presentBtn && scMain && scMain.requestFullscreen) {
    presentBtn.addEventListener("click", function () {
      scMain.requestFullscreen();
    });
  } else if (presentBtn) {
    presentBtn.hidden = true;   // 浏览器不支持时隐藏入口
  }
})();
