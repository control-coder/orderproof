"""Analyst 代码的服务端框架：计划常量、CSV 读写、编码与公式注入防护由服务端固定，模型只写 analyze 函数。

组装后的脚本仍在同一受限容器中执行并接受独立核验，框架不改变安全边界，只减少模型需要生成的确定性代码。
"""
import json

HEAD = '''import csv
import json
from collections import defaultdict
from decimal import Decimal

PLAN = json.loads(__PLAN__)


def selected(data, plan, start, end):
    return [row for row in data if row["status"] in plan["included_statuses"]
            and (start is None or row["payment_time"] >= start) and (end is None or row["payment_time"] < end)]


def group_key(row, group_by):
    return row["payment_time"][:10] if group_by == "payment_date" else row[group_by]


def ratio(numerator, denominator):
    return None if denominator == 0 else format(Decimal(numerator) / Decimal(denominator), ".6f")

# --- 模型代码开始 ---
'''

TAIL = '''
# --- 模型代码结束 ---


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\\t", "\\r")):
        return "'" + value
    return value


def _shape(result, kind):
    # 只删除当前分析类型不应出现的键（非 share 的行级 share、行内 comparison、非 comparison 的顶层 comparison），
    # 不改任何值、不补缺失的键；数值仍由独立参考逐项核验。
    if not isinstance(result, dict):
        return result
    if kind != "comparison":
        result.pop("comparison", None)
    rows = result.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                row.pop("comparison", None)
                if kind != "share":
                    row.pop("share", None)
    return result


with open("/input/orders.csv", encoding="utf-8", newline="") as _handle:
    _data = list(csv.DictReader(_handle))
_result = _shape(analyze(_data, dict(PLAN)), PLAN["kind"])
with open("/output/result.json", "w", encoding="utf-8") as _handle:
    json.dump(_result, _handle, ensure_ascii=False)
if PLAN["kind"] == "quality":
    _table = [{"row_count": _result.get("row_count")}]
else:
    _table = _result.get("rows") or [_result.get("total")]
with open("/output/table.csv", "w", encoding="utf-8-sig", newline="") as _handle:
    _writer = csv.DictWriter(_handle, fieldnames=list(_table[0]))
    _writer.writeheader()
    _writer.writerows({key: _cell(value) for key, value in row.items()} for row in _table)
'''


def assemble(plan: dict, code: str) -> str:
    # 计划以 JSON 字符串字面量写入，模型输出不能改变计划常量。
    return HEAD.replace('__PLAN__', repr(json.dumps(plan, ensure_ascii=False))) + code + TAIL


def model_part(script: str) -> str:
    """从组装后的脚本取回模型自己写的部分，用于修正反馈；非框架脚本原样返回。"""
    prefix, marker = HEAD.split('__PLAN__')
    if script.startswith(prefix) and script.endswith(TAIL) and marker in script:
        return script[script.index(marker) + len(marker):-len(TAIL)]
    return script
