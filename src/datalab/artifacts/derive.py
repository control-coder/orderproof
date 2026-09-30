"""核验通过后派生图表与说明；固定演示与模型两条路径都走这里，输入只有 result.json 与服务端元数据。"""
from __future__ import annotations

import json
from pathlib import Path

from datalab.artifacts.charts import write_charts
from datalab.artifacts.narrative import describe

# 派生文件放在容器输出目录之外，避免与 Analyst 产物混淆；文件名固定。
DERIVED_DIR = "derived"
NARRATIVE_FILE = "narrative.json"


def derive(run_folder: Path, plan: dict, *, metric_version: str, included_statuses, currency: str,
           data_range: tuple[str, str] | None) -> dict:
    """读取本次尝试的 result.json，写入 derived/ 下的图表与说明，返回索引信息。

    只应在独立核验通过后调用：此时 result.json 与独立参考逐项一致，派生内容不会展示未经核验的数。
    """
    result = json.loads((run_folder / "output/result.json").read_text(encoding="utf-8"))
    folder = run_folder / DERIVED_DIR
    charts = write_charts(result, plan, folder, currency=currency)
    narrative = describe(result, plan, metric_version=metric_version, included_statuses=included_statuses,
                         currency=currency, data_range=data_range)
    narrative["charts"] = [item["title"] for item in charts]
    (folder / NARRATIVE_FILE).write_text(json.dumps(narrative, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"narrative": narrative, "charts": charts,
            "files": {"分析说明.json": f"{DERIVED_DIR}/{NARRATIVE_FILE}",
                      **{f"图表-{index}.png": f"{DERIVED_DIR}/{item['file']}" for index, item in enumerate(charts, 1)}}}
