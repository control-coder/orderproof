"""第 1 轮固定计划入口；只有独立核验通过才标记分析成功。"""
from dataclasses import asdict
import json
from uuid import uuid4

from datalab.artifacts.derive import derive
from datalab.contracts import AnalysisPlan
from datalab.execution.codegen import fixed_code
from datalab.execution.runner import DockerRunner, OutputSpec
from datalab.verification.reference import verify
from datalab.verification.table import verify_table


def run_fixed(runner: DockerRunner, plan: AnalysisPlan, project_id: str) -> dict:
    run_id = str(uuid4())
    manifest = runner.execute_python(run_id, plan.dataset_version, fixed_code(plan), 30, OutputSpec(),
                                     project_id=project_id, metric_version=plan.metric_version)
    folder = runner.root / run_id
    (folder / "plan.json").write_text(json.dumps(plan.payload(), ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"run_id": run_id, "status": "FAILED", "execution": asdict(manifest)}
    if manifest.status == "COMPLETED":
        try:
            actual = json.loads((folder / "output/result.json").read_text(encoding="utf-8"))
            verification = verify(runner.store.directory(plan.dataset_version) / "orders.csv", plan, actual)
            table_ok = verify_table(folder / "output/table.csv", verification.reference)
            verification.checks.append({"name": "download_table", "passed": table_ok})
            verification.passed = verification.passed and table_ok
            report["verification"] = asdict(verification)
            report["status"] = "SUCCEEDED" if verification.passed else "FAILED"
            if verification.passed:
                version = runner.store.get(plan.dataset_version, project_id)
                derived = derive(folder, plan.payload(), metric_version=plan.metric_version,
                                 included_statuses=plan.included_statuses, currency=version.currency,
                                 data_range=(version.profile["time_min"], version.profile["time_max"]))
                report["narrative"], report["charts"] = derived["narrative"], derived["charts"]
        except (ValueError, TypeError, AttributeError, KeyError, OSError):
            report["reason"] = "结果格式错误，不能完成独立核验"
    (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
