-- 首版数据结构；不包含运行数据库或凭据。
CREATE TABLE IF NOT EXISTS datalab_projects (
    project_id uuid PRIMARY KEY, name text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS datalab_datasets (
    dataset_version uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES datalab_projects(project_id),
    metadata jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(project_id,dataset_version)
);
CREATE TABLE IF NOT EXISTS datalab_tasks (
    task_id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES datalab_projects(project_id),
    dataset_version uuid NOT NULL, state text NOT NULL, request jsonb NOT NULL, snapshot jsonb NOT NULL,
    cancel_requested boolean NOT NULL DEFAULT false, lease_id uuid, lease_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(project_id,dataset_version) REFERENCES datalab_datasets(project_id,dataset_version)
);
CREATE INDEX IF NOT EXISTS datalab_tasks_project ON datalab_tasks(project_id,created_at);
CREATE INDEX IF NOT EXISTS datalab_tasks_queue ON datalab_tasks(state,lease_until);
CREATE TABLE IF NOT EXISTS datalab_artifacts (
    artifact_id uuid PRIMARY KEY, task_id uuid NOT NULL REFERENCES datalab_tasks(task_id),
    run_id uuid NOT NULL, name text NOT NULL, relative_path text NOT NULL,
    UNIQUE(task_id,run_id,name)
);

-- 可重复执行的增量列：旧库再运行一次 initialize 即可补齐，已有行取默认值。
-- fence 是每次领取单调递增的围栏令牌；产物索引用它拒绝旧 worker 的迟到写入。
ALTER TABLE datalab_tasks ADD COLUMN IF NOT EXISTS fence bigint NOT NULL DEFAULT 0;
ALTER TABLE datalab_tasks ADD COLUMN IF NOT EXISTS idempotency_key text;
ALTER TABLE datalab_tasks ADD COLUMN IF NOT EXISTS request_hash text;
CREATE UNIQUE INDEX IF NOT EXISTS datalab_tasks_idempotency ON datalab_tasks(project_id,idempotency_key) WHERE idempotency_key IS NOT NULL;
ALTER TABLE datalab_artifacts ADD COLUMN IF NOT EXISTS fence bigint NOT NULL DEFAULT 0;
