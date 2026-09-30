"""为本项目启动独立回环数据库与队列；不连接或清理既有用户数据。"""
import argparse
import json
import os
import re
from pathlib import Path
import secrets
import subprocess
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'artifacts/local/runtime.json'


def docker(*args, env=None):
    return subprocess.check_output(['docker',*args],env=env,text=True,timeout=60,stderr=subprocess.DEVNULL).strip()


def main():
    parser = argparse.ArgumentParser(description='管理本项目独立本地服务')
    parser.add_argument('action',choices=['start','stop','status'])
    args = parser.parse_args()
    if not CONFIG.exists():
        if args.action != 'start':
            print('尚未创建本项目服务')
            return
        prefix = 'datalab-local-' + uuid4().hex[:10]
        password = secrets.token_urlsafe(32)
        env = dict(os.environ, POSTGRES_PASSWORD=password)
        pg, redis = prefix+'-pg', prefix+'-redis'
        docker('run','-d','--pull','never','--name',pg,'--label','datalab.local=true',
               '-e','POSTGRES_PASSWORD','-e','POSTGRES_USER=datalab','-e','POSTGRES_DB=datalab',
               '-p','127.0.0.1::5432','--mount',f'type=volume,source={pg}-data,target=/var/lib/postgresql/data',
               'postgres:17-alpine',env=env)
        pg_port = docker('port',pg,'5432').rsplit(':',1)[1]
        # 队列只含任务 UUID，回环访问；数据真相不依赖 Redis 持久化。
        docker('run','-d','--pull','never','--name',redis,'--label','datalab.local=true',
               '-p','127.0.0.1::6379','redis:7-alpine','redis-server','--save','','--appendonly','no')
        redis_port = docker('port',redis,'6379').rsplit(':',1)[1]
        config = {'postgres_container':pg,'redis_container':redis,
                  'database_url':f'postgresql://datalab:{password}@127.0.0.1:{pg_port}/datalab',
                  'broker_url':f'redis://127.0.0.1:{redis_port}/0','artifact_root':str(ROOT/'artifacts/app')}
        CONFIG.parent.mkdir(parents=True,exist_ok=True)
        CONFIG.write_text(json.dumps(config,indent=2),encoding='utf-8')
        # 配置含新生成的本机测试密码，不打印也不提交；Windows 继承当前用户目录 ACL。
        if os.name != 'nt':
            CONFIG.chmod(0o600)
    else:
        config=json.loads(CONFIG.read_text(encoding='utf-8'))
    names=[config['postgres_container'],config['redis_container']]
    if not all(name.startswith('datalab-local-') for name in names):
        raise RuntimeError('配置中的容器不属于本项目管理范围')
    if args.action == 'stop':
        for name in names:
            docker('stop',name)
        print('本项目服务已停止；数据库卷和配置保留')
        return
    if args.action == 'start':
        for name in names:
            if docker('inspect','--format','{{.State.Running}}',name) != 'true':
                docker('start',name)
        for _ in range(30):
            try:
                docker('exec',names[0],'pg_isready','-U','datalab','-d','datalab')
                break
            except subprocess.SubprocessError:
                time.sleep(1)
        else:
            raise RuntimeError('数据库未就绪')
        # Docker 重启后随机回环端口会变化；只替换端口，不改动已生成的密码。
        ports={'database_url':docker('port',names[0],'5432').rsplit(':',1)[1],
               'broker_url':docker('port',names[1],'6379').rsplit(':',1)[1]}
        refreshed={key:re.sub(r'@127\.0\.0\.1:\d+/|//127\.0\.0\.1:\d+/',
                              lambda match,port=port:match.group(0).rsplit(':',1)[0]+':'+port+'/',config[key])
                   for key,port in ports.items()}
        if any(refreshed[key]!=config[key] for key in ports):
            config.update(refreshed)
            CONFIG.write_text(json.dumps(config,indent=2),encoding='utf-8')
            print('服务端口已变化，已更新本机配置')
        from datalab.memory.postgres import PostgresMemoryRepository
        from datalab.storage.repository import Repository
        PostgresMemoryRepository(config['database_url']).initialize()
        Repository(config['database_url']).initialize()
    for name in names:
        print(name, docker('inspect','--format','{{.State.Status}}',name))
    print('配置已保存在忽略目录；不输出连接凭据')


if __name__ == '__main__':
    main()
