"""明确标注的固定三角色演示，不假装理解任意自然语言或调用模型。"""
from datalab.contracts import AnalysisPlan
from datalab.execution.codegen import fixed_code
from datalab.roles.runtime import ModelReply, Role


class GuidedBackend:
    def __init__(self, options: dict):
        self.options = dict(options)

    def invoke(self, role, context, *, timeout, token_limit):
        handoff = context['handoff']
        if role == Role.PLANNER:
            metric = handoff['metric']
            plan = AnalysisPlan(handoff['dataset_version'], metric['version'],
                included_statuses=tuple(metric['included_statuses']), **self.options)
            return ModelReply({'plan': plan.payload()}, 0)
        if role == Role.ANALYST:
            return ModelReply({'code': fixed_code(AnalysisPlan(**handoff['plan']))}, 0)
        return ModelReply({'accepted': handoff['verification']['passed'],
                          'feedback': '固定演示审查以独立参考核验为准'}, 0)
