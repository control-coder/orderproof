"""固定种子证据集；实际 Docker/数据库结果与纯 Python 手算基准对照。"""
import csv
from decimal import Decimal
import json
from pathlib import Path
import random
import statistics
import sys
import time
import unittest
from uuid import uuid4

from datalab.datasets.service import DatasetStore
from datalab.memory.postgres import PostgresMemoryRepository
from datalab.memory.service import MemoryConflict, MemoryService, Scope
from datalab.orchestration.workflow import Task
from datalab.orchestration.worker import process_task
from datalab.settings import Settings
from datalab.storage.repository import Repository

ROOT=Path(__file__).resolve().parents[1]
SEED=20260926


def expected(rows, options, statuses):
    """直接按行过滤和整数加总，不调用实现脚本或 SQL 参考模块。"""
    kind=options['kind']
    if kind=='quality':
        return {'kind':kind,'row_count':len(rows),'duplicate_orders':len(rows)-len({r['order_id'] for r in rows}),
                'missing_values':sum(not value for r in rows for value in r.values()),
                'status_counts':{s:sum(r['status']==s for r in rows) for s in ('paid','refunded','cancelled')}}
    def pick(start,end):
        return [r for r in rows if r['status'] in statuses and (not start or r['payment_time']>=start)
                and (not end or r['payment_time']<end)]
    current=pick(options.get('start'),options.get('end'))
    total=sum(int(r['amount_cents']) for r in current)
    groups={}
    group=options.get('group_by')
    for row in current:
        if group:
            key=row['payment_time'][:10] if group=='payment_date' else row[group]
            values=groups.setdefault(key,[0,0]); values[0]+=1; values[1]+=int(row['amount_cents'])
    parts=[{'group':key,'order_count':values[0],'amount_cents':values[1]} for key,values in sorted(groups.items())]
    if kind=='ranking': parts.sort(key=lambda row:(-row['amount_cents'],row['group']))
    ratio=lambda n,d: None if not d else format(Decimal(n)/Decimal(d),'.6f')
    if kind=='share':
        for part in parts: part['share']=ratio(part['amount_cents'],total)
    result={'kind':kind,'total':{'order_count':len(current),'amount_cents':total},'rows':parts}
    if kind=='comparison':
        before=pick(options['previous_start'],options['previous_end'])
        previous=sum(int(r['amount_cents']) for r in before)
        result['comparison']={'previous_order_count':len(before),'previous_amount_cents':previous,
                              'delta_cents':total-previous,'growth':ratio(total-previous,previous)}
    return result


def memory_cases(settings,project,version):
    """八个生产 PostgreSQL 范围与版本场景，另起 MemoryService 模拟跨会话。"""
    store=PostgresMemoryRepository(settings.database_url)
    service=MemoryService(store)
    scope=Scope(project,'评测记忆专用',version.schema_signature)
    record=[]
    def check(name,condition):
        record.append({'name':name,'passed':bool(condition)})
    def proposal(statuses):
        return service.propose(scope,'metric','成交金额',{'definition':'仅纳入明确状态','included_statuses':statuses},'eval-seed')
    candidate=proposal(['paid'])
    check('待确认不默认使用',not service.retrieve(scope))
    first=service.confirm(scope,candidate.memory_id,actor='trusted-eval',expected_current_id=None)
    next_session=MemoryService(store)
    found=next_session.retrieve(scope,kind='metric')
    check('跨会话复用并保留来源',len(found)==1 and 'eval-seed' in next_session.metric_snapshot(found[0]).source)
    check('跨项目隔离',not next_session.retrieve(Scope(str(uuid4()),scope.dataset_family,scope.schema_signature)))
    check('同结构不同语义隔离',not next_session.retrieve(Scope(project,'退货订单',scope.schema_signature)))
    check('结构变化不复用',not next_session.retrieve(Scope(project,scope.dataset_family,'schema-changed')))
    second=proposal(['paid','refunded'])
    try: service.confirm(scope,second.memory_id,actor='trusted-eval',expected_current_id=None)
    except MemoryConflict: conflict=True
    else: conflict=False
    check('同名冲突须提供当前版本',conflict)
    previous=service.metric_snapshot(first)
    replaced=service.confirm(scope,second.memory_id,actor='trusted-eval',expected_current_id=first.memory_id)
    check('新版本不改写旧快照',previous.included_statuses==('paid',) and replaced.version==2 and service.history(scope)[0].status=='superseded')
    service.invalidate(scope,replaced.memory_id,actor='trusted-eval')
    check('失效后不再默认使用',not service.retrieve(scope))
    return record


def fault_cases():
    """指定八个既有故障用例，实际执行而不是从旧报告抄成功数。"""
    sys.path.insert(0,str(ROOT/'tests/datalab'))
    import test_execution, test_workflow, test_app_integration
    selected=[
        (test_execution.DockerIntegrationTests,'test_real_permissions_network_and_file_isolation'),
        (test_execution.DockerIntegrationTests,'test_real_timeout_cleanup'),
        (test_execution.DockerIntegrationTests,'test_real_cancel_cleanup'),
        (test_execution.DockerIntegrationTests,'test_real_symlink_rejected'),
        (test_execution.DockerIntegrationTests,'test_real_output_disk_limit'),
        (test_execution.DockerIntegrationTests,'test_real_memory_limit'),
        (test_workflow.WorkflowTests,'test_reference_failure_never_self_approved'),
        (test_app_integration.AppIntegrationTests,'test_cancel_before_final_save_wins'),
    ]
    cases=[]
    for cls,name in selected:
        started=time.perf_counter();result=unittest.TestResult();cls(name).run(result)
        cases.append({'name':name,'passed':result.wasSuccessful() and result.testsRun==1 and not result.skipped,
                      'seconds':round(time.perf_counter()-started,3),
                      'reason':'跳过' if result.skipped else '失败' if not result.wasSuccessful() else None})
    return cases


def main():
    import os
    os.environ['DATALAB_DOCKER_TESTS']='1'
    os.environ['DATALAB_APP_TESTS']='1'
    settings=Settings.load();os.chdir(ROOT)
    repository=Repository(settings.database_url)
    project=repository.create_project('固定种子评测-'+uuid4().hex[:8])['project_id']
    dataset=DatasetStore(settings.artifact_root/'datasets')
    version=dataset.import_csv((ROOT/'examples/orders.csv').read_bytes(),project_id=project,
        dataset_family='评测订单',currency='CNY',timezone='Asia/Shanghai')
    repository.add_dataset(version)
    scope=Scope(project,version.dataset_family,version.schema_signature)
    memory=MemoryService(PostgresMemoryRepository(settings.database_url))
    confirmed=memory.confirm(scope,memory.propose(scope,'metric','基础口径',
        {'definition':'评测明确纳入 paid','included_statuses':['paid']},'trusted-seed').memory_id,
        actor='trusted-eval',expected_current_id=None)
    snapshot=memory.metric_snapshot(confirmed)
    with (dataset.directory(version.dataset_version)/'orders.csv').open(encoding='utf-8',newline='') as handle:
        rows=list(csv.DictReader(handle))
    rng=random.Random(SEED)
    numeric=[]
    kinds=['summary','trend','ranking','share','comparison','quality']
    periods=[(None,None),('2026-08-01T00:00:00','2026-09-01T00:00:00'),
             ('2026-09-01T00:00:00','2026-10-01T00:00:00'),('2026-12-01T00:00:00','2027-01-01T00:00:00')]
    for index in range(20):
        kind=kinds[index%6]
        start,end=rng.choice(periods)
        options={'kind':kind,'group_by':rng.choice(['channel','category']) if kind in ('ranking','share')
                 else 'payment_date' if kind=='trend' else None,'start':start,'end':end}
        if kind=='comparison':
            options.update(start='2026-09-01T00:00:00',end='2026-10-01T00:00:00',
                previous_start=rng.choice(['2026-08-01T00:00:00','2026-11-01T00:00:00']),
                previous_end=rng.choice(['2026-09-01T00:00:00','2026-12-01T00:00:00']))
            if options['previous_start'].startswith('2026-11'):options['previous_end']='2026-12-01T00:00:00'
            else:options['previous_end']='2026-09-01T00:00:00'
        task=Task(project,version.dataset_version,f'固定种子数值用例 {index+1}',metric=snapshot)
        repository.create_task(task,{'options':options,'mode':'guided-demo'})
        started=time.perf_counter();process_task(task.task_id)
        seconds=round(time.perf_counter()-started,3)
        saved=repository.get_task(task.task_id,project)
        outcome=saved['snapshot']
        reference=outcome['verifications'][-1]['reference'] if outcome['verifications'] else None
        wanted=expected(rows,options,['paid'])
        passed=saved['state']=='SUCCEEDED' and reference==wanted and all(
            v['passed'] for v in outcome['verifications'])
        numeric.append({'case':index+1,'kind':kind,'passed':passed,'seconds':seconds,
                        'tokens':outcome['budget']['tokens'],'failure':None if passed else outcome.get('failure') or '数值/结果不匹配'})
    memories=memory_cases(settings,project,version)
    faults=fault_cases()
    success=lambda values:sum(x['passed'] for x in values)
    times=sorted(item['seconds'] for item in numeric)
    summary={'seed':SEED,'mode':'fixed-three-role-demo-real-docker',
             'numerical':{'passed':success(numeric),'total':len(numeric),'median_seconds':statistics.median(times),
                          'p95_seconds':times[int(.95*(len(times)-1))],'tokens':sum(x['tokens'] for x in numeric),
                          'cases':numeric},
             'memory':{'passed':success(memories),'total':len(memories),'cases':memories},
             'faults':{'passed':success(faults),'total':len(faults),'cases':faults},
             'comparison':'未运行真实单 Agent/多 Agent 模型对照；零 token 仅表示固定角色未调用模型。'}
    output=ROOT/'artifacts/evals';output.mkdir(parents=True,exist_ok=True)
    (output/'fixed-seed.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({name:{key:value for key,value in summary[name].items() if key!='cases'}
        for name in ('numerical','memory','faults')},ensure_ascii=False,indent=2))
    if any(summary[part]['passed']!=summary[part]['total'] for part in ('numerical','memory','faults')):
        raise SystemExit('有评测未通过，详见忽略目录 artifacts/evals/fixed-seed.json')

if __name__=='__main__':main()
