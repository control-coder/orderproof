"""把一个或多个 model_compare 结果汇总成带置信区间的对照表（只读本地 JSON，不调用模型）。

用法：python -B evals/report.py artifacts/evals/heldout-b1.json [更多文件…] [--markdown out.md]
多个文件视为同一实验的不同批次；若提示词指纹或题集不一致会发出警告，因为那样的合并没有统计意义。
"""
import argparse
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats import cluster_bootstrap, median_ratio, paired_difference, wilson  # noqa: E402

AGENT_ARMS = ('single_agent', 'three_roles')
MEMORY_ARMS = ('with_memory', 'without_memory')


def load(paths):
    cases, meta = [], []
    for offset, path in enumerate(paths):
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        meta.append({key: data.get(key) for key in ('split', 'git_commit', 'prompts_sha256', 'heldout_sha256', 'date')})
        for item in data['cases']:
            # 多文件各自从 1 开始编批次；合并时重编号，避免不同文件的批次混在一起。
            cases.append({**item, 'batch': (item.get('batch') or 1) + 1000 * offset})
    return cases, meta


def warnings(meta):
    notes = []
    for key in ('split', 'prompts_sha256', 'heldout_sha256'):
        values = {item[key] for item in meta}
        if len(values) > 1:
            notes.append(f'各文件的 {key} 不一致：{sorted(map(str, values))}；不应合并为同一实验。')
    if any(item['prompts_sha256'] is None for item in meta):
        notes.append('部分文件缺少提示词指纹，无法确认批次之间提示没有变化。')
    return notes


def by_question(items, key, value):
    groups = {}
    for item in items:
        groups.setdefault(key(item), []).append(value(item))
    return groups


def fraction(values):
    return sum(values) / len(values) if values else float('nan')


def pct(value):
    return 'n/a' if value is None else f'{value * 100:.1f}%'


def interval(groups):
    """比例的 95% 区间：以题目为单位做 Wilson，k 取各题跨批通过率之和。

    多批重复同一道题只提高每题通过率的估计精度，不增加题目数，所以样本量取题目数而不是题目×批次。
    这比按 N×B 个独立观测算更保守，也避免了全部通过时自助法区间退化为一个点。
    """
    rates = [fraction(values) for values in groups.values() if values]
    if not rates:
        return 'n/a'
    low, high = wilson(sum(rates), len(rates))
    return f'{pct(sum(rates) / len(rates))} [{pct(low)}, {pct(high)}]'


def batch_spread(items, value):
    """各批正确率的最小、最大与标准差，用来直观看批间波动。"""
    batches = {}
    for item in items:
        batches.setdefault(item['batch'], []).append(value(item))
    rates = [fraction(values) for values in batches.values()]
    spread = statistics.pstdev(rates) if len(rates) > 1 else 0.0
    return len(rates), min(rates), max(rates), spread


def agent_table(cases, lines):
    numeric = [item for item in cases if item['experiment'] == 'agents']
    for provider in sorted({item['provider'] for item in numeric}):
        rows = {arm: [item for item in numeric if item['provider'] == provider and item['arm'] == arm] for arm in AGENT_ARMS}
        if not all(rows.values()):
            continue
        lines += [f'### {provider}：单 Agent 与三角色', '',
                  '| 对照 | 题数×批数 | 正确率（95% Wilson，以题为单位） | 批间范围 / sd | 首次即对 | 核验通过但错 | 中位秒 | p90 秒 | 中位费用（元） |',
                  '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
        for arm in AGENT_ARMS:
            items = rows[arm]
            correct = lambda item: 1 if item['correct'] else 0  # noqa: E731
            batches, low, high, sd = batch_spread(items, correct)
            seconds = sorted(item['seconds'] for item in items)
            p90 = seconds[min(len(seconds) - 1, int(round(0.9 * (len(seconds) - 1))))]
            lines.append('| {} | {}×{} | {} | {}–{} / {:.1f} 个百分点 | {} | {} | {:.1f} | {:.1f} | {:.4f} |'.format(
                arm, len({item['case'] for item in items}), batches,
                interval(by_question(items, lambda i: i['case'], correct)), pct(low), pct(high), sd * 100,
                interval(by_question(items, lambda i: i['case'], lambda i: 1 if i['correct'] and i['attempts'] == 1 else 0)),
                interval(by_question(items, lambda i: i['case'], lambda i: 1 if i['verified_but_wrong'] else 0)),
                statistics.median(seconds), p90, statistics.median(item['cost_yuan'] for item in items)))
        single = by_question(rows['single_agent'], lambda i: i['case'], lambda i: 1 if i['correct'] else 0)
        roles = by_question(rows['three_roles'], lambda i: i['case'], lambda i: 1 if i['correct'] else 0)
        point, low, high = paired_difference(roles, single)
        lines += ['', f'- 三角色减单 Agent 的正确率差：{point * 100:+.1f} 个百分点，95% CI [{low * 100:+.1f}, {high * 100:+.1f}]'
                      f'（区间含 0 即没有可确认的差异）。']
        for label, field in (('费用', 'cost_yuan'), ('时延', 'seconds')):
            # 以（题目, 批次）配对，逐对取三角色/单 Agent，再对题目聚类重抽样。
            left = {(i['case'], i['batch']): i[field] for i in rows['three_roles']}
            right = {(i['case'], i['batch']): i[field] for i in rows['single_agent']}
            pairs = {}
            for key in left.keys() & right.keys():
                pairs.setdefault(key[0], []).append((left[key], right[key]))
            ratio = lambda values: median_ratio([a for a, _ in values], [b for _, b in values])  # noqa: E731
            point, low, high = cluster_bootstrap(pairs, ratio)
            if point is not None:
                lines.append(f'- 三角色 / 单 Agent 的中位{label}倍数：{point:.2f}×，95% CI [{low:.2f}, {high:.2f}]。')
        lines.append('')


def memory_table(cases, lines):
    memory = [item for item in cases if item['experiment'] == 'memory']
    for provider in sorted({item['provider'] for item in memory}):
        rows = {arm: [i for i in memory if i['provider'] == provider and i['arm'] == arm] for arm in MEMORY_ARMS}
        if not all(rows.values()):
            continue
        lines += [f'### {provider}：有 Memory 与无 Memory', '', '| 对照 | 会话×批数 | 正确率（95% CI） | 口径选对 | 核验通过但错 |',
                  '| --- | --- | --- | --- | --- |']
        for arm in MEMORY_ARMS:
            items = rows[arm]
            session = lambda i: (i['rule'], i['session'])  # noqa: E731
            lines.append('| {} | {}×{} | {} | {} | {} |'.format(
                arm, len({session(i) for i in items}), len({i['batch'] for i in items}),
                interval(by_question(items, session, lambda i: 1 if i['correct'] else 0)),
                interval(by_question(items, session, lambda i: 1 if i.get('metric_correct') else 0)),
                interval(by_question(items, session, lambda i: 1 if i['verified_but_wrong'] else 0))))
        lines.append('')


def main():
    parser = argparse.ArgumentParser(description='汇总评测结果并给出置信区间')
    parser.add_argument('files', nargs='+')
    parser.add_argument('--markdown')
    args = parser.parse_args()
    cases, meta = load(args.files)
    lines = ['## 评测汇总', '',
             f'- 文件 {len(args.files)} 个，用例 {len(cases)} 条；题集 {sorted({str(m["split"]) for m in meta})}，'
             f'提交 {sorted({str(m["git_commit"]) for m in meta})}。', '']
    lines += [f'- 警告：{note}' for note in warnings(meta)]
    agent_table(cases, lines)
    memory_table(cases, lines)
    text = '\n'.join(lines)
    print(text)
    if args.markdown:
        Path(args.markdown).write_text(text + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
