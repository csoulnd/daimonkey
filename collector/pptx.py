"""PPTX → 网页幻灯片：构建时解析演示文稿，按内容重建为可翻页的网页版。

定位：演示文稿原件（如早前的汇报 PPT）直接"展示出来"——网页版按**文字与表格
内容**重建（标题、要点列表含层级、表格、图形占位），版式、配色与图形以原件
为准（页面上提供原件下载）。纯标准库解析（zipfile + ElementTree），无新依赖。

输出结构（每页）：
    {"index": 1, "title": "…", "subtitle": "…",
     "blocks": [{"type": "bullets", "items": [{"lvl": 0, "text": "…"}]},
                {"type": "table", "rows": [["…", "…"], …]},
                {"type": "media"}],          # 图片/图表：正文无法重建，占位提示
     "has_media": bool}
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "p": "http://schemas.openxmlformats.org/presentationml/2006/main"}


def _para_text(p) -> str:
    return "".join(t.text or "" for t in p.findall(".//a:t", NS)).strip()


def _para_level(p) -> int:
    ppr = p.find("a:pPr", NS)
    try:
        return max(0, min(3, int(ppr.get("lvl", 0)))) if ppr is not None else 0
    except (TypeError, ValueError):
        return 0


def _ph_type(sp) -> str:
    ph = sp.find(".//p:ph", NS)
    return ph.get("type", "") if ph is not None else ""


def _shape_paragraphs(sp) -> list:
    body = sp.find("p:txBody", NS)
    return body.findall("a:p", NS) if body is not None else []


def _table_rows(gf) -> list[list[str]]:
    tbl = gf.find(".//a:tbl", NS)
    if tbl is None:
        return []
    rows = []
    for tr in tbl.findall("a:tr", NS):
        cells = [" ".join(t for t in (_para_text(p) for p in tc.findall(".//a:p", NS)) if t)
                 for tc in tr.findall("a:tc", NS)]
        if any(cells):
            rows.append(cells)
    return rows


def _walk(container, out: dict, media: list) -> None:
    """按文档顺序遍历 spTree：标题/副标题/正文段落/表格/图形占位。"""
    for node in list(container):
        tag = node.tag.split("}")[-1]
        if tag == "grpSp":
            _walk(node, out, media)
        elif tag == "sp":
            paras = _shape_paragraphs(node)
            lines = [(p, _para_text(p)) for p in paras]
            lines = [(p, t) for p, t in lines if t]
            if not lines:
                continue
            kind = _ph_type(node)
            if kind in ("title", "ctrTitle") and not out["title"]:
                out["title"] = lines[0][1]
                lines = lines[1:]
            elif kind == "subTitle" and not out["subtitle"]:
                out["subtitle"] = " ".join(t for _, t in lines)
                lines = []
            if lines:
                items = [{"lvl": _para_level(p), "text": t} for p, t in lines]
                if out["blocks"] and out["blocks"][-1]["type"] == "bullets":
                    out["blocks"][-1]["items"].extend(items)
                else:
                    out["blocks"].append({"type": "bullets", "items": items})
        elif tag == "graphicFrame":
            rows = _table_rows(node)
            if rows:
                out["blocks"].append({"type": "table", "rows": rows})
            else:
                media.append(node)   # 图表等未支持的图形
        elif tag in ("pic", "chart"):
            media.append(node)


BRAND_RE = re.compile(r"^\d{1,2}\s*AgentCore\s*产品调研$")   # 页眉/页脚品牌行


def _postprocess(out: dict) -> None:
    """标题启发式与品牌行清理。

    许多 PPT 的标题并非占位符而是普通文本框（本批素材即是）：无占位标题时，
    取首个正文块的第一行为标题；同时剔除"NN AgentCore 产品调研"页眉行。
    仅做保守处理：只影响标题为空的情形，其余内容原样保留。
    """
    blocks = out["blocks"]
    for b in blocks:
        if b["type"] == "bullets":
            b["items"] = [it for it in b["items"] if not BRAND_RE.match(it["text"])]
    if not out["title"] and blocks and blocks[0]["type"] == "bullets" \
            and blocks[0]["items"]:
        out["title"] = blocks[0]["items"][0]["text"]
        blocks[0]["items"] = blocks[0]["items"][1:]
    out["blocks"] = [b for b in blocks
                     if b["type"] != "bullets" or b["items"]]


def extract_slides(path: Path) -> list[dict]:
    """解析 pptx 为幻灯片列表（按页序）。解析失败抛出，由调用方降级。"""
    slides: list[dict] = []
    with zipfile.ZipFile(path) as z:
        names = sorted((n for n in z.namelist()
                        if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                       key=lambda n: int(re.search(r"(\d+)", n).group(1)))
        for idx, name in enumerate(names, start=1):
            sp_tree = ET.fromstring(z.read(name)).find(".//p:cSld/p:spTree", NS)
            out = {"index": idx, "title": "", "subtitle": "", "blocks": []}
            media: list = []
            if sp_tree is not None:
                _walk(sp_tree, out, media)
            if media:
                out["blocks"].append({"type": "media"})
            out["has_media"] = bool(media)
            _postprocess(out)
            if out["title"] or out["blocks"]:
                slides.append(out)
    return slides
