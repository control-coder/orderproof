"""留出题集：24 道数值题（六类各 4 道），措辞与 model_compare.QUESTIONS 不同。

冻结规则：题目和真值计划在查看任何留出集模型输出之前写定，之后不得按结果改题或改提示。
修改任何一题都会改变 HELDOUT_SHA256；测试会据此报警，必须在提交信息里说明原因并同步评测文档。
真值计划由人工按题意写定，区间为左闭右开（结束日期取次日零点）。
"""
import hashlib
import json

JUL, AUG, SEP, OCT = '2026-07-01T00:00:00', '2026-08-01T00:00:00', '2026-09-01T00:00:00', '2026-10-01T00:00:00'
D = lambda text: f'2026-{text}T00:00:00'  # noqa: E731  月-日 → 当日零点

HELDOUT = [
    # summary
    ('7月最后一周（7月25日到31日）一共卖了多少钱、多少单？', {'kind': 'summary', 'start': D('07-25'), 'end': AUG}),
    ('截至8月20日（含当天），累计成交了多少订单和金额？', {'kind': 'summary', 'end': D('08-21')}),
    ('从9月16日起到数据结束，成交金额合计是多少？', {'kind': 'summary', 'start': D('09-16')}),
    ('8月15日这一天的成交额和订单数。', {'kind': 'summary', 'start': D('08-15'), 'end': D('08-16')}),
    # trend
    ('7月21日到7月31日，每天的成交额是多少？', {'kind': 'trend', 'group_by': 'payment_date', 'start': D('07-21'), 'end': AUG}),
    ('帮我画出8月上半月（1日到15日）的日成交额曲线。', {'kind': 'trend', 'group_by': 'payment_date', 'start': AUG, 'end': D('08-16')}),
    ('想看9月每天销售额的波动情况。', {'kind': 'trend', 'group_by': 'payment_date', 'start': SEP, 'end': OCT}),
    ('8月10日到9月10日（含）每日成交额的走势。', {'kind': 'trend', 'group_by': 'payment_date', 'start': D('08-10'), 'end': D('09-11')}),
    # ranking
    ('整个期间哪个渠道贡献的成交额最高？按渠道排个序。', {'kind': 'ranking', 'group_by': 'channel'}),
    ('9月下半月（16日起）各品类成交额排行。', {'kind': 'ranking', 'group_by': 'category', 'start': D('09-16'), 'end': OCT}),
    ('7月1日到8月31日合计，各渠道销售额排名。', {'kind': 'ranking', 'group_by': 'channel', 'start': JUL, 'end': SEP}),
    ('9月前15天，各渠道销售额从高到低怎么排？', {'kind': 'ranking', 'group_by': 'channel', 'start': SEP, 'end': D('09-16')}),
    # share
    ('各渠道成交额分别占总额的百分之几？', {'kind': 'share', 'group_by': 'channel'}),
    ('7月各品类销售额的构成比例。', {'kind': 'share', 'group_by': 'category', 'start': JUL, 'end': AUG}),
    ('9月各渠道的金额份额分布。', {'kind': 'share', 'group_by': 'channel', 'start': SEP, 'end': OCT}),
    ('8月16日到8月31日，各品类占这段时间总成交额的多少？', {'kind': 'share', 'group_by': 'category', 'start': D('08-16'), 'end': SEP}),
    # comparison
    ('8月下半月（16日起）比上半月（1日至15日）成交额涨了还是跌了，幅度多少？',
     {'kind': 'comparison', 'start': D('08-16'), 'end': SEP, 'previous_start': AUG, 'previous_end': D('08-16')}),
    ('7月下半月（16日起）相比上半月（1日至15日），成交额的变化率是多少？',
     {'kind': 'comparison', 'start': D('07-16'), 'end': AUG, 'previous_start': JUL, 'previous_end': D('07-16')}),
    ('9月上半月（1日至15日）相比8月全月，成交额变化了多少？',
     {'kind': 'comparison', 'start': SEP, 'end': D('09-16'), 'previous_start': AUG, 'previous_end': SEP}),
    ('8月下半月对比7月下半月，成交额差了多少？',
     {'kind': 'comparison', 'start': D('08-16'), 'end': SEP, 'previous_start': D('07-16'), 'previous_end': AUG}),
    # quality
    ('数据里有没有重复的订单编号？', {'kind': 'quality'}),
    ('订单状态分布怎么样，有没有缺失字段？', {'kind': 'quality'}),
    ('导入的数据可靠吗？看一下行数、空值与重复情况。', {'kind': 'quality'}),
    ('帮我核对数据完整性：一共多少行，各状态各占多少条。', {'kind': 'quality'}),
]

# 冻结指纹：题面与真值计划的规范化 JSON 的 SHA-256。
HELDOUT_SHA256 = '5c76ba07e81408afc5be842c98738a61195a3b5b834ac2cbb262919deab73c2e'


def fingerprint(questions=HELDOUT):
    return hashlib.sha256(json.dumps(questions, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


if __name__ == '__main__':
    print(len(HELDOUT), fingerprint())
