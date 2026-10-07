"""评测统计：Wilson 区间、按题目聚类的自助法与配对差。

同一道题在多批里重复出现，各批不是独立样本；置信区间按题目聚类重抽样，
避免把 N 题 × B 批当作 N×B 个独立观测而低估不确定性。只用标准库，固定随机种子以便复现。
"""
import math
import random
import statistics

Z95 = 1.959963984540054


def wilson(successes, total, z=Z95):
    """二项比例的 Wilson 区间；total 为 0 时返回 (None, None)。"""
    if total <= 0:
        return None, None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _percentile(sorted_values, q):
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def cluster_bootstrap(groups, statistic, iterations=4000, seed=20261007, level=0.95):
    """groups 为 {题目: 该题各批观测列表}；按题目有放回重抽样，返回 (点估计, 下界, 上界)。

    statistic 接收展平后的观测列表。观测为空时返回 (None, None, None)。
    """
    keys = [key for key, values in groups.items() if values]
    if not keys:
        return None, None, None
    point = statistic([value for key in keys for value in groups[key]])
    rng = random.Random(seed)
    draws = []
    for _ in range(iterations):
        picked = [keys[rng.randrange(len(keys))] for _ in keys]
        draws.append(statistic([value for key in picked for value in groups[key]]))
    draws.sort()
    tail = (1 - level) / 2
    return point, _percentile(draws, tail), _percentile(draws, 1 - tail)


def paired_difference(first, second, iterations=4000, seed=20261007, level=0.95):
    """first、second 为 {题目: 各批 0/1 结果}；返回 (平均差 first-second, 下界, 上界)。

    先在每道题内取各组的平均通过率再相减，按题目重抽样，所以两组必须用同一批题目。
    只使用两组共有的题目。
    """
    keys = sorted(set(first) & set(second))
    differences = {key: [statistics.fmean(first[key]) - statistics.fmean(second[key])]
                   for key in keys if first[key] and second[key]}
    return cluster_bootstrap(differences, statistics.fmean, iterations, seed, level)


def median_ratio(numerator, denominator):
    """两组逐题中位数之比；用于成本和时延倍数。任一组为空时返回 None。"""
    if not numerator or not denominator:
        return None
    base = statistics.median(denominator)
    return None if base == 0 else statistics.median(numerator) / base
