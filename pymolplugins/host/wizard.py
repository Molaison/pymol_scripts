"""On-demand, modeless introduction to the existing toolkit workflows."""

from pymol.Qt import QtCore, QtGui, QtWidgets
from html import escape
import traceback

from pymolplugins import __version__
from pymolplugins.annotation.panel_style import apply_style
from .help_catalog import exact_help


_wizard = None
_TOPICS = ("welcome", "data", "review", "tools", "finish")
_PAGES = (
    (
        "认识 PymolPlugins",
        "把结构查看、连续审阅和常用分析放在同一个工作流里。",
        "<h3>从一份结构，到一组候选</h3>"
        "<p><b>打开数据</b>：读取本机或 SSH 上的结构文件夹、CSV / TSV 评分表。</p>"
        "<p><b>连续审阅</b>：逐个查看候选、参考结构和分数，记录标签并保存进度。</p>"
        "<p><b>结构工具</b>：浏览对象或 group，设置显示样式、对齐结构、查看接触。</p>"
        "<p><b>批处理</b>：为多份结构配置处理步骤，先预览配对，再运行。</p>"
        "<hr><p>接下来用四步熟悉入口。你可以边看向导边操作 PyMOL，"
        "也可以随时关闭，再从插件菜单或审阅面板的 <b>Help</b> 打开。</p>",
    ),
    (
        "打开第一份数据",
        "先选位置，再选结构文件夹或评分表。",
        "<h3>在 Structure Annotator 中打开</h3>"
        "<p><b>1 · Open from</b><br>本机文件选 <b>This computer</b>；"
        "远端文件选已经配置好的 SSH Host。</p>"
        "<p><b>2 · Path</b><br>粘贴结构文件夹或 CSV / TSV 的路径，"
        "也可以点 <b>Browse…</b>。SSH 使用服务器路径，例如 "
        "<code>/data/review/scores.tsv</code>。</p>"
        "<p><b>3 · Open</b><br>若要求选择 <b>Structure column</b>，"
        "选择含 PDB / CIF 路径的列。其他设置可先保留默认值。</p>"
        "<p>已有同目录 <code>review.json</code> 时会读取审阅配置。"
        "导入页的 <b>Check review bundle</b> 可先检查路径、列和配置问题。</p>",
    ),
    (
        "审阅并保存",
        "默认点击完整的单候选单选标签即可保存。",
        "<h3>一次候选的审阅循环</h3>"
        "<p><b>查看</b>：检查当前结构、分数及配置好的 Review steps / reference。</p>"
        "<p><b>判断</b>：选择标签。暂时不能判断时，使用 "
        "<b>Review tools → Mark current for later</b>。</p>"
        "<p><b>保存</b>：单候选的单选标签在必填项完整时，点击即保存。"
        "<b>More</b> 中可调整点击保存和保存后前进，这些偏好会自动保存。"
        "<b>Previous / Next</b> 用于导航。</p>"
        "<h3>下次继续</h3>"
        "<p><b>Open data… → Continue last review</b> 恢复最近一次审阅；"
        "<b>Recent configurations…</b> 可选择其他记录。</p>"
        "<p><b>Review tools → Export annotations… / Export review handoff…</b> "
        "用于导出标签和交接信息。SSH 审阅结果保存在本机，源结构和评分表保持不变。</p>",
    ),
    (
        "浏览、分析与展示",
        "选择一个任务，查看现有命令帮助和示例。",
        "<p>在下方选择常用任务。示例里的对象名、group 名和链名"
        "需要换成当前场景中的实际名称，再粘贴到 PyMOL 命令行运行。</p>"
        "<p>点击主工具栏的 <b>Views</b> 调整已加载结构的显示。"
        "审阅时可从 <b>View recipes…</b> 将同类视图加入步骤："
        "先预览草稿，再确认插入并 Apply。对齐前请核对候选和参考的链对应。</p>",
    ),
    (
        "开始使用",
        "从你的数据出发，需要时再查看帮助。",
        "<h3>常用入口</h3>"
        "<p><b>Structure Annotator</b>：打开数据、逐条审阅、记录标签。</p>"
        "<p><b>Batch files</b>：配置文件或表格中的结构配对与处理步骤。</p>"
        "<p><b>Command Help</b> 或 <code>pp_help 主题</code>："
        "在 PyMOL 控制台查询命令用法，例如 <code>pp_help navigation</code>。</p>"
        "<p><b>Doctor</b>：检查本机能力及可选后端。"
        "部分分析需要额外后端，具体要求见对应命令帮助。</p>"
        "<hr><p>再次打开：<b>Plugin → PymolPlugins → Getting Started</b>，"
        "或输入 <code>pp_wizard</code>。</p>",
    ),
)
_TOOLS = (
    ("结构显示预设", "pp_view"),
    ("对象 / group 导航", "navigate"),
    ("显示表面", "surface"),
    ("结构对齐", "pp_align"),
    ("接触分析", "interactions"),
    ("展示样式", "publication"),
)


def _browser(html, parent):
    browser = QtWidgets.QTextBrowser(parent)
    browser.setFrameShape(QtWidgets.QFrame.NoFrame)
    browser.document().setDocumentMargin(10)
    browser.setOpenExternalLinks(False)
    browser.setHtml(html)
    browser.setMinimumHeight(120)
    return browser


def _button(text, callback, layout):
    button = QtWidgets.QPushButton(text)
    button.setAutoDefault(False)
    button.clicked.connect(callback)
    layout.addWidget(button)
    return button


class GettingStartedWizard(QtWidgets.QWizard):
    """Explain toolkit workflows without changing the user's scene or settings."""

    def __init__(self):
        super().__init__()
        self.setFont(QtGui.QFont("Segoe UI", 10))
        apply_style(self, "gettingStartedWizard")
        self.setWindowTitle("PymolPlugins · 入门向导")
        self.setWizardStyle(QtWidgets.QWizard.ModernStyle)
        self.setModal(False)
        self.resize(700, 590)
        self.setButtonText(self.BackButton, "上一步")
        self.setButtonText(self.NextButton, "下一步")
        self.setButtonText(self.CancelButton, "关闭")
        self.setButtonText(self.FinishButton, "完成")
        self.pages = []
        for index, (title, subtitle, body) in enumerate(_PAGES):
            page = QtWidgets.QWizardPage()
            page.setTitle(f"{index + 1} / {len(_PAGES)} · {title}")
            page.setSubTitle(subtitle)
            layout = QtWidgets.QVBoxLayout(page)
            page.body = _browser(body, page)
            layout.addWidget(page.body, 1)
            self.addPage(page)
            self.pages.append(page)
        self.pages[0].layout().addWidget(QtWidgets.QLabel("PymolPlugins " + __version__))
        self._build_actions()
        self._build_tools()

    def _build_actions(self):
        for index, actions in (
            (1, (("打开数据界面", "data"), ("SSH 连接说明", "ssh"))),
            (2, (("打开审阅面板", "review"),)),
            (4, (("打开审阅面板", "review"), ("打开批处理", "batch"))),
        ):
            page = self.pages[index]
            row = QtWidgets.QHBoxLayout()
            page.action_buttons = {}
            for label, action in actions:
                page.action_buttons[action] = _button(
                    label, lambda checked=False, key=action: self._launch(key), row,
                )
            page.layout().addLayout(row)
            page.feedback = QtWidgets.QLabel()
            page.feedback.setWordWrap(True)
            page.feedback.setTextFormat(QtCore.Qt.PlainText)
            page.feedback.hide()
            page.layout().addWidget(page.feedback)

    def _build_tools(self):
        page = self.pages[3]
        self.tool_choice = QtWidgets.QComboBox()
        self.tool_choice.setAccessibleName("选择常用任务")
        for label, topic in _TOOLS:
            self.tool_choice.addItem(label, topic)
        page.layout().addWidget(self.tool_choice)
        self.tool_help = _browser("", page)
        page.layout().addWidget(self.tool_help, 2)
        row = QtWidgets.QHBoxLayout()
        self.copy_button = _button("复制示例命令", self._copy_example, row)
        page.layout().addLayout(row)
        self.tool_choice.currentIndexChanged.connect(self._update_tool)
        self._update_tool()

    def _update_tool(self):
        help_entry = exact_help(self.tool_choice.currentData())
        self.example = help_entry["example"]
        content = (
            f"<p>{escape(help_entry['purpose'])}</p>"
            f"<p><b>用法</b><br><code>{escape(help_entry['minimal_usage'])}</code></p>"
            f"<p><b>示例</b><br><code>{escape(self.example)}</code></p>"
        )
        if help_entry["undo"]:
            content += f"<p><b>恢复方式</b><br>{escape(help_entry['undo'])}</p>"
        self.tool_help.setHtml(content)
        self.copy_button.setText("复制示例命令")

    def _copy_example(self):
        QtWidgets.QApplication.clipboard().setText(self.example)
        self.copy_button.setText("已复制 · 替换名称后运行")

    def _launch(self, action):
        page = self.currentPage()
        page.feedback.hide()
        try:
            from .plugin import (
                show_batch_processing_from_toolkit,
                show_structure_annotator_from_toolkit,
            )
            if action == "batch":
                show_batch_processing_from_toolkit()
            else:
                panel = show_structure_annotator_from_toolkit()
                if action == "data":
                    panel._show_import()
                elif action == "ssh":
                    panel.source_location.show_quick_start()
        except Exception as exc:
            page.feedback.setText("打开失败：" + str(exc))
            page.feedback.show()
            traceback.print_exc()

    def show_topic(self, topic):
        self.setStartId(0)
        self.restart()
        for _ in range(_TOPICS.index(topic)):
            self.next()
        self.show()
        self.raise_()
        self.activateWindow()


def show_wizard(topic="welcome"):
    """Open the shared guide at a named page, retaining Back navigation.

    Args:
        topic: One of welcome, data, review, tools, or finish.
    Returns:
        The modeless wizard, reused when opened from another entry point.
    """
    if topic not in _TOPICS:
        raise ValueError("Unknown wizard topic %r; choose %s" % (topic, ", ".join(_TOPICS)))
    global _wizard
    if _wizard is None:
        _wizard = GettingStartedWizard()
    _wizard.show_topic(topic)
    return _wizard
