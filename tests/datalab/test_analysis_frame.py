"""服务端框架的离线检查：把组装后的脚本改指向临时目录，用本机解释器运行，不需要 Docker。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from datalab.execution.analysis_frame import assemble, model_part

CSV = ('order_id,payment_time,amount_cents,status,channel,category\n'
       'E1,2026-08-01T10:00:00,1000,paid,广告,服饰\n'
       'E2,2026-08-02T10:00:00,3000,paid,直播,食品\n')

# 模型多写了不属于当前分析类型的键：ranking/trend 里的 share，以及行内或顶层的 comparison。
CODE = '''
def analyze(data, plan):
    rows = [{"group": "a", "order_count": 1, "amount_cents": 1000, "share": "0.250000", "comparison": {"x": 1}},
            {"group": "b", "order_count": 1, "amount_cents": 3000, "share": "0.750000", "comparison": {"x": 2}}]
    return {"kind": plan["kind"], "total": {"order_count": 2, "amount_cents": 4000}, "rows": rows,
            "comparison": {"delta_cents": 0}}
'''


class FrameTests(unittest.TestCase):
    def run_script(self, kind):
        plan = {'kind': kind, 'group_by': 'channel', 'included_statuses': ['paid'], 'start': None, 'end': None,
                'previous_start': None, 'previous_end': None}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'orders.csv').write_text(CSV, encoding='utf-8')
            script = assemble(plan, CODE).replace('/input/orders.csv', (root / 'orders.csv').as_posix()) \
                .replace('/output/result.json', (root / 'result.json').as_posix()) \
                .replace('/output/table.csv', (root / 'table.csv').as_posix())
            (root / 'run.py').write_text(script, encoding='utf-8')
            subprocess.run([sys.executable, '-I', str(root / 'run.py')], check=True, timeout=30)
            result = json.loads((root / 'result.json').read_text(encoding='utf-8'))
            header = (root / 'table.csv').read_text(encoding='utf-8-sig').splitlines()[0]
        return result, header

    def test_ranking_drops_share_and_comparison_everywhere(self):
        result, header = self.run_script('ranking')
        self.assertNotIn('comparison', result)
        for row in result['rows']:
            self.assertEqual(set(row), {'group', 'order_count', 'amount_cents'})
        self.assertEqual(header, 'group,order_count,amount_cents')

    def test_share_keeps_share_but_drops_comparison(self):
        result, header = self.run_script('share')
        self.assertNotIn('comparison', result)
        self.assertEqual(result['rows'][0]['share'], '0.250000')
        self.assertEqual(header, 'group,order_count,amount_cents,share')

    def test_comparison_keeps_top_level_comparison_only(self):
        result, _ = self.run_script('comparison')
        self.assertEqual(result['comparison'], {'delta_cents': 0})
        self.assertNotIn('comparison', result['rows'][0])
        self.assertNotIn('share', result['rows'][0])

    def test_values_are_not_rewritten(self):
        result, _ = self.run_script('ranking')
        self.assertEqual([row['amount_cents'] for row in result['rows']], [1000, 3000])

    def test_model_part_round_trips_after_shape_step(self):
        self.assertEqual(model_part(assemble({'kind': 'ranking'}, CODE)), CODE)


if __name__ == '__main__':
    unittest.main()
