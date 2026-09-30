"""由已通过独立核验的 result.json 确定性生成图表；不经模型，不执行分析代码。

在可信 worker 中以 Agg 后端渲染：输入只有结构化数值和截断后的分组名，关闭 mathtext 解析，
且不写入含版本号或时间的 PNG 元数据。同一 result.json、同一 matplotlib 版本与字体下输出字节一致。
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
import re
import warnings

import matplotlib
from matplotlib import font_manager
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MaxNLocator

MAX_CHARTS = 3
# 超过上限的分组合并为“其他”，避免条形过密；完整结果仍在下载表中。
MAX_BARS = 15
# 日期跨度不超过一年时补齐无订单日期（按口径该日订单为 0），否则只画有订单的日期。
MAX_FILL_DAYS = 366
GROUP_TITLES = {"channel": "渠道", "category": "品类", "payment_date": "支付日期"}
STATUSES = ("paid", "refunded", "cancelled")
# 优先开源字体，便于在 Linux 上以同名字体复现；找不到中文字体时中文会缺字，但数值与布局不变。
FONT_CANDIDATES = ("Noto Sans CJK SC", "Noto Sans SC", "Microsoft YaHei", "SimHei", "WenQuanYi Zen Hei")

SURFACE = "#ffffff"
INK = "#24363c"
INK_SECONDARY = "#5f6f68"
GRID = "#e7ece6"
SERIES = "#2a78d6"
MUTED = "#a3aea8"


def pick_font() -> str | None:
    # matplotlib 不能选择可变字体的字重，会渲染成最细实例；只接受有常规字重静态文件的字体。
    names = {entry.name for entry in font_manager.fontManager.ttflist
             if 350 <= font_manager.weight_dict.get(entry.weight, entry.weight) <= 500
             and "-VF" not in Path(entry.fname).name and "Variable" not in Path(entry.fname).name}
    return next((name for name in FONT_CANDIDATES if name in names), None)


def _label(value) -> str:
    # 分组名来自用户 CSV，只作为纯文本刻度；去掉控制字符并截断。
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value)).strip()
    return text if len(text) <= 18 else text[:17] + "…"


def _money(cents: int) -> str:
    return f"{Decimal(cents) / 100:,.2f}"


def _percent(ratio: Decimal) -> str:
    return f"{(ratio * 100).quantize(Decimal('0.01'), ROUND_HALF_UP)}%"


def _fold(rows: list[dict]) -> tuple[list[dict], str | None]:
    ordered = sorted(rows, key=lambda row: (-row["amount_cents"], str(row["group"])))
    if len(ordered) <= MAX_BARS:
        return ordered, None
    head, rest = ordered[:MAX_BARS - 1], ordered[MAX_BARS - 1:]
    other = {"group": f"其他 {len(rest)} 组", "order_count": sum(row["order_count"] for row in rest),
             "amount_cents": sum(row["amount_cents"] for row in rest)}
    return head + [other], f"金额排名第 {MAX_BARS} 位及之后的 {len(rest)} 组合并为“其他”"


def _bars(title, items, unit, source, note=None, emphasis=None) -> dict:
    """items 为 (标签, 绘图数值, 显示文本)；emphasis 为突出显示的下标，其余用中性色。"""
    return {"form": "hbar", "title": title, "unit": unit, "source": source, "note": note,
            "labels": [_label(label) for label, _, _ in items], "values": [value for _, value, _ in items],
            "texts": [text for _, _, text in items], "emphasis": emphasis}


def _daily(rows: list[dict]) -> tuple[list[dict], str | None]:
    days = sorted(rows, key=lambda row: row["group"])
    try:
        first, last = date.fromisoformat(days[0]["group"]), date.fromisoformat(days[-1]["group"])
    except ValueError:
        return days, None
    span = (last - first).days + 1
    if span > MAX_FILL_DAYS:
        return days, "仅显示有订单的日期"
    known = {row["group"]: row for row in days}
    filled = []
    for offset in range(span):
        key = (first + timedelta(days=offset)).isoformat()
        filled.append(known.get(key, {"group": key, "order_count": 0, "amount_cents": 0}))
    return filled, "无订单日期按 0 补齐" if len(filled) > len(days) else None


def chart_specs(result: dict, plan: dict) -> list[dict]:
    """只依据分析类型、分组字段和结果数值决定图表，同一输入得到同一组规格。"""
    kind, group = plan["kind"], plan.get("group_by")
    if kind == "quality":
        counts = result["status_counts"]
        return [_bars("各状态订单数（全部行）", [(status, counts.get(status, 0), f"{counts.get(status, 0):,}")
                                          for status in STATUSES], "count", "/status_counts")]
    specs = []
    if kind == "comparison":
        total, comparison = result["total"], result["comparison"]
        specs.append(_bars("上期与本期金额", [
            ("上期", comparison["previous_amount_cents"] / 100, _money(comparison["previous_amount_cents"])),
            ("本期", total["amount_cents"] / 100, _money(total["amount_cents"]))], "amount",
            "/comparison/previous_amount_cents,/total/amount_cents", emphasis=1))
        specs.append(_bars("上期与本期订单数", [
            ("上期", comparison["previous_order_count"], f"{comparison['previous_order_count']:,}"),
            ("本期", total["order_count"], f"{total['order_count']:,}")], "count",
            "/comparison/previous_order_count,/total/order_count", emphasis=1))
    rows = result.get("rows") or []
    if not rows:
        return specs[:MAX_CHARTS]
    name = GROUP_TITLES.get(group, "分组")
    if group == "payment_date":
        days, note = _daily(rows)
        labels = [row["group"] for row in days]
        specs.append({"form": "line", "title": "每日金额", "unit": "amount", "source": "/rows", "note": note,
                      "labels": labels, "values": [row["amount_cents"] / 100 for row in days],
                      "texts": [_money(row["amount_cents"]) for row in days]})
        specs.append({"form": "column", "title": "每日订单数", "unit": "count", "source": "/rows", "note": note,
                      "labels": labels, "values": [row["order_count"] for row in days],
                      "texts": [f"{row['order_count']:,}" for row in days]})
        return specs[:MAX_CHARTS]
    folded, note = _fold(rows)
    amount_total = result["total"]["amount_cents"]
    if kind == "share" and amount_total > 0:
        ratios = [Decimal(row["amount_cents"]) / Decimal(amount_total) for row in folded]
        specs.append(_bars(f"各{name}金额占比", [(row["group"], float(ratio * 100), _percent(ratio))
                                             for row, ratio in zip(folded, ratios)], "percent", "/rows/*/share", note))
    specs.append(_bars(f"各{name}金额", [(row["group"], row["amount_cents"] / 100, _money(row["amount_cents"]))
                                       for row in folded], "amount", "/rows/*/amount_cents", note))
    specs.append(_bars(f"各{name}订单数", [(row["group"], row["order_count"], f"{row['order_count']:,}")
                                        for row in folded], "count", "/rows/*/order_count",
                       (note + "；" if note else "") + "顺序与金额图一致"))
    return specs[:MAX_CHARTS]


STYLE = {
    "text.parse_math": False,
    "axes.unicode_minus": False,
    "font.family": "sans-serif",
    "font.size": 10,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID,
    "axes.labelcolor": INK_SECONDARY,
    "xtick.color": INK_SECONDARY,
    "ytick.color": INK_SECONDARY,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "path.simplify": False,
}


def _plain_axes(ax):
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(length=0)


def _render_bars(fig, spec):
    ax = fig.add_subplot()
    count = len(spec["values"])
    positions = list(range(count))
    colors = [SERIES] * count if spec.get("emphasis") is None else [
        SERIES if index == spec["emphasis"] else MUTED for index in positions]
    ax.barh(positions, spec["values"], height=0.55, color=colors, linewidth=0)
    ax.set_yticks(positions, spec["labels"])
    ax.invert_yaxis()
    peak = max(spec["values"], default=0) or 1
    # 每根条都有数值标签，故不再保留数值轴刻度与网格。
    for position, value, text in zip(positions, spec["values"], spec["texts"]):
        ax.text(value + peak * 0.015, position, text, va="center", ha="left", color=INK, fontsize=9)
    ax.set_xlim(0, peak * 1.22)
    ax.set_xticks([])
    _plain_axes(ax)
    ax.spines["bottom"].set_visible(False)
    ax.spines["left"].set_visible(True)
    ax.spines["left"].set_color(GRID)


def _date_ticks(ax, labels):
    step = max(1, -(-len(labels) // 8))
    ticks = list(range(0, len(labels), step))
    ax.set_xticks(ticks, [labels[index][5:] if len(labels[index]) == 10 else labels[index] for index in ticks])


def _render_line(fig, spec):
    ax = fig.add_subplot()
    values = spec["values"]
    positions = list(range(len(values)))
    ax.plot(positions, values, color=SERIES, linewidth=2, solid_joinstyle="round", solid_capstyle="round")
    # 只标注最高点和末点，其余数值见结果表。
    peak = max(positions, key=lambda index: (values[index], -index))
    marks = sorted({peak, positions[-1]})
    ax.plot(marks, [values[index] for index in marks], linestyle="none", marker="o", markersize=8,
            markerfacecolor=SERIES, markeredgecolor=SURFACE, markeredgewidth=2)
    for index in marks:
        ax.annotate(spec["texts"][index], (index, values[index]), xytext=(0, 8), textcoords="offset points",
                    ha="center", va="bottom", color=INK, fontsize=9)
    _finish_time_axes(ax, spec, max(values, default=0))


def _render_columns(fig, spec):
    ax = fig.add_subplot()
    values = spec["values"]
    positions = list(range(len(values)))
    ax.bar(positions, values, width=0.6, color=SERIES, linewidth=0)
    _finish_time_axes(ax, spec, max(values, default=0))


def _finish_time_axes(ax, spec, peak):
    _date_ticks(ax, spec["labels"])
    ax.set_ylim(0, (peak or 1) * 1.18)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=5, integer=spec["unit"] == "count"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    ax.grid(axis="y", color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    ax.set_xlim(-0.6, len(spec["labels"]) - 0.4)
    _plain_axes(ax)


UNITS = {"amount": "金额", "count": "订单数", "percent": "占合计金额比例"}


def render(spec: dict, path: Path, *, currency: str, font: str | None) -> None:
    height = 3.2 if spec["form"] != "hbar" else 1.3 + 0.36 * len(spec["values"])
    subtitle = UNITS[spec["unit"]] + (f"（{currency}）" if spec["unit"] == "amount" else "")
    if spec.get("note"):
        subtitle += " · " + spec["note"]
    with matplotlib.rc_context():
        # 从内置默认值出发，不受本机 matplotlibrc 影响。
        matplotlib.rcdefaults()
        matplotlib.rcParams.update(STYLE)
        matplotlib.rcParams["font.sans-serif"] = ([font] if font else []) + ["DejaVu Sans"]
        with warnings.catch_warnings():
            # 缺少中文字体时仅缺字，不影响数值；不把警告写入 worker 日志。
            warnings.filterwarnings("ignore", message=r"Glyph .* missing")
            fig = Figure(figsize=(7.2, height), dpi=120, layout="constrained")
            FigureCanvasAgg(fig)
            fig.suptitle(spec["title"], x=0.02, ha="left", color=INK, fontsize=13, fontweight="bold")
            {"hbar": _render_bars, "line": _render_line, "column": _render_columns}[spec["form"]](fig, spec)
            fig.supxlabel(subtitle, x=0.02, ha="left", color=INK_SECONDARY, fontsize=9)
            fig.get_layout_engine().set(w_pad=0.2, h_pad=0.12)
            fig.savefig(path, format="png", metadata={"Software": None})


def write_charts(result: dict, plan: dict, folder: Path, *, currency: str) -> list[dict]:
    """写入 chart-1.png…；返回可记录进报告的图表说明（不含绘图数值）。"""
    folder.mkdir(parents=True, exist_ok=True)
    font = pick_font()
    records = []
    for index, spec in enumerate(chart_specs(result, plan)[:MAX_CHARTS], 1):
        path = folder / f"chart-{index}.png"
        render(spec, path, currency=currency, font=font)
        records.append({"file": path.name, "title": spec["title"], "form": spec["form"], "source": spec["source"],
                        "note": spec.get("note"), "font": font, "renderer": f"matplotlib {matplotlib.__version__}"})
    return records
