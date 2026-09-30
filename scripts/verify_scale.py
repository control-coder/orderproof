"""用固定生成的模拟订单验证十万行目标，不接触客户数据。"""
import csv
import json
from pathlib import Path
import time
from uuid import uuid4

from datalab.contracts import AnalysisPlan
from datalab.datasets.service import DatasetStore, MAX_BYTES, MAX_ROWS
from datalab.execution.pipeline import run_fixed
from datalab.execution.runner import DockerRunner


def main():
    root=Path(__file__).resolve().parents[1]
    scratch=root/'artifacts/scale'
    scratch.mkdir(parents=True,exist_ok=True)
    sample=scratch/'generated.csv'
    started=time.perf_counter()
    with sample.open('w',newline='',encoding='utf-8') as handle:
        writer=csv.writer(handle)
        writer.writerow(['order_id','payment_time','amount','status','channel','category'])
        for index in range(MAX_ROWS):
            writer.writerow([f'scale-{index:06d}','2026-09-01T12:00:00','1.50','paid',
                             'channel-'+'x'*120,'category-A'])
    size=sample.stat().st_size
    if size>MAX_BYTES:
        raise RuntimeError('固定样例超过导入上限，请修改模拟字段长度')
    store=DatasetStore(scratch/'datasets')
    project=str(uuid4())
    version=store.import_csv(sample.read_bytes(),project_id=project,dataset_family='规模模拟订单',
                             currency='CNY',timezone='Asia/Shanghai')
    imported=time.perf_counter()
    plan=AnalysisPlan(version.dataset_version,'trusted-scale-eval-paid-v1',kind='summary')
    report=run_fixed(DockerRunner(store,scratch/'runs'),plan,project)
    elapsed=time.perf_counter()-started
    reference=report.get('verification',{}).get('reference',{})
    passed=(version.row_count==MAX_ROWS and report['status']=='SUCCEEDED' and
            reference.get('total')=={'order_count':MAX_ROWS,'amount_cents':MAX_ROWS*150})
    output={'rows':version.row_count,'input_bytes':size,'limit_bytes':MAX_BYTES,
            'import_seconds':round(imported-started,3),'total_seconds':round(elapsed,3),
            'execution_seconds':report['execution']['elapsed_seconds'],
            'status':report['status'],'passed':passed}
    (scratch/'scale-result.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(output,ensure_ascii=False,indent=2))
    if not passed:raise SystemExit('规模模拟任务未通过；查看忽略目录下的逐例报告')

if __name__=='__main__':main()
