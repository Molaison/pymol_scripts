"""One location for view aids and optional Molaison tools."""
import json

from pymol import cmd
from pymol.Qt import QtWidgets

from . import integrations
from .ui import CompactLabel, primary


class ViewTools(QtWidgets.QDialog):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.contact_groups = []
        self.setWindowTitle("结构查看工具")
        self.resize(400, 300)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(8)
        self.tool = QtWidgets.QComboBox()
        self.tool.addItems(["PyMOL 显示", "配色与光照", "Molaison Contacts", "Molaison 按序列选择",
                            "服务端相互作用分析"])
        layout.addWidget(self.tool)
        self.stack = QtWidgets.QStackedWidget()
        self.tool.currentIndexChanged.connect(self.stack.setCurrentIndex)
        layout.addWidget(self.stack)
        view, form = self._page()
        self.representation = QtWidgets.QComboBox()
        self.representation.addItems(["cartoon", "sticks", "surface", "spheres", "lines"])
        self.visible = QtWidgets.QCheckBox("显示")
        self.visible.setToolTip("取消勾选为隐藏所选表示方式")
        self.visible.setChecked(True)
        self.sequence_view = QtWidgets.QCheckBox("显示 PyMOL 序列条")
        self.sequence_view.setChecked(bool(cmd.get_setting_int("seq_view")))
        self.sequence_view.toggled.connect(lambda value: cmd.set("seq_view", int(value)))
        form.addRow("表示方式", self.representation)
        form.addRow(self.visible)
        form.addRow(self.sequence_view)
        _, form = self._page()
        self.color_button = QtWidgets.QPushButton("配色…")
        self.color_button.clicked.connect(lambda: self._open_appearance("color"))
        self.lighting_button = QtWidgets.QPushButton("光照…")
        self.lighting_button.clicked.connect(lambda: self._open_appearance("lighting"))
        form.addRow(self.color_button)
        form.addRow(self.lighting_button)
        _, form = self._page()
        self.contacts = QtWidgets.QComboBox()
        self.contacts.addItems(["interchain_basic", "polar_qc", "ligand_contacts", "ligand_quick", "ligand_site"])
        self.partner1 = QtWidgets.QLineEdit("chain A")
        self.partner2 = QtWidgets.QLineEdit("chain B")
        form.addRow("接触预设", self.contacts)
        form.addRow("一侧选择", self.partner1)
        form.addRow("另一侧选择", self.partner2)
        self.contacts.setToolTip("几何接触分析，不是能量评分；选择自动限制在当前对象中。")
        _, form = self._page()
        self.sequence = QtWidgets.QLineEdit()
        self.sequence.setPlaceholderText("如 GSG 或 ACDEFG")
        form.addRow("查询序列", self.sequence)
        self.sequence.setToolTip("在当前整个对象内精确匹配；返回命名选择，可直接应用设计条件。")
        _, form = self._page()
        self.interchain = QtWidgets.QCheckBox("仅链间")
        self.interchain.setChecked(True)
        form.addRow(self.interchain)
        self.hbond_model = QtWidgets.QComboBox()
        self.hbond_model.addItems(["rosetta", "plip"])
        form.addRow("氢键模型", self.hbond_model)
        self.hbond_model.setToolTip("使用与 Web 相同的相互作用分析接口；结果保存在主面板的结果详情。")
        row = QtWidgets.QHBoxLayout()
        apply = primary(QtWidgets.QPushButton("应用"))
        apply.setAutoDefault(False)
        self.apply_button = apply
        apply.clicked.connect(self.apply)
        row.addWidget(apply)
        undo = QtWidgets.QPushButton("撤销")
        undo.setAutoDefault(False)
        undo.setToolTip("清除本窗口生成的接触组")
        self.undo_button = undo
        undo.clicked.connect(self.undo)
        row.addWidget(undo)
        layout.addLayout(row)
        self.status = CompactLabel()
        layout.addWidget(self.status)
        self.tool.currentIndexChanged.connect(self._tool_changed)

    def _tool_changed(self, mode):
        self.apply_button.setVisible(mode != 1)
        self.undo_button.setVisible(mode != 1)

    def _open_appearance(self, tab):
        from pymolplugins.host.show_presets import show_presets

        try:
            if not self.panel.current or self.panel._busy:
                raise ValueError("请先打开一个结构，并等待当前操作结束。")
            source = self.panel.bridge.assert_current(self.panel.current)
            show_presets(tab=tab, source=source)
        except Exception as error:
            self.status.setText(str(error))
            self.status.set_error()
            return
        self.hide()

    def _page(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QFormLayout(widget)
        self.stack.addWidget(widget)
        return widget, layout

    @staticmethod
    def _check(result):
        if isinstance(result, dict):
            errors = result.get("errors")
            if errors or result.get("ok") is False or result.get("status") in ("failed", "error"):
                raise ValueError(str(errors or result.get("message") or result))

    def apply(self):
        try:
            sid = self.panel.current
            if not sid or self.panel._busy:
                raise ValueError("请先打开输入，并等待当前操作结束。")
            obj = self.panel.bridge.assert_current(sid)
            mode = self.tool.currentIndex()
            if mode == 0:
                (cmd.show if self.visible.isChecked() else cmd.hide)(self.representation.currentText(), "model " + obj)
            elif mode == 1:
                return
            elif mode == 2:
                a, b = self.partner1.text().strip(), self.partner2.text().strip()
                if not a or not b or not cmd.count_atoms("(%s) and model %s" % (a, obj)) or not cmd.count_atoms("(%s) and model %s" % (b, obj)):
                    raise ValueError("接触分析两侧均须在当前对象中选中原子。")
                group, result = integrations.contacts(self.contacts.currentText(), obj, a, b)
                self._check(result)
                self.contact_groups.append(group)
            elif mode == 3:
                if not self.sequence.text().strip():
                    raise ValueError("请输入查询序列。")
                name, result = integrations.select_sequence(obj, self.sequence.text().strip())
                self._check(result)
                if not cmd.count_atoms(name):
                    raise ValueError("未找到匹配序列。")
                self.panel.selection.setText(name)
                self.panel.inspect_selection()
            else:
                payload = {"inter_chain_only": self.interchain.isChecked(),
                           "hbond_model": self.hbond_model.currentText()}

                def done(result):
                    self.panel._set_report(json.dumps(result, ensure_ascii=False, indent=2))
                    self.panel.report_toggle.setText("分析详情")
                    self.status.setText("分析完成 · 结果详情")
                self.panel.request(
                    lambda: self.panel.api.request("POST", "/api/sessions/%s/interactions/analyze" % sid, payload),
                    done, "正在分析结构相互作用…")
                return
            self.status.setText("查看工具已应用。")
        except Exception as error:
            self.status.setText(str(error))
            self.status.set_error()

    def undo(self):
        try:
            for group in self.contact_groups:
                integrations.clear_contacts(group)
            self.contact_groups.clear()
            self.status.setText("已清除本窗口生成的接触组。")
        except Exception as error:
            self.status.setText(str(error))
            self.status.set_error()
