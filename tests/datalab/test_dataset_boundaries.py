"""CSV 输入与下载表的必要质量边界。"""
from pathlib import Path
from unittest.mock import patch

from test_execution import RunnerFixture
from datalab.datasets.service import DatasetError
from datalab.contracts import AnalysisPlan
from datalab.execution.fixed_analysis import analyze, export_result
from datalab.verification.reference import differences, reference_result
from datalab.verification.table import table_issue, verify_table


class DatasetBoundaryTests(RunnerFixture):
    def upload(self, data, mapping=None):
        return self.store.import_csv(data, project_id="demo", dataset_family="orders", currency="CNY",
                                     timezone="Asia/Shanghai", mapping=mapping)

    def test_mapping_and_bom(self):
        raw = '编号,时间,金额,状态,渠道,品类\n1,2026-09-01T00:00:00,0.10,paid,网页,食品\n'
        mapping = dict(zip(("order_id", "payment_time", "amount", "status", "channel", "category"),
                           ("编号", "时间", "金额", "状态", "渠道", "品类")))
        result = self.upload(raw.encode("utf-8-sig"), mapping)
        self.assertEqual(result.row_count, 1)

    def test_reject_duplicate_missing_bad_money_and_encoding(self):
        header = 'order_id,payment_time,amount,status,channel,category\n'
        row = '1,2026-09-01T00:00:00,1.23,paid,网页,食品\n'
        variants = [(header + row + row).encode(), (header + row.replace('网页', '')).encode(),
                    (header + row.replace('1.23', 'NaN')).encode(),
                    (header + row.replace('1.23', '1.234')).encode(), b'\xff\xfe\x00']
        for value in variants:
            with self.subTest(value=value[:20]), self.assertRaises(DatasetError):
                self.upload(value)

    def test_file_and_row_limits(self):
        with patch('datalab.datasets.service.MAX_BYTES', 8), self.assertRaises(DatasetError):
            self.upload(b'x' * 9)
        raw = (Path(__file__).resolve().parents[2] / 'examples/orders.csv').read_bytes()
        with patch('datalab.datasets.service.MAX_ROWS', 5), self.assertRaises(DatasetError):
            self.upload(raw)

    def test_download_table_independently_checked(self):
        plan = AnalysisPlan(self.version.dataset_version, 'trusted-v1', group_by='channel')
        source = self.store.directory(self.version.dataset_version) / 'orders.csv'
        result = analyze(source, plan.payload())
        output = Path(self.temp.name) / 'output'
        export_result(result, output)
        expected = reference_result(source, plan)
        self.assertTrue(verify_table(output / 'table.csv', expected))
        text = (output / 'table.csv').read_text(encoding='utf-8-sig')
        (output / 'table.csv').write_text(text.replace('13000', '99999'), encoding='utf-8')
        self.assertFalse(verify_table(output / 'table.csv', expected))
        issue = table_issue(output / 'table.csv', expected)
        self.assertEqual((issue['issue'], issue['columns']), ('row_content', ['amount_cents']))
        self.assertNotIn('13000', str(issue))
        lines = text.splitlines()
        header = lines[0].split(',')
        swapped = [','.join([cells[1], cells[0], *cells[2:]]) for cells in (line.split(',') for line in lines)]
        (output / 'table.csv').write_text('\n'.join(swapped) + '\n', encoding='utf-8')
        self.assertEqual(table_issue(output / 'table.csv', expected), {'issue': 'column_order'})
        self.assertEqual(header, list(expected['rows'][0]))
        (output / 'table.csv').unlink()
        self.assertEqual(table_issue(output / 'table.csv', expected), {'issue': 'file_missing'})

    def test_reference_differences_report_paths_not_values(self):
        expected = {'kind': 'comparison', 'total': {'order_count': 3, 'amount_cents': 900},
                    'rows': [], 'comparison': {'previous_amount_cents': 600, 'growth': '0.500000'}}
        actual = {'kind': 'comparison', 'total': {'order_count': True, 'amount_cents': 900},
                  'rows': [], 'comparison': {'previous_amount_cents': 600, 'growth': '0.4', 'extra': 1}}
        found = differences(expected, actual)
        self.assertEqual(found, [{'path': 'total.order_count', 'issue': 'type', 'expected_type': 'int'},
                                 {'path': 'comparison.extra', 'issue': 'unexpected'},
                                 {'path': 'comparison.growth', 'issue': 'value'}])
        self.assertNotIn('0.5', str(found))
        self.assertEqual(differences(expected, expected), [])
