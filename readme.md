<h1 align="center">PymolPlugins</h1>

<p align="center">
  <strong>在 PyMOL 中完成结构审阅、批量处理与可控的分子可视化。</strong><br>
  面向蛋白质结构分析的工具箱：连接本地或 SSH 数据源，在同一工作区查看、筛选、标注并处理结构。
</p>

<p align="center">
  <a href="https://github.com/Molaison/pymol_scripts/releases/latest"><img src="https://img.shields.io/github/v/release/Molaison/pymol_scripts?label=release" alt="Latest release"></a>
  <a href="https://github.com/Molaison/pymol_scripts/actions/workflows/package.yml"><img src="https://github.com/Molaison/pymol_scripts/actions/workflows/package.yml/badge.svg?branch=main" alt="Build and publish plugin"></a>
  <a href="https://github.com/Molaison/pymol_scripts/stargazers"><img src="https://img.shields.io/github/stars/Molaison/pymol_scripts?style=flat" alt="GitHub stars"></a>
</p>

<p align="center">
  <a href="#快速安装">快速安装</a> ·
  <a href="#第一次结构审阅">第一次结构审阅</a> ·
  <a href="#pymol-命令速览">命令速览</a> ·
  <a href="#文档导航">文档导航</a>
</p>

---

## 功能概览

| 工作流 | 能做什么 | 从哪里进入 |
| --- | --- | --- |
| **Structure Annotator** | 读取 PDB/CIF 结构目录或 CSV/TSV 评分表；筛选、抽样、查看分数、逐条或批量标注，并继续上次的审阅进度。支持本地文件和 SSH 远端数据源。 | **Plugin → PymolPlugins: Structure Annotator** |
| **Batch Processing** | 按固定 reference 或 ID 规则配对结构，预览后执行成组的 PyMOL 命令，保存处理结果与 JSON 报告。 | **Plugin → PymolPlugins: Batch Processing** / `pp_batch` |
| **PyMOL 命令工具箱** | 对象/分组导航、结构对齐、表示方式、几何方向、相互作用、样式及撤销。命令统一使用 `pp_` 前缀。 | `pp_help` |
| **可选分析扩展** | 查看预设与 AI 命令草稿；如有需要，可连接独立的 WSL worker 运行 PLIP 或已授权的 PyRosetta 分析。 | Annotator 的 **Settings** / `pp_jobs` |

**v0.16.0 起支持自定义 Review steps。** 可按顺序加载 `candidate` 和其他命名对象、执行命令，并用 `fixed` 复用跨候选不变的结构。在 **More → Review steps…** 中编辑；需要的对齐、颜色和视图操作由用户明确配置。详见 [Review steps 示例与规则](docs/structure_annotator_csv_walkthrough.md)。

## 快速安装

适用于带 **Qt 图形界面和 Plugin Manager** 的 PyMOL；主要桌面使用路径为 Windows PyMOL。结构审阅、批处理和常规 `pp_` 命令不需要额外安装 WSL worker。

1. 从 [最新 GitHub Release](https://github.com/Molaison/pymol_scripts/releases/latest) 下载 **`PymolPlugins-<version>.zip`**。请选择 Release 附带的插件包，**不要**下载 GitHub 自动生成的 `Source code (zip)`，也不要解压插件包。
2. 在 PyMOL 打开 **Plugin → Plugin Manager → Install New Plugin**，选择 ZIP 安装。
3. **完全退出并重启 PyMOL**，然后从 Plugin 菜单打开 **PymolPlugins: Structure Annotator** 或 **PymolPlugins: Batch Processing**。

**后续更新：** 打开 **Plugin → PymolPlugins: Updates** 检查新版本。自动安装是可选设置，默认关闭；安装更新后同样需要重启 PyMOL。

## 第一次结构审阅

打开 **Plugin → PymolPlugins: Structure Annotator**，按以下步骤完成一次本地审阅：

1. 在 **Open from** 选择 **This computer**。
2. 在 **Path** 粘贴包含 `.pdb` / `.cif`（含 `.gz`）文件的目录，或一个包含结构路径的 `.csv` / `.tsv` 文件；也可点击 **Browse…**。
3. 点击 **Open**。如果提示 **Structure column**，请选择记录结构文件路径的列，而不是名称或评分列。
4. 在 **Review** 中查看结构与分数，选择标签后点击 **Save and next**。普通点击只修改当前草稿，保存时才写入标注。
5. 下次从 **Data → Continue last review** 继续；暂时无法判断的结构可通过 **More → Mark current for later** 留待复核。

`Review` 支持筛选分组、排序、代表性抽样，以及相对分数排名与可选的绝对阈值规则。**源 CSV/TSV、PDB/CIF 文件不会被标注流程改写**：本地任务的标注与状态保存在对应的 `.pymolplugins/` 数据中；SSH 任务的标注与用户配置保存在本机。

### 通过 SSH 审阅远端结构

在运行 PyMOL 的 **Windows 电脑** 上先确认 `ssh my-lab` 可以连接，并在 `%USERPROFILE%/.ssh/config` 中配置 `Host` 别名；远端需提供 `python3`。

```sshconfig
Host my-lab
  HostName server.example.org
  User your-username
```

回到插件，在 **Open from** 选择 `my-lab`，于 **Path** 输入 `/data/review/scores.tsv` 或 `~/review/structures`，再点击 **Open**。无需 SSHFS 或 WSL worker；远端数据以只读方式访问。连接、OTP、路径映射和缓存问题请查阅 [SSH / CSV 完整指南](docs/structure_annotator_csv_walkthrough.md)。

## PyMOL 命令速览

安装后在 **PyMOL 控制台** 输入：

```pymol
pp_load
pp_help
pp_doctor
```

`pp_help` 可按任务或命令名查找用法；`pp_doctor` 查看运行环境。下列示例假设场景中已有对应名称的对象：

| 目标 | 命令 |
| --- | --- |
| 浏览匹配的分组 | `pp_nav groups, "design_*"` |
| 展示对象表面 | `pp_show surface, model` |
| 预览结构对齐 | `pp_align mobile, reference, preview=1` |
| 查看配体接触 | `pp_contacts complex, focus=organic` |
| 撤销最近的受管操作 | `pp_undo` |

命令对现有 PyMOL 场景生效；支持预览的操作可加 `preview=1`。更多例子见 [逐项操作指南](docs/task_command_walkthrough.md) 和 [完整命令索引](docs/script_catalog.md)。

## 文档导航

| 想完成的任务 | 对应文档 |
| --- | --- |
| CSV/TSV 审阅、筛选、**Review steps**、标注导出、SSH 与缓存 | [Structure Annotator 使用指南](docs/structure_annotator_csv_walkthrough.md) |
| 成对结构批处理、命令序列与结果文件 | [Batch Processing 指南](docs/batch_processing_specification.md) |
| PyMOL 命令与脚本入口 | [任务命令示例](docs/task_command_walkthrough.md) · [脚本目录](docs/script_catalog.md) |
| PLIP / PyRosetta 可选计算环境 | [WSL worker 配置](docs/wsl_worker.md) |
| 窗口行为、平台验收与实现状态 | [窗口模式](docs/workspace_window_modes.md) · [Windows Host 验收](docs/windows_host_acceptance.md) · [实现记录](docs/implementation_status.md) |
| 新功能与历史版本 | [Releases](https://github.com/Molaison/pymol_scripts/releases) |

## 从源码构建

需要 Python 3；安装了 [just](https://github.com/casey/just) 时可运行 `just package`，也可直接使用打包脚本：

```bash
git clone https://github.com/Molaison/pymol_scripts.git
cd pymol_scripts
python3 scripts/package_plugin.py
# 生成 dist/PymolPlugins-<version>.zip
```

核心实现位于 `pymolplugins/`，`plugins/` 是 Plugin Manager 的入口，`docs/` 存放指南，`tests/` 包含核心逻辑与 PyMOL Host 验收脚本。旧版的 `analysis/`、`workflows/`、`visualization/` 等目录保留兼容入口。

不依赖 PyMOL 的测试可在普通 Python 环境中运行：

```bash
python3 -m unittest discover -s tests -v
```

Qt 界面与 PyMOL 场景操作仍需在真实 Host 环境中验证，见 [Windows Host 验收指南](docs/windows_host_acceptance.md)。

## 反馈与授权

欢迎通过 [Issues](https://github.com/Molaison/pymol_scripts/issues) 反馈问题或提出功能建议；请尽量附上 PyMOL 版本、操作步骤与报错信息。

本仓库目前**尚无顶层 `LICENSE` 文件**。PyRosetta、PLIP、OpenBabel、ANVIL 等外部工具分别受其自身许可约束；可选 worker 不会代为安装或重新分发这些工具。
