"""以公开样例和明确的可信演示口径验证容器闭环，不调用模型。"""
import argparse
import json
from pathlib import Path
from datalab.contracts import AnalysisPlan
from datalab.datasets.service import DatasetStore
from datalab.execution.pipeline import run_fixed
from datalab.execution.runner import DockerRunner


def main():
    parser = argparse.ArgumentParser(description="运行固定分析容器示例")
    parser.add_argument("--output", type=Path, default=Path("artifacts/demo"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    store = DatasetStore(args.output / "datasets")
    version = store.import_csv((root / "examples/orders.csv").read_bytes(), project_id="demo",
        dataset_family="standard-orders", currency="CNY", timezone="Asia/Shanghai")
    plan = AnalysisPlan(version.dataset_version, "trusted-demo-paid-v1", kind="ranking", group_by="channel")
    report = run_fixed(DockerRunner(store, args.output / "runs"), plan, "demo")
    print(json.dumps({"run_id": report["run_id"], "status": report["status"]}, ensure_ascii=False))
    raise SystemExit(0 if report["status"] == "SUCCEEDED" else 1)


if __name__ == "__main__":
    main()
