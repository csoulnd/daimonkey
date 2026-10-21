"""构建期 SVG 图表（规范 9.3：图表在构建期渲染，不在浏览器跑图表库）。

设计要点
--------
- 纯函数、零依赖：输入 Python 数据，输出内联 ``<svg>`` 字符串；
- 颜色走 CSS 变量（``--chart-1`` ~ ``--chart-6``），值后带暗色兜底，
  明暗主题只切换 ``charts.css`` 里的变量；
- 可访问性：``role="img"`` + ``<title>`` + ``<desc>``，所有文本转义；
- 原生 tooltip：每个数据点带 ``<title>``（不引入 JS 图表库）；
- 体积控制：数据点超过 ``max_points``（默认 120）时等距抽样并保留极值；
- 空态：返回带说明的占位块，而不是空 ``<svg>``；
- 向后兼容：``multi_line_chart(labels, series, width, height)`` 与
  ``line_chart(points, width, height, stroke)`` 的旧调用方式保持不变，
  新增参数全部为关键字参数。
"""
from __future__ import annotations

import hashlib
import itertools
import math
import re
from html import escape

PALETTE = ["#58a6ff", "#f87171", "#4ade80", "#fbbf24", "#c39bff", "#22d3ee"]
DEFAULT_MAX_POINTS = 120
# 悬停热区预算：按日期生成垂直热区（多线图一个日期合并所有系列），
# 超过预算时等距选取，避免逐点元素把 SVG 撑大。
HOVER_TARGET_BUDGET = 32

# 允许的颜色表达式：普通色值、rgb()/hsl()、CSS 变量（含兜底值）。
# 禁止引号/分号/花括号，杜绝经 style 属性注入。
_COLOR_RE = re.compile(r"^[#a-zA-Z0-9(),.%\s\-]+$")
_SEQ = itertools.count(1)

__all__ = ["PALETTE", "multi_line_chart", "line_chart", "sparkline", "bar_chart"]


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _uid(*parts) -> str:
    """确定性短 id：同一图表重复构建得到同一 id，便于 diff 与缓存。"""
    raw = "\x1f".join(str(p) for p in parts).encode("utf-8")
    return "c" + hashlib.sha1(raw).hexdigest()[:12]


def _safe_color(value, fallback: str) -> str:
    if isinstance(value, str) and value and _COLOR_RE.match(value):
        return value
    return fallback


def _palette(i: int) -> str:
    n = i % len(PALETTE)
    return f"var(--chart-{n + 1}, {PALETTE[n]})"


def _color_at(i: int, colors=None) -> str:
    if colors and i < len(colors):
        return _safe_color(colors[i], _palette(i))
    return _palette(i)


def _clean(v: float) -> float:
    return round(float(v), 10)


def _fmt(v, decimals=None) -> str:
    """人类可读的数值（tooltip / 末端标注 / 条形值）。"""
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    if decimals is not None:
        return f"{v:,.{max(0, int(decimals))}f}"
    av = abs(v)
    if av >= 1_000_000:
        s = f"{v / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{s}M"
    if av >= 10_000:
        return f"{v / 1_000:.0f}k"
    if av >= 1_000:
        s = f"{v / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{s}k"
    if v == int(v):
        return str(int(v))
    return f"{v:.4f}".rstrip("0").rstrip(".")


def _fmt_axis(v) -> str:
    """坐标轴刻度：保留千分位，不做过早的 k/M 缩写。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    av = abs(v)
    if av >= 1_000_000:
        digits = 2 if av < 10_000_000 else 1
        return f"{v / 1_000_000:.{digits}f}".rstrip("0").rstrip(".") + "M"
    if av >= 10_000:
        return f"{v:,.0f}"
    if v == int(v):
        return f"{int(v):,}"
    if av >= 10:
        return f"{v:.1f}".rstrip("0").rstrip(".")
    if av >= 1:
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return f"{v:.4f}".rstrip("0").rstrip(".")


def _nice_step(span: float, target: int = 4) -> float:
    if span <= 0 or not math.isfinite(span):
        return 1.0
    raw = span / max(1, target - 1)
    mag = 10 ** math.floor(math.log10(raw))
    norm = raw / mag
    if norm <= 1:
        nice = 1.0
    elif norm <= 2:
        nice = 2.0
    elif norm <= 2.5:
        nice = 2.5
    elif norm <= 5:
        nice = 5.0
    else:
        nice = 10.0
    return nice * mag


def _nice_ticks(lo: float, hi: float, target: int = 5,
                integer: bool = False) -> list[float]:
    """生成完整覆盖 [lo, hi] 的「好看」刻度（两端都取整到步长）。"""
    if not math.isfinite(lo) or not math.isfinite(hi):
        return [0.0, 1.0]
    if hi < lo:
        lo, hi = hi, lo
    if hi == lo:
        pad = 1.0 if lo == 0 else max(abs(lo) * 0.1, 1.0)
        lo, hi = lo - pad, hi + pad
    step = _nice_step(hi - lo, max(2, target))
    if integer:
        step = max(1.0, float(round(step)))
    ticks: list[float] = []
    for _ in range(8):
        start = math.floor(lo / step) * step
        end = math.ceil(hi / step) * step
        ticks = []
        v = start
        # 浮点步进可能多/少一格，用计数上限兜底。
        while v <= end + step * 1e-9 and len(ticks) <= 32:
            ticks.append(_clean(v))
            v += step
        if len(ticks) >= (2 if integer else 3):
            break
        if integer:
            break
        step /= 2
    if len(ticks) < 2:
        return [_clean(lo), _clean(hi)]
    if len(ticks) > 8:
        # 只保留首尾与均匀中间点，避免坐标轴拥挤。
        idx = sorted({0, len(ticks) - 1,
                      *{round(i * (len(ticks) - 1) / 5) for i in range(1, 5)}})
        ticks = [ticks[i] for i in idx]
    return ticks


def _short_label(label, all_labels) -> str:
    """时间标签压缩：同年序列把 2026-10-19 显示成 10-19。"""
    s = str(label)
    core = s[:10] if len(s) > 10 and s[10] in "T " else s
    if len(core) >= 10 and core[4] == "-" and core[7] == "-" and core[:4].isdigit():
        years = set()
        for x in all_labels:
            xs = str(x)
            xc = xs[:10] if len(xs) > 10 and xs[10] in "T " else xs
            if len(xc) >= 10 and xc[4] == "-":
                years.add(xc[:4])
        if len(years) <= 1:
            return core[5:]
    return core


def _normalize_points(points) -> list[tuple[str, float | None]]:
    out: list[tuple[str, float | None]] = []
    for item in points or []:
        if isinstance(item, (tuple, list)):
            if len(item) >= 2:
                label, value = item[0], item[1]
            elif len(item) == 1:
                label, value = "", item[0]
            else:
                continue
        elif isinstance(item, dict):
            label, value = item.get("label", item.get("date", "")), item.get("value")
        else:
            label, value = "", item
        if value is None:
            out.append((str(label), None))
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = None
        if v is not None and not math.isfinite(v):
            v = None
        out.append((str(label), v))
    return out


def _normalize_series(series, n: int) -> dict[str, list[float | None]]:
    out: dict[str, list[float | None]] = {}
    if isinstance(series, dict):
        items = series.items()
    elif series:
        items = series
    else:
        items = []
    for name, pts in items:
        vals: list[float | None] = []
        pts = list(pts or [])
        for i in range(n):
            raw = pts[i] if i < len(pts) else None
            if raw is None:
                vals.append(None)
                continue
            try:
                v = float(raw)
            except (TypeError, ValueError):
                v = None
            if v is not None and not math.isfinite(v):
                v = None
            vals.append(v)
        out[str(name)] = vals
    return out


def _sample_indices(count: int, limit: int, series_values=None) -> list[int]:
    """等距抽样 + 保留每条线的极值点；结果数量 <= limit。"""
    if count <= 0:
        return []
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_POINTS
    limit = max(2, limit)
    if count <= limit:
        return list(range(count))
    keep = {0, count - 1}
    for vals in series_values or []:
        lo_i = hi_i = None
        for i, v in enumerate(vals[:count]):
            if v is None:
                continue
            if lo_i is None or v < vals[lo_i]:
                lo_i = i
            if hi_i is None or v > vals[hi_i]:
                hi_i = i
        if lo_i is not None:
            keep.add(lo_i)
        if hi_i is not None:
            keep.add(hi_i)
    if len(keep) > limit:
        # 极端点本身超过预算（极多曲线）时退回纯等距，保证体积上限。
        keep = {0, count - 1}
    room = limit - len(keep)
    if room > 0:
        step = (count - 1) / (room + 1)
        for j in range(1, room + 1):
            keep.add(min(count - 1, max(0, int(round(j * step)))))
    return sorted(keep)


def _x_tick_indices(count: int, max_labels: int) -> list[int]:
    if count <= 0:
        return []
    max_labels = max(2, min(8, int(max_labels)))
    if count <= max_labels:
        return list(range(count))
    step = (count - 1) / (max_labels - 1)
    return sorted({int(round(i * step)) for i in range(max_labels)})


def _segments(sampled) -> list[list[tuple[int, float]]]:
    """把带 None 缺口的点列拆成连续线段，缺口不连线。"""
    segs: list[list[tuple[int, float]]] = []
    cur: list[tuple[int, float]] = []
    for orig_i, _label, value in sampled:
        if value is None:
            if cur:
                segs.append(cur)
                cur = []
        else:
            cur.append((orig_i, float(value)))
    if cur:
        segs.append(cur)
    return segs


def _text_px(s: str) -> float:
    """粗略文字宽度：CJK 按 11px、拉丁按 6.8px（11px 字号）。"""
    return sum(11.0 if ord(ch) > 0x2E80 else 6.8 for ch in str(s))


def _truncate_label(name: str, max_px: float) -> str:
    if _text_px(name) <= max_px:
        return name
    out = []
    used = 0.0
    for ch in name:
        w = 11.0 if ord(ch) > 0x2E80 else 6.8
        if used + w > max_px - 8:
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"


def _empty(message: str, hint: str = "", *, inline: bool = False) -> str:
    body = escape(str(message))
    if hint:
        body += f'<span class="ce-hint">{escape(str(hint))}</span>'
    if inline:
        return f'<span class="chart-empty inline" role="status">{body}</span>'
    return f'<div class="chart-empty" role="status">{body}</div>'


def _svg_open(width: int, height: int, title: str, desc: str, cls: str,
              extra_attrs: str = "") -> str:
    tid = _uid(cls, title, desc, width, height, "title") + str(next(_SEQ))
    did = tid + "d"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {int(width)} {int(height)}" '
        f'class="{cls}" role="img" aria-labelledby="{tid} {did}"{extra_attrs}>'
        f'<title id="{tid}">{escape(str(title))}</title>'
        f'<desc id="{did}">{escape(str(desc))}</desc>'
    )


def _hover_indices(count: int, limit: int = HOVER_TARGET_BUDGET) -> list[int]:
    """按显示点等距选取悬停热区，结果数量 <= limit 且包含首尾。"""
    if count <= 0:
        return []
    limit = max(2, min(int(limit), count))
    if count <= limit:
        return list(range(count))
    step = (count - 1) / (limit - 1)
    return sorted({int(round(i * step)) for i in range(limit)})


def _hit_band(x: float, width: float, tip: str, top: float, height: float) -> str:
    """垂直透明热区：悬停日期的任意纵向位置都能看到该日全部数值。"""
    return (f'<rect class="c-hit" x="{x - width / 2:.1f}" y="{top:.1f}" '
            f'width="{max(2.0, width):.1f}" height="{height:.1f}" '
            f'fill="transparent" pointer-events="all"><title>{escape(tip)}</title></rect>')


def _smooth_path(pts: list[tuple[float, float]]) -> str:
    """Catmull-Rom → 三次贝塞尔；控制点夹在相邻点区间内，避免过冲。"""
    if len(pts) < 3:
        return ""
    d = [f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"]
    for k in range(len(pts) - 1):
        p0 = pts[k - 1] if k > 0 else pts[k]
        p1 = pts[k]
        p2 = pts[k + 1]
        p3 = pts[k + 2] if k + 2 < len(pts) else p2
        c1x = p1[0] + (p2[0] - p0[0]) / 6
        c1y = p1[1] + (p2[1] - p0[1]) / 6
        c2x = p2[0] - (p3[0] - p1[0]) / 6
        c2y = p2[1] - (p3[1] - p1[1]) / 6
        lo_x, hi_x = min(p1[0], p2[0]), max(p1[0], p2[0])
        lo_y, hi_y = min(p1[1], p2[1]), max(p1[1], p2[1])
        c1x = min(hi_x, max(lo_x, c1x))
        c2x = min(hi_x, max(lo_x, c2x))
        c1y = min(hi_y, max(lo_y, c1y))
        c2y = min(hi_y, max(lo_y, c2y))
        d.append(f" C{c1x:.1f},{c1y:.1f} {c2x:.1f},{c2y:.1f} {p2[0]:.1f},{p2[1]:.1f}")
    return "".join(d)


# ---------------------------------------------------------------------------
# 单折线图
# ---------------------------------------------------------------------------

def line_chart(points, width: int = 640, height: int = 200,
               stroke: str = "var(--acc, #58a6ff)", *,
               unit: str = "", title: str = "", desc: str = "",
               smooth: bool = False, max_points: int = DEFAULT_MAX_POINTS,
               hover: bool = True, annotate: bool = True, area: bool = True,
               decimals=None, x_label: str = "") -> str:
    """单指标时序图。``points`` 为 ``[(label, value), ...]``，按时间升序。

    新增可选参数（全部关键字）：
    ``unit`` 纵轴单位、``title``/``desc`` 无障碍文案、``smooth`` 平滑曲线、
    ``max_points`` 降采样上限、``hover`` 逐点原生 tooltip、
    ``annotate`` 末端数值标注、``area`` 面积填充、``decimals`` 数值小数位。
    """
    width, height = max(160, int(width)), max(80, int(height))
    raw = _normalize_points(points)
    if not raw:
        return _empty("暂无数据点", "接入数据源后，下一次构建会生成趋势图")
    if not any(v is not None for _, v in raw):
        return _empty("暂无可绘制的数值", "所有时间点的值均为空")

    total = len(raw)
    idx = _sample_indices(total, max_points, [[v for _, v in raw]])
    sampled = [(i, raw[i][0], raw[i][1]) for i in idx]
    shown = len(sampled)
    values = [v for _, _, v in sampled if v is not None]
    int_axis = all(float(v).is_integer() for v in values)
    ticks = _nice_ticks(min(values), max(values), integer=int_axis)
    lo, hi = ticks[0], ticks[-1]

    label_strings = [_fmt_axis(t) for t in ticks]
    pad_l = int(min(max(38, max(len(s) for s in label_strings) * 6.6 + 14),
                    max(38, width * 0.28)))
    pad_r, pad_t, pad_b = 18, 26, (34 if x_label else 30)
    plot_w = max(20, width - pad_l - pad_r)
    plot_h = max(20, height - pad_t - pad_b)
    base_y = pad_t + plot_h
    single = total == 1

    def X(i: int) -> float:
        if single:
            return pad_l + plot_w / 2
        return pad_l + plot_w * i / (total - 1)

    def Y(v: float) -> float:
        return pad_t + plot_h - (v - lo) / (hi - lo) * plot_h if hi > lo else pad_t + plot_h / 2

    color = _safe_color(stroke, "var(--acc, #58a6ff)")
    segs = _segments(sampled)
    first_label = next((l for _, l, _ in sampled if l), "")
    last_label = next((l for _, l, _ in reversed(sampled) if l), "")
    chart_title = title or "趋势图"
    chart_desc = desc or (
        f"{chart_title}：{shown} 个数据点"
        + (f"，时间范围 {first_label} 至 {last_label}" if first_label or last_label else "")
        + (f"，纵轴单位 {unit}" if unit else "")
        + (f"，已从 {total} 个点降采样" if shown < total else "")
    )
    attrs = (f' data-points="{shown}" data-total-points="{total}"'
             f' data-downsampled="{"true" if shown < total else "false"}"')
    out = [_svg_open(width, height, chart_title, chart_desc, "chart", attrs)]

    # 面积填充（在网格之下）
    if area:
        for seg in segs:
            if len(seg) < 2:
                continue
            xy = [(X(i), Y(v)) for i, v in seg]
            d = (f"M{xy[0][0]:.1f},{base_y:.1f} "
                 + " ".join(f"L{x:.1f},{y:.1f}" for x, y in xy)
                 + f" L{xy[-1][0]:.1f},{base_y:.1f} Z")
            out.append(f'<path class="c-area" d="{d}" style="fill:{color}" '
                       f'fill-opacity="0.10" stroke="none"/>')

    # 网格 + y 轴刻度
    for t in ticks:
        y = Y(t)
        cls = "gl c-grid-line c-zero" if t == 0 else "gl c-grid-line"
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + plot_w}" '
                   f'y2="{y:.1f}" class="{cls}"/>')
        out.append(f'<text x="{pad_l - 6}" y="{y + 3.5:.1f}" text-anchor="end" '
                   f'class="ax c-y">{escape(_fmt_axis(t))}</text>')

    # 坐标轴
    axis_style = 'style="stroke:var(--line,#30363d);stroke-width:1"'
    out.append(f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{base_y}" '
               f'class="c-axis" {axis_style}/>')
    out.append(f'<line x1="{pad_l}" y1="{base_y}" x2="{pad_l + plot_w}" y2="{base_y}" '
               f'class="c-axis" {axis_style}/>')
    if unit:
        out.append(f'<text x="6" y="14" text-anchor="start" class="ax c-unit">'
                   f'{escape(unit)}</text>')
    if x_label:
        out.append(f'<text x="{pad_l + plot_w / 2:.1f}" y="{height - 7}" '
                   f'text-anchor="middle" class="ax c-xlabel">{escape(x_label)}</text>')

    # x 轴标签智能抽稀
    max_labels = max(2, min(8, int(plot_w // 72)))
    tick_idx = _x_tick_indices(shown, max_labels)
    labels_all = [l for _, l, _ in sampled]
    xtick_y = height - 25 if x_label else height - 9
    for k in tick_idx:
        orig_i, label, _ = sampled[k]
        x = X(orig_i)
        anchor = "start" if k == 0 else ("end" if k == shown - 1 else "middle")
        out.append(f'<text x="{x:.1f}" y="{xtick_y}" text-anchor="{anchor}" '
                   f'class="ax c-x">{escape(_short_label(label, labels_all))}</text>')

    # 折线
    series_tip = chart_title
    for seg in segs:
        xy = [(X(i), Y(v)) for i, v in seg]
        if len(xy) >= 3 and smooth:
            d = _smooth_path(xy)
            out.append(f'<path class="c-line" d="{d}" fill="none" '
                       f'style="stroke:{color};stroke-width:2;stroke-linejoin:round;'
                       f'stroke-linecap:round"><title>{escape(series_tip)}</title></path>')
        elif len(xy) >= 2:
            poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
            out.append(f'<polyline class="c-line" points="{poly}" fill="none" '
                       f'style="stroke:{color};stroke-width:2;stroke-linejoin:round;'
                       f'stroke-linecap:round"><title>{escape(series_tip)}</title></polyline>')
        else:
            x, y = xy[0]
            out.append(f'<circle class="c-dot" cx="{x:.1f}" cy="{y:.1f}" r="2.6" '
                       f'style="fill:{color}"/>')

    # 悬停热区（原生 tooltip）：按预算等距选点，避免逐点元素过多
    if hover:
        band_w = plot_w / max(1, total - 1) if total > 1 else plot_w
        band_w = min(48.0, max(10.0, band_w))
        for k in _hover_indices(shown):
            orig_i, label, v = sampled[k]
            if v is None:
                continue
            tip = f"{label} · {_fmt(v, decimals)}" + (f" {unit}" if unit else "")
            out.append(_hit_band(X(orig_i), band_w, tip, pad_t, plot_h))

    # 末端点高亮 + 数值标注
    last = next((s for s in reversed(sampled) if s[2] is not None), None)
    if last is not None:
        orig_i, label, v = last
        lx, ly = X(orig_i), Y(v)
        tip = f"{label} · {_fmt(v, decimals)}" + (f" {unit}" if unit else "")
        out.append(f'<circle class="c-dot c-last-dot" cx="{lx:.1f}" cy="{ly:.1f}" r="3.4" '
                   f'style="fill:{color};stroke:var(--panel,#101018);stroke-width:1.5">'
                   f'<title>{escape(tip)}</title></circle>')
        if annotate:
            tx = min(max(lx - 8, pad_l + 2), pad_l + plot_w)
            ty = min(max(ly - 9, pad_t + 10), base_y - 2)
            out.append(f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="end" '
                       f'class="last c-last" style="fill:{color}">'
                       f'{escape(_fmt(v, decimals))}</text>')

    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------------------
# 多线对比图
# ---------------------------------------------------------------------------

def multi_line_chart(labels, series, width: int = 640, height: int = 220, *,
                     unit: str = "", title: str = "", desc: str = "",
                     smooth: bool = False, max_points: int = DEFAULT_MAX_POINTS,
                     hover: bool = True, annotate=None, decimals=None,
                     colors=None, area: bool = False, x_label: str = "") -> str:
    """多产品/多模型趋势对比。``series``: ``{名称: 与 labels 等长的数值列表}``。

    旧签名 ``multi_line_chart(labels, series, width, height)`` 完全兼容。
    新增参数均为关键字：``unit``/``title``/``desc``/``smooth``/``max_points``/
    ``hover``/``annotate``（默认 ≤6 条曲线标注末值，可关）/``decimals``/
    ``colors``/``area``/``x_label``。
    """
    width, height = max(160, int(width)), max(80, int(height))
    labels = list(labels or [])
    total = len(labels)
    clean_series = _normalize_series(series, total)
    if total < 2:
        return _empty(
            f"历史快照不足 2 份，趋势图将在多日更新后自动出现（当前 {total} 份）",
            "多日采集后自动出图",
        )
    if not clean_series or not any(
            v is not None for vals in clean_series.values() for v in vals):
        return _empty("暂无可绘制的趋势数据", "序列中没有有效数值")

    idx = _sample_indices(total, max_points, list(clean_series.values()))
    sampled_labels = [labels[i] for i in idx]
    shown = len(idx)
    all_vals = [vals[i] for vals in clean_series.values() for i in idx if vals[i] is not None]
    int_axis = all(float(v).is_integer() for v in all_vals)
    ticks = _nice_ticks(min(all_vals), max(all_vals), integer=int_axis)
    lo, hi = ticks[0], ticks[-1]

    label_strings = [_fmt_axis(t) for t in ticks]
    pad_l = int(min(max(38, max(len(s) for s in label_strings) * 6.6 + 14),
                    max(38, width * 0.28)))
    pad_r, pad_t, pad_b = 18, 26, (34 if x_label else 30)
    plot_w = max(20, width - pad_l - pad_r)
    plot_h = max(20, height - pad_t - pad_b)
    base_y = pad_t + plot_h

    def X(orig_i: int) -> float:
        return pad_l + plot_w * orig_i / (total - 1)

    def Y(v: float) -> float:
        return pad_t + plot_h - (v - lo) / (hi - lo) * plot_h if hi > lo else pad_t + plot_h / 2

    names = list(clean_series.keys())
    if annotate is None:
        annotate = len(names) <= 6
    chart_title = title or (" / ".join(names[:3]) + (" 等" if len(names) > 3 else "") + " 趋势")
    first_label = str(sampled_labels[0]) if sampled_labels else ""
    last_label = str(sampled_labels[-1]) if sampled_labels else ""
    chart_desc = desc or (
        f"{chart_title}：{len(names)} 条曲线、{shown} 个时间点"
        + (f"，时间范围 {first_label} 至 {last_label}" if first_label or last_label else "")
        + (f"，纵轴单位 {unit}" if unit else "")
        + (f"，已从 {total} 个点降采样" if shown < total else "")
    )
    attrs = (f' data-points="{shown}" data-total-points="{total}" '
             f'data-series="{len(names)}"'
             f' data-downsampled="{"true" if shown < total else "false"}"')
    out = [_svg_open(width, height, chart_title, chart_desc, "chart mchart-svg", attrs)]
    out.append('<g class="c-series">')

    # 面积填充（可选，多线默认关闭以免互相遮挡）
    if area:
        for color_i, (name, vals) in enumerate(clean_series.items()):
            color = _color_at(color_i, colors)
            sampled = [(i, labels[i], vals[i]) for i in idx]
            for seg in _segments(sampled):
                if len(seg) < 2:
                    continue
                xy = [(X(i), Y(v)) for i, v in seg]
                d = (f"M{xy[0][0]:.1f},{base_y:.1f} "
                     + " ".join(f"L{x:.1f},{y:.1f}" for x, y in xy)
                     + f" L{xy[-1][0]:.1f},{base_y:.1f} Z")
                out.append(f'<path class="c-area" d="{d}" style="fill:{color}" '
                           f'fill-opacity="0.10" stroke="none"/>')

    # 网格 + y 轴刻度
    for t in ticks:
        y = Y(t)
        cls = "gl c-grid-line c-zero" if t == 0 else "gl c-grid-line"
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + plot_w}" '
                   f'y2="{y:.1f}" class="{cls}"/>')
        out.append(f'<text x="{pad_l - 6}" y="{y + 3.5:.1f}" text-anchor="end" '
                   f'class="ax c-y">{escape(_fmt_axis(t))}</text>')
    axis_style = 'style="stroke:var(--line,#30363d);stroke-width:1"'
    out.append(f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{base_y}" '
               f'class="c-axis" {axis_style}/>')
    out.append(f'<line x1="{pad_l}" y1="{base_y}" x2="{pad_l + plot_w}" y2="{base_y}" '
               f'class="c-axis" {axis_style}/>')
    if unit:
        out.append(f'<text x="6" y="14" text-anchor="start" class="ax c-unit">'
                   f'{escape(unit)}</text>')
    if x_label:
        out.append(f'<text x="{pad_l + plot_w / 2:.1f}" y="{height - 7}" '
                   f'text-anchor="middle" class="ax c-xlabel">{escape(x_label)}</text>')

    max_labels = max(2, min(8, int(plot_w // 72)))
    tick_idx = _x_tick_indices(shown, max_labels)
    xtick_y = height - 25 if x_label else height - 9
    for k in tick_idx:
        orig_i = idx[k]
        x = X(orig_i)
        anchor = "start" if k == 0 else ("end" if k == shown - 1 else "middle")
        out.append(f'<text x="{x:.1f}" y="{xtick_y}" text-anchor="{anchor}" '
                   f'class="ax c-x">'
                   f'{escape(_short_label(sampled_labels[k], sampled_labels))}</text>')

    # 曲线
    legend: list[str] = []
    for color_i, (name, vals) in enumerate(clean_series.items()):
        color = _color_at(color_i, colors)
        sampled = [(i, labels[i], vals[i]) for i in idx]
        latest = next((v for _, _, v in reversed(sampled) if v is not None), None)
        legend_tip = f"{name}：最新 {_fmt(latest, decimals)}" + (f" {unit}" if unit else "")
        legend.append(f'<span class="lg" title="{escape(legend_tip)}">'
                      f'<i style="background:{color}"></i>{escape(name)}</span>')
        for seg in _segments(sampled):
            xy = [(X(i), Y(v)) for i, v in seg]
            if len(xy) >= 3 and smooth:
                d = _smooth_path(xy)
                out.append(f'<path class="c-line" d="{d}" fill="none" '
                           f'style="stroke:{color};stroke-width:2;stroke-linejoin:round;'
                           f'stroke-linecap:round"><title>{escape(legend_tip)}</title></path>')
            elif len(xy) >= 2:
                poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
                out.append(f'<polyline class="c-line" points="{poly}" fill="none" '
                           f'style="stroke:{color};stroke-width:2;stroke-linejoin:round;'
                           f'stroke-linecap:round"><title>{escape(legend_tip)}</title></polyline>')
            elif len(xy) == 1:
                x, y = xy[0]
                out.append(f'<circle class="c-dot" cx="{x:.1f}" cy="{y:.1f}" r="2.6" '
                           f'style="fill:{color}"/>')

        if latest is not None:
            last_seg = next((s for s in reversed(_segments(sampled)) if s), [])
            if last_seg:
                orig_i, v = last_seg[-1]
                lx, ly = X(orig_i), Y(v)
                out.append(
                    f'<circle class="c-dot c-last-dot" cx="{lx:.1f}" cy="{ly:.1f}" r="3.0" '
                    f'style="fill:{color};stroke:var(--panel,#101018);stroke-width:1.5">'
                    f'<title>{escape(legend_tip)}</title></circle>')
                if annotate:
                    tx = min(max(lx - 7, pad_l + 2), pad_l + plot_w)
                    ty = min(max(ly - 8, pad_t + 10), base_y - 2)
                    out.append(f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="end" '
                               f'class="last c-last" style="fill:{color}">'
                               f'{escape(_fmt(v, decimals))}</text>')

    # 按日期合并的悬停热区：一个日期一个热区，title 汇总所有系列当日数值
    if hover:
        band_w = plot_w / max(1, total - 1)
        band_w = min(48.0, max(10.0, band_w))
        for k in _hover_indices(shown):
            orig_i = idx[k]
            parts = []
            for name, vals in clean_series.items():
                v = vals[orig_i]
                if v is None:
                    continue
                parts.append(f"{name} {_fmt(v, decimals)}" + (f" {unit}" if unit else ""))
            if not parts:
                continue
            tip = f"{labels[orig_i]} · " + " · ".join(parts)
            out.append(_hit_band(X(orig_i), band_w, tip, pad_t, plot_h))
    out.append("</g></svg>")
    return (f'<div class="mchart">{"".join(out)}'
            f'<div class="legend">{"".join(legend)}</div></div>')


# ---------------------------------------------------------------------------
# Sparkline（列表 / 榜单单元格）
# ---------------------------------------------------------------------------

def sparkline(points, width: int = 96, height: int = 28,
              stroke: str = "", *, fill: bool = True,
              max_points: int = DEFAULT_MAX_POINTS, decimals=None,
              title: str = "") -> str:
    """迷你趋势线。``points`` 支持 ``[(label, value), ...]`` 或纯数值列表。

    无坐标轴、无刻度，只保留趋势线 + 末端点；空数据返回行内占位符。
    """
    width, height = max(24, int(width)), max(12, int(height))
    raw = _normalize_points(points)
    if not raw:
        return _empty("暂无趋势", inline=True)
    if not any(v is not None for _, v in raw):
        return _empty("暂无趋势", inline=True)

    total = len(raw)
    idx = _sample_indices(total, max_points, [[v for _, v in raw]])
    sampled = [(i, raw[i][0], raw[i][1]) for i in idx]
    shown = len(sampled)
    values = [v for _, _, v in sampled if v is not None]
    lo, hi = min(values), max(values)
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    pad_x, pad_y = 2.0, 3.0
    plot_w = max(1.0, width - pad_x * 2)
    plot_h = max(1.0, height - pad_y * 2)

    def X(orig_i: int) -> float:
        if total <= 1:
            return width / 2
        return pad_x + plot_w * orig_i / (total - 1)

    def Y(v: float) -> float:
        return pad_y + plot_h - (v - lo) / (hi - lo) * plot_h

    color = _safe_color(stroke, _palette(0))
    first_label = next((l for _, l, _ in sampled if l), "")
    last_v = values[-1]
    chart_title = title or "趋势"
    chart_desc = (f"{chart_title}：{shown} 个点"
                  + (f"，范围 {_fmt(min(values), decimals)} 至 {_fmt(max(values), decimals)}"
                     if shown > 1 else f"，数值 {_fmt(last_v, decimals)}")
                  )
    attrs = (f' data-points="{shown}" data-total-points="{total}"'
             f' data-downsampled="{"true" if shown < total else "false"}"')
    out = [_svg_open(width, height, chart_title, chart_desc, "chart chart-spark", attrs)]
    segs = _segments(sampled)
    if fill:
        for seg in segs:
            if len(seg) < 2:
                continue
            xy = [(X(i), Y(v)) for i, v in seg]
            d = (f"M{xy[0][0]:.1f},{height - pad_y:.1f} "
                 + " ".join(f"L{x:.1f},{y:.1f}" for x, y in xy)
                 + f" L{xy[-1][0]:.1f},{height - pad_y:.1f} Z")
            out.append(f'<path class="c-area" d="{d}" style="fill:{color}" '
                       f'fill-opacity="0.14" stroke="none"/>')
    for seg in segs:
        xy = [(X(i), Y(v)) for i, v in seg]
        if len(xy) >= 2:
            poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
            out.append(f'<polyline class="c-line" points="{poly}" fill="none" '
                       f'style="stroke:{color};stroke-width:1.6;stroke-linejoin:round;'
                       f'stroke-linecap:round"><title>{escape(chart_title)}</title></polyline>')
        elif len(xy) == 1:
            x, y = xy[0]
            out.append(f'<circle class="c-dot" cx="{x:.1f}" cy="{y:.1f}" r="1.8" '
                       f'style="fill:{color}"/>')
    last_seg = next((s for s in reversed(segs) if s), [])
    if last_seg:
        orig_i, v = last_seg[-1]
        lx, ly = X(orig_i), Y(v)
        tip = f"{first_label + ' · ' if first_label else ''}最新 {_fmt(v, decimals)}"
        out.append(f'<circle class="c-dot c-last-dot" cx="{lx:.1f}" cy="{ly:.1f}" r="2.1" '
                   f'style="fill:{color}"><title>{escape(tip)}</title></circle>')
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------------------
# 横向条形图（算力 / 带宽 / 份额对比）
# ---------------------------------------------------------------------------

def _normalize_bars(items) -> tuple[list[tuple[str, float]], int]:
    rows: list[tuple[str, float]] = []
    omitted = 0
    if isinstance(items, dict):
        iterable = list(items.items())
    else:
        iterable = list(items or [])
    for item in iterable:
        if isinstance(item, dict):
            label = item.get("label", item.get("name", ""))
            value = item.get("value")
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            label, value = item[0], item[1]
        else:
            omitted += 1
            continue
        if value is None:
            omitted += 1
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            omitted += 1
            continue
        if not math.isfinite(v):
            omitted += 1
            continue
        rows.append((str(label), v))
    return rows, omitted


def bar_chart(items, width: int = 640, height: int | None = None, *,
              unit: str = "", title: str = "", desc: str = "",
              sort: bool = True, max_bars: int = 12, decimals=None,
              color: str = "", value_format=None) -> str:
    """横向条形图。``items`` 可为 ``{名称: 数值}`` 或 ``[(名称, 数值), ...]``。

    适合算力/带宽对比：左侧名称、右侧数值、条长按最大值归一。
    空数据返回带说明的占位块；超过 ``max_bars`` 时截断并在 ``<desc>`` 注明。
    """
    width = max(240, int(width))
    rows, omitted = _normalize_bars(items)
    if not rows:
        return _empty("暂无可对比的数据", "接入算力或带宽指标后自动生成")
    if sort:
        rows.sort(key=lambda r: r[1], reverse=True)
    max_bars = max(1, int(max_bars))
    truncated = max(0, len(rows) - max_bars)
    if truncated:
        rows = rows[:max_bars]

    label_w = int(min(max(60, max(_text_px(name) for name, _ in rows) + 12),
                      max(60, width * 0.42)))
    value_w = 64
    pad_t, pad_b = 22, 8
    row_h = 26
    plot_w = max(30, width - label_w - value_w - 12)
    fit_h = len(rows) * row_h + pad_t + pad_b
    chart_h = max(60, fit_h, int(height) if height else 0)
    vmax = max(abs(v) for _, v in rows) or 1.0
    bar_color = _safe_color(color, _palette(0))
    chart_title = title or "横向对比"
    chart_desc = desc or (
        f"{chart_title}：{len(rows)} 项"
        + (f"，单位 {unit}" if unit else "")
        + (f"，另有 {truncated} 项未显示" if truncated else "")
        + (f"，{omitted} 项缺少数据" if omitted else "")
    )
    attrs = (f' data-bars="{len(rows)}" data-total-bars="{len(rows) + truncated}"'
             f' data-unit="{escape(unit)}"')
    out = [_svg_open(width, chart_h, chart_title, chart_desc, "chart chart-bar", attrs)]
    if unit:
        out.append(f'<text x="{width - 6}" y="14" text-anchor="end" '
                   f'class="ax c-unit">{escape(unit)}</text>')
    for i, (name, value) in enumerate(rows):
        y = pad_t + i * row_h
        mid = y + row_h / 2
        label = _truncate_label(name, label_w - 12)
        out.append(f'<text x="6" y="{mid + 3.8:.1f}" text-anchor="start" '
                   f'class="ax c-bar-label"><title>{escape(name)}</title>'
                   f'{escape(label)}</text>')
        out.append(f'<rect class="c-bar-track" x="{label_w}" y="{mid - 5:.1f}" '
                   f'width="{plot_w}" height="10" rx="3" '
                   f'style="fill:var(--panel2,#171724)"/>')
        bar_w = max(0.0, plot_w * abs(value) / vmax)
        tip = f"{name}：{_fmt(value, decimals)}" + (f" {unit}" if unit else "")
        if bar_w > 0:
            style = f"fill:{bar_color}"
            if value < 0:
                style = "fill:var(--down,#ff6b81)"
            out.append(f'<rect class="c-bar" x="{label_w}" y="{mid - 5:.1f}" '
                       f'width="{bar_w:.1f}" height="10" rx="3" style="{style}">'
                       f'<title>{escape(tip)}</title></rect>')
        val_txt = (value_format(value) if callable(value_format)
                   else _fmt(value, decimals))
        out.append(f'<text x="{width - 6}" y="{mid + 3.8:.1f}" text-anchor="end" '
                   f'class="ax c-bar-val">{escape(str(val_txt))}</text>')
    out.append("</svg>")
    return "".join(out)
