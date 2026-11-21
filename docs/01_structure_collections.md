# Structure Collections

状态：七项用户审查问题已修复，整体验证通过；界面移除二级标签后，
隐藏桌面 Qt 流程再次通过。验收命令和证据见文末。
维护 owner：当前仓库的 `plugins/structure_collections.py` 与
`workflows/dataset_navigation/collections_core.py`。

## 在 PyMOL 中打开

```pymol
run D:/SyncPath/GitRepo/PymolPlugins/plugins/structure_collections.py
collections_panel
```

需要自动加载时，可在自己的 PyMOL 启动脚本中加入上述 `run` 行。
保留脚本与同仓库的核心模块路径。

界面沿用当前仓库 Structure Annotator 的原生 Qt 分组框和系统样式。
macOS 下可从任意工作目录通过脚本绝对路径执行 `run`，随后使用 `collections_panel`。
左侧直接显示数据、采样和朝向设置，右侧始终显示样本；不使用二级标签页。
设置较多时左侧可滚动，预览区域保持可见。
Windows 和 macOS 使用相同的基础字号、表单对齐和间距，并跟随系统配色。
悬停在底部状态文字上可查看当前 Collection 文件的完整路径。

1. 选择打分文件，点击「读取打分列」，选择结构路径列与结构根目录。
2. 勾选重点指标，为每列设定层数；可填最低 / 最高值限定展示范围。
3. 设置样本数、结构差异阈值，检查 target / binder 的链选择。
4. 选择统一朝向，点击「生成 Collection」，自动生成预览。
5. 双击样本在 PyMOL 中查看；转动后「保存当前朝向」，整套预览自动刷新。
6. 点击「批量导出 PNG」。输出中包含一个 HTML 展示页，可以直接打开浏览。

默认 target 是 `chain A`，binder 是 `chain B`，根据数据实际链名修改。
链选择支持普通 PyMOL selection，例如 `chain A+C` 或 `chain B and resi 1-100`。
导入和渲染采用结构的第一状态；PDB、mmCIF 及其 gzip 文件由 PyMOL 读取。

## 打分表

支持 CSV、TSV、JSON 行列表及 Rosetta `.sc` / `.score`：

```csv
path,iptm,pae,energy
models/design_001.cif,0.87,4.2,-123.5
models/design_002.pdb,0.81,5.6,-118.2
```

JSON 使用 `[{"path":"design_001.cif","iptm":0.87,"pae":4.2}]`。
结构路径可以是绝对路径或相对于结构根目录的路径。
Rosetta 的 `description` 或未带扩展名的名称会依次尝试 `.pdb`、`.cif`、
`.pdb.gz`、`.cif.gz`，不递归扫描结构库。
数值缺失、无穷值、文件缺失、重复路径及范围筛选都会记录原因。

## 采样与结构多样性

每个关注的指标按分位数划层，组合为**联合分层**。轮询实际非空的层，
每轮每层最多接受一个样本；随机种子决定层顺序与层内候选顺序。
同分不拆层，因此实际层数可能少于设定值。这是分布覆盖，不是按总分排序；
需要仅展示优质区间时，先填写各指标的最低 / 最高值。

默认的结构多样性选择是 binder；也可填整个复合物或指定域。

- **形状**：匹配 CA 后做最优刚体配准，比较 RMSD，排除旋转和平移。
- **结合姿态**：先按 target 配准，比较对应 binder CA 的位置，
  不再对 binder 单独配准。相同 binder 形状在不同结合位置可以入选。

默认最小差异为 **2 Å**；每个新样本与全部已选样本比较。
原生 `super` 建立结构对应；重复序列等导致覆盖率不足时尝试原生 `align`。
不做离群点剔除，避免只保留小块相似区域而把不同结构误判为重复。
对应 CA 数 / 两者较大的 CA 数必须达到覆盖率（默认 0.8）。
无法充分对应的结构记为不可比较，**默认暂不纳入**，
保存到 `unverified_candidates`，不会用无限 RMSD 代表结构不同。
确实需要展示不同长度或拓扑的样本时，可以在采样前勾选
「允许纳入无法验证差异的样本」；样本列表与 HTML 都会明确标记“差异未验证”。
旧 collection 中已纳入的不可比较样本也会显示该标记。
pose 模式中 target 无法充分对应会拒绝该候选并记录原因。
`nearest_rmsd=null` 表示没有可用的比较值，不能解释为不同。

候选不够、相似结构过多或文件无效时，返回实际样本数和缺额，
不会使用重复结构补满。样本少于非空层数时，只能覆盖部分层，报告实际覆盖数。
结构去重先于展示朝向计算，两者可以独立设置。
每个候选先检查所需链和朝向能否计算；缺链、损坏文件、配准失败或
不适用的对称轴只影响该候选。有效样本继续保存，错误写入 `invalid_structures`。
全部失败时也保存空 collection 与原因，供修正选择后重新采样。

## 整理展示样本

样本页支持 Ctrl / Shift 多选，macOS 使用 Command / Shift：

- **固定 / 解除固定**：固定状态保存在 collection，固定样本不能直接删除或替换。
- **删除选中**：移出 collection，不删除输入结构；至少保留一个样本。
- **替换选中**：选择一个未固定样本，优先从同一分层寻找其他候选，
  同时检查与当前所有样本（包括被替换者）的结构差异；同层没有合格候选时尝试其他层。

替换后保留其他样本与固定状态，并自动刷新预览。
没有合格替代者时保留原样本，明确报告原因。
已主动删除或替换的结构路径不再作为该 collection 的自动替代候选。
操作只改变当前 collection；「生成 Collection」仍用于另一个新保存目录。

## 统一视角

| 模式 | 作用 |
| --- | --- |
| target | 把复合物整体配准到同一个参考 target，再使用该 target 的 PCA 朝向。 |
| binder | 把复合物整体配准到同一个参考 binder，再使用该 binder 的 PCA 朝向。 |
| PCA | 按各结构的选定部分计算主轴，最长轴横向；固定轴符号并保持右手系。 |
| 对称轴 | 将轴纵向摆放，并由原子顺序确定侧向；支持手动三维向量。 |

参考文件留空时使用第一个入选结构。PCA / 对称轴 selection 默认是
`polymer.protein`；对称复合物包含额外 binder 时，应选择真正对称的链。
自动对称轴来自至少三条链的质心平面，并检查半径、平面性与角间距，
适用于近似环状 Cn，是几何估计。C2、螺旋或更复杂对称应填写已知轴向量。
PCA 特征值相等或接近时，主轴本身不唯一；此类对象优先用参考配准或已知对称轴。

所有样本共用整个 collection 的包围范围、正交相机与缩放，保持同等比例。
手动保存朝向会把 PyMOL 相机记录写入 manifest，但导出使用其中的**旋转朝向**，
再根据所有样本的范围和输出宽高重新居中、缩放和设置裁切面。
因此改变横竖比例不会沿用旧比例的裁切；手动缩放和平移不作为最终构图。
预览使用与导出相同的宽高比例，通常最长边不超过 640 像素。

打开旧 collection 时回填采样、链、参考结构和朝向设置。
修改朝向后点击「应用朝向并刷新预览」，沿用已选样本重新配准；
直接点击导出也会先应用修改，不能悄悄使用旧设置。
应用新的配准方式会清除原先保存的手动旋转，需在新坐标系中重新调整。
新朝向不适用于部分样本时保留有效结果并记录失败；若固定样本失败
或全部失败，则保持原 collection，提示修正选择。
点击「恢复原场景」或关闭面板可恢复预览前的可见对象与相机。

## 输出与并行

选择一个新 collection 保存目录：

```text
00_config.json
01_collection.json      # 样本、分层边界、去重记录、参考结构、变换矩阵、相机
01_analysis.log
01_progress.json        # 当前采样 / 调整进度
00_edit.json            # 最近一次样本或朝向修改请求（执行修改后出现）
01_edit.log
02_samples.csv          # 入选路径、分层和打分
previews/               # 与导出比例一致的 ray 预览
images/                 # 默认 1600×1200、300 dpi PNG
    0001_….png
    01_worker_01.log
    02_worker_01.json
    03_export.json      # 每张图的成功 / 失败与实际 worker PID、时间
    04_gallery.html
```

独立后台 PyMOL 进程并行处理分块，每个进程重复使用自己的实例。
进程数受样本数和 CPU 数限制；每进程的 ray 线程数分配为
`CPU 数 // 实际进程数`，避免每个进程都占满所有 CPU。
推荐从 **2 个进程**开始；大结构或高分辨率图的内存开销随并发增加。
采样也是后台进程，界面保持可用；导出失败保留明确错误和日志。
进度显示已检查的候选数或已完成的图片数。
点击「取消任务」会终止并等待当前任务所属的后台进程退出；
运行中关闭面板会先取消并等待，避免窗口消失后继续占用 CPU。
取消渲染时保留已经成功的 PNG 和逐项结果，清理未完成的临时图片。
若取消发生在采样阶段提交 manifest 之前，保留配置和日志，但需要重新执行采样。

「重试失败 / 未完成项」复用当前设置下成功的图片，仅补渲染失败、缺失或过期项。
源结构坐标改变后，该样本会报告需要重新应用朝向。点击「应用朝向并刷新预览」重算取景，
再导出 PNG。命令行可用 `revise --manifest <manifest> --request <请求.json>` 执行同一步，
请求文件内容为 `{"action": "orient", "config": {}}`。
变换、朝向、颜色选择或输出尺寸改变时，受影响图片需重新渲染。
已成功且仍有效的 PNG 不会被覆盖；旧缩略图与展示页不会冒充新设置的结果。

打开旧 collection 不会重新采样：

```pymol
collections_open D:/results/collection/01_collection.json
collections_preview 1
collections_capture_view
```

可直接从命令行导出已保存的 collection：

```powershell
D:\Softwares\Pymol3\python.exe D:\SyncPath\GitRepo\PymolPlugins\workflows\dataset_navigation\collections_core.py export --manifest D:\results\collection\01_collection.json --workers 2 --width 1600 --height 1200 --retry-failed
```

无人值守采样可将上述设置保存为 JSON，使用同一个脚本的
`analyze --config <配置.json> --output <collection目录>` 子命令。
配置键的完整示例见 `tests/01_collections_checks.py` 中的 `config`。
读取原文件，不复制输入结构；重新导出时输入结构路径必须仍然有效。

本实现使用 PyMOL 的实例 API 与原生 PNG/ray 接口：
[PyMOL API 官方说明](https://pymol.org/pymol-command-ref.html)，
[PyMOL PNG 实现](https://github.com/schrodinger/pymol-open-source/blob/master/modules/pymol/exporting.py)。

## 验收与恢复

```powershell
D:\Softwares\Pymol3\python.exe D:\SyncPath\GitRepo\PymolPlugins\tests\01_collections_checks.py --stage all
```

也可用 `--stage math`、`--stage native`、`--stage regression` 或 `--stage qt` 分别检查；
`regression` 和 `qt` 读取 `native` 已生成的 collection。证据位于
`output/01_collections_checks/04_all_results.json` 与 `03_collections_panel.png`。
最终无标签页界面的结果以同目录 `04_qt_results.json` 为准。

已观察到：6 个候选选出 3 个不同结构，拒绝 3 个刚体重复，
缺额 3 正确报告；移动 binder 后 shape RMSD 接近零而 pose RMSD 约 10 Å。
target 配准、binder / PCA / 手动与自动对称轴的原生变换均检查。
两个不同 PID 有实际运行重叠，输出 3 张有效 640×480 PNG。
CSV / TSV / JSON / Rosetta 表解析、配置文件启动的外部采样和失败 worker
逐样本报告也通过检查。
Qt 面板在独立隐藏 Windows 桌面检查读取、预览、保存视角、恢复场景及截图；
不切换用户输入桌面。离屏 Qt 字体渲染不完整，因此未采用离屏截图作为验收。

本轮回归覆盖：截短同一结构默认不同时入选、明确允许后的标记、
坏 target 保留部分结果、保留样本重新配准、固定 / 删除 / 替换、
手动旋转后的竖图边界、失败和取消后的选择性重试。
真实 Qt 操作覆盖设置回填、修改朝向再导出、保存朝向刷新图片、
固定样本以及关闭时取消并等待。
最终界面检查确认没有 `QTabWidget`，使用与现有面板一致的原生分组框。

这些是可复现的小规模运行证据；未测定真实大数据集的加速比。
如恢复开发，先读取本说明与结果 JSON，再从失败边界继续，不重建其他任务记录。
