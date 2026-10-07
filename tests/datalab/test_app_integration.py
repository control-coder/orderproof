"""真实 PostgreSQL、Redis/Celery 和 Docker 联动；角色明确使用固定演示。"""
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from uuid import uuid4


@unittest.skipUnless(os.environ.get('DATALAB_APP_TESTS') == '1','需显式启用真实应用集成')
class AppIntegrationTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from datalab.api.app import create_app
        from datalab.settings import Settings
        from datalab.storage.repository import Repository
        # 使用临时模型配置并显式切到固定演示，既不依赖也不改写本机 .env。
        import tempfile
        scratch=(Path(__file__).resolve().parents[2]/'.tmp').resolve()
        scratch.mkdir(exist_ok=True)
        folder=tempfile.TemporaryDirectory(dir=scratch,prefix='app-env-')
        self.addCleanup(folder.cleanup)
        self.env_file=Path(folder.name)/'.env'
        self.env_file.write_text((Path(__file__).resolve().parents[2]/'.env.example').read_text(encoding='utf-8')
                                 .replace('DATALAB_MODEL_PROVIDER=mimo','DATALAB_MODEL_PROVIDER=guided'),encoding='utf-8')
        environment=patch.dict(os.environ,{'DATALAB_ENV_FILE':str(self.env_file),'DATALAB_MIMO_API_KEY':'','DATALAB_DEEPSEEK_API_KEY':''})
        environment.start()
        self.addCleanup(environment.stop)
        self.settings=Settings.load()
        self.repository=Repository(self.settings.database_url)
        self.client=TestClient(create_app(self.settings),headers={'X-Datalab-Client':'local-ui'})
        self.addCleanup(self.client.close)
        response=self.client.post('/api/projects',json={'name':'集成-test-'+uuid4().hex[:8]})
        self.assertEqual(response.status_code,201,response.text)
        self.project=response.json()['project_id']
        self.addCleanup(self.cleanup_project)
        self.base='/api/projects/'+self.project
        data=(Path(__file__).resolve().parents[2]/'examples/orders.csv').read_bytes()
        response=self.client.post(self.base+'/datasets',files={'file':('orders.csv',data,'text/csv')},
            data={'dataset_family':'orders','currency':'CNY','timezone':'Asia/Shanghai','mapping':'{}'})
        self.assertEqual(response.status_code,201,response.text)
        self.dataset=response.json()['dataset_version']
        self.assertEqual(response.json()['field_semantics_candidates'],6)

    def cleanup_project(self):
        with self.repository.connection() as connection:
            connection.execute('DELETE FROM datalab_artifacts WHERE task_id IN (SELECT task_id FROM datalab_tasks WHERE project_id=%s)',(self.project,))
            for table in ('datalab_tasks','datalab_memories','datalab_datasets','datalab_projects'):
                connection.execute(f'DELETE FROM {table} WHERE project_id=%s',(self.project,))

    def wait_task(self,identifier,states):
        until=time.monotonic()+45
        while time.monotonic()<until:
            response=self.client.get(self.base+'/tasks/'+identifier)
            self.assertEqual(response.status_code,200,response.text)
            value=response.json()
            if value['state'] in states:
                return value
            if value['state'] in {'FAILED','CANCELLED'}:
                self.fail(str(value))
            time.sleep(.2)
        self.fail('任务未在时限内进入预期状态')

    def confirm_rule(self):
        response=self.client.post(self.base+'/memories',json={'dataset_version':self.dataset,'name':'成交金额',
            'definition':'仅计算已支付且未退款订单','included_statuses':['paid']})
        self.assertEqual(response.status_code,201,response.text)
        memory_id=response.json()['memory_id']
        response=self.client.post(self.base+'/memories/'+memory_id+'/confirm',
            json={'dataset_version':self.dataset,'expected_current_id':None})
        self.assertEqual(response.status_code,200,response.text)
        return memory_id

    def test_confirmation_queue_report_download_and_refresh(self):
        response=self.client.post(self.base+'/tasks',json={'dataset_version':self.dataset,'question':'按渠道汇总金额',
                                                           'options':{'kind':'ranking','group_by':'channel'}})
        self.assertEqual(response.status_code,202,response.text)
        task_id=response.json()['task_id']
        waiting=self.wait_task(task_id,{'WAITING_CONFIRMATION'})
        self.assertEqual(waiting['snapshot']['budget']['calls'],0)
        memory_id=self.confirm_rule()
        response=self.client.post(self.base+'/tasks/'+task_id+'/confirm',json={'memory_id':memory_id})
        self.assertEqual(response.status_code,200,response.text)
        complete=self.wait_task(task_id,{'SUCCEEDED'})
        self.assertEqual(complete['snapshot']['verifications'][-1]['reference']['total']['amount_cents'],35000)
        self.assertEqual(complete['snapshot']['budget']['tokens'],0)
        # trace 随任务快照落库：等待确认阶段没有调用，恢复后依次是三个角色；只有字段路径与指纹，没有内容。
        trace=complete['snapshot']['trace']
        self.assertEqual([item['role'] for item in trace],['Planner','Analyst','Reviewer'])
        self.assertTrue(all(item['outcome']=='ok' and len(item['handoff_sha256'])==64 for item in trace))
        self.assertNotIn('profile.dataset_family',trace[0]['handoff_fields'])
        self.assertEqual(complete['request']['mode'],'guided-demo')
        artifact=next(item for item in complete['artifacts'] if item['name']=='结果表.csv')
        download=self.client.get(self.base+'/artifacts/'+artifact['artifact_id'])
        self.assertEqual(download.status_code,200)
        self.assertIn('22000',download.content.decode('utf-8-sig'))
        # 两条路径共用的派生产物：模板说明标明口径版本与期间，图表可内联查看与下载。
        narrative=complete['narrative']
        self.assertIn(complete['snapshot']['metric']['version'],narrative['text'])
        self.assertIn('自然流量',narrative['text'])
        charts=sorted(item['name'] for item in complete['artifacts'] if item['name'].endswith('.png'))
        self.assertEqual(charts,['图表-1.png','图表-2.png'])
        chart=next(item for item in complete['artifacts'] if item['name']=='图表-1.png')
        inline=self.client.get(self.base+'/artifacts/'+chart['artifact_id']+'?inline=true')
        self.assertEqual(inline.headers['content-type'],'image/png')
        self.assertTrue(inline.content.startswith(b'\x89PNG'))
        self.assertEqual(self.client.get(self.base+'/artifacts/'+artifact['artifact_id']+'?inline=true').headers['content-type'],
                         'application/octet-stream')
        fields=[item for item in self.client.get(self.base+'/memories',params={'dataset_version':self.dataset}).json()
                if item['kind']=='field_semantics']
        self.assertEqual(sorted(item['name'] for item in fields),sorted(['order_id','payment_time','amount','status','channel','category']))
        self.assertTrue(all(item['status']=='pending' for item in fields))
        from datalab.api.app import create_app
        from fastapi.testclient import TestClient
        with TestClient(create_app(self.settings)) as refreshed:
            self.assertEqual(refreshed.get(self.base+'/tasks/'+task_id).json()['state'],'SUCCEEDED')
        # 重复分发不增加执行尝试，也不能覆盖结果。
        from celery import Celery
        publisher=Celery('test',broker=self.settings.broker_url)
        publisher.send_task('datalab.execute',args=[task_id])
        publisher.close()
        time.sleep(.5)
        self.assertEqual(len(self.client.get(self.base+'/tasks/'+task_id).json()['snapshot']['attempts']),1)

    def test_model_switch_and_missing_key(self):
        self.assertEqual(self.client.get('/api/model').json()['active'],'guided')
        response=self.client.put('/api/model',json={'provider':'deepseek'},headers={'X-Datalab-Client':''})
        self.assertEqual(response.status_code,403)
        response=self.client.put('/api/model',json={'provider':'deepseek'})
        self.assertEqual(response.status_code,200,response.text)
        state=response.json()
        self.assertEqual(state['active'],'deepseek')
        self.assertFalse(any(item['configured'] for item in state['providers']))
        self.assertNotIn('api_key',response.text)
        self.assertEqual(self.client.get('/api/health').json()['provider'],'deepseek')
        # 未配置密钥时拒绝创建模型任务，不静默降级为固定演示。
        response=self.client.post(self.base+'/tasks',json={'dataset_version':self.dataset,'question':'按渠道汇总金额'})
        self.assertEqual(response.status_code,400)
        self.assertIn('API Key',response.json()['detail'])
        self.assertEqual(self.client.put('/api/model',json={'provider':'gpt'}).status_code,422)
        self.client.put('/api/model',json={'provider':'mimo'})
        with patch.dict(os.environ,{'DATALAB_MIMO_API_KEY':'test-key-not-real'}):
            response=self.client.post(self.base+'/tasks',json={'dataset_version':self.dataset,'question':'按渠道汇总金额'})
        self.assertEqual(response.status_code,202,response.text)
        # 未确认口径时任务停在确认点，不会发出模型请求；记录创建时的模型供应商。
        waiting=self.wait_task(response.json()['task_id'],{'WAITING_CONFIRMATION'})
        self.assertEqual(waiting['request']['model'],{'provider':'mimo','model':'mimo-v2.6-flash'})
        self.assertEqual((waiting['snapshot']['budget']['calls'],waiting['snapshot']['budget']['max_tokens']),(0,150000))

    def test_project_scope_and_cross_site_write(self):
        other=str(uuid4())
        response=self.client.post('/api/projects/'+other+'/tasks',json={'dataset_version':self.dataset,'question':'不应访问'})
        self.assertEqual(response.status_code,404)
        response=self.client.post('/api/projects',json={'name':'不应创建'},headers={'Origin':'https://evil.example'})
        self.assertEqual(response.status_code,403)
        response=self.client.post('/api/projects',json={'name':'不应创建'},headers={'X-Datalab-Client':''})
        self.assertEqual(response.status_code,403)

    def test_invalid_csv_and_plan_rejected(self):
        response=self.client.post(self.base+'/datasets',files={'file':('bad.csv',b'bad')},
            data={'dataset_family':'orders','currency':'CNY','timezone':'Asia/Shanghai'})
        self.assertEqual(response.status_code,400)
        response=self.client.post(self.base+'/tasks',json={'dataset_version':self.dataset,'question':'对比',
                                                          'options':{'kind':'comparison'}})
        self.assertEqual(response.status_code,400)

    def test_single_claim_cancel_and_stale_lease(self):
        from datalab.orchestration.workflow import Task
        task=Task(self.project,self.dataset,'不派发的领取测试')
        self.repository.create_task(task,{'options':{},'mode':'guided-demo'})
        first=self.repository.claim(task.task_id)
        self.assertIsNotNone(first)
        self.assertIsNone(self.repository.claim(task.task_id))
        self.repository.cancel(task.task_id,self.project)
        self.assertTrue(self.repository.heartbeat(task.task_id,first[0]))
        with self.repository.connection() as connection:
            connection.execute("UPDATE datalab_tasks SET lease_until=now()-interval '1 minute' WHERE task_id=%s",(task.task_id,))
        self.repository.pending_ids()
        self.assertEqual(self.repository.get_task(task.task_id)['state'],'FAILED')
        with self.assertRaises(RuntimeError):
            self.repository.save(task,first[0])

    def test_cancel_before_final_save_wins(self):
        from datalab.orchestration.workflow import State, Task
        task=Task(self.project,self.dataset,'并发取消边界')
        self.repository.create_task(task,{'options':{},'mode':'guided-demo'})
        token,_=self.repository.claim(task.task_id)
        self.repository.cancel(task.task_id,self.project)
        task.state=State.SUCCEEDED
        self.repository.save(task,token,release=True)
        row=self.repository.get_task(task.task_id,self.project)
        self.assertEqual(row['state'],'CANCELLED')
        self.assertEqual(row['snapshot']['state'],'CANCELLED')
    def test_memory_conflict_is_http_409(self):
        self.confirm_rule()
        pending=self.client.post(self.base+'/memories',json={'dataset_version':self.dataset,'name':'成交金额',
            'definition':'修订口径','included_statuses':['paid','refunded']}).json()['memory_id']
        response=self.client.post(self.base+'/memories/'+pending+'/confirm',
                                  json={'dataset_version':self.dataset,'expected_current_id':None})
        self.assertEqual(response.status_code,409)
