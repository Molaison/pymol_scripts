# 查找命令和脚本

在 PyMOL 运行 `pp_load`，再用 `pp_help` 或 `help 命令名` 查可用命令。按下表选择任务，随后查看对应目录；本页覆盖统一实现、兼容入口和独立工具。Host 条目需要 PyMOL；标为 WSL/Both 的核心可以在普通 Python 中运行或测试，但界面仍属于 Host。

| 要做什么 | 使用入口 | 结果与限制 |
| --- | --- | --- |
| 检查安装 | `pp_doctor check_worker=0` | 列出命令归属与环境；不用连接可选 worker |
| 浏览对象、group 或文件 | `pp_nav` | 高层对象/group 默认方向键导航，`pp_nav stop` 恢复；见 [操作指南](task_command_walkthrough.md) |
| 分组、接触、样式 | `pp_group`、`pp_contacts`、`pp_style_list` | 查看 [0.17.0 候选使用指南](01_group_interaction_styles.md)；局部接触不是能量结论 |
| 导入并标注结构 | Structure Annotator 菜单 | 查看 [审阅指南](structure_annotator_csv_walkthrough.md)，源表与标注分开保存 |
| 配对并处理结构 | `pp_batch` | 先预览输入对，再运行并生成独立输出；见 [批处理](batch_processing_specification.md#usage) |
| 请求 PLIP / PyRosetta | `pp_worker_health`、`pp_jobs` | 后端需单独安装，见 [worker](wsl_worker.md)；取消不保证终止后端计算 |

0.17.0 候选 manifest 包含 75 个 canonical 和 8 个兼容命令；具体参数以已安装版本的 `pp_help` 为准。canonical 是推荐入口，compatibility 保留旧调用；standalone 独立运行，legacy/experimental 不由 `pp_load` 自动加载。旧入口的存在不代表已纳入统一撤销或生命周期。

## 统一实现位置

| 领域 | 目录或文件 | 查找内容 |
| --- | --- | --- |
| 装载与帮助 | `pymolplugins/host/` | `pp_load`、manifest、`pp_help`、doctor、补全 |
| 任务命令 | `pymolplugins/tasks/` | 显示、导航、对齐、方向、接触、任务、撤销 |
| 目标与批处理 | `pymolplugins/targets/` | 目标解析、逐项命令、配对计划和批处理 UI |
| 导航与 group | `pymolplugins/navigation/`、`pymolplugins/grouping/`、`pymolplugins/follow/` | 状态、场景切换、分组和刚体跟随 |
| 标注与源 | `pymolplugins/annotation/` | 表格/目录/SSH、路径映射、缓存、状态、标注、scene groups |
| 序列、对齐、几何 | `pymolplugins/alignment/`、`pymolplugins/geometry/`、`pymolplugins/core/` | 序列映射、拟合、轴、膜、通用数据和数学 |
| 样式与相互作用 | `pymolplugins/styles/`、`pymolplugins/interactions/` | preset、快照、接触数据、渲染、PLIP 协议 |
| 远程分析 | `pymolplugins/remote/`、`pymolplugins/worker/` | Host 客户端、staging、HTTP 服务和可选后端 |

## 查兼容和独立功能

历史 Python 导入和 `run` 路径保留在 `analysis/`、`workflows/`、`utilities/`、`visualization/`、`services/`；不要仅因旧文件可运行就把它当成 canonical。独立和实验工具位于 `extras/`；`archive/` 为历史参考，不由 `pp_load` 加载。原有逐文件目录、每条状态和注意事项保留在 [详细脚本索引](06_reference_catalog_2026-11-06.md)。

## 查测试和证据

`tests/` 包含纯 Python 与 fake-PyMOL 测试；`tests/host/` 包含真实 PyMOL/Qt 检查。按 [Windows Host 验收](windows_host_acceptance.md) 选择相关边界，并把版本、结果和限制写入记录。旧版记录在 [历史索引](03_history_index.md)；旧通过不代表当前修改重新通过。

<details>
<summary>旧标题及详细条款索引</summary>

原地址保留为兼容入口；以下条目指向重写前的详细参考。

<a id="canonical-toolkit"></a>
- [Canonical toolkit](06_reference_catalog_2026-11-06.md#canonical-toolkit)
<a id="compatibility-adapters"></a>
- [Compatibility adapters](06_reference_catalog_2026-11-06.md#compatibility-adapters)
<a id="standalone-and-in-progress-tools"></a>
- [Standalone and in-progress tools](06_reference_catalog_2026-11-06.md#standalone-and-in-progress-tools)
<a id="examples-and-archive"></a>
- [Examples and archive](06_reference_catalog_2026-11-06.md#examples-and-archive)
<a id="tests-and-operational-documentation"></a>
- [Tests and operational documentation](06_reference_catalog_2026-11-06.md#tests-and-operational-documentation)

</details>
