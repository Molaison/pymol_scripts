# 切换和维护工作区窗口

Structure Annotator 和 Batch Processing 可以嵌入 PyMOL，也可以作为浮动窗口。打开任一工具，在右上角窗口模式菜单选择 **Embedded in PyMOL** 或 **Floating window**；切换后原草稿、候选、批处理预览和运行中的工作应保留。窗口模式与位置按工具分别记住。

## 切换、关闭和恢复

嵌入时可用菜单或原生停靠控件切换；Windows 浮动标题栏使用操作系统窗口控件。再次打开已显示的工具应激活现有窗口，不建立第二份工作区。标注保存期间关闭可能被阻止；批处理运行中关闭先请求停止，实际停止后才关闭。

两个嵌入工具位于同一侧时可成为标签页；PyMOL 视图区、日志和其他原生 dock 应保留。浮动窗口可调整位置和大小；重新打开应恢复已保存的几何位置。切换不是重新导入，也不应触发关闭清理。

## 修改窗口实现

使用实际 `pymol.gui.get_qtwindow()` 返回的 PyMOL 主窗口作为 dock owner；不要以当前 active window 代替。内容 QWidget 由原生 `QDockWidget` 承载，模式切换只改变宿主状态。设置使用 `PYMOLPLUGINS_UI_SETTINGS` 隔离测试；退出最后一个插件 dock 时只恢复插件拥有的最小尺寸与角落约束。

原合同规定嵌入工具最小 640×440，并为中央视图区至少保留 320×180；浮动标注布局应在 760×560 可用。长输入要能滚动，主要按钮不能被实际 Windows taskbar 遮挡。样式表的最小尺寸可能覆盖 `setFixedSize`，图标按钮需在共享样式明确 min/max。

## 验证窗口变更

1. 在真实 Windows PyMOL/Qt 以隔离设置打开两个工具，检查默认嵌入、标签化、菜单和原生控件切换、浮动大小与重开位置。
2. 在真实异步保存、数据准备和批处理运行时切换；检查草稿、游标、预览、进度和 A/B 输出。检查关闭 veto、停止、重开及按键恢复。
3. 在桌面和紧凑大小检查视图区、滚动输入、主要按钮及实际 taskbar 矩形；分别观察表头与数据 viewport。不要只用 `visibleRegion()` 判断是否被任务栏遮挡。
4. 保存最终 JSON 和截图后再关闭宿主。检查真实 OpenGL framebuffer 时按环境显式重绘；`QWidget.grab()` 可能遗漏 QGLWidget。独立 Qt harness 必须持有 QApplication 直到写完结果。

0.7.0 原始合同、失败、截图、逐项验收和未验证边界保留在 [窗口详细参考](10_reference_windows_2026-11-06.md)。这些记录不证明新版本重跑通过；具体本轮结果查 [验收索引](03_history_index.md)。

<details>
<summary>旧标题及详细条款索引</summary>

原地址保留为兼容入口；以下条目指向重写前的详细参考。

<a id="contract"></a>
- [Contract](10_reference_windows_2026-11-06.md#contract)
<a id="acceptance"></a>
- [Acceptance](10_reference_windows_2026-11-06.md#acceptance)
<a id="execution-record"></a>
- [Execution Record](10_reference_windows_2026-11-06.md#execution-record)
<a id="delivered-artifact"></a>
- [Delivered Artifact](10_reference_windows_2026-11-06.md#delivered-artifact)
<a id="reproduce"></a>
- [Reproduce](10_reference_windows_2026-11-06.md#reproduce)

</details>
