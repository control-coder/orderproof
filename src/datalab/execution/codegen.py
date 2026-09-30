"""将已确认的分析计划装配为可审计的固定脚本。"""
import json
from pathlib import Path
from datalab.contracts import AnalysisPlan


def fixed_code(plan: AnalysisPlan) -> str:
    source = Path(__file__).with_name("fixed_analysis.py").read_text(encoding="utf-8")
    payload = repr(json.dumps(plan.payload(), ensure_ascii=False))
    return source + f'\nPLAN = json.loads({payload})\nexport_result(analyze("/input/orders.csv", PLAN), "/output")\n'
