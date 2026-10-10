/* 新闻页客户端筛选：分类 chips + 地区下拉 + 文本过滤（原生 JS，无依赖） */
(function () {
  "use strict";

  var list = document.getElementById("news-list");
  var count = document.getElementById("news-count");
  if (!list) return;

  var items = Array.prototype.slice.call(list.querySelectorAll(".news-item"));
  var chips = Array.prototype.slice.call(
    document.querySelectorAll("#news-filter .chip"));
  var region = document.getElementById("news-region");
  var query = document.getElementById("news-q");

  function apply() {
    var cat = "";
    chips.forEach(function (c) { if (c.classList.contains("active")) cat = c.dataset.cat || ""; });
    var reg = region ? region.value : "";
    var q = query ? query.value.trim().toLowerCase() : "";
    var shown = 0;
    items.forEach(function (el) {
      var ok = (!cat || el.dataset.cat === cat)
        && (!reg || el.dataset.region === reg)
        && (!q || (el.dataset.text || "").toLowerCase().indexOf(q) !== -1);
      el.classList.toggle("hidden", !ok);
      if (ok) shown++;
    });
    if (count) count.textContent = "显示 " + shown + " / " + (count.dataset.total || items.length) + " 条，按发布时间排列";
  }

  chips.forEach(function (c) {
    c.addEventListener("click", function () {
      chips.forEach(function (x) { x.classList.remove("active"); });
      c.classList.add("active");
      apply();
    });
  });
  if (region) region.addEventListener("change", apply);
  if (query) query.addEventListener("input", apply);
})();
