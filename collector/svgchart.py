"""构建期 SVG 折线图（规范 9.3：图表在构建期渲染，不在浏览器跑图表库）。"""
from __future__ import annotations

from html import escape

PALETTE = ["#58a6ff", "#f87171", "#4ade80", "#fbbf24", "#c39bff", "#22d3ee"]


def multi_line_chart(labels: list[str], series: dict[str, list],
                     width: int = 640, height: int = 220) -> str:
    """多产品趋势对比。series: {名称: 与 labels 等长的数值列表（None=缺口）}。"""
    valid_labels = [l[5:] if len(l) > 5 else l for l in labels]
    if len(labels) < 2:
        return ('<div class="chart-empty">历史快照不足 2 份，趋势图将在多日更新后自动出现'
                f'（当前 {len(labels)} 份）</div>')
    all_vals = [v for pts in series.values() for v in pts if v is not None]
    if not all_vals:
        return '<div class="chart-empty">暂无可绘制的趋势数据</div>'
    pad_l, pad_r, pad_t, pad_b = 42, 14, 14, 26
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    lo, hi = min(all_vals), max(all_vals)
    if lo == hi:
        lo, hi = lo - 1, hi + 1

    def X(i: float) -> float:
        return pad_l + w * i / (len(labels) - 1)

    def Y(v: float) -> float:
        return pad_t + h - (v - lo) / (hi - lo) * h

    grid = "".join(
        f'<line x1="{pad_l}" y1="{Y(v):.1f}" x2="{pad_l + w}" y2="{Y(v):.1f}" class="gl"/>'
        f'<text x="{pad_l - 6}" y="{Y(v) + 4:.1f}" text-anchor="end" class="ax">{_fmt(v)}</text>'
        for v in (hi, (hi + lo) / 2, lo))
    label_idx = sorted({0, len(labels) // 2, len(labels) - 1})
    xlabels = "".join(
        f'<text x="{X(i):.0f}" y="{height - 8}" class="ax">{escape(valid_labels[i])}</text>'
        for i in label_idx)
    lines, legend = [], []
    for k, (name, pts) in enumerate(series.items()):
        color = PALETTE[k % len(PALETTE)]
        xy = [(X(i), Y(v)) for i, v in enumerate(pts) if v is not None]
        if len(xy) >= 2:
            poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
            lines.append(f'<polyline points="{poly}" fill="none" stroke="{color}" '
                         f'stroke-width="2" stroke-linejoin="round"/>')
        lines.append(f'<circle cx="{xy[-1][0]:.1f}" cy="{xy[-1][1]:.1f}" r="2.5" '
                     f'fill="{color}"/>' if xy else '')
        legend.append(f'<span class="lg"><i style="background:{color}"></i>{escape(name)}</span>')
    return (f'<div class="mchart"><svg viewBox="0 0 {width} {height}" class="chart" role="img">'
            f'{grid}{"".join(lines)}{xlabels}</svg>'
            f'<div class="legend">{"".join(legend)}</div></div>')


def line_chart(points: list[tuple[str, float]], width: int = 640, height: int = 200,
               stroke: str = "var(--acc, #58a6ff)") -> str:
    """points: [(label, value), ...] 按时间升序。返回内联 SVG 字符串。"""
    if not points:
        return ('<div class="chart-empty">暂无数据点</div>')
    if len(points) == 1:
        points = [points[0], points[0]]
    pad_l, pad_r, pad_t, pad_b = 46, 14, 14, 26
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    vals = [v for _, v in points]
    lo, hi = min(vals), max(vals)
    if lo == hi:
        lo, hi = lo - 1, hi + 1

    def X(i: int) -> float:
        return pad_l + (w * i / (len(points) - 1))

    def Y(v: float) -> float:
        return pad_t + h - (v - lo) / (hi - lo) * h

    poly = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, (_, v) in enumerate(points))
    area = f"{pad_l},{pad_t + h} " + poly + f" {pad_l + w},{pad_t + h}"
    last_x, last_y = X(len(points) - 1), Y(vals[-1])
    label_idx = sorted({0, len(points) // 2, len(points) - 1})
    xlabels = "".join(
        f'<text x="{X(i):.0f}" y="{height - 8}" class="ax">{escape(points[i][0][5:])}</text>'
        for i in label_idx)
    grid = "".join(
        f'<line x1="{pad_l}" y1="{Y(v):.1f}" x2="{pad_l + w}" y2="{Y(v):.1f}" class="gl"/>'
        f'<text x="{pad_l - 6}" y="{Y(v) + 4:.1f}" text-anchor="end" class="ax">'
        f'{_fmt(v)}</text>'
        for v in (hi, (hi + lo) / 2, lo))
    return (
        f'<svg viewBox="0 0 {width} {height}" class="chart" role="img">'
        f'<polygon points="{area}" fill="{stroke}" opacity="0.08"/>'
        f'{grid}'
        f'<polyline points="{poly}" fill="none" stroke="{stroke}" '
        f'stroke-width="2" stroke-linejoin="round"/>'
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3.5" fill="{stroke}"/>'
        f'<text x="{min(last_x, width - 60):.0f}" y="{max(last_y - 8, 12):.0f}" '
        f'class="last">{_fmt(vals[-1])}</text>'
        f'{xlabels}</svg>')


def _fmt(v: float) -> str:
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"{v / 1_000:.0f}k"
    if v == int(v):
        return str(int(v))
    return f"{v:.1f}"
