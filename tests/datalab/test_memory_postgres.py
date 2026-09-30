"""显式启用的真实 PostgreSQL 验收；不以模拟连接冒充数据库事务。"""
import os
import unittest
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

from datalab.memory.service import MemoryConflict, MemoryService, Scope


@unittest.skipUnless(os.environ.get('DATALAB_POSTGRES_TESTS') == '1', '需显式启用真实 PostgreSQL 集成')
class PostgresMemoryTests(unittest.TestCase):
    def setUp(self):
        from datalab.memory.postgres import PostgresMemoryRepository
        # 仅在用户显式启用测试后读取安全配置；不打印连接字符串。
        self.dsn = os.environ['DATALAB_TEST_DATABASE_URL']
        self.repository = PostgresMemoryRepository(self.dsn)
        self.repository.initialize()
        self.scope = Scope('test-' + uuid4().hex, 'orders', 'schema-v1')
        self.service = MemoryService(self.repository)
        self.addCleanup(self.clean_own_project)

    def clean_own_project(self):
        import psycopg
        if not self.scope.project_id.startswith('test-'):
            raise RuntimeError('测试清理范围错误')
        with psycopg.connect(self.dsn, connect_timeout=5) as connection:
            connection.execute('DELETE FROM datalab_memories WHERE project_id=%s', (self.scope.project_id,))

    def candidate(self):
        return self.service.propose(self.scope, 'metric', '成交金额',
            {'definition': '已支付订单金额', 'included_statuses': ['paid']}, 'test-session')

    def test_real_restart_reuse_and_history(self):
        from datalab.memory.postgres import PostgresMemoryRepository
        old = self.candidate()
        self.service.confirm(self.scope, old.memory_id, actor='test-user', expected_current_id=None)
        next_service = MemoryService(PostgresMemoryRepository(self.dsn))
        self.assertEqual(next_service.retrieve(self.scope)[0].memory_id, old.memory_id)
        new = self.candidate()
        next_service.confirm(self.scope, new.memory_id, actor='test-user', expected_current_id=old.memory_id)
        self.assertEqual([entry.status for entry in next_service.history(self.scope)], ['superseded', 'confirmed'])

    def test_real_concurrent_confirmation_has_one_winner(self):
        first, second = self.candidate(), self.candidate()
        def confirm(entry):
            try:
                self.service.confirm(self.scope, entry.memory_id, actor='test-user', expected_current_id=None)
                return True
            except MemoryConflict:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(confirm, (first, second)))
        self.assertEqual(sum(outcomes), 1)
        self.assertEqual(len(self.service.retrieve(self.scope)), 1)
