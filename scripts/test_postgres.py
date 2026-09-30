"""在独占临时 PostgreSQL 中运行真实记忆验收，不读现有凭据。"""
import os
import secrets
import subprocess
import sys
import time
from uuid import uuid4


def main():
    name = 'datalab-test-pg-' + uuid4().hex[:12]
    password = secrets.token_urlsafe(32)
    environment = dict(os.environ, POSTGRES_PASSWORD=password)
    created = False
    try:
        # 仅使用已存在的小型数据库镜像，绑定随机回环端口，不连接已有卷。
        subprocess.run(['docker', 'run', '--detach', '--pull', 'never', '--name', name,
            '--label', 'datalab.integration=true', '-e', 'POSTGRES_PASSWORD',
            '-e', 'POSTGRES_USER=datalab', '-e', 'POSTGRES_DB=datalab_test',
            '-p', '127.0.0.1::5432', '--tmpfs', '/var/lib/postgresql/data:rw,size=256m',
            'postgres:17-alpine'], env=environment, check=True, stdout=subprocess.DEVNULL, timeout=30)
        created = True
        for _ in range(30):
            ready = subprocess.run(['docker', 'exec', name, 'pg_isready', '-U', 'datalab', '-d', 'datalab_test'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('测试 PostgreSQL 未就绪')
        port = subprocess.check_output(['docker', 'port', name, '5432'], text=True, timeout=5).strip().rsplit(':', 1)[1]
        env = dict(os.environ, DATALAB_POSTGRES_TESTS='1',
                   DATALAB_TEST_DATABASE_URL=f'postgresql://datalab:{password}@127.0.0.1:{port}/datalab_test')
        return subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests/datalab',
            '-p', 'test_memory_postgres.py', '-v'], env=env, timeout=120).returncode
    finally:
        if created:
            # 只移除本脚本创建的随机名称容器，不清理用户卷或其他容器。
            subprocess.run(['docker', 'rm', '--force', name], check=True, stdout=subprocess.DEVNULL, timeout=20)


if __name__ == '__main__':
    raise SystemExit(main())
