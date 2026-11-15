# RFD4 合并与正式安装

0.19.0 将 RFD4-PyMOL 0.2.1 纳入 `pymolplugins.rfd4`。统一入口为 **Plugin → PymolPlugins: RFD4 · Proteina**，点击时才导入面板。配色、Contacts、序列选择直接使用同包工具；不再注册独立插件或 `rfd4_studio` 命令。使用方式和科学评估边界见 [RFD4 指南](../../pymolplugins/rfd4/README.md)。

## 实际验收

两端均从 `scripts/package_plugin.py` 生成的同一个 ZIP 安装到真实用户的插件目录，保留其他插件。旧 `rfd4_pymol`、插件注册配置和独立快捷入口先备份，再移出加载位置。既有连接设置和凭据逐字节保持。

| 环境 | 宿主 | 验收 |
| --- | --- | --- |
| beta / Windows | PyMOL 3.1.8、Python 3.10.20、Qt 5.15.15 | 正常启动、全新进程重启均 passed |
| 本机 / Linux | OSS PyMOL 3.1.0、Python 3.11.16、Qt 5.15.15 | 正常启动、通过普通 PyMOL 可执行文件重启均 passed |

四次启动均使用正常用户配置。`tests/host/19_rfd4_integration.py` 点击真实 Plugin 菜单，验证入口唯一、延迟加载、原生停靠面板、隐藏后重开仍为同一个面板、自动认证连接真实服务、默认 f102，以及 candidate/轨迹/条件评估三项协议能力。实际从服务导入 2150 原子的双链输入，执行并撤销配色、按序列选择、链间 Contacts 与清除；所有 RFD4 模块均来自统一安装包。

原始结果在 `239_rfd4_proteina_web_interface/runs/002_pymol_interface/01_native_client/05_toolkit_integration/`，包括 `local_install.json`、`beta_install.json` 和四份 `*_start.json` / `*_restart.json`。判定依据为最终 JSON 和安装文件哈希，不仅是宿主退出码。截图保存原生 Qt 与 OpenGL 缓冲，按真实控件位置合成；不是桌面录屏。两端截图已人工查看。

本机构建 ZIP SHA256：`9f7f8b16144aa75dfbb29bb838969f4112e2ff2476dfba57d5287a319839aeff`。安装内容逐文件对照 ZIP。正式 Release 由 CI 再构建，ZIP 时间戳可导致压缩包哈希不同，应比较文件内容并使用该 Release 附带的 `SHA256SUMS`。

仓库现有 packaging/layout/updater/annotation 六组检查共 46 项通过。首轮唯一失败是既有菜单集合断言缺少新增入口，更新该断言后通过；未新增单元测试。原生宿主验收是独立的集成脚本。

## 科学验证的复用范围

`bridge.py`、`client.py`、`credentials.py`、`editors.py`、`panel.py`、`trajectory.py`、`ui.py` 与已验收的独立版逐字节相同。集成只调整入口、包内工具引用和移除外部依赖下载按钮。本次没有提交新的推理任务，也没有更改后端。

原 beta 实测已完成两轮 f102 推理：第一轮生成两个 candidate，再从 index 0 继续第二轮生成一个 candidate；两轮各四帧原生 x1 轨迹。两个氢键角色目标在第一轮首帧均无法判断，其余保存帧和最终结构均满足；第二轮全部满足。详细条件、原始任务 ID、结果和四段 GIF 保存在原项目 `analysis/002_pymol_interface/01_native_client/03_beta_live_rounds/01_report.md`。这项证据不是本次重新生成的结果，不能外推到任意输入或后端。

本机继续使用已有 OSS PyMOL，应用菜单入口为 **PyMOL (Open Source)**；原商业版许可证限制未修改。beta 使用现有 PyMOL。两端均完全退出并重启后，从统一 Plugin 菜单使用；旧快捷入口已备份退役。
