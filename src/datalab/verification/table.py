"""独立检查下载表，防止 JSON 正确而 CSV 展示被篡改。"""
import csv
from pathlib import Path


def expected_table(reference: dict) -> list[dict]:
    rows = reference.get("rows") or [reference.get("total", {"row_count": reference.get("row_count", 0)})]
    expected = []
    for row in rows:
        clean = {}
        for key, value in row.items():
            if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
                value = "'" + value
            clean[key] = "" if value is None else str(value)
        expected.append(clean)
    return expected


def table_issue(path: Path, reference: dict) -> dict | None:
    """返回首个不匹配的类型与位置（行号、列名），不含任何期望值；一致时返回 None。"""
    expected = expected_table(reference)
    columns = list(expected[0])
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            header = reader.fieldnames or []
            actual = list(reader)
    except FileNotFoundError:
        return {"issue": "file_missing"}
    except (OSError, UnicodeError, csv.Error):
        return {"issue": "unreadable"}
    if header != columns:
        return {"issue": "column_order" if sorted(header) == sorted(columns) else "columns_mismatch"}
    if len(actual) != len(expected):
        return {"issue": "row_count"}
    for index, (got, want) in enumerate(zip(actual, expected)):
        if got != want:
            return {"issue": "row_content", "row": index, "columns": [key for key in columns if got.get(key) != want[key]]}
    return None


def verify_table(path: Path, reference: dict) -> bool:
    return table_issue(path, reference) is None
