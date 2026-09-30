"""数据库为阶段真相；重复队列投递通过租约和写入令牌隔离。"""
from contextlib import contextmanager
from dataclasses import asdict
from importlib.resources import files
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from datalab.contracts import AnalysisPlan, ExecutionManifest, VerificationReport
from datalab.orchestration.workflow import MetricSnapshot, State, Task
from datalab.roles.runtime import CallBudget


def encode_task(task: Task) -> dict:
    payload = asdict(task)
    payload['state'] = task.state.value
    # monotonic 起点不能跨进程复用；只保存已消耗的有效时间。
    budget = payload['budget']
    budget['spent_seconds'] = task.budget.seconds - task.budget.remaining_time()
    budget['started'] = None
    return payload


def decode_task(payload: dict) -> Task:
    payload = dict(payload)
    payload['state'] = State(payload['state'])
    payload['metric'] = MetricSnapshot(**payload['metric']) if payload['metric'] else None
    payload['plan'] = AnalysisPlan(**payload['plan']) if payload['plan'] else None
    payload['attempts'] = [ExecutionManifest(**item) for item in payload['attempts']]
    payload['verifications'] = [VerificationReport(**item) for item in payload['verifications']]
    payload['budget'] = CallBudget(**payload['budget'])
    return Task(**payload)


class Repository:
    def __init__(self, database_url):
        self._dsn = database_url

    @contextmanager
    def connection(self):
        with psycopg.connect(self._dsn, connect_timeout=5, row_factory=dict_row) as connection:
            connection.execute("SET LOCAL statement_timeout='10s'")
            yield connection

    def initialize(self):
        with self.connection() as connection:
            connection.execute(files('datalab.storage').joinpath('schema.sql').read_text(encoding='utf-8'))

    def projects(self):
        with self.connection() as connection:
            return connection.execute('SELECT * FROM datalab_projects ORDER BY created_at').fetchall()

    def create_project(self, name):
        identifier = str(uuid4())
        with self.connection() as connection:
            connection.execute('INSERT INTO datalab_projects(project_id,name) VALUES(%s,%s)', (identifier, name))
        return {'project_id': identifier, 'name': name}

    def require_project(self, identifier):
        with self.connection() as connection:
            if not connection.execute('SELECT 1 FROM datalab_projects WHERE project_id=%s', (identifier,)).fetchone():
                raise KeyError('项目不存在')

    def add_dataset(self, metadata):
        with self.connection() as connection:
            connection.execute('INSERT INTO datalab_datasets VALUES(%s,%s,%s,now())',
                               (metadata.dataset_version, metadata.project_id, Jsonb(asdict(metadata))))

    def datasets(self, project_id):
        with self.connection() as connection:
            return [row['metadata'] for row in connection.execute(
                'SELECT metadata FROM datalab_datasets WHERE project_id=%s ORDER BY created_at', (project_id,))]

    def dataset(self, project_id, version):
        with self.connection() as connection:
            row = connection.execute('SELECT metadata FROM datalab_datasets WHERE project_id=%s AND dataset_version=%s',
                                     (project_id, version)).fetchone()
        if not row:
            raise KeyError('当前项目不存在该数据版本')
        return row['metadata']

    def create_task(self, task, request):
        self.dataset(task.project_id, task.dataset_version)
        with self.connection() as connection:
            connection.execute('INSERT INTO datalab_tasks(task_id,project_id,dataset_version,state,request,snapshot) VALUES(%s,%s,%s,%s,%s,%s)',
                (task.task_id, task.project_id, task.dataset_version, task.state.value, Jsonb(request), Jsonb(encode_task(task))))

    def get_task(self, task_id, project_id=None):
        with self.connection() as connection:
            row = connection.execute('SELECT * FROM datalab_tasks WHERE task_id=%s', (task_id,)).fetchone()
        if not row or (project_id is not None and str(row['project_id']) != str(project_id)):
            raise KeyError('当前项目不存在该任务')
        return row

    def tasks(self, project_id):
        with self.connection() as connection:
            rows = connection.execute('SELECT task_id,state,snapshot,created_at,updated_at FROM datalab_tasks WHERE project_id=%s ORDER BY created_at DESC LIMIT 100', (project_id,)).fetchall()
        return rows

    def claim(self, task_id):
        token = str(uuid4())
        with self.connection() as connection:
            row = connection.execute(
                "UPDATE datalab_tasks SET lease_id=%s,lease_until=now()+interval '30 seconds' WHERE task_id=%s AND state='QUEUED' AND lease_id IS NULL RETURNING *",
                (token, task_id)).fetchone()
        return (token, row) if row else None

    def heartbeat(self, task_id, token):
        with self.connection() as connection:
            row = connection.execute(
                "UPDATE datalab_tasks SET lease_until=now()+interval '30 seconds' WHERE task_id=%s AND lease_id=%s RETURNING cancel_requested",
                (task_id, token)).fetchone()
        return None if not row else row['cancel_requested']

    def save(self, task, token, *, release=False):
        with self.connection() as connection:
            current = connection.execute(
                "SELECT cancel_requested FROM datalab_tasks WHERE task_id=%s AND lease_id=%s FOR UPDATE",
                (task.task_id, token)).fetchone()
            if not current:
                raise RuntimeError("任务租约失效，禁止旧 worker 覆盖结果")
            if release and current["cancel_requested"] and task.state != State.CANCELLED:
                # 行锁保证最终提交不会覆盖已经确认的取消请求。
                task.timeline.append({"from":task.state.value,"to":"CANCELLED","note":"最终提交前收到用户取消"})
                task.state = State.CANCELLED
                task.failure = None
            row = connection.execute(
                'UPDATE datalab_tasks SET state=%s,snapshot=%s,updated_at=now(),lease_id=CASE WHEN %s THEN NULL ELSE lease_id END,lease_until=CASE WHEN %s THEN NULL ELSE lease_until END WHERE task_id=%s AND lease_id=%s RETURNING task_id',
                (task.state.value, Jsonb(encode_task(task)), release, release, task.task_id, token)).fetchone()
        if not row:
            raise RuntimeError('任务租约失效，禁止旧 worker 覆盖结果')

    def cancel(self, task_id, project_id):
        with self.connection() as connection:
            row = connection.execute('SELECT * FROM datalab_tasks WHERE task_id=%s AND project_id=%s FOR UPDATE', (task_id,project_id)).fetchone()
            if not row:
                raise KeyError('任务不存在')
            if row['state'] in ('SUCCEEDED','FAILED','CANCELLED'):
                return
            task = decode_task(row['snapshot'])
            if row['lease_id'] is None:
                task.move(State.CANCELLED, '用户取消未执行任务')
            connection.execute('UPDATE datalab_tasks SET cancel_requested=true,state=%s,snapshot=%s,updated_at=now() WHERE task_id=%s',
                (task.state.value if row['lease_id'] is None else row['state'], Jsonb(encode_task(task)), task_id))

    def confirm_task(self, task_id, project_id, metric):
        with self.connection() as connection:
            row = connection.execute('SELECT * FROM datalab_tasks WHERE task_id=%s AND project_id=%s FOR UPDATE', (task_id,project_id)).fetchone()
            if not row or row['state'] != 'WAITING_CONFIRMATION' or row['lease_id'] is not None:
                raise ValueError('任务不在可确认的等待阶段')
            task = decode_task(row['snapshot'])
            task.metric = metric
            # 数据库可派发状态与领域恢复快照分开；worker 调用 resume。
            connection.execute("UPDATE datalab_tasks SET state='QUEUED',snapshot=%s,cancel_requested=false,updated_at=now() WHERE task_id=%s",
                               (Jsonb(encode_task(task)), task_id))

    def pending_ids(self):
        with self.connection() as connection:
            # 崩溃中的 Python 不从中途恢复；保留证据并明确失败，可新建任务重试。
            stale = connection.execute('SELECT task_id,snapshot FROM datalab_tasks WHERE lease_until<now() FOR UPDATE SKIP LOCKED').fetchall()
            for row in stale:
                task = decode_task(row['snapshot'])
                task.state = State.FAILED
                task.failure = 'WORKER_LEASE_EXPIRED'
                connection.execute("UPDATE datalab_tasks SET state='FAILED',snapshot=%s,lease_id=NULL,lease_until=NULL,updated_at=now() WHERE task_id=%s", (Jsonb(encode_task(task)),row['task_id']))
            return [str(row['task_id']) for row in connection.execute("SELECT task_id FROM datalab_tasks WHERE state='QUEUED' AND lease_id IS NULL ORDER BY created_at LIMIT 100")]

    def add_artifact(self, task_id, run_id, name, relative_path):
        with self.connection() as connection:
            connection.execute('INSERT INTO datalab_artifacts VALUES(%s,%s,%s,%s,%s) ON CONFLICT(task_id,run_id,name) DO NOTHING',
                (str(uuid4()),task_id,run_id,name,relative_path))

    def artifacts(self, task_id, project_id):
        self.get_task(task_id,project_id)
        with self.connection() as connection:
            return connection.execute('SELECT artifact_id,run_id,name FROM datalab_artifacts WHERE task_id=%s ORDER BY name', (task_id,)).fetchall()

    def artifact(self, artifact_id, project_id):
        with self.connection() as connection:
            row = connection.execute('SELECT a.* FROM datalab_artifacts a JOIN datalab_tasks t USING(task_id) WHERE a.artifact_id=%s AND t.project_id=%s', (artifact_id,project_id)).fetchone()
        if not row:
            raise KeyError('当前项目不存在该产物')
        return row
