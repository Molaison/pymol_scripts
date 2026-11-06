# Batch Processing：批量处理结构

> 历史详细参考：本页保留 2026-11-06 重写前的条款、示例和验收范围。旧版本措辞和测试结果不自动适用于当前版。操作请从 [现行页面](batch_processing_specification.md) 开始。

对自己的结构目录或 CSV 运行相同的成对操作：打开 **Plugin → PymolPlugins: Batch Processing**，选择固定参考或按 ID 配对，先点 **Preview pairs**，核对后点 **Run batch**。结果写入单独输出目录及 JSON 报告。直接跳到 [操作步骤](#usage)；后面的合同和验收保留 0.6.0 的历史背景。

Window hosting in 0.7.0 is shared with Structure Annotator: embedded in PyMOL
by default, with a top-right Window mode menu and remembered floating geometry.
Changing mode preserves preparation, preview and running work. See
[workspace window modes](workspace_window_modes.md).

Status: implemented and release-validated on Windows PyMOL 3.1.3.1 / PyQt5,
2026-09-16. User request: all against one reference, or one A/B pair per ID
with additional operations between A and B.
Existing 0.5.1 work and unrelated dirty changes remain preserved.

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

## Contract

- The user confirmed folder/CSV structure files as the input scope. Support
  file patterns and explicitly chosen CSV ID/A/B columns. Do not require all
  structures to be loaded into PyMOL first.
- Reference mode resolves an A set and one exact B reference. Exclude the
  reference itself from the A set, and reject an empty resulting plan.
- ID-pair mode supports literal `{id}` templates, wildcard patterns (one `*`
  captures the ID; an explicit `{id}` permits other wildcards), and filename
  regexes with `(?P<id>...)` or exactly one capture group. Regex mode takes two
  directories and two separate expressions, matched against complete filenames.
  Pair equal IDs, use natural ID order for files, preserve CSV row order, and
  allow an explicit ID subset in the requested order.
  Missing partners and duplicate IDs are errors, not skipped or position-paired.
- A per-pair recipe contains ordered PyMOL command lines. `{A}`, `{B}`, `{id}`,
  `{index}`, `{A_source}` and `{B_source}` identify the current pair; file mode
  also exposes its output path. Alignment is an editable initial recipe, not
  the only supported operation. Validate all templates before any mutation.
- Preview lists exact pairs and rendered steps without loading structures,
  changing the scene, or writing files. Editing inputs invalidates the preview.
- Execute sequentially on PyMOL's main thread, reusing the checked parser
  dispatch already used by `pp_foreach`. GUI execution yields between steps;
  Stop takes effect at a step boundary, not in the middle of a PyMOL call.
- Failures report pair ID, step, command and traceback. Stop on error is the
  default; continuing with the next pair is explicit. Completed arbitrary
  commands are not claimed to be transactional or generically undoable.
- Load only owned temporary objects and automatically save both processed A and
  B structures by default, as explicitly requested, retaining their file format.
  Never overwrite source structures or existing automatic output files. Release
  only owned temporaries. Every pair gets unique temporary object names; a fixed
  reference source stays loaded once and is copied into a fresh B for each pair.
- Provide a discoverable Batch processing dialog and `pp_batch` command,
  preview/run/stop controls, per-pair progress and JSON result export. Reuse
  existing target resolution, command parsing and Windows path helpers rather
  than adding a navigation cursor, transform journal or cache implementation.

## Acceptance

1. Pure planning tests: fixed reference, self-exclusion, natural ID order,
   subsets, missing/duplicate IDs, template/glob/regex captures and path handling.
2. Runner tests: exact pair/step order, preflight with zero mutation, stop versus
   continue, cancellation, source/output isolation and owned-only cleanup.
3. Real Windows PyMOL: visible dialog preview/run for all matching modes; actual
   alignment plus a second A/B operation; parser failure reports; file outputs
   and unchanged input bytes; compact geometry and screenshot review.
4. Existing regression plus new tests, relevant annotation smoke,
   final ZIP source-byte/CRC/version audit and real Plugin Manager installation.

Implementation evidence belongs in `docs/validation/batch_processing_2026-09-16.md`;
logs, JSON and screenshots use the existing `output/` directory. Windows tests
run serially with the bundled PyMOL Python from `/mnt/c`, isolated temporary
fixtures, and blocking terminal supervision. PyMOL 2.x and other Qt bindings
remain separate compatibility gates.

## Investigation

`pp_foreach` resolves one target at a time and has checked same-line parser
completion, but no pair identity or per-pair multi-step recipe. The annotator's
batch annotation writes labels to selected rows; it is not a structure-processing
loop. The read-only scout returned upstream HTTP 502 without editing files;
the main thread owns the integration decisions.

## Shared UI - 0.6.1

Batch Processing applies the same local theme as Structure Annotator from both
entry paths. Normal controls share its font, colors, spacing, table treatment,
primary action, error feedback and progress line; commands remain monospace.
File locations uses the same collapsible control. Icon tools have explicit
34 x 34 bounds, and compact input forms remain scrollable without clipping
their controls. Application-wide PyMOL styling and processing behavior are
unchanged. Real Qt comparisons and screenshots are recorded in
`docs/validation/batch_ui_consistency_2026-09-16.md`.
