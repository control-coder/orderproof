"""模型适配器与切换验收：用 httpx 模拟传输，不发送真实请求、不读取真实密钥。"""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from datalab.execution.analysis_frame import model_part
from datalab.orchestration.workflow import MetricSnapshot, State, Task, Workflow
from datalab.roles.llm import ChatClient, ModelBackend, parse_object
from datalab.roles.model_config import ModelConfigError, ModelProfile, load_model_config, switch_provider
from datalab.roles.runtime import CallBudget, ModelCallError, RoleRuntime

ROOT = Path(__file__).resolve().parents[2]
FAKE_KEY = 'test-key-not-real'

# 按 Analyst 提示中的输出契约独立手写的 analyze 函数，用于检查提示、服务端框架与独立核验口径一致；不是模型产物。
SPEC_FUNCTION = r'''
def pick(data, plan, start, end):
    return [r for r in data if r["status"] in plan["included_statuses"]
            and (start is None or r["payment_time"] >= start) and (end is None or r["payment_time"] < end)]

def share(a, b):
    return None if b == 0 else format(Decimal(a) / Decimal(b), ".6f")

def analyze(data, plan):
    if plan["kind"] == "quality":
        fields = ("order_id", "payment_time", "status", "channel", "category")
        return {"kind": "quality", "row_count": len(data),
                "duplicate_orders": len(data) - len({r["order_id"] for r in data}),
                "missing_values": sum(r[k] == "" for r in data for k in fields),
                "status_counts": {s: sum(r["status"] == s for r in data) for s in ("paid", "refunded", "cancelled")}}
    rows = pick(data, plan, plan["start"], plan["end"])
    total = {"order_count": len(rows), "amount_cents": sum(int(r["amount_cents"]) for r in rows)}
    out = []
    if plan["group_by"]:
        groups = defaultdict(lambda: [0, 0])
        for r in rows:
            key = r["payment_time"][:10] if plan["group_by"] == "payment_date" else r[plan["group_by"]]
            groups[key][0] += 1
            groups[key][1] += int(r["amount_cents"])
        out = [{"group": k, "order_count": v[0], "amount_cents": v[1]} for k, v in groups.items()]
        if plan["kind"] == "ranking":
            out.sort(key=lambda x: (-x["amount_cents"], x["group"]))
        else:
            out.sort(key=lambda x: x["group"])
        if plan["kind"] == "share":
            for x in out:
                x["share"] = share(x["amount_cents"], total["amount_cents"])
    result = {"kind": plan["kind"], "total": total, "rows": out}
    if plan["kind"] == "comparison":
        prev = pick(data, plan, plan["previous_start"], plan["previous_end"])
        amount = sum(int(r["amount_cents"]) for r in prev)
        result["comparison"] = {"previous_order_count": len(prev), "previous_amount_cents": amount,
                                "delta_cents": total["amount_cents"] - amount,
                                "growth": share(total["amount_cents"] - amount, amount)}
    return result
'''


def env_text(provider='mimo'):
    return (ROOT / '.env.example').read_text(encoding='utf-8').replace('DATALAB_MODEL_PROVIDER=mimo',
                                                                       'DATALAB_MODEL_PROVIDER=' + provider)


class ScriptedModel:
    """按角色返回预设 JSON 的模拟 OpenAI 兼容服务，并记录请求用于断言。"""

    def __init__(self, plan_fields, *, codes=None, reviews=None, review_finish='stop'):
        self.review_finish = review_finish
        self.plan_fields = plan_fields
        self.codes = list(codes or ['print("ok")'])
        self.reviews = list(reviews or [{'accepted': True, 'feedback': '计划与核验一致'}])
        self.requests = []

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append((request, body))
        system = body['messages'][0]['content']
        finish = 'stop'
        if '角色：Planner' in system:
            reply = {'plan': self.plan_fields}
        elif '角色：Analyst' in system:
            reply = {'code': self.codes.pop(0) if len(self.codes) > 1 else self.codes[0]}
        else:
            reply = self.reviews.pop(0) if len(self.reviews) > 1 else self.reviews[0]
            finish = self.review_finish
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(reply, ensure_ascii=False)},
                                                      'finish_reason': finish}], 'usage': {'total_tokens': 100}})


def profile(provider='mimo'):
    with scratch_dir() as folder:
        path = Path(folder) / '.env'
        path.write_text(with_key(env_text(provider)), encoding='utf-8')
        return load_model_config(path).profile(provider)


def scratch_dir():
    scratch = (ROOT / '.tmp').resolve()
    scratch.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=scratch, prefix='model-env-')


def with_key(text):
    return text.replace('_API_KEY=\n', '_API_KEY=' + FAKE_KEY + '\n')


class ModelConfigTests(unittest.TestCase):
    def setUp(self):
        folder = scratch_dir()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / '.env'
        self.path.write_text(with_key(env_text()), encoding='utf-8')

    def test_default_mimo_and_key_hidden(self):
        config = load_model_config(self.path)
        self.assertEqual(config.active, 'mimo')
        self.assertEqual(config.profile().model, 'mimo-v2.6-flash')
        self.assertEqual(config.profile('deepseek').base_url, 'https://api.deepseek.com')
        self.assertNotIn(FAKE_KEY, repr(config))
        self.assertNotIn(FAKE_KEY, json.dumps([item.public() for item in config.profiles.values()]))

    def test_switch_keeps_other_lines(self):
        before = self.path.read_text(encoding='utf-8')
        self.assertEqual(switch_provider('deepseek', self.path), 'deepseek')
        after = self.path.read_text(encoding='utf-8')
        self.assertEqual(load_model_config(self.path).active, 'deepseek')
        self.assertEqual(before.replace('DATALAB_MODEL_PROVIDER=mimo', 'DATALAB_MODEL_PROVIDER=deepseek'), after)
        switch_provider('guided', self.path)
        self.assertEqual(load_model_config(self.path).active, 'guided')
        with self.assertRaises(ModelConfigError):
            switch_provider('gpt', self.path)
        self.assertEqual(list(self.path.parent.glob('.env.*.tmp')), [])

    def test_rejects_plain_http_and_environment_key_wins(self):
        self.path.write_text(env_text().replace('https://api.deepseek.com', 'http://api.deepseek.com'), encoding='utf-8')
        with self.assertRaises(ModelConfigError):
            load_model_config(self.path)
        self.path.write_text(env_text(), encoding='utf-8')
        self.assertFalse(load_model_config(self.path).profile().configured)
        with patch.dict(os.environ, {'DATALAB_MIMO_API_KEY': FAKE_KEY}):
            self.assertTrue(load_model_config(self.path).profile().configured)


class ChatClientTests(unittest.TestCase):
    def test_request_shape_and_sanitized_errors(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={'choices': [{'message': {'content': '{"a": 1}'}, 'finish_reason': 'stop'}],
                                             'usage': {'total_tokens': 42}})
        item = profile()
        content, tokens = ChatClient(item, transport=httpx.MockTransport(handler)).complete(
            [{'role': 'user', 'content': 'x'}], timeout=5, max_tokens=300)
        self.assertEqual((content, tokens), ('{"a": 1}', 42))
        request = seen[0]
        self.assertEqual(str(request.url), 'https://api.xiaomimimo.com/v1/chat/completions')
        self.assertEqual(request.headers['authorization'], 'Bearer ' + FAKE_KEY)
        body = json.loads(request.content)
        self.assertEqual((body['model'], body['max_tokens'], body['response_format']), ('mimo-v2.6-flash', 300, {'type': 'json_object'}))
        for response in (httpx.Response(401, text=FAKE_KEY), httpx.Response(200, json={'choices': []}),
                         httpx.Response(200, json={'choices': [{'message': {'content': '{}'}, 'finish_reason': 'length'}]})):
            with self.assertRaises(ModelCallError) as caught:
                ChatClient(item, transport=httpx.MockTransport(lambda request, r=response: r)).complete(
                    [{'role': 'user', 'content': 'x'}], timeout=5, max_tokens=300)
            self.assertNotIn(FAKE_KEY, str(caught.exception))

        # 推理模型思考阶段耗尽上限时正文为空，须报告为截断以便调整输出上限。
        empty = httpx.Response(200, json={'choices': [{'message': {'content': ''}, 'finish_reason': 'length'}]})
        with self.assertRaisesRegex(ModelCallError, '截断'):
            ChatClient(item, transport=httpx.MockTransport(lambda request: empty)).complete(
                [{'role': 'user', 'content': 'x'}], timeout=5, max_tokens=300)

        def timeout(request):
            raise httpx.ReadTimeout('slow', request=request)
        with self.assertRaises(ModelCallError):
            ChatClient(item, transport=httpx.MockTransport(timeout)).complete([], timeout=1, max_tokens=10)

    def test_missing_key_and_fenced_json(self):
        with self.assertRaises(ModelCallError):
            ChatClient(ModelProfile('mimo', 'https://example.invalid', 'm')).complete([], timeout=1, max_tokens=10)
        self.assertEqual(parse_object('```json\n{"code": "x"}\n```'), {'code': 'x'})
        with self.assertRaises(ModelCallError):
            parse_object('没有 JSON')
        # 代码字符串中未转义的换行仍可解析。
        self.assertEqual(parse_object('{"code": "a = 1\nprint(a)"}'), {'code': 'a = 1\nprint(a)'})

    def test_planner_review_prompt_only_on_replan(self):
        model = ScriptedModel({'kind': 'summary'}, reviews=[{'accepted': False, 'feedback': '期间不对'}])
        task = Workflow(RoleRuntime(ModelBackend(profile(), transport=httpx.MockTransport(model))),
                        FakeServices()).run(metric_task())
        self.assertEqual(task.state, State.SUCCEEDED, task.failure)
        systems = [body['messages'][0]['content'] for _, body in model.requests]
        self.assertNotIn('若交接含 review', systems[0])
        self.assertIn('若交接含 review', systems[3])
        review = json.loads(model.requests[2][1]['messages'][1]['content'])['handoff']
        self.assertEqual(review['question'], '各渠道成交额排行')
        self.assertNotIn('reference', review['verification'])


class FakeServices:
    def __init__(self, statuses=('COMPLETED',), checks=(True,)):
        self.statuses, self.checks, self.codes = list(statuses), list(checks), []

    def profile(self, task):
        return {'dataset_family': 'orders', 'schema_signature': 'schema-v1', 'quality': {'row_count': 6}}

    def execute(self, task, code, timeout, cancelled):
        from datalab.contracts import ExecutionManifest
        self.codes.append(code)
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return ExecutionManifest(str(uuid4()), task.dataset_version, task.plan.metric_version, 'test-only',
                                 'input/analysis.py', [], 0, status)

    def verify(self, task, manifest):
        from datalab.contracts import VerificationReport
        passed = self.checks.pop(0) if len(self.checks) > 1 else self.checks[0]
        return VerificationReport(passed, [{'name': 'independent_reference', 'passed': passed}], {'total': {'amount_cents': 1}})


def metric_task(question='各渠道成交额排行'):
    metric = MetricSnapshot('rule-v1', ('paid',), 'confirmed', 'p1', 'orders', 'schema-v1')
    return Task('p1', str(uuid4()), question, metric=metric, budget=CallBudget(6, 60000, 120))


class ModelWorkflowTests(unittest.TestCase):
    def run_flow(self, model, services, task=None):
        backend = ModelBackend(profile(), {'kind': 'summary'}, transport=httpx.MockTransport(model))
        task = task or metric_task()
        return Workflow(RoleRuntime(backend), services).run(task, cancelled=threading.Event()), backend

    def test_success_binds_versions_and_counts_tokens(self):
        # 模型尝试替换口径版本与状态，服务端必须忽略并使用交接中已确认的值。
        model = ScriptedModel({'kind': 'ranking', 'group_by': 'channel', 'metric_version': 'fake',
                               'included_statuses': ['paid', 'refunded'], 'start': '', 'end': None})
        task, _ = self.run_flow(model, FakeServices())
        self.assertEqual(task.state, State.SUCCEEDED, task.failure)
        self.assertEqual((task.plan.kind, task.plan.group_by, task.plan.metric_version, task.plan.included_statuses),
                         ('ranking', 'channel', 'rule-v1', ('paid',)))
        self.assertEqual((task.budget.calls, task.budget.tokens), (3, 300))
        planner = json.loads(model.requests[0][1]['messages'][1]['content'])
        self.assertIn('ui_options', planner)
        analyst = json.loads(model.requests[1][1]['messages'][1]['content'])
        self.assertNotIn('profile', analyst['handoff'])
        self.assertNotIn('ui_options', analyst)

    def test_execution_failure_feeds_back_previous_code_once(self):
        model = ScriptedModel({'kind': 'summary'}, codes=['bad()', 'print("fixed")'])
        services = FakeServices(statuses=('FAILED', 'COMPLETED'))
        task, _ = self.run_flow(model, services)
        self.assertEqual(task.state, State.SUCCEEDED, task.failure)
        # 执行的是服务端框架 + 模型函数；回传给模型的 previous_code 只含它自己写的部分。
        self.assertEqual([model_part(code) for code in services.codes], ['bad()', 'print("fixed")'])
        self.assertIn('PLAN = json.loads(', services.codes[0])
        second = json.loads(model.requests[2][1]['messages'][1]['content'])['handoff']['feedback']
        self.assertEqual((second['type'], second['previous_code']), ('execution_failure', 'bad()'))

    def test_verification_failure_cannot_be_approved_by_model(self):
        model = ScriptedModel({'kind': 'summary'}, reviews=[{'accepted': True, 'feedback': '模型认为可以'}])
        task, _ = self.run_flow(model, FakeServices(checks=(False,)))
        self.assertEqual((task.state, task.failure), (State.FAILED, 'VERIFICATION_FAILED'))
        # 核验失败时不调用 Reviewer，第三次请求即 Analyst 的修正。
        self.assertEqual(len(model.requests), 3)
        self.assertNotIn('角色：Reviewer', json.dumps([body['messages'][0]['content'] for _, body in model.requests], ensure_ascii=False))
        feedback = json.loads(model.requests[2][1]['messages'][1]['content'])['handoff']['feedback']
        # 回传给 Analyst 的只有检查名与结论，不含独立参考数值。
        self.assertNotIn('amount_cents', json.dumps(feedback))

    def test_reviewer_uses_own_limits_and_truncation_falls_back(self):
        model = ScriptedModel({'kind': 'summary'}, review_finish='length')
        task, _ = self.run_flow(model, FakeServices())
        self.assertEqual((task.state, task.failure), (State.SUCCEEDED, None))
        self.assertIn('截断', task.review_skipped)
        limits = [body['max_tokens'] for _, body in model.requests]
        self.assertEqual(limits, [16384, 16384, 2048])
        model = ScriptedModel({'kind': 'summary'}, reviews=[{'accepted': True}])
        task, _ = self.run_flow(model, FakeServices())
        self.assertEqual(task.state, State.SUCCEEDED)
        self.assertIn('契约', task.review_skipped)

    def test_invalid_plan_and_confirmation(self):
        task, _ = self.run_flow(ScriptedModel({'kind': 'forecast'}), FakeServices())
        self.assertEqual((task.state, task.failure), (State.FAILED, 'MODEL_ERROR'))

        def ask(request):
            return httpx.Response(200, json={'choices': [{'message': {'content': '{"confirmation": "请给出对比期间"}'},
                                                          'finish_reason': 'stop'}], 'usage': {'total_tokens': 10}})
        task, _ = self.run_flow(ask, FakeServices())
        self.assertEqual((task.state, task.confirmation), (State.WAITING_CONFIRMATION, '请给出对比期间'))


@unittest.skipUnless(os.environ.get('DATALAB_DOCKER_TESTS') == '1', '需显式启用真实 Docker 集成')
class ModelDockerLoopTests(unittest.TestCase):
    """模拟模型 + 真实受限容器 + 独立核验，检查提示中的输出契约可以被独立参考接受。"""

    def test_prompt_contract_passes_reference_in_container(self):
        from datalab.artifacts.derive import derive
        from datalab.datasets.service import DatasetStore
        from datalab.execution.runner import DockerRunner
        from datalab.orchestration.services import ExecutionServices
        scratch = (ROOT / '.tmp').resolve()
        scratch.mkdir(exist_ok=True)
        folder = tempfile.TemporaryDirectory(dir=scratch, prefix='model-loop-')
        self.addCleanup(folder.cleanup)
        store = DatasetStore(Path(folder.name) / 'datasets')
        version = store.import_csv((ROOT / 'examples/orders.csv').read_bytes(), project_id='p1',
                                   dataset_family='orders', currency='CNY', timezone='Asia/Shanghai')
        services = ExecutionServices(DockerRunner(store, Path(folder.name) / 'runs'))
        cases = [{'kind': 'summary'}, {'kind': 'ranking', 'group_by': 'channel'}, {'kind': 'share', 'group_by': 'category'},
                 {'kind': 'trend', 'group_by': 'payment_date'}, {'kind': 'quality'},
                 {'kind': 'comparison', 'start': '2026-08-15T00:00:00', 'end': '2026-09-01T00:00:00',
                  'previous_start': '2026-08-01T00:00:00', 'previous_end': '2026-08-15T00:00:00'}]
        for fields in cases:
            with self.subTest(kind=fields['kind']):
                metric = MetricSnapshot('rule-v1', ('paid',), 'confirmed', 'p1', 'orders', version.schema_signature)
                task = Task('p1', version.dataset_version, '测试问题', metric=metric, budget=CallBudget(6, 60000, 300))
                backend = ModelBackend(profile(), transport=httpx.MockTransport(ScriptedModel(fields, codes=[SPEC_FUNCTION])))
                task = Workflow(RoleRuntime(backend), services).run(task)
                self.assertEqual(task.state, State.SUCCEEDED, (task.failure, [item.checks for item in task.verifications]))
                # 模型路径的容器产物同样可由服务端确定性派生图表与说明（worker 与固定演示共用 derive）。
                derived = derive(services.runner.root / task.attempts[-1].run_id, task.plan.payload(),
                                 metric_version=metric.version, included_statuses=metric.included_statuses,
                                 currency='CNY', data_range=(version.profile['time_min'], version.profile['time_max']))
                self.assertIn('rule-v1', derived['narrative']['text'])
                self.assertEqual(fields['kind'] == 'summary', not derived['charts'])


if __name__ == '__main__':
    unittest.main()
