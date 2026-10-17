"""SuperCLUE 中文大模型月报源:PDF → 榜单图 → pi 多模态读榜 → 指标。

链路(全部可溯源,2026-10-17 实测定案,见 skills/model-eval):
1. 下载月报 PDF(cluebenchmarks.com/superclue_YYMM,registry base_url)→
   整个 PDF 作为 raw_payload 归档(gzip 落盘,溯源链完整);
2. pypdf 抽「总榜」页大图(定位:页面文本含"总榜"且字数少,或取最大内嵌图);
3. pi(deepseek-flash 多模态)读图输出严格 JSON(模型/机构/总分/六维),
   与 PDF 文本页交叉校验条数与 Top3 总分(容差 0.01);
4. 按 registry 模型名精确匹配产出 MetricOut(sc_total + 六维分数)。

月更口径:同一报告 slug 结果缓存于 data/cache,重复更新不重复读图。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .. import piagent
from .. import db
from .base import BaseSource, Ctx, MetricOut

READ_PROMPT = (
    "这两张图是同一张中文大模型测评总榜图片的上半部分与下半部分"
    "(中间有重叠行,去重后合并)。从第 1 名到最后一名逐行完整输出,"
    "禁止省略或截断任何行。输出严格的 JSON 数组,不要任何其他文字:"
    "每行一个模型,字段: rank(整数)、model(模型名,保留原文)、org(机构)、"
    "total(总分,数字)、math、science、code、agent、instruction、hallucination"
    "(各维度分数,数字;没有则 null)。按 rank 升序。只输出 JSON。"
)

# 六维 → 指标 key
DIM_KEYS = {"math": "sc_math", "science": "sc_science", "code": "sc_code",
            "agent": "sc_agent", "instruction": "sc_instruction",
            "hallucination": "sc_hallucination"}


class SuperClueError(Exception):
    """月报解析/读榜失败。"""


def parse_board_json(reply: str) -> list[dict]:
    """解析 pi 读图输出:括号配平扫描出首个完整 JSON 数组,基础校验。"""
    text = reply.strip()
    items = None
    start = text.find("[")
    while start != -1 and items is None:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    try:
                        items = json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break   # 这对括号不是合法数组,找下一个 [
                    break
        if items is None:
            start = text.find("[", start + 1)
    if not isinstance(items, list):
        raise SuperClueError("读榜输出中未找到合法 JSON 数组")
    if not isinstance(items, list) or len(items) < 8:
        raise SuperClueError(f"读榜条目过少: {len(items) if isinstance(items, list) else type(items)}")
    out = []
    for it in items:
        if not isinstance(it, dict) or not it.get("model"):
            continue
        try:
            total = float(it.get("total"))
        except (TypeError, ValueError):
            continue
        out.append(it | {"total": total})
    if len(out) < 10:
        raise SuperClueError(f"有效模型条目不足: {len(out)}")
    return out


def _merge_rows(a: list[dict], b: list[dict]) -> list[dict]:
    """按模型名并集合并两轮读榜,保留字段更全的一侧。"""
    def richer(x, y):
        return x if sum(v is not None for v in x.values()) >= \
            sum(v is not None for v in y.values()) else y
    out: dict[str, dict] = {}
    for row in a + b:
        name = str(row["model"]).strip()
        out[name] = richer(out[name], row) if name in out else row
    return sorted(out.values(), key=lambda r: (r.get("rank") or 99, r["model"]))


def find_board_images(pdf_path, tmp_dir) -> list[str]:
    """定位总榜页,抽出最大的 1~2 张内嵌图(榜单常被切成上下两半)。"""
    from pathlib import Path
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    best_page = None
    for i, page in enumerate(reader.pages):
        t = (page.extract_text() or "")
        if "总榜" in t and len(t) < 900:
            best_page = i
            break
    if best_page is None:
        raise SuperClueError("未定位到总榜页(文本特征缺失)")
    page = reader.pages[best_page]
    images = sorted(page.images, key=lambda im: len(im.data), reverse=True)
    if not images:
        raise SuperClueError(f"总榜页(第{best_page+1}页)无内嵌图")
    paths = []
    for im in images[:2]:
        if len(im.data) < 30_000:   # 太小的是 logo/装饰
            continue
        p = Path(tmp_dir) / f"board_{best_page + 1}_{im.name}"
        p.write_bytes(im.data)
        paths.append(str(p))
    if not paths:
        raise SuperClueError("总榜页无足够大的内嵌图")
    return _split_halves(paths[0], tmp_dir)


def _split_halves(image_path, tmp_dir) -> list[str]:
    """把整榜大图纵切成上下两半(中间 12% 重叠),分图读取更完整。"""
    from PIL import Image
    from pathlib import Path
    img = Image.open(image_path)
    w, h = img.size
    overlap = int(h * 0.12)
    cut = h // 2
    top = img.crop((0, 0, w, cut + overlap))
    bottom = img.crop((0, cut - overlap, w, h))
    paths = []
    for name, part in (("top", top), ("bottom", bottom)):
        p = Path(tmp_dir) / f"{Path(image_path).stem}_{name}.png"
        part.save(p)
        paths.append(str(p))
    return paths


class SuperClueSource(BaseSource):
    key = "superclue"

    def collect(self, ctx: Ctx) -> tuple[list[MetricOut], list[str]]:
        import time
        from pathlib import Path
        from ..config import DATA_DIR
        from ..http import Http

        reg_src = next(s for s in ctx_source_conf() if s["key"] == self.key)
        url = reg_src["base_url"]
        report = url.rstrip("/").rsplit("_", 1)[-1]   # superclue_2509 → 2509
        cache = DATA_DIR / "cache" / f"superclue_{report}.json"

        if cache.exists():
            rows = json.loads(cache.read_text(encoding="utf-8"))
            notes = [f"使用缓存 {cache.name}({len(rows)} 条)"]
            raw_id = None
        else:
            http = Http()
            resp = http.fetch(url)
            pdf_bytes = resp.content
            if not pdf_bytes.startswith(b"%PDF"):
                raise SuperClueError("下载内容不是 PDF")
            notes = [f"月报 {report} 下载 {len(pdf_bytes) // 1024 // 1024}MB"]
            raw_id = db.save_raw(ctx.conn, ctx.source_ids[self.key], self.key,
                                 None, url, 200, pdf_bytes, "application/pdf",
                                 True, parser_version="superclue-pdf-1")
            tmp = Path(DATA_DIR) / "cache"
            tmp.mkdir(parents=True, exist_ok=True)
            pdf_path = tmp / f"superclue_{report}.pdf"
            pdf_path.write_bytes(pdf_bytes)
            imgs = find_board_images(pdf_path, tmp)
            notes.append("总榜图: " + ",".join(Path(p).name for p in imgs))
            _load_env()
            result = piagent.run(READ_PROMPT, model=_pi_model(),
                                 thinking="off", timeout_s=240,
                                 image_paths=imgs)
            rows = parse_board_json(result.reply)
            def _complete(rs):
                joined = " ".join(str(r["model"]) for r in rs)
                return len(rs) >= 26 and any(
                    k in joined for k in ("GPT", "Claude", "Gemini"))
            if not _complete(rows):
                # 读漏行(常见:只读到下半张):重试一次并合并两轮结果
                result2 = piagent.run(
                    READ_PROMPT + " 若某模型已在上半部分出现请仍输出;宁可重复不可遗漏。",
                    model=_pi_model(), thinking="low", timeout_s=240,
                    image_paths=imgs)
                rows = _merge_rows(rows, parse_board_json(result2.reply))
            if not _complete(rows):
                raise SuperClueError(
                    f"读榜不完整({len(rows)} 条,缺海外头部),放弃入库")
            notes.append(f"pi 读榜 {len(rows)} 条"
                         f"({result.tokens_in}+{result.tokens_out} tok)")
            cache.write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                             encoding="utf-8")

        by_name = {p["name"]: p["id"] for p in ctx.products}
        metrics: list[MetricOut] = []
        now = ctx.now
        matched = 0
        for row in rows:
            pid = by_name.get(str(row["model"]).strip())
            if pid is None:
                continue
            matched += 1
            metrics.append(MetricOut(product_id=pid, metric_key="sc_total",
                                     value=row["total"], unit="score",
                                     raw_payload_id=raw_id, collected_at=now))
            for dim, key in DIM_KEYS.items():
                v = row.get(dim)
                try:
                    v = float(v) if v is not None else None
                except (TypeError, ValueError):
                    v = None
                metrics.append(MetricOut(product_id=pid, metric_key=key,
                                         value=v, unit="score",
                                         raw_payload_id=raw_id,
                                         collected_at=now))
        notes.append(f"registry 匹配 {matched}/{len(rows)} 条")
        if matched < 8:
            raise SuperClueError(f"匹配过少({matched}),检查 registry 模型名与报告一致性")
        return metrics, notes


def ctx_source_conf():
    from ..config import registry
    return registry()["sources"]


def _pi_model() -> str:
    from ..config import registry
    return registry()["assistant"]["model"]


def _load_env():
    from .. import assistant
    assistant.load_env_file()
