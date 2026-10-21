"""硬件规格数据层测试：加载/校验/别名归一。

纪律：所有测试都 patch `db.DB_PATH` 到临时目录，绝不触碰生产库；
本模块本身不读写数据库，测试同时守住“数据层不应产生副作用”这一点。
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from collector import db, hw


class HwDataCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        patcher = patch.object(db, "DB_PATH", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.specs = hw.load()

    def errors_after(self, mutate):
        specs = copy.deepcopy(self.specs)
        mutate(specs)
        return hw.validate(specs)

    def assert_has_error(self, errors, needle):
        self.assertTrue(any(needle in e for e in errors),
                        f"未找到包含「{needle}」的错误：{errors}")


class TestLoad(HwDataCase):
    def test_db_path_is_patched_and_untouched(self):
        self.assertEqual(db.DB_PATH, self.db_path)
        hw.by_model("H100 SXM")
        self.assertFalse(self.db_path.exists(), "hw.py 不应创建/写入数据库")

    def test_returns_full_spec_list(self):
        self.assertIsInstance(self.specs, list)
        self.assertGreaterEqual(len(self.specs), 8)
        self.assertTrue(all(isinstance(s, dict) for s in self.specs))

    def test_each_vendor_has_at_least_two_models(self):
        counts = {}
        for spec in self.specs:
            counts[spec["vendor"]] = counts.get(spec["vendor"], 0) + 1
        self.assertEqual({"NVIDIA", "寒武纪", "平头哥", "华为"}, set(counts))
        for vendor, count in counts.items():
            self.assertGreaterEqual(count, 2, f"{vendor} 型号不足两个")

    def test_required_fields_present(self):
        for i, spec in enumerate(self.specs):
            for field in hw.REQUIRED_FIELDS:
                self.assertIn(field, spec, f"specs[{i}] 缺少 {field}")

    def test_every_model_has_official_doc(self):
        for spec in self.specs:
            self.assertTrue(spec["official_docs"], spec["model"])
            for doc in spec["official_docs"]:
                self.assertTrue(doc["title"])
                host = (urlparse(doc["url"]).hostname or "").lower()
                self.assertTrue(
                    any(host == allowed or host.endswith("." + allowed)
                        for allowed in hw.OFFICIAL_DOC_HOSTS),
                    f"{spec['model']} 的链接不是官方域名：{doc['url']}")
                self.assertTrue(doc["url"].startswith("https://"))

    def test_servers_and_clusters_only_have_documented_status(self):
        for spec in self.specs:
            for entry in spec["servers"] + spec["clusters"]:
                self.assertIn(entry["status"], hw.SERVER_STATUSES)

    def test_ppu_scope_is_documented(self):
        raw = json.loads(hw.SPEC_PATH.read_text(encoding="utf-8"))
        note = raw["vendor_note"]["PPU"]
        self.assertIn("平头哥", note)
        self.assertIn("真武", note)
        # 三种含义并存时只按最主流一家收录
        self.assertIn("后摩智能", note)

    def test_file_is_utf8_and_human_maintainable(self):
        raw_bytes = hw.SPEC_PATH.read_bytes()
        text = raw_bytes.decode("utf-8")
        self.assertIn("寒武纪", text)
        self.assertIn("\n  \"specs\"", text)   # 缩进 2，便于人工 diff

    def test_bad_json_raises(self):
        bad = Path(self.tmp.name) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with patch.object(hw, "SPEC_PATH", bad):
            with self.assertRaises(hw.HwSpecError):
                hw.load()

    def test_invalid_table_raises_with_errors(self):
        bad = Path(self.tmp.name) / "invalid.json"
        bad.write_text(json.dumps({"specs": [self.specs[0]]}, ensure_ascii=False),
                       encoding="utf-8")
        with patch.object(hw, "SPEC_PATH", bad):
            with self.assertRaises(hw.HwSpecError) as ctx:
                hw.load()
        self.assertIn("至少", str(ctx.exception))

    def test_missing_file_raises(self):
        with patch.object(hw, "SPEC_PATH", Path(self.tmp.name) / "nope.json"):
            with self.assertRaises(hw.HwSpecError):
                hw.load()

    def test_registry_nv_config_matches_data_layer(self):
        from collector.config import registry
        nv = registry()["directions"]["nv"]
        self.assertEqual(nv["spec_source"], "collector/hw_specs.json")
        labels = hw.field_labels()
        for field in nv["spec_fields"]:
            self.assertIn(field, labels)
        vendors = {spec["vendor"] for spec in self.specs}
        self.assertEqual(set(nv["spec_vendors"]), vendors)


class TestByModel(HwDataCase):
    def test_exact_model(self):
        self.assertEqual(hw.by_model("H100 SXM")["model"], "H100 SXM")
        self.assertEqual(hw.by_model("昇腾910C")["model"], "Ascend 910C")

    def test_mlperf_style_vendor_and_memory_name(self):
        hit = hw.by_model("NVIDIA H100 80GB HBM3")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["model"], "H100 SXM")
        self.assertEqual(hw.by_model("NVIDIA H200 141GB HBM3e")["model"],
                         "H200 SXM")

    def test_case_space_and_suffix_tolerant(self):
        hit = hw.by_model("  nvidia   h100   sxm5  80gb hbm3  ")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["model"], "H100 SXM")

    def test_chinese_aliases(self):
        self.assertEqual(hw.by_model("寒武纪 MLU370-X8 48GB")["model"],
                         "MLU370-X8")
        self.assertEqual(hw.by_model("真武 810E")["model"], "真武810E")
        self.assertEqual(hw.by_model("平头哥 真武V900")["model"], "真武V900")

    def test_h100_and_h200_do_not_cross_match(self):
        self.assertEqual(hw.by_model("H100")["model"], "H100 SXM")
        self.assertEqual(hw.by_model("H200")["model"], "H200 SXM")

    def test_unknown_and_empty_return_none(self):
        self.assertIsNone(hw.by_model("不存在的加速卡 X999"))
        self.assertIsNone(hw.by_model(""))
        self.assertIsNone(hw.by_model(None))

    def test_ambiguous_query_returns_none(self):
        # 同时命中两张卡且得分相同 → 不做猜测
        self.assertIsNone(hw.by_model("H100 H200"))

    def test_returns_spec_dict(self):
        hit = hw.by_model("MLU370-S4")
        self.assertIsInstance(hit, dict)
        self.assertEqual(hit["vendor"], "寒武纪")
        self.assertIn("official_docs", hit)


class TestFieldLabels(HwDataCase):
    def test_every_field_has_chinese_label_and_unit(self):
        labels = hw.field_labels()
        for field in hw.REQUIRED_FIELDS:
            self.assertIn(field, labels)
            self.assertTrue(labels[field]["label"])
            self.assertIn("unit", labels[field])

    def test_units(self):
        labels = hw.field_labels()
        self.assertEqual(labels["fp16_tflops"]["unit"], "TFLOPS")
        self.assertEqual(labels["int8_tops"]["unit"], "TOPS")
        self.assertEqual(labels["hbm_gb"]["unit"], "GB")
        self.assertEqual(labels["hbm_bw_gbs"]["unit"], "GB/s")
        self.assertEqual(labels["tdp_w"]["unit"], "W")
        self.assertEqual(labels["vendor"]["label"], "厂商")

    def test_returns_copy(self):
        labels = hw.field_labels()
        labels["vendor"]["label"] = "被改了"
        self.assertEqual(hw.field_labels()["vendor"]["label"], "厂商")


class TestValidate(HwDataCase):
    def test_valid_table_passes(self):
        self.assertEqual(hw.validate(self.specs), [])

    def test_non_list_input(self):
        self.assertEqual(hw.validate({"specs": []}), ["规格表必须是 JSON 数组（或含 specs 数组的对象）"])

    def test_missing_required_field(self):
        errors = self.errors_after(lambda specs: specs[0].pop("official_docs"))
        self.assert_has_error(errors, "official_docs")

    def test_negative_number(self):
        errors = self.errors_after(lambda specs: specs[0].update(tdp_w=-1))
        self.assert_has_error(errors, "不能为负数")

    def test_non_official_url(self):
        errors = self.errors_after(
            lambda specs: specs[0]["official_docs"][0].update(
                url="https://example.com/h100"))
        self.assert_has_error(errors, "官方域名")

    def test_http_url_rejected(self):
        errors = self.errors_after(
            lambda specs: specs[0]["official_docs"][0].update(
                url="http://www.nvidia.com/en-us/data-center/h100/"))
        self.assert_has_error(errors, "官方域名")

    def test_bad_server_status(self):
        errors = self.errors_after(
            lambda specs: specs[0]["servers"][0].update(status="已发布"))
        self.assert_has_error(errors, "status 必须是")

    def test_duplicate_alias(self):
        errors = self.errors_after(
            lambda specs: specs[1]["aliases"].append("NVIDIA H100 80GB HBM3"))
        self.assert_has_error(errors, "别名")

    def test_duplicate_model(self):
        errors = self.errors_after(lambda specs: specs[1].update(model="H100 SXM"))
        self.assert_has_error(errors, "型号")

    def test_too_few_models(self):
        errors = self.errors_after(lambda specs: specs.__delitem__(slice(0, 3)))
        self.assert_has_error(errors, "至少")

    def test_vendor_needs_two_models(self):
        errors = self.errors_after(
            lambda specs: specs.__delitem__(slice(9, 10)))  # 仅剩 1 个华为型号
        self.assert_has_error(errors, "华为")

    def test_null_number_needs_sources_note(self):
        def mutate(specs):
            for spec in specs:
                if spec["model"] == "真武810E":
                    spec["sources_note"] = ""
        errors = self.errors_after(mutate)
        self.assert_has_error(errors, "sources_note")

    def test_numbers_must_be_numeric(self):
        errors = self.errors_after(lambda specs: specs[0].update(fp16_tflops="989"))
        self.assert_has_error(errors, "必须是数字")


if __name__ == "__main__":
    unittest.main()
