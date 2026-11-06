# 可选 worker 入口

需要 PLIP 或已获许可的 PyRosetta 时，按 [WSL worker 指南](../docs/wsl_worker.md) 配置和连接。`python -m services.worker` 是保留的兼容入口；统一服务位于 `pymolplugins.worker.app`，后端位于 `pymolplugins.worker.backends`，客户端位于 `pymolplugins.remote`。普通审阅不需要 worker。
