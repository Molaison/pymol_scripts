# 配置标注字段和评分说明

用 Structure Annotator 为单个或多个候选保存单选、多选和文字标注，并在查看结构时比较评分。先打开自己的结构目录或评分表，再选择标注或查看评分。

## 设置字段并保存

在 **Settings → Annotations** 配置字段名、可选值和必填状态，返回 Review 选择值，点击 **Save and next**。有必填项未完成或写入失败时，插件保留草稿和当前位置。**More actions → Undo last save** 撤销上一次完整保存。

要对多行赋值，在列表用 Shift 选范围、Ctrl 切换单行，然后显式保存。批量保存先验证所有必填值，不逐个加载其他结构，也不对未预览候选执行标签命令。相同结构路径的不同行保持独立标注；非空 ID 不能重复。

文件数据源的质量标签绑定到成功加载的结构版本。批量保存前需要查看各目标行；任何一行尚无已确认版本时，整批保留草稿并拒绝写入。重开数据源后，加载到相同版本才恢复对应标签和已审阅状态。未确认的条目仍进入待审，不被旧标签直接跳过。

同一路径的新结构版本保留自己的标签，旧版本和没有版本信息的旧标签仍在原 JSONL 历史中，后者需要人工重新确认。Later 等工作流状态继续跟随稳定的行 ID。版本精度沿用既有来源指纹，具体检查和限制见 [整合验收](validation/04_reviewed_integration_2026-11-23.md)。

## 选择保存手势

**Save on label click** 默认开启。单候选的单选按钮或下拉选项会在必填项完整时保存。点击保存和保存后前进可独立设置，修改后自动记住。单候选 Ctrl+click 保存并前进。Shift+click、普通多选和批量选择可继续编辑草稿，再显式保存。Alt+数字对单选保存一次，对多选只切换草稿。

## 解释评分和代表性标签

选择表格实际评分列后，评分卡最多显示六项数值。相对排名基于当前筛选集合；改变筛选会改变比较范围。若要使用绝对规则，明确指定评分方向、阈值或通过列；方向未知时保持中性。高数值本身不证明结构质量。

源表 `label` 是描述性候选标题，可标记典型模式或异常，不等于人工 Good/Bad。结构详情可查看完整值和已保存字段，即使字段已从当前表单移除。人工标注与原始 CSV/PDB 分开；导出时选择新路径。

## 排查与交接

需要回看时使用 Later 路线；重开时主动选择 **Continue last review**。导出 handoff 只包含进度和标签摘要，不含结构。不要让多个 PyMOL 进程同时编辑同一 JSONL；删除数据目录前备份状态和标注。配置、快捷键和原存储合同的细节见 [详细参考](09_reference_annotation_2026-11-06.md)，实际导入步骤见 [CSV 指南](structure_annotator_csv_walkthrough.md)。

<details>
<summary>旧标题及详细条款索引</summary>

原地址保留为兼容入口；以下条目指向重写前的详细参考。

<a id="配置标注并保存"></a>
- [配置标注并保存](09_reference_annotation_2026-11-06.md#%E9%85%8D%E7%BD%AE%E6%A0%87%E6%B3%A8%E5%B9%B6%E4%BF%9D%E5%AD%98)
<a id="解释评分卡"></a>
- [解释评分卡](09_reference_annotation_2026-11-06.md#%E8%A7%A3%E9%87%8A%E8%AF%84%E5%88%86%E5%8D%A1)
<a id="accepted-behavior"></a>
- [Accepted behavior](09_reference_annotation_2026-11-06.md#accepted-behavior)
<a id="validation-and-delivery"></a>
- [Validation and delivery](09_reference_annotation_2026-11-06.md#validation-and-delivery)
<a id="execution-record"></a>
- [Execution record](09_reference_annotation_2026-11-06.md#execution-record)
<a id="storage-compatibility"></a>
- [Storage compatibility](09_reference_annotation_2026-11-06.md#storage-compatibility)

</details>
