# PymolPlugins

在 PyMOL 中查看、筛选和标注结构，批量处理结构对，并使用统一命令制作可视化。

[安装](#安装) · [第一次审阅](#第一次审阅) · [命令](#使用命令) · [指南](#选择指南)

## 安装

需要带 Qt 界面和 Plugin Manager 的桌面 PyMOL。普通审阅、批处理和命令不需要 WSL worker。

1. 从 [正式 Release](https://github.com/Molaison/pymol_scripts/releases/latest) 下载附件 `PymolPlugins-<version>.zip`。不要选择 GitHub 自动生成的 Source code ZIP，也不要解压插件包。
2. 在 PyMOL 选择 **Plugin → Plugin Manager → Install New Plugin**，选择下载的 ZIP。
3. 完全退出并重启 PyMOL。Plugin 菜单中出现 **PymolPlugins: Structure Annotator** 和 **PymolPlugins: Batch Processing**。

本工作树的文档包含尚未正式发布的 0.17.0 候选功能：场景 group 审阅、候选及派生产物整组切换、统一补全和六个样式。正式版以 [Release](https://github.com/Molaison/pymol_scripts/releases/latest) 为准；候选功能见 [操作指南](docs/01_group_interaction_styles.md) 和 [验收记录](docs/validation/02_toolkit_unification_2026-11-06.md)。

安装带更新器的版本后，可在 **Plugin → PymolPlugins: Updates** 检查正式版本。启动时后台检查默认开启，自动安装默认关闭；更新后仍需完全重启。

## 第一次审阅

1. 打开 **Plugin → PymolPlugins: Structure Annotator**。
2. **Open from** 选择 **This computer**，在 **Path** 选择自己的 PDB/CIF 目录或 CSV/TSV 评分表，再点 **Open**。支持 `.gz` 结构文件。
3. 如果询问 **Structure column**，选择包含结构路径的列。相对路径默认相对于表格目录。
4. 在 **Review** 查看当前结构和分数，选择标签并点 **Save and next**。你会看到下一条候选；普通点选是草稿，保存后才写入标注。
5. 下次从 **Data → Continue last review** 恢复。无法判断时可用 **More → Mark current for later** 留待复核。

源结构和评分表不会被标注流程改写；本地审阅状态和标注另存。SSH 数据源使用本机 SSH 配置，远端需 Python 3，原表和结构只读。0.17.0 候选版可在 **Connections…** 为服务器启用内置 **Automatic OTP**；填写认证器的 Base32 设置密钥，留空 SSH program，无需安装额外 wrapper。首次操作与截图见 [快速开始](docs/structure_annotator_quickstart.md)，设置和保存边界见 [SSH 指南](docs/structure_annotator_csv_walkthrough.md#连接-ssh-数据源)。

## 使用命令

在 PyMOL 控制台输入：

```pymol
pp_load
pp_help
pp_doctor check_worker=0
```

`pp_help` 列出任务入口；`pp_doctor` 检查当前环境。下例的 `model` 是已经加载的对象名，请替换为自己的对象：

```pymol
pp_show surface, model
pp_undo
```

你会看到对象表面，随后撤销受管显示操作。按任务查找其他命令：[命令示例](docs/task_command_walkthrough.md) · [脚本与命令索引](docs/script_catalog.md)。

## 选择指南

| 任务 | 指南 |
| --- | --- |
| 导入 CSV、SSH、筛选、Review steps、导出和缓存 | [Structure Annotator 完整指南](docs/structure_annotator_csv_walkthrough.md) |
| 让 agent 整理已有结果，再导入和标注 | [整理并审阅运行产物](docs/02_agent_review.md) |
| 成对处理结构、预览并保存结果 | [Batch Processing](docs/batch_processing_specification.md#usage) |
| 切换场景 group、查看接触和使用绘图样式 | [0.17.0 候选版指南](docs/01_group_interaction_styles.md) |
| 配置可选 PLIP / PyRosetta 计算服务 | [WSL worker](docs/wsl_worker.md) |
| 查找旧设计和验收证据 | [历史与验收索引](docs/03_history_index.md) |

## 构建与反馈

需要从源码构建时，在仓库根目录执行 `python scripts/package_plugin.py`，生成 `dist/PymolPlugins-<version>.zip`；也可用 `just package`。可选的 PLIP、PyRosetta、OpenBabel 和 ANVIL 不随插件包分发，各自的许可和安装要求仍适用。

问题反馈请附 PyMOL 版本、操作步骤和报错，提交至 [Issues](https://github.com/Molaison/pymol_scripts/issues)。仓库目前没有顶层 `LICENSE` 文件。
