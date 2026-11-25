# 批量结构对照

从 **Review → Tools → Compare or process structures…** 打开 **Batch**，选择 **Review in PyMOL**。设置候选和参考后，点 **Preview pairs** 核对配对，再点 **Create and open review…** 选择保存位置。工具生成一个新的比较目录，并在当前 Annotator 窗口切回审阅。下次可直接打开目录内的 sum_scores.csv。

从已有本地审阅转入 Batch 时，可选择表格中选中的行、当前筛选集合或整个数据源。返回 Batch 保留命令、输出位置和错误处理草稿。Batch 占用场景时暂停场景预载和审阅导航，返回 Review 后恢复。远端数据需先准备本机可读的输入文件。

| 任务 | 配对设置 | Review view |
| --- | --- | --- |
| 一组结构对同一参考 | All against reference，选候选目录或文件模式，再选参考文件 | Aligned overlay |
| 每个候选对应自己的父本 | CSV table → Reference per row。指定候选路径、参考路径、唯一 ID。多个候选可重复同一参考路径 | Sequence differences |
| 逐轮审阅优化路径 | CSV table → Reference per row。每轮按展示顺序排列，把晋级候选放在该轮最后一行 | Optimization path |

回看差异时，参考用浅灰半透明主链，候选用青色 cartoon。发生替换、插入或缺失的残基在对应一侧用 sticks 显示，候选变化位点为橙色。优化路径额外用青色虚线显示新增接近关系，用橙色虚线显示消失的关系。默认比较变化位点侧链与其他残基或配体的重原子在 4 Å 内是否接近。每类最多画 12 条最近的线，PyMOL 控制台显示完整计数。这是几何对照，氢键、作用类型和能量需要专门的分析。

普通对照将每个候选对齐到该行参考。优化路径用第一轮父本建立同一坐标系和相机，每轮内复用一个父本。当前行的 Promotion 显示 Next parent 时，下一行开始用它作新父本。晋级顺序由 CSV 决定。

## 由脚本或 %pymol 生成输入表

下面有两轮。第一轮查看甲、乙两个候选，乙晋级，随后查看乙的子代。路径可用相对 CSV 所在目录的文件路径。

```csv
ID,Candidate file,Parent file
trial-a,models/child-a.pdb,models/parent-1.pdb
trial-b,models/child-b.pdb,models/parent-1.pdb
trial-c,models/child-c.pdb,models/child-b.pdb
```

CSV 的 ID 必须非空且唯一，行中的 Parent file 决定配对。同一参考可对应多个候选；候选和参考不能是同一文件或其硬链接。优化路径换父本时，新父本必须是上一行的候选。文件模式可用已有 ID 模板、通配符或正则完成一对一配对。

生成的 comparison-* 目录包含 sum_scores.csv 和 review.json，引用原有结构文件，不修改或复制源结构，也不保留输入表的其他打分列。预览后输入文件的大小或修改时间变化会要求重新预览；写入期间也会再次检查。取消或写入失败会清理本次新建目录。成功发布以 sum_scores.csv 的原子重命名为界；发布后的取消不撤销已完成的目录。源文件仍须保留在表中记录的位置。Batch Processing 的 **Process and save structures** 适用于要运行配方并导出结构坐标的任务。

如果已有完整的打分表，保留其原始列和行顺序。在普通 Annotator review.json 的 steps 中，先绑定父本路径列并将 fixed 设为 true，再加入 candidate 和 commands。用现有 **Review steps** 编辑回访和重新加载时的规则。需要指定对齐链时，可把下面的命令用在 Step commands，花括号由 Annotator 替换成场景对象名。

```pymol
pp_compare {candidate}, {parent}, mode=mutations, mobile_fit=chain A, parent_fit=chain A
```

默认用蛋白 Cα 对齐，参与拟合的链用 PyMOL 序列比对建立对应。选择链内局部残基拟合时，仍按完整链建立序列对应，仅用所选残基中匹配的 Cα 做最小二乘刚体拟合；至少需三对 Cα。其他链用相同的 segment、chain 和 residue ID 对应，插入码属于 residue ID 的一部分。链名、编号或同源关系不一致时，应明确选择拟合链并核对对应关系；本工具不推断非拟合链或配体的任意重编号映射。

模式 overlay 只做对齐和叠合，mutations 加上序列差异，contacts 再加变化位点的接触差异，fit 只做坐标叠合。pp_compare 只移动第一个对象的第一帧，第二个对象的坐标和变换矩阵保持不变。已有对象 TTT 会计入拟合，其他帧不变；比较和接触虚线均使用第一帧。生成输入要求本机可读路径。通过 Annotator 的 SSH 数据源打开远端 review 时，也可以在标准 Review steps 中使用同一命令。
