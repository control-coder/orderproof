"""检查点恢复：在不同阶段模拟 worker 进程消失，用最后一次落库的快照接续，不重复已完成的工作。

“进程消失”用 BaseException 子类模拟：工作流只捕获 Exception，所以它会一路冒出，任务停在最后一次检查点，
正如真实崩溃时数据库里留下的样子。快照经 JSON 往返，和 jsonb 落库一致。
"""
import json
import threading
import unittest

from test_prompt_injection import GOOD, InjectionFixture, ScriptedBackend
from datalab.execution.analysis_frame import assemble
from datalab.orchestration.services import ExecutionServices
from datalab.orchestration.workflow import State, Task, Workflow
from datalab.roles.runtime import ModelReply, Role, RoleRuntime
from datalab.storage.repository import decode_task, encode_task


class Crash(BaseException):
    """模拟进程被杀：不是 Exception，不会被工作流的异常处理吞掉。"""


class GoodBackend(ScriptedBackend):
    """Analyst 一次就写对，便于断言恢复后到底重做了哪些调用。"""

    def invoke(self, role, context, *, timeout, token_limit):
        if role == Role.ANALYST:
            self.contexts.append(json.dumps(context, ensure_ascii=False, sort_keys=True))
            return ModelReply({'code': assemble(context['handoff']['plan'], GOOD)}, 20)
        return super().invoke(role, context, timeout=timeout, token_limit=token_limit)


class CrashingServices(ExecutionServices):
    def __init__(self, runner, crash_on):
        super().__init__(runner)
        self.crash_on = crash_on

    def _maybe(self, where):
        if self.crash_on == where:
            self.crash_on = None
            raise Crash(where)

    def execute(self, task, code, timeout, cancelled):
        self._maybe('execute')
        return super().execute(task, code, timeout, cancelled)

    def verify(self, task, manifest):
        self._maybe('verify')
        return super().verify(task, manifest)


class CrashingBackend(GoodBackend):
    def __init__(self, crash_role):
        super().__init__()
        self.crash_role = crash_role

    def invoke(self, role, context, *, timeout, token_limit):
        if role == self.crash_role:
            raise Crash(role)
        return super().invoke(role, context, timeout=timeout, token_limit=token_limit)


class RecoveryTests(InjectionFixture):
    def crash_and_checkpoint(self, backend, services):
        task = Task('demo', self.version.dataset_version, '各渠道成交额排行', metric=self.metric)
        checkpoints = []
        task._on_transition = lambda current: checkpoints.append(json.loads(json.dumps(encode_task(current))))
        with self.assertRaises(Crash):
            Workflow(RoleRuntime(backend), services).run(task, cancelled=threading.Event())
        return decode_task(checkpoints[-1])

    def recover(self, task, backend):
        return Workflow(RoleRuntime(backend), self.services).recover(task, cancelled=threading.Event())

    def roles(self, backend):
        return [json.loads(item)['role'] for item in backend.contexts]

    def test_crash_while_executing_regenerates_code_without_replanning(self):
        checkpoint = self.crash_and_checkpoint(GoodBackend(), CrashingServices(self.services.runner, 'execute'))
        self.assertEqual(checkpoint.state, State.EXECUTING)
        self.assertIsNotNone(checkpoint.plan)
        after = GoodBackend()
        task = self.recover(checkpoint, after)
        self.assertEqual(task.state, State.SUCCEEDED, task.timeline[-1])
        # 计划已在检查点里，不再调用 Planner；重新生成代码并通过审查。
        self.assertEqual(self.roles(after), ['Analyst', 'Reviewer'])
        self.assertEqual(len(task.attempts), 1)

    def test_crash_while_verifying_reruns_verification_not_the_analyst(self):
        checkpoint = self.crash_and_checkpoint(GoodBackend(), CrashingServices(self.services.runner, 'verify'))
        self.assertEqual(checkpoint.state, State.VERIFYING)
        self.assertEqual(len(checkpoint.attempts), 1)
        first_run = checkpoint.attempts[0].run_id
        after = GoodBackend()
        task = self.recover(checkpoint, after)
        self.assertEqual(task.state, State.SUCCEEDED, task.timeline[-1])
        # 已完成的执行尝试被复用，确定性的核验重跑；唯一的模型调用是 Reviewer。
        self.assertEqual(self.roles(after), ['Reviewer'])
        self.assertEqual([item.run_id for item in task.attempts], [first_run])
        self.assertEqual(len(task.verifications), 1)

    def test_crash_while_planning_replans(self):
        checkpoint = self.crash_and_checkpoint(CrashingBackend(Role.PLANNER), self.services)
        self.assertEqual(checkpoint.state, State.PLANNING)
        self.assertIsNone(checkpoint.plan)
        after = GoodBackend()
        task = self.recover(checkpoint, after)
        self.assertEqual(task.state, State.SUCCEEDED, task.timeline[-1])
        self.assertEqual(self.roles(after), ['Planner', 'Analyst', 'Reviewer'])

    def test_feedback_survives_the_checkpoint(self):
        # 第一次 Analyst 输出带订单文本键名，核验失败并产生反馈；第二次执行前崩溃。
        scripted = ScriptedBackend()
        services = CrashingServices(self.services.runner, None)
        original = services.execute
        calls = []

        def execute(task, code, timeout, cancelled):
            calls.append(1)
            if len(calls) == 2:
                raise Crash('second execute')
            return original(task, code, timeout, cancelled)
        services.execute = execute
        checkpoint = self.crash_and_checkpoint(scripted, services)
        self.assertEqual(checkpoint.state, State.EXECUTING)
        self.assertEqual(checkpoint.feedback['type'], 'verification_failure')
        self.assertEqual(checkpoint.review_feedbacks, 1)
        after = GoodBackend()
        task = self.recover(checkpoint, after)
        self.assertEqual(task.state, State.SUCCEEDED, task.timeline[-1])
        analyst = json.loads(after.contexts[0])
        self.assertEqual(analyst['role'], 'Analyst')
        # 恢复后的 Analyst 仍拿到定位反馈，而不是从零开始。
        self.assertEqual(analyst['handoff']['feedback']['type'], 'verification_failure')

    def test_budget_is_not_reset(self):
        checkpoint = self.crash_and_checkpoint(GoodBackend(), CrashingServices(self.services.runner, 'verify'))
        calls, tokens = checkpoint.budget.calls, checkpoint.budget.tokens
        self.assertGreater(calls, 0)
        task = self.recover(checkpoint, GoodBackend())
        self.assertGreater(task.budget.calls, calls)
        self.assertGreater(task.budget.tokens, tokens)

    def test_only_checkpointed_states_can_be_recovered(self):
        for state in (State.QUEUED, State.WAITING_CONFIRMATION, State.SUCCEEDED):
            task = Task('demo', self.version.dataset_version, '问题', metric=self.metric)
            task.state = state
            with self.assertRaises(ValueError):
                self.recover(task, GoodBackend())


if __name__ == '__main__':
    unittest.main()
