-- PostgreSQL 是业务记忆的唯一权威存储；不创建平行 SQLite 真相。
CREATE TABLE IF NOT EXISTS datalab_memories (
    memory_id uuid PRIMARY KEY,
    project_id text NOT NULL,
    dataset_family text NOT NULL,
    schema_signature text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('metric', 'field_semantics', 'display_preference', 'summary')),
    name text NOT NULL,
    content jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('pending', 'confirmed', 'superseded', 'invalidated', 'recorded')),
    version integer NOT NULL CHECK (version > 0),
    source_run_id text NOT NULL,
    confirmed_by text,
    effective_from timestamptz,
    supersedes_id uuid REFERENCES datalab_memories(memory_id),
    invalidated_by text,
    invalidated_at timestamptz,
    CHECK (status <> 'confirmed' OR (confirmed_by IS NOT NULL AND effective_from IS NOT NULL AND kind <> 'summary')),
    UNIQUE (project_id, dataset_family, schema_signature, kind, name, version)
);
-- 同名规则只有一个当前确认版本；确认替换在一个事务内完成。
CREATE UNIQUE INDEX IF NOT EXISTS datalab_memories_active
    ON datalab_memories (project_id, dataset_family, schema_signature, kind, name)
    WHERE status = 'confirmed';
CREATE INDEX IF NOT EXISTS datalab_memories_scope
    ON datalab_memories (project_id, dataset_family, schema_signature);
