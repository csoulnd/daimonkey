"""热度评分与 Harness 分层榜（v1.7）。

核心规则：
- 分位数归一化（组内，0-1），方向感知（appstore_rank 越小越好）；
- 缺失指标赋中性分位 0.5；coverage < 阈值 -> 观察区不入榜；
- 某指标"全榜缺失"时剔除并重归一化；单产品缺失赋 0.5，不重归一化；
- 双分：heat（存量）与 momentum（动量）；momentum 无可用增长数据时为 None；
- 核心榜（harness_core）由隐藏分类源榜派生，只取 tier=core 的主分类条目，
  再对各分类的综合分做第二层分位数；生态榜（harness_eco）按 tier=eco 独立评分。
"""
from __future__ import annotations

import json
from bisect import bisect_left, bisect_right

from .config import date_days_ago, registry


def weighted_coverage(heat_cfg: dict, pm: dict, active: dict | None = None) -> float:
    """按权重计算产品指标覆盖率（0-1），观察区展示复用。"""
    cfg = active if active is not None else heat_cfg
    total = sum(cfg.values()) or 1.0
    got = sum(w for k, w in cfg.items() if pm.get(k, {}).get("value") is not None)
    return round(got / total, 4)


def board_members(products: list[dict], board_key: str) -> list[dict]:
    """榜单成员：board_tags 命中，或 registry 的 select 条件命中（如 tier=eco）。"""
    cfg = registry()["boards"].get(board_key, {})
    select = cfg.get("select") or {}
    if select:
        return [p for p in products
                if all(p.get(k) == v for k, v in select.items())]
    return [p for p in products if board_key in p.get("boards", [])]


def percentile_ranks(values: list[float | None], higher_better: bool = True) -> list[float | None]:
    """并列取平均位次的分位数；None 保留为 None。单个有效值时为 0.5。"""
    valid = sorted(v for v in values if v is not None)
    n = len(valid)
    out: list[float | None] = []
    for v in values:
        if v is None:
            out.append(None)
            continue
        if n == 1:
            out.append(0.5)
            continue
        lo, hi = bisect_left(valid, v), bisect_right(valid, v)
        mid = (lo + hi - 1) / 2          # 并列平均
        p = mid / (n - 1)
        out.append(p if higher_better else 1.0 - p)
    return out


def _weighted_score(values: list[float | None], weights: list[float]) -> float | None:
    s, n = 0.0, 0
    for w, v in zip(weights, values):
        if v is not None:
            s += w * v
            n += 1
    return 100.0 * s if n else None


def load_latest_metrics(c, snapshot_date: str) -> dict[int, dict]:
    """{product_id: {metric_key: {value, unit, raw_payload_id, collected_at, source}}}"""
    rows = c.execute(
        "SELECT m.product_id, m.metric_key, m.value, m.unit, m.raw_payload_id,"
        " m.collected_at, s.key AS source_key, s.name AS source_name"
        " FROM metrics m JOIN sources s ON s.id = m.source_id"
        " WHERE substr(m.collected_at, 1, 10) <= ?"
        " ORDER BY m.collected_at", (snapshot_date,)).fetchall()
    latest: dict[int, dict] = {}
    for r in rows:
        latest.setdefault(r["product_id"], {})[r["metric_key"]] = dict(r)
    return latest


def load_history(c, base_key: str, cutoff_date: str) -> dict[int, tuple[str, float]]:
    """历史增长用：截至 cutoff 日期前的最近一个值 {product_id: (date, value)}"""
    rows = c.execute(
        "SELECT product_id, substr(collected_at,1,10) AS d, value FROM metrics"
        " WHERE metric_key=? AND substr(collected_at,1,10) <= ?"
        " ORDER BY collected_at", (base_key, cutoff_date)).fetchall()
    out: dict[int, tuple[str, float]] = {}
    for r in rows:
        out[r["product_id"]] = (r["d"], r["value"])
    return out


def compute_board(board_key: str, cfg: dict, products: list[dict],
                  latest: dict[int, dict], hist_base: dict[str, dict],
                  snapshot_date: str, threshold: float) -> dict:
    """计算单个榜单。返回 {items: [...], observation: [...], dropped_metrics: [...]}。"""
    heat_cfg: dict[str, float] = cfg.get("heat", {})
    mom_cfg: dict[str, float] = cfg.get("momentum", {})
    mdefs = registry()["metric_definitions"]
    growth_bases = registry().get("derived_growth", {})

    # 收集各指标列（heat）
    cols: dict[str, list[float | None]] = {k: [] for k in heat_cfg}
    pids: list[int] = []
    for p in products:
        pids.append(p["id"])
        pm = latest.get(p["id"], {})
        for k in heat_cfg:
            cols[k].append(pm.get(k, {}).get("value"))

    # 整榜缺失指标剔除；分位数只在真实观测值间计算，缺失直接赋中性 0.5。
    active_heat = {k: w for k, w in heat_cfg.items() if any(v is not None for v in cols[k])}
    dropped = [k for k in heat_cfg if k not in active_heat]
    wsum = sum(active_heat.values()) or 1.0
    norms_by_metric: dict[str, list[float | None]] = {}
    for k in active_heat:
        higher = mdefs.get(k, {}).get("direction", "higher") == "higher"
        norms_by_metric[k] = [0.5 if q is None else q
                              for q in percentile_ranks(cols[k], higher)]

    heat_scores: list[float | None] = []
    coverages: list[float] = []
    for i, p in enumerate(products):
        pm = latest.get(p["id"], {})
        norms = [norms_by_metric[k][i] for k in active_heat]
        ws = [w / wsum for w in active_heat.values()]
        heat_scores.append(_weighted_score(norms, ws))
        coverages.append(weighted_coverage(heat_cfg, pm, active_heat))

    # 动量：直接指标 + 推导增长率（近30天 vs 30天前）
    mom_vals: dict[str, list[float | None]] = {k: [] for k in mom_cfg}
    mom_absolute: dict[str, list[float | None]] = {k: [] for k in mom_cfg}
    for p in products:
        pm = latest.get(p["id"], {})
        cutoff = date_days_ago(30)
        for k in mom_cfg:
            if k in growth_bases:
                base_key = growth_bases[k]
                cur = pm.get(base_key, {}).get("value")
                hist = hist_base.get(base_key, {}).get(p["id"])
                if cur is not None and hist:
                    hd, hv = hist
                    if hd <= cutoff:
                        delta = cur - hv
                        floor = 100 if base_key in ("github_stars", "appstore_rating_count") else 1000
                        mom_vals[k].append(delta / (hv + floor) * 100)
                        mom_absolute[k].append(delta)
                    else:
                        mom_vals[k].append(None)
                        mom_absolute[k].append(None)
                else:
                    mom_vals[k].append(None)
                    mom_absolute[k].append(None)
            elif k == "npm_downloads_growth_30d":
                pct = pm.get(k, {}).get("value")
                cur = pm.get("npm_monthly_downloads", {}).get("value")
                if pct is not None and cur is not None and pct > -100:
                    prev = cur / (1 + pct / 100)
                    delta = cur - prev
                    mom_vals[k].append(delta / (prev + 1000) * 100)
                    mom_absolute[k].append(delta)
                else:
                    mom_vals[k].append(pct)
                    mom_absolute[k].append(None)
            else:
                mom_vals[k].append(pm.get(k, {}).get("value"))
                mom_absolute[k].append(None)
    active_mom = {k: w for k, w in mom_cfg.items()
                  if any(v is not None for v in mom_vals[k])}
    dropped += [k for k in mom_cfg if k not in active_mom]
    msum = sum(active_mom.values()) or 1.0
    momentum_scores: list[float | None] = []
    mom_norms: dict[str, list[float]] = {}
    for k in active_mom:
        higher = mdefs.get(k, {}).get("direction", "higher") == "higher"
        relative = percentile_ranks(mom_vals[k], higher)
        if k.endswith("_growth_30d"):
            absolute = percentile_ranks(mom_absolute[k], higher)
            mom_norms[k] = [0.5 if r is None else (r + (0.5 if a is None else a)) / 2
                            for r, a in zip(relative, absolute)]
        else:
            mom_norms[k] = [0.5 if q is None else q for q in relative]
    for i in range(len(products)):
        if not active_mom:
            momentum_scores.append(None)
            continue
        available_w = sum(w for k, w in active_mom.items()
                          if mom_vals[k][i] is not None)
        if available_w / msum < threshold:
            momentum_scores.append(None)
        else:
            norms = [mom_norms[k][i] for k in active_mom]
            ws = [w / msum for w in active_mom.values()]
            momentum_scores.append(_weighted_score(norms, ws))

    items = []
    for i, p in enumerate(products):
        items.append({
            "product": p,
            "heat_score": heat_scores[i],
            "momentum_score": momentum_scores[i],
            "coverage": coverages[i],
        })
    ranked_set = set()
    ranked = []
    for it in items:
        if it["heat_score"] is not None and it["coverage"] >= threshold:
            ranked.append(it)
            ranked_set.add(id(it))
    ranked.sort(key=lambda x: -x["heat_score"])
    observation = sorted((it for it in items if id(it) not in ranked_set),
                         key=lambda x: -(x["coverage"] or 0))
    return {"items": items, "ranked": ranked, "observation": observation,
            "dropped": dropped,
            "weights": {"heat": {k: w / wsum for k, w in active_heat.items()},
                        "momentum": {k: w / msum for k, w in active_mom.items()}}}


def _rank_delta(c, board: str, snapshot_date: str, current: dict[int, int],
                days: int) -> dict[int, int | None]:
    """与 days 天前（或更早的最近一份）快照比，名次变化；正数=上升。"""
    cutoff = date_days_ago(days)
    row = c.execute(
        "SELECT snapshot_date FROM rankings WHERE board=? AND snapshot_date<=?"
        " AND snapshot_date<>? ORDER BY snapshot_date DESC LIMIT 1",
        (board, cutoff, snapshot_date)).fetchone()
    if not row:
        return {pid: None for pid in current}
    prev = {r["product_id"]: r["rank"] for r in c.execute(
        "SELECT product_id, rank FROM rankings WHERE board=? AND snapshot_date=?",
        (board, row["snapshot_date"]))}
    return {pid: (prev[pid] - rk if pid in prev else None)
            for pid, rk in current.items()}


def compute_all(c, products: list[dict], snapshot_date: str) -> dict:
    """执行全部榜单评分并写入 rankings 表；返回汇总信息。"""
    reg = registry()
    boards = reg["boards"]
    threshold = reg.get("methodology", {}).get("observation_threshold", 0.5)
    version = reg.get("methodology", {}).get("version", "v1.0")
    latest = load_latest_metrics(c, snapshot_date)
    hist_base = {base: load_history(c, base, date_days_ago(30))
                 for base in reg.get("derived_growth", {}).values()}

    enabled = {k: v for k, v in boards.items()
               if v.get("enabled", True) and not v.get("derived")}
    board_results: dict[str, dict] = {}
    summary: dict[str, dict] = {}
    # 同一天重算时，已不合格或已移除的条目不可留在旧快照中。
    c.execute("DELETE FROM rankings WHERE snapshot_date=?", (snapshot_date,))

    for bkey, bcfg in enabled.items():
        members = board_members(products, bkey)
        if not members:
            continue
        res = compute_board(bkey, bcfg, members, latest, hist_base,
                            snapshot_date, threshold)
        board_results[bkey] = res
        current: dict[int, int] = {}
        for rank, it in enumerate(res["ranked"], 1):
            current[it["product"]["id"]] = rank
        d7 = _rank_delta(c, bkey, snapshot_date, current, 7)
        d30 = _rank_delta(c, bkey, snapshot_date, current, 30)
        for rank, it in enumerate(res["ranked"], 1):
            pid = it["product"]["id"]
            c.execute(
                "INSERT INTO rankings(board, snapshot_date, rank, product_id,"
                " heat_score, momentum_score, delta_7d, delta_30d, coverage,"
                " methodology_version) VALUES(?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(board, snapshot_date, product_id) DO UPDATE SET"
                " rank=excluded.rank, heat_score=excluded.heat_score,"
                " momentum_score=excluded.momentum_score, delta_7d=excluded.delta_7d,"
                " delta_30d=excluded.delta_30d, coverage=excluded.coverage",
                (bkey, snapshot_date, rank, pid, it["heat_score"],
                 it["momentum_score"], d7.get(pid), d30.get(pid),
                 it["coverage"], version))
        summary[bkey] = {"ranked": len(res["ranked"]),
                         "observation": len(res["observation"]),
                         "dropped": res["dropped"]}

    # ---- 派生榜（harness_core）：源分类榜按 tier 过滤后做第二层分位数 ----
    # 只取产品主分类对应的源榜，避免 board_tags 多标签导致重复入选。
    for dkey, dcfg in boards.items():
        if not dcfg.get("derived") or not dcfg.get("enabled", True):
            continue
        tier = dcfg.get("tier")
        best: dict[int, dict] = {}
        for bkey in dcfg.get("derived_from") or []:
            res = board_results.get(bkey)
            if not res:
                continue
            for it in res["ranked"]:
                prod = it["product"]
                if prod.get("category") != bkey:
                    continue
                if tier and prod.get("tier") != tier:
                    continue
                u = dict(it)
                u["board"] = bkey
                best[prod["id"]] = u
        for bkey in {it["board"] for it in best.values()}:
            members = [it for it in best.values() if it["board"] == bkey]
            heat_q = percentile_ranks([it["heat_score"] for it in members])
            mom_q = percentile_ranks([it["momentum_score"] for it in members])
            for it, hq, mq in zip(members, heat_q, mom_q):
                it["category_heat_score"] = it["heat_score"]
                it["heat_score"] = 100 * hq
                it["momentum_score"] = 100 * mq if mq is not None else None
        derived_items = sorted(
            best.values(), key=lambda x: (-x["heat_score"],
                                          -x["category_heat_score"],
                                          x["product"]["slug"]))
        current = {it["product"]["id"]: i
                   for i, it in enumerate(derived_items, 1)}
        d7 = _rank_delta(c, dkey, snapshot_date, current, 7)
        d30 = _rank_delta(c, dkey, snapshot_date, current, 30)
        for rank, it in enumerate(derived_items, 1):
            pid = it["product"]["id"]
            c.execute(
                "INSERT INTO rankings(board, snapshot_date, rank, product_id,"
                " heat_score, momentum_score, delta_7d, delta_30d, coverage,"
                " methodology_version) VALUES(?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(board, snapshot_date, product_id) DO UPDATE SET"
                " rank=excluded.rank, heat_score=excluded.heat_score,"
                " momentum_score=excluded.momentum_score, delta_7d=excluded.delta_7d,"
                " delta_30d=excluded.delta_30d, coverage=excluded.coverage",
                (dkey, snapshot_date, rank, pid, it["heat_score"],
                 it["momentum_score"], d7.get(pid), d30.get(pid),
                 it["coverage"], version))
        summary[dkey] = {"ranked": len(derived_items),
                         "derived_from": list(dcfg.get("derived_from") or [])}
    return summary
