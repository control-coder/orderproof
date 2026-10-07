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
    # 根因：CI 没有 .env，worker 为模型任务读取配置时直接报“缺少配置文件”；本机有 .env 所以一直没暴露。
    # 这里给 worker 一份由 .env.example 生成的独立配置和假密钥：本机与 CI 行为一致，也绝不会用到真实密钥。
    env_file=temporary/'worker.env'
    env_file.write_text((root/'.env.example').read_text(encoding='utf-8'),encoding='utf-8')
    env=dict(os.environ,DATALAB_APP_TESTS='1',PYTHONDONTWRITEBYTECODE='1',DATALAB_ENV_FILE=str(env_file),
             DATALAB_MIMO_API_KEY='test-key-not-real',DATALAB_DEEPSEEK_API_KEY='')
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
