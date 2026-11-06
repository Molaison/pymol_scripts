# 整理运行产物并在 PyMOL 审阅

把下方提示词交给了解本次运行和评分背景的 agent，可得到结构链接、评分表和审阅配置。先提供真实运行目录、目标目录，以及此前确认的关注指标；没有足够上下文时先确认指标，不要猜测科学意义。

## 复制基础提示词

```text
请整理本次运行产物：
将结构文件 hardlink 到指定文件夹。
从已有 raw 打分结果汇总 sum_scores.csv，不重新计算打分。每个结构一行，包含相对路径、绝对路径、可选 label，以及我最关注的评分指标（结合此前对话确认）；评分列按查看重要性排列。
label 标记不同典型模式、异常或有解释价值的代表性结构，不要只选最好或最差。
同级生成 review.json：
{
  "path_column": "structure_path",
  "display_columns": ["score1", "score2", "score3"],
  "label_column": "label",
  "step_cmd": "hide everything, {object}\nshow cartoon, {object}\norient {object}\nzoom {object}"
}

其中 display_columns 从 sum_scores.csv 的已有字段中选择最值得人工查看的 4–6 项；step_cmd 是整批结构共用的 PyMOL 命令模板，{object} 表示当前结构对象，不要写死具体结构名或逐结构生成命令。
附简洁 README.md，只保留快速理解结果所需的信息。
```

JSON 中三个 `score` 名称只是占位示例；实际 `display_columns` 应填表中已有的 4–6 个字段，按查看顺序排列。不要凭空确定指标、方向或阈值。`label` 描述值得注意的代表性模式，人工质量标注在插件中另行保存。

hardlink 的源和目标必须位于同一文件系统。无法创建时应报告原因，不能静默复制，也不能重新计算评分。确认每个表格路径与结构相对应，保留已有 raw 结果和人工 `review.override.json`。

## 导入并标注

1. 将 `sum_scores.csv` 和生成的 `review.json` 放在同一目录，保留表中相对路径所指向的结构文件。
2. 打开 **Plugin → PymolPlugins: Structure Annotator**，在 **Open from** 选择本机或已有 SSH 主机，选择 `sum_scores.csv`，点击 **Open**。插件会读取同目录配置；如需确认结构列，选择 `structure_path`。
3. 在 **Review** 查看结构、选定的评分列和代表性 `label`。选择人工标签，点击 **Save and next**；稍后可用 **Data → Continue last review** 继续。
4. 如需交接，使用 **Data → Export review handoff…** 导出进度与标签摘要；它不包含结构文件。导出带标注的表格时选择新的输出路径。

插件不会把源表中的 `label` 当成人工质量结论；配置修改写入 `review.override.json`，人工标注另存，不覆盖生成的配置、评分表或结构文件。打开失败时检查路径、列名及 JSON 是否与实际表格一致。详见 [CSV、SSH 与审阅步骤](structure_annotator_csv_walkthrough.md)。

## 配置进阶分组

需要固定参考、多个 Review steps 或基于已知条件切换筛选组时，可使用 [自定义审阅步骤](structure_annotator_csv_walkthrough.md#自定义审阅步骤0160-起) 和 [进阶筛选分组提示词](structure_annotator_csv_walkthrough.md#产物整理prompt含筛选分组)。这些配置是可选项；只有现有数据和约定能支持时才指定分组条件。
