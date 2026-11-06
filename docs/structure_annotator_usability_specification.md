# 维护 Structure Annotator 交互

修改导入、审阅、标注、窗口或 SSH 交互时，用本页核对用户路径和验收范围。先准备独立数据源、临时设置和真实 Windows PyMOL/Qt 环境；完成后应能打开、审阅、保存、继续和导出，同时保留原始科学输入。旧版设计、历史截图及验收逐项记录见 [详细参考](12_reference_usability_2026-11-06.md)。

## 维护首次打开与导入

用户从 **Open from** 选择本机或 SSH，输入目录/CSV/TSV，点击 **Open**；需要时选择结构路径列。面板打开不能自动连接历史源，恢复须由用户选择 **Continue last review** 或明确恢复配置。导入失败、取消或迟到结果不得替换当前数据集和场景；准备工作放后台，PyMOL `cmd.*` 和提交保留主线程。

CSV 的 header、路径映射、缓存和远端结构按需读取；GUI 保持响应并显示等待、取消与失败。切换数据源不得继承前一源的路径列、reference 列或其他不属于新源的配置。原始 CSV、PDB/CIF 和远端文件不得被标注流程改写。

## 维护审阅和保存

普通标签点击是草稿；显式保存后才持久化。单选点击保存、自动前进是独立设置；Ctrl+click 单候选保存并前进，多选普通点击暂存，批量选择需显式保存。必填字段、失败重试和撤销要保留草稿与当前位置。Later 是复核路线，不改变 Good/Bad 的含义。按当前筛选集合说明相对评分；未知方向不能自动判断优劣。

选择多个行不应加载所有结构；批量保存检查必填项且作为一个持久操作和一次 undo。评分卡最多六项数值，源表 label 是描述性候选名，不是人工结论。人工配置写 override，生成配置和原表保持独立。

## 维护输入、补全和导航

在输入框和补全弹窗分别验证 Tab、Return 和数字键盘 Enter；接受建议不得顺带提交筛选。粘贴路径后立即点导入，检查原生 completer 不得先插入首个历史项。SSH 复用认证连接；未启用内置 OTP 时保留已配置登录程序，错误不能静默换客户端。后台目录、header 和 inline 标注读取不能阻塞 Qt。

维护内置 OTP 时，在 **Connections…** 按 Host 与 SSH config 区分设置。**Automatic OTP** 接收 Base32 认证器设置密钥，**SSH program** 留空以使用系统 OpenSSH；旧 `.py` wrapper 在启用内置 OTP 前必须清空。默认仅当前进程使用，Windows 勾选 **Remember encrypted key on this computer** 后才由当前用户 DPAPI 加密保存。取消对话框不改保存值；禁用并确认删除该主机保存密钥。密钥不进入 SSH URL、`review.json` 或评分表。

保持 SSH config、跳板/代理与 `known_hosts` 的现有行为。OTP 只响应匹配的验证码提示，不填写普通密码、私钥口令或接受新主机；**Advanced → OTP prompt** 可配置服务器提示的正则表达式。使用 SHA1、六位、30 秒的 TOTP，每次连接最多提交一次，拒绝后不自动重试。同进程同密钥避免复用时间窗口；不同进程和外部工具之间不宣称协调。

Annotator 的 Alt+Left/Right 切候选。高层 `pp_nav` 裸方向键用于独立导航；空命令行接收快捷键，有文字时保留编辑。切换模式、保存、关闭或换源时，检查按键租约、未保存草稿和异步结果的所有权。

## 执行验收

1. 用隔离设置和可丢弃文件验证首次打开、本地及真实 SSH 导入、路径错误、取消与旧场景保留。
2. 检查评分显示、筛选/排序、空结果、抽样数量、单行与批量保存、必填、撤销、Later、继续和新路径导出。
3. 检查窗口嵌入/浮动、紧凑布局、实际 Windows taskbar、长字段与滚动，并在真实异步保存、准备和批处理期间切换窗口。
4. 检查 review.json 与 override、固定对象、显式步骤、输入哈希和换源隔离；报告未执行项与真实 Host 限制。

内置 OTP 已在 Windows 原生 SSH 和临时本地挑战服务上验证默认/自定义提示、拒绝只提交一次、普通密码拒填及约一秒取消；Qt/DPAPI 验证保存重开、取消、禁用与仅会话使用。真实 `f101` 登录和 Unix 端尚未验收；后续修改仍须按受影响边界记录新结果。

具体窗口合同见 [窗口模式](workspace_window_modes.md)，真实 Host 检查见 [验收清单](windows_host_acceptance.md)。历史需求或旧版本通过不自动成为本次结果。

<details>
<summary>旧标题及详细条款索引</summary>

原地址保留为兼容入口；以下条目指向重写前的详细参考。

<a id="2026-09-17-ssh-sources-and-selection-gestures---090"></a>
- [2026-09-17 SSH sources and selection gestures - 0.9.0](12_reference_usability_2026-11-06.md#2026-09-17-ssh-sources-and-selection-gestures---090)
<a id="2026-09-17-embedded-and-floating-workspaces---070"></a>
- [2026-09-17 embedded and floating workspaces - 0.7.0](12_reference_usability_2026-11-06.md#2026-09-17-embedded-and-floating-workspaces---070)
<a id="2026-09-16-optional-click-to-save-restoration---051"></a>
- [2026-09-16 optional click-to-save restoration - 0.5.1](12_reference_usability_2026-11-06.md#2026-09-16-optional-click-to-save-restoration---051)
<a id="2026-09-16-visual-hierarchy-redesign--050"></a>
- [2026-09-16 visual hierarchy redesign — 0.5.0](12_reference_usability_2026-11-06.md#2026-09-16-visual-hierarchy-redesign--050)
<a id="2026-09-16-follow-up-import-mapping-profiles-and-switching"></a>
- [2026-09-16 follow-up: import, mapping profiles and switching](12_reference_usability_2026-11-06.md#2026-09-16-follow-up-import-mapping-profiles-and-switching)
<a id="execution-record"></a>
- [Execution record](12_reference_usability_2026-11-06.md#execution-record)
<a id="problems-and-proposed-behavior"></a>
- [Problems and proposed behavior](12_reference_usability_2026-11-06.md#problems-and-proposed-behavior)
<a id="shared-architecture-and-consistency-requirements"></a>
- [Shared architecture and consistency requirements](12_reference_usability_2026-11-06.md#shared-architecture-and-consistency-requirements)
<a id="evidence-collected-before-implementation"></a>
- [Evidence collected before implementation](12_reference_usability_2026-11-06.md#evidence-collected-before-implementation)
<a id="implementation-and-acceptance-order"></a>
- [Implementation and acceptance order](12_reference_usability_2026-11-06.md#implementation-and-acceptance-order)
<a id="design-references"></a>
- [Design references](12_reference_usability_2026-11-06.md#design-references)

</details>
