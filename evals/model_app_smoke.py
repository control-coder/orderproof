"""真实模型全链路冒烟：API → PostgreSQL 任务 → Celery worker → 三角色 → 受限容器 → 独立核验 → 图表与说明。

会发出付费模型请求，须经用户授权后运行。数据为 model_compare 的固定种子订单，口径经 API 确认；
任务直接写入与 API 相同结构的模型请求，以便不改写 .env 的当前供应商即可分别测试各模型。
逐例结果写入忽略目录 artifacts/evals/，不输出密钥。
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from celery import Celery
from fastapi.testclient import TestClient

from datalab.api.app import create_app
from datalab.contracts import AnalysisPlan
from datalab.datasets.service import DatasetStore
from datalab.orchestration.workflow import Task
from datalab.roles.model_config import load_model_config
from datalab.roles.runtime import CallBudget
from datalab.settings import Settings
from datalab.storage.repository import Repository
from datalab.verification.reference import reference_result
from model_compare import QUESTIONS, dataset_bytes

ROOT = Path(__file__).resolve().parents[1]
# 六类分析各一题（题号为 model_compare.QUESTIONS 下标）。
CASES = [0, 5, 7, 12, 14, 18]
HEADERS = {'X-Datalab-Client': 'local-ui'}


def start_workers(count, log_folder):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    processes = []
    for index in range(count):
        log = (log_folder / f'smoke-worker-{index}.log').open('w', encoding='utf-8')
        processes.append((subprocess.Popen([sys.executable, '-B', '-m', 'celery', '-A', 'datalab.orchestration.worker:celery_app',
                                            'worker', '--pool=solo', '--concurrency=1', '--loglevel=WARNING', '--without-gossip',
                                            '--without-mingle', '-n', f'smoke{index}@%h'],
                                           cwd=ROOT, env=env, stdout=log, stderr=log), log))
    time.sleep(4)
    if any(process.poll() is not None for process, _ in processes):
        raise RuntimeError('worker 未启动，请查 .tmp 中的日志')
    return processes


def stop_workers(processes):
    for process, log in processes:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


def main():
    parser = argparse.ArgumentParser(description='真实模型全链路冒烟（付费调用）')
    parser.add_argument('--providers', default='mimo,deepseek')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--output', default='model-app-smoke.json')
    args = parser.parse_args()
    providers = [item for item in args.providers.split(',') if item]
    settings = Settings.load()
    config = load_model_config()
    for provider in providers:
        if not config.profile(provider).configured:
            raise SystemExit(provider + ' 未配置 API Key')
    repository = Repository(settings.database_url)
    store = DatasetStore(settings.artifact_root / 'datasets')
    client = TestClient(create_app(settings), headers=HEADERS)
    project = client.post('/api/projects', json={'name': '模型全链路冒烟-' + uuid4().hex[:8]}).json()['project_id']
    base = '/api/projects/' + project
    response = client.post(base + '/datasets', files={'file': ('orders.csv', dataset_bytes(), 'text/csv')},
                           data={'dataset_family': '冒烟订单', 'currency': 'CNY', 'timezone': 'Asia/Shanghai', 'mapping': '{}'})
    response.raise_for_status()
    dataset, signature = response.json()['dataset_version'], response.json()['schema_signature']
    memory_id = client.post(base + '/memories', json={'dataset_version': dataset, 'name': '成交额',
                                                      'definition': '仅统计已支付订单，不含退款与取消',
                                                      'included_statuses': ['paid']}).json()['memory_id']
    client.post(base + '/memories/' + memory_id + '/confirm',
                json={'dataset_version': dataset, 'expected_current_id': None}).raise_for_status()
    from datalab.memory.postgres import PostgresMemoryRepository
    from datalab.memory.service import MemoryService, Scope
    scope = Scope(project, '冒烟订单', signature)
    metric = MemoryService.metric_snapshot(next(item for item in MemoryService(PostgresMemoryRepository(settings.database_url))
                                                .retrieve(scope, kind='metric') if item.memory_id == memory_id))

    temporary = (ROOT / '.tmp').resolve()
    temporary.mkdir(exist_ok=True)
    workers = start_workers(args.workers, temporary)
    publisher = Celery('smoke', broker=settings.broker_url)
    jobs = []
    try:
        for index in CASES:
            for provider in providers:
                profile = config.profile(provider)
                task = Task(project, dataset, QUESTIONS[index][0], metric=metric,
                            budget=CallBudget(config.max_calls, config.max_tokens, config.seconds))
                options = {'kind': 'summary', 'group_by': None, 'start': None, 'end': None,
                           'previous_start': None, 'previous_end': None}
                repository.create_task(task, {'options': options, 'mode': 'model',
                                              'model': {'provider': profile.provider, 'model': profile.model}})
                publisher.send_task('datalab.execute', args=[task.task_id])
                jobs.append((provider, index, task.task_id, time.perf_counter()))
        records = []
        pending = list(jobs)
        deadline = time.monotonic() + config.seconds * len(jobs) / max(1, args.workers) + 120
        while pending and time.monotonic() < deadline:
            for job in list(pending):
                provider, index, task_id, started = job
                detail = client.get(base + '/tasks/' + task_id).json()
                if detail['state'] not in {'SUCCEEDED', 'FAILED', 'CANCELLED', 'WAITING_CONFIRMATION'}:
                    continue
                pending.remove(job)
                records.append(inspect(client, base, store, dataset, metric, provider, index, detail, started))
                item = records[-1]
                print(len(records), provider, item['kind'], item['state'], item['correct'], item['charts'], item['seconds'], flush=True)
            time.sleep(2)
        for provider, index, task_id, _ in pending:
            records.append({'provider': provider, 'case': index + 1, 'state': 'TIMEOUT', 'correct': False})
    finally:
        publisher.close()
        stop_workers(workers)
        client.close()
    summary = {'date': time.strftime('%Y-%m-%d'), 'project_id': project, 'models': {name: config.profile(name).public() for name in providers},
               'totals': {provider: {'cases': sum(r['provider'] == provider for r in records),
                                     'correct': sum(r['provider'] == provider and r['correct'] for r in records),
                                     'derived_ok': sum(r['provider'] == provider and r.get('derived_ok', False) for r in records),
                                     'tokens': sum(r.get('tokens', 0) for r in records if r['provider'] == provider),
                                     'calls': sum(r.get('calls', 0) for r in records if r['provider'] == provider)}
                          for provider in providers},
               'cases': sorted(records, key=lambda r: (r['provider'], r['case']))}
    output = ROOT / 'artifacts/evals'
    output.mkdir(parents=True, exist_ok=True)
    (output / args.output).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary['totals'], ensure_ascii=False, indent=2))


def inspect(client, base, store, dataset, metric, provider, index, detail, started):
    question, fields = QUESTIONS[index]
    snapshot = detail['snapshot']
    truth = reference_result(store.directory(dataset) / 'orders.csv',
                             AnalysisPlan(dataset, 'truth', included_statuses=metric.included_statuses, **fields))
    reference = snapshot['verifications'][-1]['reference'] if snapshot.get('verifications') else None
    charts = sorted(item['name'] for item in detail['artifacts'] if item['name'].endswith('.png'))
    inline = []
    for item in detail['artifacts']:
        if item['name'].endswith('.png'):
            response = client.get(base + '/artifacts/' + item['artifact_id'] + '?inline=true')
            inline.append(response.headers['content-type'] == 'image/png' and response.content.startswith(b'\x89PNG'))
    narrative = detail.get('narrative') or {}
    succeeded = detail['state'] == 'SUCCEEDED'
    expected_charts = fields['kind'] != 'summary'
    derived_ok = succeeded and metric.version in narrative.get('text', '') and bool(charts) == expected_charts and all(inline)
    return {'provider': provider, 'case': index + 1, 'kind': fields['kind'], 'question': question, 'state': detail['state'],
            'failure': snapshot.get('failure'), 'plan': snapshot.get('plan'),
            'correct': succeeded and json.loads(json.dumps(truth)) == reference,
            'charts': charts, 'inline_png': all(inline), 'narrative': narrative.get('text'), 'derived_ok': derived_ok,
            'review_skipped': snapshot.get('review_skipped'), 'attempts': len(snapshot.get('attempts', [])),
            'calls': snapshot['budget']['calls'], 'tokens': snapshot['budget']['tokens'],
            'seconds': round(time.perf_counter() - started, 1)}


if __name__ == '__main__':
    main()
