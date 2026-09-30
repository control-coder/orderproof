# DataLab：可验证数据分析助手

面向电商订单 CSV 的独立工程，目标是完成口径确认、三角色协作、受限 Python 执行、独立数值核验和跨会话业务记忆。

> 当前状态：本机 Docker、PostgreSQL 与队列联动的固定三角色演示已通过真实集成和浏览器验收；MiMo/DeepSeek 模型适配与一键切换已实现（见 [模型配置](docs/operations/模型配置与切换.md)），两个模型已完成真实三角色调用与对照评测（见 [真实模型对照](docs/development/2026-09-28-真实模型对照评测.md)）。详见 [当前状态](docs/development/当前状态.md) 和 [四页面运行](docs/operations/四页面本地演示.md)。

## 开发环境与验证

仅使用 Python 3.12 的 conda `datalab` 环境，不使用 base 或额外的 `.venv`。

```powershell
conda activate datalab
python -c "import sys; print(sys.executable)"
python -m pip install --no-build-isolation -e .
python -B -m unittest discover -s tests/datalab -v
```

新机器可用 `conda env create -f environment.yml` 建立环境；已有环境不要重复创建。真实 Docker 和 PostgreSQL 验收另有专门入口；本机四页面说明见 [运行说明](docs/operations/四页面本地演示.md)。固定演示不调用模型；20+8+8 固定种子回归和本机十万行汇总已通过，真实模型单/三角色与有/无 Memory 对照见 [真实模型对照](docs/development/2026-09-28-真实模型对照评测.md)，固定演示详见 [评测记录](docs/development/2026-09-26-固定种子评测.md) 与 [部署复现](docs/operations/部署复现与验收.md)。

## 工程组织

- `src/datalab/`：自有 Python 业务包，通过 `datalab` 导入，不依赖本地参考源码。
- `tests/datalab/`：领域与工程结构测试。
- `examples/`：公开模拟订单和演示输入。
- `scripts/`：构建和辅助脚本；含专用镜像、固定演示、服务启动与真实集成脚本。
- `docs/`：设计、运行、开发记录和许可证资料，按职责分类。
- `frontend/`：React 四页面；`evals/` 在评测轮次有实际内容时创建。
- `artifacts/`、`.tmp/`：忽略的运行产物和临时文件，按需创建。

详细约定见 [AGENTS.md](AGENTS.md)、[项目实施计划](项目实施计划.md)、[目录职责](docs/目录职责.md) 和 [贡献说明](docs/development/贡献说明.md)。

## 参考与版权

参考源码和原始资料仅供本地研读，已排除在版本跟踪、包发现和容器构建范围之外。当前工程不包装或启动参考项目的应用，不批量改名冒充原创；必要的第三方版权与许可证保留，见 `LICENSE` 和 [第三方许可说明](THIRD_PARTY_LICENSES.md)。
