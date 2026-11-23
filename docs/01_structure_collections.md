# Structure Collections

唯一实现位于 `pymolplugins/collections`：`core.py` 拥有采样、编辑和后台渲染，
`plugin.py` 拥有 Qt 面板，`commands.py` 提供延迟入口 `pp_collections(manifest="")`。
主插件负责命令注册、菜单、帮助和安装打包。本包已完成真实 headless PyMOL 验证；
统一安装包与隐藏桌面 GUI 的实际范围见 [整合验收](validation/04_reviewed_integration_2026-11-23.md)。

## 在 PyMOL 中打开

```pymol
pp_collections
pp_collections D:/results/collection/01_collection.json
```

也可从 **Plugin → PymolPlugins → Structure Collections…** 打开。接入位置为
`from pymolplugins.collections.commands import pp_collections`；导入不会加载核心、
PyMOL、NumPy、Qt 或面板，也不会注册命令。调用时才加载面板，返回面板对象；
非空路径调用 `load_manifest`，失败向调用者抛出异常。

未接入主插件时，可在 PyMOL 执行 `run <仓库绝对路径>/plugins/structure_collections.py`，
再执行 `collections_panel`。仅此旧入口显式注册 `collections_panel`、`collections_open`、
`collections_preview` 和 `collections_capture_view`，不维护第二套面板或算法。
旧核心路径保留 import/CLI 别名，指向同一个 canonical core 模块。

面板复用主插件 `annotation.panel_style.apply_style`，父窗口来自 `pymol.gui.get_qtwindow()`。
左侧直接显示数据、采样和朝向设置，右侧始终显示样本；不使用二级标签页。
设置较多时左侧可滚动，预览区域保持可见。
表单布局保持 legacy 最终版，字号与配色由现有主题控制。
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
先判断分数，再解析通过筛选的行所对应的结构路径。

## 采样与结构多样性

每个关注的指标按分位数划层，组合为**联合分层**。轮询实际非空的层，
每轮每层最多接受一个样本；随机种子决定层顺序与层内候选顺序。
同分不拆层，因此实际层数可能少于设定值。这是分布覆盖，不是按总分排序；
需要仅展示优质区间时，先填写各指标的最低 / 最高值。
所有分位切点都落在最低同分组时，以第二个不同分值为切点，允许少量高分结构单独成层。

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
结构去重与展示朝向可以独立设置；比较使用源坐标，展示矩阵不会改变去重输入。
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
替换使用 Collection 保存的分层边界。
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
点击「恢复原场景」或关闭面板可恢复预览前的可见对象、命名选择与相机。

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
进度显示已检查的候选数或已完成的图片数。采样 / 编辑 CLI 合并 0.1 秒内的高频更新，
正常完成时补写最后一条进度；不会为填满进度条伪造未检查的候选数。
同一个输出目录一次只运行一个导出任务。预览和正式导出使用各自目录。
如果已有任务写入同一目录，后启动的任务会明确报错，可以等前一任务结束后重试。
同一次导出的各进程使用任务启动时读取的同一份采样和朝向；之后保存的修改在下一次导出时生效。
点击「取消任务」会终止并等待当前任务所属的后台进程退出；
运行中关闭面板会先取消并等待，避免窗口消失后继续占用 CPU。
取消渲染时保留已经成功的 PNG 和逐项结果，清理未完成的临时图片。
若取消发生在采样阶段提交 manifest 之前，保留配置和日志，但需要重新执行采样。

「重试失败 / 未完成项」复用当前设置下成功的图片，仅补渲染失败、缺失或过期项。
重试在后台校验源结构和已生成的 PNG 文件。源结构摘要来自实际用于渲染的副本。
旧报告缺少校验值或源副本记录时，首次重试会重新渲染对应图片。
源结构坐标改变后，该样本会报告需要重新应用朝向。点击「应用朝向并刷新预览」重算取景，
再导出 PNG。命令行可用 `revise --manifest <manifest> --request <请求.json>` 执行同一步，
请求文件内容为 `{"action": "orient", "config": {}}`。
变换、朝向、颜色选择或输出尺寸改变时，受影响图片需重新渲染。
已成功且仍有效的 PNG 不会被覆盖；旧缩略图与展示页不会冒充新设置的结果。

打开旧 collection 不会重新采样。使用 canonical 命令打开后，通过面板预览与保存朝向：

```pymol
pp_collections D:/results/collection/01_collection.json
```

可直接从命令行导出已保存的 collection：

```powershell
D:\Softwares\Pymol3\python.exe -m pymolplugins.collections.core export --manifest D:\results\collection\01_collection.json --workers 2 --width 1600 --height 1200 --retry-failed
```

在仓库根目录运行以上命令，或直接执行 `pymolplugins/collections/core.py` 的绝对路径。
无人值守采样可将上述设置保存为 JSON，使用同一个模块的
`analyze --config <配置.json> --output <collection目录>` 子命令。
配置键的完整示例见 `tests/01_collections_checks.py` 中的 `config`。
采样读取原文件；渲染时创建任务私有的临时源副本，将源摘要与实际 PNG 绑定，结束后清理。
重新导出时输入结构路径必须仍然有效。

本实现使用 PyMOL 的实例 API 与原生 PNG/ray 接口：
[PyMOL API 官方说明](https://pymol.org/pymol-command-ref.html)，
[PyMOL PNG 实现](https://github.com/schrodinger/pymol-open-source/blob/master/modules/pymol/exporting.py)。

## 2026-11-23 移植判断

基线为 `542a57a`，完整 legacy 源为 `9967d56`。以下只列 Collections 的增量，
不移植其他插件；`data/helical_bundle.pdb` 使用 main 已有文件。

| 来源提交 | 判断与保留内容 |
| --- | --- |
| `6f05ee7`、`4f1b2e2`、`472ecb0`、`0463e31`、`aab2301` | 接受 Collections 核心、并排无二级标签面板及相关合同。历史平台测试声明不作为本次证明。 |
| `2648116`、`b003ea8`、`5451a39`、`9d1284f` | 接受分层、重复指标、打分输入、引号路径和候选复用修复。 |
| `68d3154`、`1ba8605`、`8cad3af`、`6e64a38` | 接受 Rosetta 行、重试收据、源副本、独立导出 owner 和编辑锁。 |
| `daff356`、`defbf07`、`fc2d64b`、`8d5f5da`、`b0b2e2c` | 接受源路径、first-state、选择恢复、失败回滚、展示页摘要核对和 Windows 收据共享冲突重试。 |
| `b446362`、`d16925a`、`0819b72`、`27513d0` | 接受固定参考帧、格式保留、结构筛选签名、数据源确认和首状态对应修复。 |
| `74e87fb`、`277a8b7`、`64a9db6`、`1189efd`、`ebfaedb`、`5c4bb8a` | 接受非有限坐标、旧 bounds 恢复、失败隐式参考清理、非文件路径拒绝、元数据查询优化、不可达覆盖率和大小写路径身份修复。 |
| `5073739`、`5dfc08d`、`41fcc39`、`57e1710`、`ec57186`、`9717de3`、`bb98ffd`、`7a6748b`、`98d26da` | 重复：有限坐标与 CIF 预检已被 legacy 覆盖，不再覆盖较新的核心或测试。 |
| `ce034aa`、`8ede3e7` | 重复：float32 舍入溢出边界、十六进制和数值前缀检测已在 legacy 中；保留其后续 PDB 指数语法修复。 |
| `f7dec787` 相对 `277a8b7` 的独有 +55/-5 | 接受：0.1 秒进度节流和最后进度补写，以及对应 analyze/revise 检查。其余重复临时补丁不重新引入。 |

移植时去掉旧面板的导入即注册、独立菜单注册和 `activeWindow()` 父窗口选择。
canonical 导入不产生全局命令；旧 run 脚本保留四个兼容命令。未复制第二份算法、
未重做 Qt 布局，也未在此包修改全局菜单、host、帮助或安装配置。

## 本次验证与复现

在仓库根目录，使用 `D:/Softwares/Pymol3/python.exe`（PyMOL 3.1.4.1、NumPy 1.26.4）。
以下命令不创建 GUI；`native` / `regression` 将协调器可见 CPU 数限制为 2，
两个渲染 worker 各用 1 个 ray 线程，单 worker 最多 2 个线程。采样实例使用 1 个线程。

```powershell
D:/Softwares/Pymol3/python.exe -X utf8 tests/04_collections_sampling.py
D:/Softwares/Pymol3/python.exe -X utf8 tests/collections/01_package_checks.py
D:/Softwares/Pymol3/python.exe -X utf8 tests/01_collections_checks.py --stage math --output output/07_reviewed_integration/08_collections_evidence
D:/Softwares/Pymol3/python.exe -X utf8 -c "import os,runpy,sys; os.cpu_count=lambda:2; sys.argv=['tests/01_collections_checks.py','--stage','native','--output','output/07_reviewed_integration/08_collections_evidence']; runpy.run_path(sys.argv[0],run_name='__main__')"
D:/Softwares/Pymol3/python.exe -X utf8 -c "import os,runpy,sys; os.cpu_count=lambda:2; sys.argv=['tests/01_collections_checks.py','--stage','regression','--output','output/07_reviewed_integration/08_collections_evidence']; runpy.run_path(sys.argv[0],run_name='__main__')"
```

本次结果：

- 采样合同 **38/38 通过**：分层覆盖、CA/pose、first-state、固定参考、源身份、
  重定向和替换、文件发布失败回滚、非有限坐标及进度节流。预期的无效输入会打印 traceback，
  测试验证其进入失败记录且不排除后续有效样本。
  对冻结 `9967d56` 单独运行进度用例，analyze/revise 均复现每候选写盘
  `[1,2,3,4,5]`；canonical 合并后观察到 `[1,1,1,4,4]`，结束补写 `5/9`。
  这证明写入节流与尾项保留，不代表已测得整体采样速度提升。
- 包级 **4 项通过**：无宿主依赖导入、旧核心与 canonical 模块同一身份；
  真实 headless 预览的对象、选择、相机和首状态；独立进程的三个目录锁及释放后重试；
  原生 PNG 渲染、有效收据复用、损坏 PNG 拒绝，以及同大小/同 mtime 的源字节变化拒绝。
  首轮前三项通过，第四项因测试误将全 Gly 样本中的 ALA 替换为 GLY 而未改变输入；
  改成 GLY→ALA 后只重跑该项并通过，不将测试构造错误记为产品缺陷。
- `math`、`native`、`regression` 均退出 **0**。6 个候选选出 3 个结构、拒绝 3 个重复；
  shape RMSD 接近 0、pose RMSD 约 10 Å；两个不同 PID 重叠渲染 3 张 640×480 有内容的 PNG。
  固定样本保护、竖图取景、真实 Windows 共享冲突、取消后 worker 回收及选择性重试通过。
  小型结果记录由本地整合任务归档在 `output/07_reviewed_integration/08_collections_evidence/04_*_results.json`，不随产品分发；输入和 PNG 可由命令重建。

上述算法验收没有运行 Qt 窗口或操作当前桌面。统一安装包的主窗口 parent、主题、
`pp_collections` GUI 路径和菜单 / completion / help 由独立隐藏桌面验收覆盖，
具体结果见整合验收记录；`--stage qt` 需先生成 `native` 的输入。
历史 Windows/macOS GUI 声明不作为本次证明。没有测量大数据集性能或声称加速比。
