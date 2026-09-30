"""Reviewer 单角色重放：只调用 MiMo，检验审查能否接受正确计划、拒绝读错题的计划。

用真值计划与错误计划各构造一份“独立核验已通过”的交接，按工作流实际交接格式调用 Reviewer。
会发出付费请求（56 次、约 0.05 元），须经授权运行；逐例结果写入忽略目录 artifacts/evals/。
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
from uuid import uuid4

from model_compare import AUG, FAMILY, JUL, MID, OCT, QUESTIONS, ROOT, SEP, dataset_bytes
from datalab.contracts import AnalysisPlan, VerificationReport
from datalab.datasets.service import DatasetStore
from datalab.orchestration.workflow import Workflow
from datalab.roles.llm import ModelBackend
from datalab.roles.model_config import load_model_config
from datalab.roles.runtime import CallBudget, ModelCallError, Role, RoleRuntime

# 人工构造的读错题计划：月份错、分组错、期间边界错、两期颠倒。
WRONG = [(0, {'kind': 'summary', 'start': JUL, 'end': AUG}),
         (4, {'kind': 'trend', 'group_by': 'payment_date', 'start': SEP, 'end': OCT}),
         (7, {'kind': 'ranking', 'group_by': 'category'}),
         (12, {'kind': 'share', 'group_by': 'category', 'start': AUG, 'end': SEP}),
         (14, {'kind': 'comparison', 'start': AUG, 'end': SEP, 'previous_start': SEP, 'previous_end': OCT}),
         (2, {'kind': 'summary', 'start': SEP, 'end': '2026-09-15T00:00:00'}),
         (9, {'kind': 'ranking', 'group_by': 'channel'}),
         (16, {'kind': 'comparison', 'start': SEP, 'end': MID, 'previous_start': MID, 'previous_end': OCT})]


def main():
    parser = argparse.ArgumentParser(description='Reviewer 单角色重放（仅 MiMo，付费调用）')
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--output', default='review-replay.json')
    args = parser.parse_args()
    profile_config = load_model_config(ROOT / '.env').profile('mimo')
    scratch = ROOT / '.tmp'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch, prefix='review-replay-') as folder:
        store = DatasetStore(Path(folder))
        version = store.import_csv(dataset_bytes(), project_id='replay', dataset_family=FAMILY,
                                   currency='CNY', timezone='Asia/Shanghai')
        profile = {'dataset_family': FAMILY, 'schema_signature': version.schema_signature, 'currency': 'CNY',
                   'timezone': 'Asia/Shanghai', 'quality': version.profile}
        cases = [('truth', i, fields) for i, (_, fields) in enumerate(QUESTIONS)] + [('wrong', i, f) for i, f in WRONG]

        def run(job):
            label, index, fields, repeat = job
            plan = AnalysisPlan(version.dataset_version, 'rule-v1', included_statuses=('paid',), **fields)
            checks = [{'name': 'independent_reference', 'passed': True}, {'name': 'download_table', 'passed': True}]
            handoff = Workflow.review_handoff(str(uuid4()), QUESTIONS[index][0], profile, plan, [str(uuid4())],
                                              VerificationReport(True, checks))
            backend = ModelBackend(profile_config)
            try:
                reply = RoleRuntime(backend).invoke(Role.REVIEWER, handoff, CallBudget(1, 150000, 600))
            except ModelCallError as error:
                reply = {'accepted': None, 'feedback': str(error)}
            return {'label': label, 'case': index + 1, 'repeat': repeat, **reply, 'usage': backend.client.usage}

        with ThreadPoolExecutor(6) as pool:
            rows = list(pool.map(run, [(*case, r) for case in cases for r in range(args.repeats)]))
    output = ROOT / 'artifacts/evals'
    output.mkdir(parents=True, exist_ok=True)
    (output / args.output).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
    for label, want in (('truth', True), ('wrong', False)):
        items = [row for row in rows if row['label'] == label]
        print(label, '符合预期', sum(row['accepted'] is want for row in items), '/', len(items),
              '调用错误', sum(row['accepted'] is None for row in items))
    for row in rows:
        if row['accepted'] is not (row['label'] == 'truth'):
            print(row['label'], row['case'], row['accepted'], row['feedback'][:160])


if __name__ == '__main__':
    main()
