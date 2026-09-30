"""把已核验结果的关键字段套入固定中文模板；不经模型，句中每个数都能指回 result.json。"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

GROUP_TITLES = {"channel": "渠道", "category": "品类", "payment_date": "支付日期"}
STATUS_TITLES = {"paid": "已支付", "refunded": "已退款", "cancelled": "已取消"}


def _money(cents: int) -> str:
    return f"{Decimal(cents) / 100:,.2f}"


def _signed_money(cents: int) -> str:
    return ("+" if cents > 0 else "") + _money(cents)


def _percent(ratio) -> str:
    value = (Decimal(ratio) * 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return f"{value}%"


def _stamp(value: str) -> str:
    return value.replace("T", " ")


def period_text(start: str | None, end: str | None, data_range: tuple[str, str] | None = None) -> str:
    """期间按左闭右开书写；未限定时写出数据自身的起止（含终点）。"""
    if start and end:
        return f"{_stamp(start)} 至 {_stamp(end)}（不含终点）"
    if start:
        return f"{_stamp(start)} 起"
    if end:
        return f"{_stamp(end)} 之前"
    if data_range:
        return f"全部数据期间 {_stamp(data_range[0])} 至 {_stamp(data_range[1])}"
    return "全部数据期间"


def describe(result: dict, plan: dict, *, metric_version: str, included_statuses: tuple[str, ...] | list[str],
             currency: str, data_range: tuple[str, str] | None = None) -> dict:
    """返回 2–3 句说明及每句引用的结果字段；输入相同则输出相同。"""
    kind = plan["kind"]
    statuses = "、".join(STATUS_TITLES.get(item, item) for item in included_statuses)
    scope = f"口径版本 {metric_version}（纳入{statuses}订单）"
    period = period_text(plan.get("start"), plan.get("end"), data_range)
    sentences: list[tuple[str, list[str]]] = []
    if kind == "quality":
        counts = result["status_counts"]
        parts = "、".join(f"{STATUS_TITLES[key]} {counts.get(key, 0):,}" for key in STATUS_TITLES)
        sentences.append((f"数据质量检查覆盖全部 {result['row_count']:,} 行（{period_text(None, None, data_range)}），"
                          f"不按订单状态过滤；本任务关联{scope}。", ["/row_count"]))
        sentences.append((f"其中{parts} 笔；缺失值 {result['missing_values']:,} 个，"
                          f"重复订单编号 {result['duplicate_orders']:,} 个。",
                          ["/status_counts", "/missing_values", "/duplicate_orders"]))
        return _pack(sentences, metric_version, period_text(None, None, data_range))
    total = result["total"]
    if kind == "comparison":
        comparison = result["comparison"]
        previous = period_text(plan.get("previous_start"), plan.get("previous_end"))
        sentences.append((f"按{scope}，本期 {period}共 {total['order_count']:,} 笔订单、金额 "
                          f"{_money(total['amount_cents'])} {currency}；上期 {previous}共 "
                          f"{comparison['previous_order_count']:,} 笔、金额 {_money(comparison['previous_amount_cents'])} {currency}。",
                          ["/total", "/comparison/previous_order_count", "/comparison/previous_amount_cents"]))
        growth = comparison["growth"]
        rate = "上期金额为 0，不计算环比变化率" if growth is None else f"环比变化率 {('+' if Decimal(growth) > 0 else '')}{_percent(growth)}"
        count_delta = total["order_count"] - comparison["previous_order_count"]
        sentences.append((f"金额较上期变化 {_signed_money(comparison['delta_cents'])} {currency}，{rate}；"
                          f"订单数变化 {('+' if count_delta > 0 else '')}{count_delta:,} 笔。",
                          ["/comparison/delta_cents", "/comparison/growth"]))
        return _pack(sentences, metric_version, period, previous)
    sentences.append((f"按{scope}，{period}，共有 {total['order_count']:,} 笔订单，金额合计 "
                      f"{_money(total['amount_cents'])} {currency}。", ["/total"]))
    rows = result.get("rows") or []
    if total["order_count"] == 0:
        sentences.append(("该期间没有符合口径的订单，未生成分组结论。", ["/total/order_count"]))
        return _pack(sentences, metric_version, period)
    if kind == "summary":
        average = (Decimal(total["amount_cents"]) / total["order_count"]).quantize(Decimal("1"), ROUND_HALF_UP)
        sentences.append((f"平均每笔订单金额 {_money(int(average))} {currency}。", ["/total"]))
        return _pack(sentences, metric_version, period)
    name = GROUP_TITLES.get(plan.get("group_by"), "分组")
    ordered = sorted(rows, key=lambda row: (-row["amount_cents"], str(row["group"])))
    top = ordered[0]
    if kind == "trend":
        low = min(rows, key=lambda row: (row["amount_cents"], str(row["group"])))
        sentences.append((f"共 {len(rows):,} 个有订单的日期，单日金额最高为 {top['group']}（{_money(top['amount_cents'])} {currency}），"
                          f"最低为 {low['group']}（{_money(low['amount_cents'])} {currency}）。", ["/rows"]))
        first, last = rows[0], rows[-1]
        if len(rows) > 1:
            sentences.append((f"首个日期 {first['group']} 与最后日期 {last['group']} 的单日金额相差 "
                              f"{_signed_money(last['amount_cents'] - first['amount_cents'])} {currency}，仅为描述性比较。",
                              ["/rows/0/amount_cents", f"/rows/{len(rows) - 1}/amount_cents"]))
        return _pack(sentences, metric_version, period)
    ratio = (Decimal(top["amount_cents"]) / Decimal(total["amount_cents"])) if total["amount_cents"] else None
    share = f"，占合计 {_percent(ratio)}" if ratio is not None else ""
    sentences.append((f"共 {len(rows):,} 个{name}，金额最高的是“{top['group']}”（{_money(top['amount_cents'])} {currency}{share}）。",
                      ["/rows"]))
    if len(ordered) > 1:
        second = ordered[1]
        if kind == "share" and total["amount_cents"] and len(ordered) > 2:
            head = sum(row["amount_cents"] for row in ordered[:3])
            sentences.append((f"金额前三的{name}合计占 {_percent(Decimal(head) / Decimal(total['amount_cents']))}；"
                              "分组差异为描述性分解，不推断原因。", ["/rows"]))
        else:
            sentences.append((f"其次为“{second['group']}”（{_money(second['amount_cents'])} {currency}）；"
                              "分组差异为描述性分解，不推断原因。", ["/rows"]))
    return _pack(sentences, metric_version, period)


def _pack(sentences, metric_version, period, previous_period=None) -> dict:
    return {"text": "".join(text for text, _ in sentences), "sentences": [
        {"text": text, "sources": sources} for text, sources in sentences[:3]],
        "metric_version": metric_version, "period": period, "previous_period": previous_period,
        "generator": "template-v1"}
