"""启动真实 Celery worker 验证应用链路；测试期间不运行付费模型。"""
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    root=Path(__file__).resolve().parents[1]
    temporary=(root/'.tmp').resolve()
    temporary.mkdir(exist_ok=True)
    # worker 是独立进程，看不到测试里打的补丁。给它一个假密钥：模型任务停在确认点、不会发出请求，
    # 同时让有无本机 .env 的环境（本机与 CI）行为一致，也保证测试不会用到真实密钥。
    env=dict(os.environ,DATALAB_APP_TESTS='1',PYTHONDONTWRITEBYTECODE='1',DATALAB_MIMO_API_KEY='test-key-not-real')
    with (temporary/'integration-worker.log').open('w',encoding='utf-8') as log:
        process=subprocess.Popen([sys.executable,'-B','-m','celery','-A','datalab.orchestration.worker:celery_app',
            'worker','--pool=solo','--concurrency=1','--loglevel=WARNING','--without-gossip','--without-mingle'],
            cwd=root,env=env,stdout=log,stderr=log)
        try:
            time.sleep(3)
            if process.poll() is not None:
                raise RuntimeError('测试 worker 未启动，请查忽略目录中的测试日志')
            return subprocess.run([sys.executable,'-B','-m','unittest','discover','-s','tests/datalab',
                '-p','test_app_integration.py','-v'],env=env,cwd=root,timeout=180).returncode
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__=='__main__':
    raise SystemExit(main())
