"""CI 专用：按环境变量生成本机运行配置并初始化表结构。

只接受 CI 服务容器的一次性连接串，不读取、不写入任何长期凭据。
"""
import json
import os
from pathlib import Path

from datalab.memory.postgres import PostgresMemoryRepository
from datalab.storage.repository import Repository

ROOT = Path(__file__).resolve().parents[1]


def main():
    database_url = os.environ['DATALAB_CI_DATABASE_URL']
    broker_url = os.environ['DATALAB_CI_BROKER_URL']
    target = Path(os.environ['DATALAB_CONFIG'])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({'database_url': database_url, 'broker_url': broker_url,
                                  'artifact_root': str(ROOT / 'artifacts/app')}), encoding='utf-8')
    PostgresMemoryRepository(database_url).initialize()
    Repository(database_url).initialize()
    print('CI 运行配置已生成，数据库表已初始化')


if __name__ == '__main__':
    main()
