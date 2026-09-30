"""可独立放入受限容器的固定分析；不依赖宿主项目导入。"""
import csv
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path


def ratio(numerator, denominator):
    if denominator == 0:
        return None
    return str((Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.000001")))


def analyze(csv_path, plan):
    with open(csv_path, encoding="utf-8", newline="") as handle:
        source = list(csv.DictReader(handle))
    if plan["kind"] == "quality":
        return {"kind": "quality", "row_count": len(source),
                "status_counts": {status: sum(row["status"] == status for row in source)
                                  for status in ("paid", "refunded", "cancelled")},
                "missing_values": sum(not value for row in source for value in row.values()),
                "duplicate_orders": len(source) - len({row["order_id"] for row in source})}

    def selected(start, end):
        return [row for row in source if row["status"] in plan["included_statuses"]
                and (not start or row["payment_time"] >= start)
                and (not end or row["payment_time"] < end)]

    rows = selected(plan["start"], plan["end"])
    total = {"order_count": len(rows), "amount_cents": sum(int(row["amount_cents"]) for row in rows)}
    groups = defaultdict(lambda: {"order_count": 0, "amount_cents": 0})
    group_by = plan["group_by"]
    if group_by:
        for row in rows:
            key = row["payment_time"][:10] if group_by == "payment_date" else row[group_by]
            groups[key]["order_count"] += 1
            groups[key]["amount_cents"] += int(row["amount_cents"])
    result_rows = [{"group": key, **value} for key, value in sorted(groups.items())]
    if plan["kind"] == "ranking":
        result_rows.sort(key=lambda row: (-row["amount_cents"], row["group"]))
    if plan["kind"] == "share":
        for row in result_rows:
            row["share"] = ratio(row["amount_cents"], total["amount_cents"])
    result = {"kind": plan["kind"], "total": total, "rows": result_rows}
    if plan["kind"] == "comparison":
        previous = selected(plan["previous_start"], plan["previous_end"])
        previous_amount = sum(int(row["amount_cents"]) for row in previous)
        delta = total["amount_cents"] - previous_amount
        result["comparison"] = {"previous_order_count": len(previous), "previous_amount_cents": previous_amount,
                                "delta_cents": delta, "growth": ratio(delta, previous_amount)}
    return result


def export_result(result, output):
    output = Path(output)
    output.mkdir(exist_ok=True)
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    rows = result.get("rows") or [result.get("total", {"row_count": result.get("row_count", 0)})]
    with (output / "table.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        # 防止表格软件把用户文本当成公式；数值不变。
        writer.writerows({key: ("'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")) else value)
                          for key, value in row.items()} for row in rows)
