"""PPTX → 网页幻灯片解析：标题启发式、要点层级、表格、图形占位、品牌行剔除。"""
import sys
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector import pptx

SLIDE = textwrap.dedent('''\
<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree>
    <p:sp><p:txBody>
      <a:p><a:r><a:t>第一页标题</a:t></a:r></a:p>
      <a:p><a:r><a:t>01 AgentCore 产品调研</a:t></a:r></a:p>
      <a:p><a:pPr lvl="0"/><a:r><a:t>要点一</a:t></a:r></a:p>
      <a:p><a:pPr lvl="1"/><a:r><a:t>子要点</a:t></a:r></a:p>
    </p:txBody></p:sp>
    <p:graphicFrame><a:graphic><a:graphicData><a:tbl>
      <a:tr><a:tc><a:txBody><a:p><a:r><a:t>列A</a:t></a:r></a:p></a:txBody></a:tc><a:tc><a:txBody><a:p><a:r><a:t>列B</a:t></a:r></a:p></a:txBody></a:tc></a:tr>
      <a:tr><a:tc><a:txBody><a:p><a:r><a:t>甲</a:t></a:r></a:p></a:txBody></a:tc><a:tc><a:txBody><a:p><a:r><a:t>乙</a:t></a:r></a:p></a:txBody></a:tc></a:tr>
    </a:tbl></a:graphicData></a:graphic></p:graphicFrame>
    <p:pic/>
  </p:spTree></p:cSld>
</p:sld>''')

SLIDE_TITLE_PH = textwrap.dedent('''\
<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree>
    <p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
      <p:txBody><a:p><a:r><a:t>占位符标题</a:t></a:r></a:p></p:txBody></p:sp>
    <p:sp><p:txBody><a:p><a:r><a:t>正文段</a:t></a:r></a:p></p:txBody></p:sp>
  </p:spTree></p:cSld>
</p:sld>''')


class PptxExtractTests(unittest.TestCase):
    def make_pptx(self, slides: list[str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "deck.pptx"
        with zipfile.ZipFile(path, "w") as z:
            for i, xml in enumerate(slides, start=1):
                z.writestr(f"ppt/slides/slide{i}.xml", xml)
        return path

    def test_extract_heuristics(self):
        slides = pptx.extract_slides(self.make_pptx([SLIDE, SLIDE_TITLE_PH]))
        self.assertEqual(len(slides), 2)
        s1 = slides[0]
        # 标题启发式：无占位符时取首块首行
        self.assertEqual(s1["title"], "第一页标题")
        # 品牌行剔除（"01 AgentCore 产品调研"）
        texts = [it["text"] for b in s1["blocks"] if b["type"] == "bullets"
                 for it in b["items"]]
        self.assertNotIn("01 AgentCore 产品调研", texts)
        # 要点与层级
        items = [it for b in s1["blocks"] if b["type"] == "bullets" for it in b["items"]]
        self.assertEqual([(it["lvl"], it["text"]) for it in items],
                         [(0, "要点一"), (1, "子要点")])
        # 表格
        tables = [b for b in s1["blocks"] if b["type"] == "table"]
        self.assertEqual(tables[0]["rows"], [["列A", "列B"], ["甲", "乙"]])
        # 图形占位
        self.assertTrue(s1["has_media"])
        self.assertEqual(s1["blocks"][-1]["type"], "media")
        # 占位符标题优先，且不进入正文
        s2 = slides[1]
        self.assertEqual(s2["title"], "占位符标题")
        self.assertFalse(s2["has_media"])


if __name__ == "__main__":
    unittest.main()
