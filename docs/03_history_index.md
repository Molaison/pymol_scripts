# 查找历史设计与验收证据

查版本、失败原因或复现条件时，先按功能选择记录，再核对其中的日期、运行环境、JSON 路径和限制。旧版通过只证明记录中实际执行的范围；未运行、失败和受许可限制的项目仍按原记录理解。

## 当前候选与连续审阅

- [2026-11-06 工具箱整合](validation/02_toolkit_unification_2026-11-06.md)：0.17.0 候选，group、interaction、样式、补全、真实 Host 和临时安装；包含失败基线及渲染许可限制。最终包与后续键盘验证以该记录的后续更新为准。
- [2026-09-29 起的连续审阅、SSH、发布和 Review steps](validation/01_sampling_reference_2026-09-29.md)：多个版本的分阶段结果、原始失败及 JSON 定位。
- [实现状态汇总](implementation_status.md)：截至 0.14.0 的历史矩阵；不是当前版本通过清单。

## 按功能查旧验收

| 功能 | 历史记录 |
| --- | --- |
| 批处理及界面一致性 | [批处理](validation/batch_processing_2026-09-16.md) · [界面](validation/batch_ui_consistency_2026-09-16.md) |
| 插件包安装 | [2026-09-14 插件包](validation/plugin_package_2026-09-14.md) |
| 标注和审阅 | [审阅](validation/structure_annotator_review_2026-09-15.md) · [可用性](validation/structure_annotator_usability_2026-09-15.md) · [布局](validation/structure_annotator_layout_2026-09-16.md) · [点击保存](validation/structure_annotator_click_save_2026-09-16.md) · [保存可见性](validation/structure_annotator_save_visibility_2026-09-22.md) |
| 导入、路径和 SSH | [导入](validation/structure_annotator_import_2026-09-17.md) · [映射](validation/structure_annotator_mapping_2026-09-16.md) · [数据源界面](validation/structure_annotator_source_ui_2026-09-17.md) · [SSH 与模糊选择](validation/structure_annotator_ssh_fuzzy_2026-09-17.md) |
| 筛选与性能 | [Enter 补全](validation/structure_annotator_filter_enter_2026-09-17.md) · [2026-07-26 优化](validation/structure_annotator_optimization_2026-07-26.md) |
| Windows Host 早期基线 | [07-22](validation/windows_host_smoke_2026-07-22.md) · [07-24](validation/windows_host_smoke_2026-07-24.md) · [07-25](validation/windows_host_smoke_2026-07-25.md) |

## 查设计和验收方法

[Windows Host 检查清单](windows_host_acceptance.md) 说明怎样准备环境与记录结果；[窗口模式](workspace_window_modes.md)、[标注字段与评分](annotation_fields_and_score_guidance.md)、[批处理规范](batch_processing_specification.md)、[命令体验规范](command_ux_specification.md)、[可用性规范](structure_annotator_usability_specification.md) 和 [统一设计规范](development_specification.md) 保留对应设计与历史条件。日常操作从 [首页](../readme.md) 开始。

## 查重写前的详细条款

以下参考保留 2026-11-06 重写前的完整内容，便于追溯原始示例、验收标准和历史限制；其中旧界面、计划与结果要按原日期理解。实际操作以现行页面为入口。

| 范围 | 详细参考 |
| --- | --- |
| CSV、路径、缓存及进阶整理提示词 | [审阅参考](04_reference_csv_2026-11-06.md) |
| 命令逐项示例与脚本位置 | [命令参考](05_reference_tasks_2026-11-06.md) · [脚本目录](06_reference_catalog_2026-11-06.md) |
| Worker 和 Windows Host | [worker 参考](07_reference_worker_2026-11-06.md) · [Host 验收参考](08_reference_windows_acceptance_2026-11-06.md) |
| 标注与窗口 | [标注参考](09_reference_annotation_2026-11-06.md) · [窗口参考](10_reference_windows_2026-11-06.md) |
| 开发规格 | [命令体验](11_reference_command_ux_2026-11-06.md) · [可用性](12_reference_usability_2026-11-06.md) · [统一设计](13_reference_development_2026-11-06.md) · [批处理](14_reference_batch_2026-11-06.md) |

文档写作参考：[Agent Text at Scale](https://fsdatalab.github.io/blog/agent-text-at-scale/)、[uv README](https://github.com/astral-sh/uv) 和 [ripgrep README](https://github.com/BurntSushi/ripgrep)。
