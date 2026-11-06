# 分析入口与兼容路径

需要对齐、几何或相互作用命令时，先在 PyMOL 运行 `pp_load` 和 `pp_help`，再查 [命令示例](../docs/task_command_walkthrough.md)。本目录保留历史 Python 导入和 PyMOL `run` 路径；统一实现位于 `pymolplugins/alignment/`、`pymolplugins/geometry/` 和 `pymolplugins/interactions/`。新增代码使用这些统一入口；可选独立工具位于 `extras/`。
