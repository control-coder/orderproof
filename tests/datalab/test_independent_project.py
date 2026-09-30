"""独立工程迁移的最小回归；不作为容器隔离验收。"""
from __future__ import annotations

import json
import tempfile
import tomllib
import unittest
from pathlib import Path

import datalab
from datalab.contracts import AnalysisPlan
from datalab.datasets.service import DatasetStore
from datalab.execution.codegen import fixed_code
from datalab.execution.fixed_analysis import analyze
from datalab.verification.reference import verify

ROOT = Path(__file__).resolve().parents[2]


class IndependentProjectTests(unittest.TestCase):
    """迁移后入口、导入和固定脚本保持可用。"""

    def setUp(self):
        scratch = (ROOT / ".tmp").resolve()
        if not scratch.is_relative_to(ROOT):
            raise RuntimeError("临时目录越出项目")
        scratch.mkdir(exist_ok=True)
        self.sandbox = tempfile.TemporaryDirectory(prefix="migration-", dir=scratch)
        if not Path(self.sandbox.name).resolve().is_relative_to(scratch):
            raise RuntimeError("测试目录越出临时工作区")
        self.addCleanup(self.sandbox.cleanup)
        self.store = DatasetStore(Path(self.sandbox.name) / "datasets")
        self.version = self.store.import_csv(
            (ROOT / "examples/orders.csv").read_bytes(),
            project_id="demo", dataset_family="standard-orders", currency="CNY", timezone="Asia/Shanghai",
        )
        self.csv_path = self.store.directory(self.version.dataset_version) / "orders.csv"

    def test_package_is_owned_by_src_layout(self):
        self.assertEqual(Path(datalab.__file__).resolve().parent, ROOT / "src/datalab")
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(config["project"]["name"], "datalab")
        self.assertEqual(config["tool"]["setuptools"]["packages"]["find"]["where"], ["src"])

    def test_csv_version_and_project_boundary(self):
        self.assertEqual(self.version.row_count, 6)
        self.assertEqual(self.store.get(self.version.dataset_version, "demo"), self.version)
        with self.assertRaises(PermissionError):
            self.store.get(self.version.dataset_version, "another-project")

    def test_fixed_analysis_matches_independent_reference(self):
        for kind, group in (("summary", None), ("trend", "payment_date"), ("ranking", "channel"),
                            ("share", "category"), ("comparison", None), ("quality", None)):
            with self.subTest(kind=kind):
                dates = {} if kind != "comparison" else {
                    "start": "2026-09-01T00:00:00", "end": "2026-10-01T00:00:00",
                    "previous_start": "2026-08-01T00:00:00", "previous_end": "2026-09-01T00:00:00",
                }
                plan = AnalysisPlan(self.version.dataset_version, "trusted-demo-v1", kind=kind, group_by=group, **dates)
                result = analyze(self.csv_path, plan.payload())
                self.assertTrue(verify(self.csv_path, plan, result).passed)
                if kind == "summary":
                    self.assertEqual(result["total"], {"order_count": 4, "amount_cents": 35000})
                if kind == "comparison":
                    self.assertEqual(result["comparison"]["growth"], "0.333333")

    def test_generated_script_is_standalone_python(self):
        plan = AnalysisPlan(self.version.dataset_version, "trusted-demo-v1")
        source = fixed_code(plan)
        compile(source, "analysis.py", "exec")
        self.assertIn('/input/orders.csv', source)
        self.assertNotIn("from datalab", source)

    def test_wrong_result_cannot_pass_reference(self):
        plan = AnalysisPlan(self.version.dataset_version, "trusted-demo-v1")
        result = analyze(self.csv_path, plan.payload())
        result["total"]["amount_cents"] += 1
        self.assertFalse(verify(self.csv_path, plan, result).passed)

    def test_empty_previous_period_has_no_growth(self):
        plan = AnalysisPlan(
            self.version.dataset_version, "trusted-demo-v1", kind="comparison",
            start="2026-09-01T00:00:00", end="2026-10-01T00:00:00",
            previous_start="2026-07-01T00:00:00", previous_end="2026-08-01T00:00:00",
        )
        result = json.loads(json.dumps(analyze(self.csv_path, plan.payload())))
        self.assertIsNone(result["comparison"]["growth"])
        self.assertTrue(verify(self.csv_path, plan, result).passed)


if __name__ == "__main__":
    unittest.main()
