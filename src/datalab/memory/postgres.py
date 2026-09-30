"""短事务、按范围串行确认和数据库唯一约束组成持久化边界。"""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
from importlib.resources import files
import json

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from datalab.memory.service import MemoryEntry, Scope


class PostgresMemoryRepository:
    def __init__(self, dsn: str):
        # DSN 仅保存在服务对象中，不出现在 repr、日志或模型上下文。
        self._dsn = dsn

    def initialize(self):
        with psycopg.connect(self._dsn, connect_timeout=5) as connection:
            connection.execute(files("datalab.memory").joinpath("schema.sql").read_text(encoding="utf-8"))

    @contextmanager
    def transaction(self, scope: Scope):
        key = json.dumps(asdict(scope), sort_keys=True).encode()
        lock_id = int.from_bytes(hashlib.sha256(key).digest()[:8], "big", signed=True)
        with psycopg.connect(self._dsn, connect_timeout=5, row_factory=dict_row) as connection:
            connection.execute("SET LOCAL statement_timeout = '10s'")
            connection.execute("SET LOCAL lock_timeout = '5s'")
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock_id,))
            yield PostgresMemoryTransaction(connection, scope)


class PostgresMemoryTransaction:
    def __init__(self, connection, scope: Scope):
        self.connection = connection
        self.scope = scope

    def list_entries(self) -> list[MemoryEntry]:
        rows = self.connection.execute(
            "SELECT * FROM datalab_memories WHERE project_id=%s AND dataset_family=%s AND schema_signature=%s ORDER BY version",
            (self.scope.project_id, self.scope.dataset_family, self.scope.schema_signature)).fetchall()
        entries = []
        for row in rows:
            row["memory_id"] = str(row["memory_id"])
            row["supersedes_id"] = str(row["supersedes_id"]) if row["supersedes_id"] else None
            row["effective_from"] = row["effective_from"].isoformat() if row["effective_from"] else None
            row["invalidated_at"] = row["invalidated_at"].isoformat() if row["invalidated_at"] else None
            entries.append(MemoryEntry(**row))
        return entries

    def insert(self, entry: MemoryEntry):
        if entry.scope != self.scope:
            raise PermissionError("不能写入其他记忆范围")
        self.connection.execute(
            "INSERT INTO datalab_memories (memory_id,project_id,dataset_family,schema_signature,kind,name,content,status,version,source_run_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (entry.memory_id, entry.project_id, entry.dataset_family, entry.schema_signature, entry.kind,
             entry.name, Jsonb(entry.content), entry.status, entry.version, entry.source_run_id))

    def update_status(self, entry: MemoryEntry):
        if entry.scope != self.scope:
            raise PermissionError("不能修改其他记忆范围")
        result = self.connection.execute(
            "UPDATE datalab_memories SET status=%s,confirmed_by=%s,effective_from=%s,supersedes_id=%s,invalidated_by=%s,invalidated_at=%s "
            "WHERE memory_id=%s AND project_id=%s AND dataset_family=%s AND schema_signature=%s",
            (entry.status, entry.confirmed_by, entry.effective_from, entry.supersedes_id, entry.invalidated_by, entry.invalidated_at, entry.memory_id,
             self.scope.project_id, self.scope.dataset_family, self.scope.schema_signature))
        if result.rowcount != 1:
            raise KeyError("记忆不存在或范围不符")
