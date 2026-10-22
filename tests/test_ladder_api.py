"""硬件天梯规格 API：/api/v1/hardware/ladder.json 与页面同源。

纪律：全部在临时目录建库/导出，绝不触碰生产库与 dist/。
验收点：16 行、FP16 降序缺值最后、固定 schema、null 不用 0/— 占位、
页面（display_board_items）与 API 行序一致、预压缩 .gz 可解压。
"""
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tests  # noqa: E402,F401  单测护栏:离线 + 不调真实模型

# 生产 /rankings/nv_infer 同一批成员（14 张 MLPerf + 2 张手工昇腾）。
NV_PRODUCTS = [
    ("mlperf-amd-instinct-mi355x-288gb-hbm3e",
     "AMD Instinct MI355X 288GB HBM3e", "AMD"),
    ("mlperf-nvidia-b300-sxm-270gb", "NVIDIA B300-SXM-270GB", "NVIDIA"),
    ("mlperf-nvidia-gb200", "NVIDIA GB200", "NVIDIA"),
    ("mlperf-nvidia-gb300", "NVIDIA GB300", "NVIDIA"),
    ("mlperf-amd-instinct-mi350x-288gb-hbm3e",
     "AMD Instinct MI350X 288GB HBM3e", "AMD"),
    ("mlperf-nvidia-b200-sxm-180gb", "NVIDIA B200-SXM-180GB", "NVIDIA"),
    ("mlperf-amd-instinct-mi300x-192gb-hbm3",
     "AMD Instinct MI300X 192GB HBM3", "AMD"),
    ("mlperf-amd-instinct-mi350p", "AMD Instinct MI350P", "AMD"),
    ("mlperf-nvidia-rtx-pro-6000-blackwell-server-edition",
     "NVIDIA RTX PRO 6000 Blackwell Server Edition", "NVIDIA"),
    ("mlperf-nvidia-h200-sxm-141gb", "NVIDIA H200-SXM-141GB", "NVIDIA"),
    ("atlas-300i-a2", "Atlas 300I A2", "华为"),
    ("atlas-300i-pro", "Atlas 300I Pro", "华为"),
    ("mlperf-intel-arc-tm-pro-b70", "Intel Arc(TM) Pro B70", "Intel"),
    ("mlperf-nvidia-rtx-pro-4500-blackwell",
     "NVIDIA RTX PRO 4500 Blackwell", "NVIDIA"),
    ("mlperf-nvidia-vr200", "NVIDIA VR200", "NVIDIA"),
    ("mlperf-nvidia-vera-rubin", "NVIDIA Vera Rubin", "NVIDIA"),
]

EXPECTED_FP16 = [2500, 2500, 2500, 2500, 2300, 2250, 1300, 1150,
                 1000, 989, 280, 70, None, None, None, None]


def seed(db_mod):
    with db_mod.conn() as c:
        c.executescript(db_mod.DDL)
        db_mod.ensure_news_tables(c)
        db_mod.ensure_ai_tables(c)
        for i, (slug, name, vendor) in enumerate(NV_PRODUCTS, 1):
            c.execute(
                "INSERT INTO products(id,slug,name,vendor,category,origin,"
                " status,board_tags,created_at,updated_at) VALUES"
                " (?,?,?,?,'nv','mlperf','active','[\"nv_infer\"]',?,?)",
                (i, slug, name, vendor, "2026-10-01", "2026-10-01"))
        # 非 nv_infer 成员：不得出现在天梯里。
        c.execute(
            "INSERT INTO products(id,slug,name,vendor,category,origin,"
            " status,board_tags,created_at,updated_at) VALUES"
            " (99,'other-product','Other Product','X','coding','registry',"
            " 'active','[\"harness_core\"]',?,?)",
            ("2026-10-01", "2026-10-01"))
        # 有快照才会进入榜单导出流程；天梯端点不依赖 rankings 内容。
        c.execute(
            "INSERT INTO rankings(board,snapshot_date,rank,product_id,"
            " heat_score,coverage,methodology_version)"
            " VALUES('nv_infer','2026-10-21',1,1,90.0,0.9,'v1.8')")


class LadderApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        import collector.db as db
        import collector.export as export
        self.db, self.export = db, export
        self._patches = [
            patch.object(db, "DB_PATH", self.tmp / "t.sqlite3"),
            patch.object(db, "DATA_DIR", self.tmp),
            patch.object(db, "RAW_DIR", self.tmp / "raw"),
            patch.object(export, "DIST_DIR", self.tmp / "dist"),
            patch.object(export, "SNAPSHOT_DIR", self.tmp / "snapshots"),
            patch.object(export, "ensure_dirs", lambda: None),
        ]
        for p in self._patches:
            p.start()
        seed(db)
        self.export.export_all()
        self.path = self.tmp / "dist" / "api" / "v1" / "hardware" / "ladder.json"
        self.payload = json.loads(self.path.read_text(encoding="utf-8"))

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def test_endpoint_exported_with_meta_and_16_rows(self):
        self.assertTrue(self.path.is_file())
        meta, items = self.payload["meta"], self.payload["items"]
        self.assertEqual(meta["total"], 16)
        self.assertEqual(len(items), 16)
        self.assertEqual(meta["specs_updated_at"], "2026-10-22")
        self.assertIn("FP16", meta["criteria"])
        self.assertEqual(meta["source"], "collector/hw_specs.json + MLPerf 成员")
        self.assertTrue(meta["generated_at"].endswith("Z"))

    def test_items_use_fixed_schema(self):
        from collector import queries
        for item in self.payload["items"]:
            self.assertEqual(tuple(item), queries.LADDER_API_FIELDS)
            self.assertTrue(item["slug"])
            self.assertTrue(item["name"])
            self.assertEqual(item["product_url"], f"/product/{item['slug']}")

    def test_sorted_by_fp16_desc_nulls_last(self):
        items = self.payload["items"]
        self.assertEqual([it["fp16_tflops"] for it in items], EXPECTED_FP16)
        self.assertEqual([it["rank"] for it in items], list(range(1, 17)))
        self.assertEqual(items[0]["name"], "AMD Instinct MI355X 288GB HBM3e")
        # 缺值四张卡按名称升序：Intel 在前、Vera Rubin 最后。
        self.assertEqual([it["name"] for it in items[-4:]], [
            "Intel Arc(TM) Pro B70", "NVIDIA RTX PRO 4500 Blackwell",
            "NVIDIA VR200", "NVIDIA Vera Rubin"])

    def test_missing_values_are_null_not_placeholders(self):
        items = self.payload["items"]
        by_name = {it["name"]: it for it in items}
        rtx = by_name["NVIDIA RTX PRO 4500 Blackwell"]
        for key in ("fp16_tflops", "fp8_tflops", "int8_tops", "bf16_tflops",
                    "interconnect_bw_gbs"):
            self.assertIsNone(rtx[key], key)
        self.assertIsNone(rtx["lowp_label"])
        self.assertNotIn("—", json.dumps(items, ensure_ascii=False))

    def test_lowp_label_matches_page_text(self):
        item = self.payload["items"][0]
        self.assertEqual(item["lowp_label"],
                         "FP8 5000 · INT8 5000 TOPS · BF16 2500")

    def test_official_docs_are_http_urls(self):
        items = self.payload["items"]
        self.assertTrue(all(it["official_docs"] for it in items))
        for it in items:
            for doc in it["official_docs"]:
                self.assertTrue(doc["title"])
                self.assertTrue(doc["url"].startswith("http"), doc["url"])

    def test_only_nv_infer_members_included(self):
        slugs = {it["slug"] for it in self.payload["items"]}
        self.assertNotIn("other-product", slugs)
        self.assertIn("atlas-300i-a2", slugs)
        self.assertIn("atlas-300i-pro", slugs)

    def test_page_rows_share_ladder_order(self):
        from collector import queries
        c = self.db.conn()
        try:
            products = queries.all_products(c)
            ladder = queries.hardware_ladder(c)
            display = queries.display_board_items(c, "nv_infer", products, {})
        finally:
            c.close()
        api_slugs = [it["slug"] for it in self.payload["items"]]
        self.assertEqual([it["slug"] for it in ladder["items"]], api_slugs)
        self.assertEqual([row["slug"] for row in display["rows"]], api_slugs)
        self.assertEqual([row["rank"] for row in display["rows"]],
                         list(range(1, 17)))

    def test_precompressed_gz_matches_json(self):
        from collector.compression import compress_site
        compress_site(self.tmp / "dist")
        gz = self.path.with_name(self.path.name + ".gz")
        self.assertTrue(gz.is_file())
        self.assertEqual(gzip.decompress(gz.read_bytes()), self.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
