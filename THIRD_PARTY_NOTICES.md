# Third-party Notices

## 设计参考：Poirot

- 项目：Poirot，`https://github.com/HezaoHezao/poirot`
- 许可：MIT，Copyright (c) 2026 Poirot Authors
- 关系：OrderProof 在角色职责拆分、角色运行时与容器执行接口等方面参考了 Poirot 的设计思路。本仓库不包含 Poirot 源码、技能或资源文件，也不在运行时依赖 Poirot。

## 运行依赖

以下依赖由包管理器在安装时获取，不随本仓库分发，各自遵循其上游许可证。

| 组件 | 用途 | 许可 |
| --- | --- | --- |
| FastAPI、Uvicorn、python-multipart | HTTP 接口与上传 | MIT / BSD-3-Clause / Apache-2.0 |
| Celery、redis-py | 任务派发 | BSD-3-Clause / MIT |
| psycopg | PostgreSQL 访问 | LGPL-3.0（动态链接使用，未修改） |
| httpx | 模型接口调用 | BSD-3-Clause |
| matplotlib | 图表渲染 | Matplotlib License（PSF 风格） |
| React、React DOM | 前端界面 | MIT |
| Vite、TypeScript、Playwright | 前端构建与浏览器测试（开发依赖） | MIT / Apache-2.0 / Apache-2.0 |

完整依赖与版本见 `pyproject.toml` 与 `frontend/package-lock.json`。

## 数据

`examples/orders.csv` 与评测脚本使用的订单数据均为本项目生成的模拟数据（`evals/run_suite.py` 种子 20260926，`evals/model_compare.py` 种子 20260928），不含真实客户信息，也不来自第三方数据集。
