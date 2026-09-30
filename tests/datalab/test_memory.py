"""连续会话、范围和版本测试；内存仓库仅在测试文件定义。"""
from contextlib import contextmanager
from copy import deepcopy
from threading import RLock
import unittest

from datalab.memory.service import FIELD_MEANINGS, MemoryConflict, MemoryService, Scope


class FakeMemoryTransaction:
    def __init__(self, entries, scope):
        self.entries = entries
        self.scope = scope

    def list_entries(self):
        return [deepcopy(item) for item in self.entries.values() if item.scope == self.scope]

    def insert(self, entry):
        assert entry.scope == self.scope
        self.entries[entry.memory_id] = deepcopy(entry)

    def update_status(self, entry):
        assert entry.scope == self.scope
        self.entries[entry.memory_id] = deepcopy(entry)


class FakeMemoryRepository:
    """模拟事务回滚，不导出给生产业务入口。"""

    def __init__(self):
        self.entries = {}
        self.lock = RLock()

    @contextmanager
    def transaction(self, scope):
        with self.lock:
            staged = deepcopy(self.entries)
            yield FakeMemoryTransaction(staged, scope)
            self.entries = staged


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.repository = FakeMemoryRepository()
        self.service = MemoryService(self.repository)
        self.scope = Scope('demo', 'paid-orders', 'schema-v1')

    def propose(self, statuses=None):
        return self.service.propose(self.scope, 'metric', '成交金额',
            {'included_statuses': statuses or ['paid'], 'definition': '按确认订单状态计算金额'}, 'session-1')

    def confirmed(self):
        candidate = self.propose()
        return self.service.confirm(self.scope, candidate.memory_id, actor='user:test', expected_current_id=None)

    def test_pending_never_used_implicitly(self):
        entry = self.propose()
        self.assertEqual(entry.status, 'pending')
        self.assertEqual(self.service.retrieve(self.scope), [])
        with self.assertRaises(ValueError):
            self.service.metric_snapshot(entry)

    def test_confirmation_reused_by_next_session_with_source(self):
        entry = self.confirmed()
        next_session = MemoryService(self.repository)
        selected = next_session.retrieve(self.scope, keyword='成交', kind='metric')
        self.assertEqual(len(selected), 1)
        snapshot = next_session.metric_snapshot(selected[0])
        self.assertIn('session-1', snapshot.source)
        self.assertIn('user:test', snapshot.source)
        self.assertEqual(snapshot.version, f'{entry.memory_id}:v1')

    def test_revision_preserves_old_report_snapshot(self):
        old = self.confirmed()
        frozen = self.service.metric_snapshot(old)
        candidate = self.propose(['paid', 'refunded'])
        current = self.service.confirm(self.scope, candidate.memory_id, actor='user:test', expected_current_id=old.memory_id)
        self.assertEqual(current.version, 2)
        self.assertEqual(current.supersedes_id, old.memory_id)
        self.assertEqual(frozen.included_statuses, ('paid',))
        history = self.service.history(self.scope)
        self.assertEqual([entry.status for entry in history], ['superseded', 'confirmed'])
        self.assertEqual(history[0].content['included_statuses'], ['paid'])

    def test_same_name_conflict_requires_explicit_current_id(self):
        old = self.confirmed()
        candidate = self.propose(['paid', 'refunded'])
        with self.assertRaises(MemoryConflict):
            self.service.confirm(self.scope, candidate.memory_id, actor='user:test', expected_current_id=None)
        self.assertEqual(self.service.retrieve(self.scope)[0].memory_id, old.memory_id)

    def test_stale_confirmation_cannot_overwrite_new_rule(self):
        old = self.confirmed()
        first, second = self.propose(['paid', 'refunded']), self.propose(['paid', 'cancelled'])
        newest = self.service.confirm(self.scope, second.memory_id, actor='user:test', expected_current_id=old.memory_id)
        with self.assertRaises(MemoryConflict):
            self.service.confirm(self.scope, first.memory_id, actor='user:test', expected_current_id=old.memory_id)
        with self.assertRaises(MemoryConflict):
            self.service.confirm(self.scope, first.memory_id, actor='user:test', expected_current_id=newest.memory_id)

    def test_cross_project_cannot_read_or_confirm(self):
        entry = self.confirmed()
        other = Scope('other-project', 'paid-orders', 'schema-v1')
        self.assertEqual(self.service.retrieve(other), [])
        with self.assertRaises(KeyError):
            self.service.confirm(other, entry.memory_id, actor='user:test', expected_current_id=None)

    def test_same_schema_different_semantics_needs_confirmation(self):
        self.confirmed()
        other = Scope('demo', 'refunded-orders', 'schema-v1')
        self.assertEqual(self.service.retrieve(other), [])
        changed_schema = Scope('demo', 'paid-orders', 'schema-v2')
        self.assertEqual(self.service.retrieve(changed_schema), [])

    def test_invalidated_rule_is_not_reused(self):
        entry = self.confirmed()
        self.service.invalidate(self.scope, entry.memory_id, actor='user:test')
        self.assertEqual(self.service.retrieve(self.scope), [])
        self.assertEqual(self.service.history(self.scope)[0].status, 'invalidated')
        self.assertEqual(self.service.history(self.scope)[0].invalidated_by, 'user:test')

    def test_summary_does_not_upgrade_after_repeated_access(self):
        summary = self.service.propose(self.scope, 'summary', '历史分析', {'text': '可能只计 paid'}, 'session-1')
        for _ in range(3):
            self.assertEqual(self.service.retrieve(self.scope), [])
        with self.assertRaises(ValueError):
            self.service.confirm(self.scope, summary.memory_id, actor='user:test', expected_current_id=None)

    def test_preference_requires_explicit_confirmation(self):
        pending = self.service.propose(self.scope, 'display_preference', '显示金额', {'digits': 2}, 'settings-action')
        self.assertEqual(self.service.retrieve(self.scope), [])
        self.service.confirm(self.scope, pending.memory_id, actor='user:test', expected_current_id=None)
        self.assertEqual(self.service.retrieve(self.scope, kind='display_preference')[0].content, {'digits': 2})

    def test_returned_content_cannot_mutate_authority(self):
        confirmed = self.confirmed()
        confirmed.content['included_statuses'].append('cancelled')
        self.assertEqual(self.service.retrieve(self.scope)[0].content['included_statuses'], ['paid'])


class FieldSemanticsTests(unittest.TestCase):
    """导入时写入的字段语义候选：待确认、去重、可按版本确认，不改变指标口径检索。"""

    def setUp(self):
        self.service = MemoryService(FakeMemoryRepository())
        self.scope = Scope('demo', 'paid-orders', 'schema-v1')
        self.identity = {name: name for name in FIELD_MEANINGS}

    def test_import_candidates_are_pending_and_not_repeated(self):
        created = self.service.suggest_field_semantics(self.scope, self.identity, 'dataset-import:v1')
        self.assertEqual({entry.name for entry in created}, set(FIELD_MEANINGS))
        self.assertTrue(all(entry.status == 'pending' and entry.kind == 'field_semantics' for entry in created))
        self.assertEqual(self.service.retrieve(self.scope), [])
        self.assertEqual(self.service.suggest_field_semantics(self.scope, self.identity, 'dataset-import:v2'), [])

    def test_changed_mapping_creates_new_version_and_confirm_supersedes(self):
        first = {entry.name: entry for entry in self.service.suggest_field_semantics(self.scope, self.identity, 'import:1')}
        confirmed = self.service.confirm(self.scope, first['payment_time'].memory_id, actor='local-user', expected_current_id=None)
        mapped = dict(self.identity, payment_time='支付时间')
        second = self.service.suggest_field_semantics(self.scope, mapped, 'import:2')
        self.assertEqual([(entry.name, entry.version, entry.content['source_column']) for entry in second],
                         [('payment_time', 2, '支付时间')])
        with self.assertRaises(MemoryConflict):
            self.service.confirm(self.scope, second[0].memory_id, actor='local-user', expected_current_id=None)
        self.service.confirm(self.scope, second[0].memory_id, actor='local-user', expected_current_id=confirmed.memory_id)
        current = self.service.retrieve(self.scope, kind='field_semantics')
        self.assertEqual([(entry.name, entry.version) for entry in current], [('payment_time', 2)])
        self.assertEqual(self.service.retrieve(self.scope, kind='metric'), [])

    def test_rejects_unknown_field_or_missing_meaning(self):
        for content in ({'field': 'profit', 'source_column': 'profit', 'meaning': '利润'},
                        {'field': 'amount', 'source_column': 'amount', 'meaning': ' '}):
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.service.propose(self.scope, 'field_semantics', content['field'], content, 'import:1')

    def test_scope_isolated(self):
        self.service.suggest_field_semantics(self.scope, self.identity, 'import:1')
        self.assertEqual(self.service.history(Scope('other', 'paid-orders', 'schema-v1')), [])
