# Batch Processing：批量处理结构

对自己的结构目录或 CSV 运行相同的成对操作：打开 **Plugin → PymolPlugins: Batch Processing**，选择固定参考或按 ID 配对，先点 **Preview pairs**，核对后点 **Run batch**。结果写入单独输出目录及 JSON 报告。直接跳到 [操作步骤](#usage)；后面的合同和验收保留 0.6.0 的历史背景。

窗口默认嵌入 PyMOL；可在窗口模式菜单切换浮动，当前计划、预览和运行状态保留。详见 [窗口模式](workspace_window_modes.md)。

## Usage

打开 **Plugin → PymolPlugins: Batch Processing**，或在 PyMOL 控制台运行 `pp_batch`。也可从 Annotator 的 **Data → Batch processing...** 进入；它带入当前数据源和路径映射，不移动审阅位置。

### 所有结构对同一参考

```pymol
pp_batch "D:/structures", "D:/reference.pdb", output="D:/processed", preview=1
```

把示例路径替换成实际目录、参考结构和输出目录。界面选择 **All against reference**：A 为目录或文件 glob，B 为一个参考结构。CSV 模式选择 A 路径列和可选 ID 列；相对路径从表格目录解析，也可选择基准目录或映射。只读取本地或已挂载路径，不自动提交远端 staging；参考文件自身会从 A 中排除。

### 按 ID 配对

选择 **A / B by ID**，按文件名或 CSV 列指定配对。预览前把示例路径替换为真实路径：

```pymol
pp_batch "D:/A/A_*.pdb", "D:/B/B_*.pdb", mode=paired, match=glob, output="D:/paired", preview=1
pp_batch "D:/A", "D:/B", mode=paired, match=regex, a_pattern="A_(?P<id>[0-9]+)[.]pdb", b_pattern="B_(?P<id>[0-9]+)[.]pdb", output="D:/paired", preview=1
pp_batch "D:/pairs.csv", B_path, source=csv, mode=paired, a_column=A_path, id_column=ID, output="D:/paired", preview=1
```

文件名可用 `{id}` 模板、一个用于提取 ID 的 `*`，或带命名组 `(?P<id>...)` 的正则。glob 也支持 `?` 和字符类；多个 `*` 时需显式使用 `{id}`。正则只扫描所选目录的直接文件并匹配完整文件名。CSV 模式明确选择 ID、A 路径和 B 路径列。

ID 按字符串精确匹配；`1-20,25` 表示未补零的数字范围，源 ID 带前导零时显式填写。缺少请求的 ID、缺失配对、空 ID 或重复 ID 会报错；不匹配正则的文件不属于输入集合。

### 编写命令并运行

在 **Commands per pair** 按执行顺序输入 PyMOL 命令。例如：

```pymol
align {A}, {B}, cycles=0
translate [0, 2, 0], {B}
```

`{A}`、`{B}` 是当前加载的对象；还可使用 `{id}`、`{index}`、`{A_source}`、`{B_source}`、`{output_A}`、`{output_B}`、`{output_dir}` 和 `{output}`（等同 `{output_A}`）。命令中的路径占位符请加引号；空行和 `#` 开头行会跳过。关闭 B 输出后不能使用 `{output_B}`。

点击 **Preview pairs**，检查每对路径、渲染后的命令和计划输出；改变输入后重新预览。确认后点 **Run batch**。控制台中将 `preview=1` 改为 `preview=0` 执行。`encoding` 和 `delimiter` 可在控制台设置。**Stop** 在步骤之间生效，不能中断正在运行的单条 load、align 或 save。

默认在独立目录保存 A、B，文件名为 `000001_A_<原文件名>` 和 `000001_B_<原文件名>`，并生成 `batch-<运行标识>.jsonl` 和最终 `.json` 报告；**Export results** 可另存报告。自动输出不会覆盖已有文件。

默认遇错停止；**Continue with next pair** 可明确选择继续。报告记录已完成步骤、输出、失败命令和 traceback；取消或失败时当前对可能已有部分输出，已完成输出保留。自定义命令是可信的可执行配置，其任意副作用不保证回滚。

## 维护和验收批处理

预览必须列出精确配对、命令和输出，不加载结构、不修改场景或写文件；输入变化后旧预览失效。文件配对按 ID 自然排序，CSV 保留行顺序；明确请求的 ID 子集按请求顺序执行。缺失或重复 ID 是错误，不按行位置猜配。

真实运行在 PyMOL 主线程按步骤顺序执行。固定 reference 只读，每对使用独立临时对象和 reference 副本；只清理当前任务拥有的临时对象。保留原格式，不覆盖源结构或已有自动输出。任意命令的外部副作用不承诺事务回滚。

验收时分别检查固定参考、自身排除、模板/glob/正则/CSV 配对、空或重复 ID、预览零修改、执行顺序、停止与继续、源哈希、输出坐标和长序列临时对象有效性。Qt 检查包含紧凑布局、真实运行时窗口切换及结果报告；真实 PyMOL 2.x 与其他 Qt 绑定需分别验证。原始合同、完整验收与历史调查见 [批处理详细参考](14_reference_batch_2026-11-06.md)，历史结果见 [验收记录](validation/batch_processing_2026-09-16.md)。

<details>
<summary>旧标题及详细条款索引</summary>

原地址保留为兼容入口；以下条目指向重写前的详细参考。

<a id="contract"></a>
- [Contract](14_reference_batch_2026-11-06.md#contract)
<a id="acceptance"></a>
- [Acceptance](14_reference_batch_2026-11-06.md#acceptance)
<a id="investigation"></a>
- [Investigation](14_reference_batch_2026-11-06.md#investigation)
<a id="shared-ui---061"></a>
- [Shared UI - 0.6.1](14_reference_batch_2026-11-06.md#shared-ui---061)

</details>
