# 创建 PyMOL review bundle

把下面整段提示词交给了解本次运行的 agent，替换开头的输入。它覆盖结构、评分、固定参考、审阅命令和可选筛选组；这是本项目生成 review bundle 的统一入口。

## 复制提示词

````text
请把已有运行产物整理为可在 PyMOL Structure Annotator 中打开的 review bundle，并实际写出文件。

输入：
- 运行目录：<真实路径，可列出多个>
- bundle 目标目录：<真实路径>
- 候选范围和关注指标：<此前已确认的范围、指标与方向；未知处注明>
- 固定参考及链对应：<参考路径、用途、候选/参考链；不需要则写无>
- 查看方式：<保持共同坐标系 / 明确要求对齐；默认不移动坐标>

先读取本次运行的已有说明、结构清单和 raw 评分，确认候选粒度、ID 与评分来源。不要凭文件排序、相似文件名或行号猜配对。若缺失信息会改变候选范围、结构对应或科学解释，提出具体缺口；仍可先完成已明确的部分。

1. 结构与评分
- 将指定范围内的候选结构 hardlink 到 structures/；需要的参考放在 references/。名称用稳定候选 ID，避免同名覆盖。hardlink 失败时报告路径和原因，不静默改用复制。硬链接共享内容，后续不得原地修改链接文件。
- 只汇总已有 raw 分数，不重新计算结构评分，不把可视化对齐后的结构覆盖源文件。保留 raw 字段名、数值精度、单位和缺失值；缺失留空，不填 0。多模型、多轮设计或多个预测对应同一设计时，明确一行代表什么；需要拆行就用包含模型/轮次的唯一 ID，不静默取第一条或“最好”的一条。
- 写 sum_scores.csv：每个候选一行，至少包含 id（唯一非空）、structure_path（相对 CSV）、absolute_path、label 和已确认的重要评分列。保留 run_id、原始候选标识和 raw 记录定位等必要溯源字段；这些可留在表中，不必全放进界面。
- 检查并报告：纳入候选数、缺失结构、缺失评分、重复 ID、未匹配 raw 记录与排除原因。结构存在但缺分的候选仍保留，除非已约定排除；无法匹配的记录不要伪造对应关系。
- label 仅稀疏标记典型模式、异常或有解释价值的代表性结构，覆盖多种模式，不只选最好/最差，也不代替人工质量标签。

2. review.json
与 CSV 同级生成配置。基础模板如下；score1–score4 仅表示实际评分列，必须替换：
```json
{
  "path_column": "structure_path",
  "display_columns": ["score1", "score2", "score3", "score4"],
  "label_column": "label",
  "step_cmd": "hide everything, {object}\nshow cartoon, {object}\norient {object}\nzoom {object}"
}
```
- display_columns 优先选人工最需要的 4–6 项，按重要性排序；真实指标不足四项时如实列出，不补造字段。
- review.json 不接受 id_column；id 保存在 CSV，需要以它作为标注身份时在 Import options 中选择 Row ID column。
- step_cmd 是整批共用模板。保留字面量 {object}/{selection}/{candidate}，它们都指当前候选；不写死 sa_* 临时对象名，不逐候选生成命令。
- 保持共同坐标系审阅时，去掉 orient/zoom/center 和 align/super，只设置显示、颜色及对象级透明度。相机保持不动需要候选本来就在可比坐标系中；不要因此偷偷对齐。
- 界面查看先确认真实链名；把命令限制在当前候选的蛋白链和指定参考。5 Å 邻域可用于显示接触区域，不能当成结合或成功的评分结论。不要创建无必要的命名 selection。

3. 固定参考和多对象步骤（仅有明确输入时）
参考路径写入 CSV 的 reference_path 列；同一参考可在多行复用。使用通用 steps，按“加载参考 → 加载候选 → 显示/对齐命令”的实际顺序配置。示例仅做叠合查看，要求已有共同坐标系：
```json
{
  "path_column": "structure_path",
  "display_columns": ["score1", "score2", "score3", "score4"],
  "label_column": "label",
  "steps": [
    {"type": "object", "name": "reference", "path_column": "reference_path", "fixed": true,
     "on_load_cmd": "show cartoon, {reference}\ncolor gray70, {reference}"},
    {"type": "object", "name": "candidate"},
    {"type": "commands"}
  ],
  "step_cmd": "hide everything, {candidate}\nshow cartoon, {candidate}\ncolor cyan, {candidate}"
}
```
- candidate 恰好一次，使用 path_column 指定的主结构列，不能 fixed。辅助对象名唯一，以字母开头，只用字母、数字、下划线；不可占用模板保留名。
- fixed 按对象角色和路径复用，不表示插件会阻止用户命令移动它。命令只能引用当前或此前已加载的对象；辅助路径为空会跳过加载，需要引用它的候选必须有有效路径。
- on_load_cmd 仅在对象加载或定义变化时运行。每候选都需执行的命令放在 commands 项或 step_cmd。{"type":"commands"} 省略 commands 字段才调用 step_cmd；显式空字符串不会调用它。显式 steps 不会额外自动执行一次 step_cmd。
- 仅在要求对齐且链对应已确认时，加命令对齐 candidate 到 reference；固定参考不动。复合物优先限定对应链的 Cα，避免把整复合物链错误配对。说明移动的是会话中的候选；源结构与 raw 分数不变。没有链对应依据就保留叠合显示并说明限制。
- 不使用旧 reference_column 来期待自动对齐，不生成插件不支持的 preset 名称字段；需要的实际命令写进 step_cmd/steps。

4. 筛选组（有数据依据时才增加）
- 可选 filter_presets 数组，每组包含唯一 name、expression，可选 sort_column、sort_descending（布尔值）。根据实际数据覆盖优先审阅、指标不一致、缺失/异常或代表性模式，通常 3–6 组；不要为凑数量造组。
- 表达式只用实际 CSV 列、常量、比较、and/or/not、括号、contains(列, "文字")、is None / is not None；特殊列名用反引号。禁止 Python、in、算术、分位数函数或引用其他组名。例：label is not None；score1 is None（须替换真实列名）。
- 阈值先用已约定或有来源的判据。若以已有分数分布分层，先求出具体阈值再写入表达式，并注明这是探索性分层，不是通过标准；不重新运行结构评分。
- 允许重叠。用实际 CSV 计算每组命中数、组间重叠和未覆盖数，注明有意保留的空组。不拆分/复制每组结构，不自动填写人工标签。除非明确指定默认子集，打开时保留全集，不设置 filter_text 隐藏候选。

5. 保留人工工作
- 已有 review.override.json、.pymolplugins/ 标注和状态、人工导出文件均保留。重新生成不能覆盖这些文件，不能重排身份映射或随意更换稳定 ID/结构路径。
- override 按字段覆盖 review.json；steps、filter_presets 等数组整体替代，不逐项合并，[] 表示清空。若旧 override 遮住新默认值，明确列出受影响字段，由用户决定如何合并。
- 目标目录已有 bundle 时先比较变更；只更新本任务拥有的生成文件，未知来源的文件不得覆盖。

6. 验收与交付
- 用完整 CSV 检查所有结构/参考路径、ID 唯一性、结构/raw 配对和列名；检查 JSON、steps 顺序、命令占位符及筛选表达式。可用插件的 validate_review_config/validate_review_columns 与 compile_filter 做合同检查。
- 有真实 PyMOL 时，在隔离会话验证打开、至少两条候选切换、固定参考复用与坐标不动、命令作用范围、人工修改保存后重开。可能干扰当前桌面的 GUI 操作在独立隐藏桌面进行，先验证真实渲染可取证，不切换用户输入桌面。
- 未运行的项目如实写“未验证”；解析成功不等于真实 PyMOL 验收通过，不声称候选有实验活性。
- 最终只保留必要的 structures/、可选 references/、sum_scores.csv、review.json、简洁 README.md 和必要溯源/验收证据。README 写清来源、候选粒度与数量、指标方向、参考/链对应、命令是否移动候选、分组依据与数量、异常和实际验收范围，以及可复制的打开方式。返回 bundle 路径与尚未解决的缺口。
````

## 在插件中打开

## 导入并标注

1. 将 `sum_scores.csv` 和生成的 `review.json` 放在同一目录，保留表中相对路径所指向的结构文件。
2. 打开 **Plugin → PymolPlugins: Structure Annotator**，在 **Open from** 选择本机或已有 SSH 主机，选择 `sum_scores.csv`，点击 **Open**。结构列选 `structure_path`；需要 ID 身份时将导入选项的 **Row ID column** 设为 `id`。
3. 在 **Review** 查看结构、选定的评分列和代表性 `label`。选择人工标签，点击 **Save and next**；稍后可用 **Data → Continue last review** 继续。
4. 如需交接，使用 **Data → Export review handoff…** 导出进度与标签摘要；它不包含结构文件。导出带标注的表格时选择新的输出路径。

插件不会把源表中的 `label` 当成人工质量结论；配置修改写入 `review.override.json`，人工标注另存，不覆盖生成的配置、评分表或结构文件。打开失败时检查路径、列名及 JSON 是否与实际表格一致。详见 [CSV、SSH 与审阅步骤](structure_annotator_csv_walkthrough.md)。

## 用 Step commands 预设调整显示

打开 **Viewing presets…**，选择预设和触发时机，点 **Use preset** 检查草稿，再 **Insert draft… → 确认 → Apply commands**。生成和插入都不执行命令；应用后在下次打开/重载候选或保存对应标签时执行。

| 预设 | 适用场景 | 相机/坐标 |
| --- | --- | --- |
| Review · keep camera | 同坐标系连续查看蛋白候选 | 都保持 |
| PPI · interface surface | 显示蛋白链间 5 Å 邻域的表面和侧链 | 缩放到界面，坐标保持 |
| Reference · overlay, keep coordinates | 青色候选与灰色半透明固定参考叠合 | 都保持 |
| Reference · align candidate Cα | 按全部蛋白 Cα 对齐候选到固定参考 | 移动候选并缩放，参考不动 |

两个 Reference 预设需在 **Fixed reference** 选择已加载的固定 Review steps 对象。复合物可能需要将草稿改为特定链；全蛋白 Cα 预设不推断链对应。原生 `align` 会创建或更新 `_align1`、`_align2` 选择，草稿提示中也会说明。预设根据当前结构生成，异构候选应检查链选择是否适用。
