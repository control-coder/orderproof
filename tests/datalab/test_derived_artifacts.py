"""图表与模板说明只由核验通过的结果确定性派生；不调用模型或容器。"""
import json
import tempfile
import unittest
from pathlib import Path

from datalab.artifacts.charts import MAX_BARS, MAX_CHARTS, chart_specs
from datalab.artifacts.derive import derive
from datalab.artifacts.narrative import describe
from datalab.contracts import AnalysisPlan
from datalab.datasets.service import DatasetStore
from datalab.execution.fixed_analysis import analyze

ROOT = Path(__file__).resolve().parents[2]
PERIODS = {"start": "2026-09-01T00:00:00", "end": "2026-10-01T00:00:00",
           "previous_start": "2026-08-01T00:00:00", "previous_end": "2026-09-01T00:00:00"}
CASES = (("summary", None, {}), ("trend", "payment_date", {}), ("ranking", "channel", {}),
         ("share", "category", {}), ("comparison", None, PERIODS), ("quality", None, {}))


class DerivedArtifactTests(unittest.TestCase):
    def setUp(self):
        scratch = (ROOT / ".tmp").resolve()
        scratch.mkdir(exist_ok=True)
        folder = tempfile.TemporaryDirectory(prefix="derived-", dir=scratch)
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.store = DatasetStore(self.root / "datasets")
        self.version = self.store.import_csv((ROOT / "examples/orders.csv").read_bytes(), project_id="demo",
            dataset_family="orders", currency="CNY", timezone="Asia/Shanghai")
        self.csv = self.store.directory(self.version.dataset_version) / "orders.csv"
        self.range = (self.version.profile["time_min"], self.version.profile["time_max"])

    def plan(self, kind, group, dates):
        return AnalysisPlan(self.version.dataset_version, "rule-1:v2", kind=kind, group_by=group, **dates).payload()

    def run_folder(self, name, result):
        folder = self.root / name
        (folder / "output").mkdir(parents=True)
        (folder / "output/result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return folder

    def test_same_result_gives_identical_files(self):
        for kind, group, dates in CASES:
            with self.subTest(kind=kind):
                plan = self.plan(kind, group, dates)
                result = analyze(self.csv, plan)
                outputs = []
                for name in ("a", "b"):
                    derived = derive(self.run_folder(f"{kind}-{name}", result), plan, metric_version="rule-1:v2",
                                     included_statuses=("paid",), currency="CNY", data_range=self.range)
                    outputs.append({key: (self.root / f"{kind}-{name}" / path).read_bytes()
                                    for key, path in derived["files"].items()})
                self.assertEqual(outputs[0], outputs[1])
                charts = [key for key in outputs[0] if key.endswith(".png")]
                self.assertLessEqual(len(charts), MAX_CHARTS)
                self.assertEqual(kind != "summary", bool(charts))
                for key in charts:
                    self.assertTrue(outputs[0][key].startswith(b"\x89PNG"))
                    self.assertNotIn(b"matplotlib", outputs[0][key])

    def test_narrative_names_metric_version_and_period(self):
        for kind, group, dates in CASES:
            with self.subTest(kind=kind):
                plan = self.plan(kind, group, dates)
                text = describe(analyze(self.csv, plan), plan, metric_version="rule-1:v2",
                                included_statuses=("paid",), currency="CNY", data_range=self.range)
                self.assertTrue(2 <= len(text["sentences"]) <= 3, text)
                self.assertEqual(text["text"], "".join(item["text"] for item in text["sentences"]))
                if kind != "quality":
                    self.assertIn("rule-1:v2", text["text"])
                self.assertTrue(text["period"])

    def test_comparison_numbers_come_from_result(self):
        plan = self.plan("comparison", None, PERIODS)
        text = describe(analyze(self.csv, plan), plan, metric_version="rule-1:v2", included_statuses=("paid",),
                        currency="CNY", data_range=self.range)["text"]
        # 样例：本期 2 笔 200.00，上期 2 笔 150.00，差额 +50.00，环比 +33.33%。
        for fragment in ("2 笔订单、金额 200.00 CNY", "150.00 CNY", "+50.00 CNY", "+33.33%",
                         "2026-09-01 00:00:00 至 2026-10-01 00:00:00（不含终点）"):
            self.assertIn(fragment, text)

    def test_zero_previous_period_skips_growth(self):
        plan = self.plan("comparison", None, {**PERIODS, "previous_start": "2026-07-01T00:00:00",
                                              "previous_end": "2026-08-01T00:00:00"})
        text = describe(analyze(self.csv, plan), plan, metric_version="v", included_statuses=("paid",),
                        currency="CNY", data_range=self.range)["text"]
        self.assertIn("上期金额为 0，不计算环比变化率", text)

    def test_many_groups_fold_into_other_and_labels_are_plain_text(self):
        rows = [{"group": f"$x^{index}$\n渠道", "order_count": 1, "amount_cents": 100 + index} for index in range(40)]
        result = {"kind": "ranking", "total": {"order_count": 40, "amount_cents": sum(r["amount_cents"] for r in rows)},
                  "rows": rows}
        specs = chart_specs(result, {"kind": "ranking", "group_by": "channel"})
        self.assertEqual(len(specs[0]["labels"]), MAX_BARS)
        self.assertTrue(specs[0]["labels"][-1].startswith("其他"))
        self.assertEqual(sum(specs[1]["values"]), 40)
        self.assertTrue(all("\n" not in label for label in specs[0]["labels"]))
        derive(self.run_folder("folded", result), {"kind": "ranking", "group_by": "channel"}, metric_version="v",
               included_statuses=("paid",), currency="CNY", data_range=self.range)

    def test_empty_period_has_no_group_chart(self):
        plan = self.plan("ranking", "channel", {"start": "2020-01-01T00:00:00", "end": "2020-02-01T00:00:00"})
        result = analyze(self.csv, plan)
        self.assertEqual(chart_specs(result, plan), [])
        self.assertIn("没有符合口径的订单", describe(result, plan, metric_version="v", included_statuses=("paid",),
                                             currency="CNY", data_range=self.range)["text"])


if __name__ == "__main__":
    unittest.main()
