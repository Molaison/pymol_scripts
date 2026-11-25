# PymolPlugins

在 PyMOL 中查看、筛选和标注结构，比较候选与参考，抽样展示结构集合，批量处理结构对，并连接 RFD4 后端设计蛋白。

[安装](#安装) · [第一次审阅](#第一次审阅) · [命令](#使用命令) · [指南](#选择指南)

## 安装

需要带 Qt 界面和 Plugin Manager 的桌面 PyMOL。普通审阅、批处理和命令不需要 WSL worker。

1. 从 [正式 Release](https://github.com/Molaison/pymol_scripts/releases/latest) 下载附件 `PymolPlugins-<version>.zip`。不要选择 GitHub 自动生成的 Source code ZIP，也不要解压插件包。
2. 在 PyMOL 选择 **Plugin → Plugin Manager → Install New Plugin**，选择下载的 ZIP。
3. 完全退出并重启 PyMOL。在 **Plugin → PymolPlugins** 中打开 **Review structures…**、**Structure Collections…** 或 **RFD4 · Proteina**。在 Review 中通过 **Compare → Align loaded objects…** 对齐已加载对象，通过 **Tools → Compare or process structures…** 打开批处理。

0.19.0 将 RFD4 原生面板纳入同一插件包：输入、条件编辑、candidate 生成及下一轮、轨迹与氢键条件检查均在 PyMOL 内操作。模型通过 HTTPS 或 SSH 转发连接服务端，已有连接和登录配置沿用。见 [RFD4 使用与迁移](pymolplugins/rfd4/README.md)。

0.18.0 增加保持相机、界面表面、固定参考叠合和候选 Cα 对齐预设，并优化大场景切组时的分组读取，仍逐次识别手动改组。准备结构与评分表可直接使用 [review bundle 生成提示词](docs/02_agent_review.md)。其他功能见 [操作指南](docs/01_group_interaction_styles.md) 和 [SSH 指南](docs/structure_annotator_csv_walkthrough.md#连接-ssh-数据源)。

更新插件时，打开 **Plugin → PymolPlugins → Maintenance → Updates**，点击 **Check now**，再点击 **Download and install**。也可启用 **Automatically install stable updates (restart required)**；**Check for updates when PyMOL starts** 默认开启，自动安装默认关闭。更新后完全退出并重新打开 PyMOL，当前会话不会热重载。

0.17.1 起，遇到 GitHub API 匿名限流时，更新器会尝试公开 Release 页面并继续校验，无需 GitHub token；其他网络错误仍会报告。如果旧版更新器因 API 限流无法检查，可在 [最新正式 Release](https://github.com/Molaison/pymol_scripts/releases/latest) 提供 0.17.1 或更新版本后下载插件 ZIP，并按上述步骤安装。

## 第一次审阅

1. 打开 **Plugin → PymolPlugins → Review structures…**。
2. **Open from** 选择 **This computer**，在 **Path** 选择自己的 PDB/CIF 目录或 CSV/TSV 评分表，再点 **Open**。支持 `.gz` 结构文件。
3. 如果询问 **Structure column**，选择包含结构路径的列。相对路径默认相对于表格目录。
4. 在 **Review** 查看当前结构和分数。默认单候选的单选标签在必填项完整时点击即保存并前进；Shift 点击保留草稿，再用 **Save and next** 保存。可在 **More** 调整点击保存和自动前进。
5. 下次从 **Data → Continue last review** 恢复。无法判断时可用 **More → Mark current for later** 留待复核。

源结构和评分表不会被标注流程改写；本地审阅状态和标注另存。SSH 数据源使用本机 SSH 配置，远端需 Python 3，原表和结构只读。0.17.0 起可在 **Connections…** 为服务器启用内置 **Automatic OTP**；填写认证器的 Base32 设置密钥，留空 SSH program，无需安装额外 wrapper。首次操作与截图见 [快速开始](docs/structure_annotator_quickstart.md)，设置和保存边界见 [SSH 指南](docs/structure_annotator_csv_walkthrough.md#连接-ssh-数据源)。

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
| 蛋白设计、candidate 下一轮、轨迹与局部条件检查 | [RFD4 · Proteina](pymolplugins/rfd4/README.md) |
| 导入 CSV、SSH、筛选、Review steps、导出和缓存 | [Structure Annotator 完整指南](docs/structure_annotator_csv_walkthrough.md) |
| 让 agent 整理已有结果，再导入和标注 | [整理并审阅运行产物](docs/02_agent_review.md) |
| 候选对固定参考、逐行父本或优化路径 | [批量结构对照](docs/03_comparison_review.md) |
| 按分数分层与结构差异抽样，统一朝向并导出 PNG | [Structure Collections](docs/01_structure_collections.md) |
| 成对处理结构、预览并保存结果 | [Batch Processing](docs/batch_processing_specification.md#usage) |
| 切换场景 group、查看接触和使用绘图样式 | [Group、interaction 和样式指南](docs/01_group_interaction_styles.md) |
| 导出扩展 FASTA，精确搜索修饰残基与异构 component | [序列导出与残基 token](docs/04_extended_sequence.md) |
| 配置可选 PLIP / PyRosetta 计算服务 | [WSL worker](docs/wsl_worker.md) |
| 查找旧设计和验收证据 | [历史与验收索引](docs/03_history_index.md) |

## 构建与反馈

需要从源码构建时，在仓库根目录执行 `python scripts/package_plugin.py`，生成 `dist/PymolPlugins-<version>.zip`；也可用 `just package`。可选的 PLIP、PyRosetta、OpenBabel、ANVIL 和 ANARCII 不随插件包分发。CDR 编号使用独立的本机 ANARCII 环境；安装位置和 `PYMOLPLUGINS_ANTIBODY_PYTHON` 见包内安装指南。普通审阅、比较和 Collections 不需要该环境。

问题反馈请附 PyMOL 版本、操作步骤和报错，提交至 [Issues](https://github.com/Molaison/pymol_scripts/issues)。仓库目前没有顶层 `LICENSE` 文件。
