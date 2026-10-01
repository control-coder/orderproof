"""本机受控演示统一启动器；仅监听回环，子进程异常退出时一起停止。"""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def main():
    root=Path(__file__).resolve().parents[1]
    logs=root/'artifacts/local/logs'
    logs.mkdir(parents=True,exist_ok=True)
    if not (root/'frontend/dist/index.html').is_file():
        raise RuntimeError('缺少前端构建，请先执行 npm run build --prefix frontend')
    children=[]
    handles=[]
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
    commands={
        'api':[sys.executable,'-B','-m','uvicorn','datalab.api.app:create_app','--factory','--host','127.0.0.1','--port','18080'],
        'worker':[sys.executable,'-B','-m','celery','-A','datalab.orchestration.worker:celery_app','worker','--pool=solo','--concurrency=1','--loglevel=WARNING','--without-gossip','--without-mingle'],
        'dispatcher':[sys.executable,'-B','-m','celery','-A','datalab.orchestration.worker:celery_app','beat','--schedule',str(root/'artifacts/local/celerybeat-schedule'),'--loglevel=WARNING'],
    }
    def stop(signum,frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:
        for name,command in commands.items():
            log=(logs/(name+'.log')).open('a',encoding='utf-8')
            handles.append(log)
            children.append(subprocess.Popen(command,cwd=root,env=env,stdout=log,stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0))
        print('OrderProof 本地入口：http://127.0.0.1:18080；三角色模型见 .env，python -B scripts/switch_model.py 可切换',flush=True)
        while True:
            if any(child.poll() is not None for child in children):
                raise RuntimeError('服务子进程退出，请检查 artifacts/local/logs；未自动更换端口或降级执行')
            time.sleep(1)
    except KeyboardInterrupt:
        print('停止本次启动的应用进程；数据库和队列由 local_services.py 管理')
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        for log in handles:
            log.close()


if __name__=='__main__':
    main()
