"""A single native workflow, with asynchronous HTTP and main-thread PyMOL calls."""
import json
import os
from pathlib import Path
import threading
import uuid
import zipfile

from pymol import cmd
from pymol.Qt import QtCore, QtWidgets

from .bridge import DisplayBridge
from .client import ApiClient, ApiError
from .credentials import load_token, save_token
from .editors import ConditionEditor, OperationEditor
from .ui import CompactLabel, Disclosure, button, collapsible, icon_button, menu_button, primary

Signal = getattr(QtCore, "Signal", None) or QtCore.pyqtSignal


class WorkflowPanel(QtWidgets.QWidget):
    finished = Signal(int, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = QtCore.QSettings("Molaison", "RFD4Studio")
        self.api = None
        self.capabilities = {}
        self.bridge = DisplayBridge()
        self.sessions = {}
        self.current = None
        self.job_data = None
        self.job_api = None
        self.job_active = False
        self.accept_results = True
        self.batches = {}
        self._callbacks = {}
        self._request_id = 0
        self._busy = False
        self._applying_result = 0
        self._polling = False
        self._tools = None
        self.last_error = None
        self.finished.connect(self._finish_request)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.poll_job)
        self._build()
        self._set_ready()
        QtCore.QTimer.singleShot(0, self._restore_connection)

    def _build(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)
        connection = QtWidgets.QHBoxLayout()
        self.connection_label = CompactLabel("● 未连接")
        connection.addWidget(self.connection_label, 1)
        self.connect_button = icon_button(button("", self.connection_dialog, connection),
                                          QtWidgets.QStyle.SP_DriveNetIcon, "连接设置")
        root.addLayout(connection)
        active = QtWidgets.QHBoxLayout()
        self.object_combo = QtWidgets.QComboBox()
        self.object_combo.setToolTip("当前输入 / 结果对象")
        self.object_combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.object_combo.setMinimumContentsLength(12)
        self.object_combo.activated.connect(self._choose_session)
        active.addWidget(self.object_combo, 1)
        self.tools_button = button("查看工具", self.open_tools, active)
        root.addLayout(active)
        self.tabs = QtWidgets.QTabWidget()
        root.addWidget(self.tabs, 1)
        self._build_input()
        self._build_edit()
        self._build_generate()
        self._build_results()
        self.progress = QtWidgets.QProgressBar()
        self.progress.setMaximumHeight(5)
        self.progress.setTextVisible(False)
        self.progress.hide()
        root.addWidget(self.progress)
        footer = QtWidgets.QHBoxLayout()
        self.status = CompactLabel("就绪")
        footer.addWidget(self.status, 1)
        task_details = QtWidgets.QWidget()
        task = QtWidgets.QHBoxLayout(task_details)
        task.setContentsMargins(0, 0, 0, 0)
        self.job_id = QtWidgets.QLineEdit()
        self.job_id.setPlaceholderText("任务 ID")
        self.job_id.setToolTip("完整任务 ID；断线后查询原任务，不会重新提交")
        task.addWidget(self.job_id, 1)
        self.recover_button = button("查询", self.recover_job, task)
        self.cancel_button = button("取消等待", self.cancel_job, task)
        self.cancel_button.setToolTip("丢弃此任务结果；已开始的推理仍会安全退出")
        self.task_toggle = Disclosure("任务", task_details)
        footer.addWidget(self.task_toggle)
        root.addLayout(footer)
        root.addWidget(task_details)
        self.setMinimumWidth(410)

    def _page(self, label):
        content = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(10)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(content)
        self.tabs.addTab(scroll, label)
        return layout

    def _build_input(self):
        layout = self._page("1 输入")
        self.import_button = primary(button("打开 CIF…", self.import_file, layout))
        self.import_button.setToolTip("打开带注释的 CIF / mmCIF，保留设计条件")
        examples = QtWidgets.QHBoxLayout()
        self.example_combo = QtWidgets.QComboBox()
        self.example_combo.setToolTip("设计示例")
        examples.addWidget(self.example_combo, 1)
        self.example_button = button("加载示例", self.import_example, examples)
        layout.addLayout(examples)
        import_options = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(import_options)
        self.parse_preset = QtWidgets.QComboBox()
        self.parse_preset.addItems(["minimal", "rcsb"])
        self.assembly = QtWidgets.QLineEdit("1")
        self.model = QtWidgets.QSpinBox()
        self.model.setRange(-1, 9999)
        self.model.setValue(-1)
        self.model.setSpecialValueText("自动")
        self.remove_waters = QtWidgets.QCheckBox("去水")
        self.hydrogen = QtWidgets.QComboBox()
        self.hydrogen.addItem("保留氢", "keep")
        self.hydrogen.addItem("移除氢", "remove")
        form.addRow("导入预设", self.parse_preset)
        form.addRow("组装体", self.assembly)
        form.addRow("模型", self.model)
        form.addRow(self.remove_waters)
        form.addRow("氢策略", self.hydrogen)
        collapsible("导入选项", import_options, layout)
        native = QtWidgets.QWidget()
        native_layout = QtWidgets.QVBoxLayout(native)
        row = QtWidgets.QHBoxLayout()
        self.native_object = QtWidgets.QComboBox()
        row.addWidget(self.native_object, 1)
        button("刷新", self.refresh_objects, row)
        native_layout.addLayout(row)
        native_button = button("导入对象", self.import_object, native_layout)
        native_button.setToolTip("仅导入 state 1 的结构，需要重新设置设计条件。\n保留已有条件请打开原始注释 CIF。")
        collapsible("使用已有 PyMOL 对象", native, layout)
        self.input_summary = CompactLabel()
        layout.addWidget(self.input_summary)
        button("开始编辑 →", lambda: self.tabs.setCurrentIndex(1), layout)
        layout.addStretch()

    def _build_edit(self):
        layout = self._page("2 编辑")
        selection_form = QtWidgets.QHBoxLayout()
        self.selection = QtWidgets.QLineEdit("sele")
        self.selection.setPlaceholderText("PyMOL 选择，如 sele 或 chain A")
        self.selection.setToolTip("PyMOL 选择表达式；也可点击结构或序列条选择")
        self.scope = QtWidgets.QComboBox()
        for label, value in (("原子", "atom"), ("残基", "residue"), ("链", "chain")):
            self.scope.addItem(label, value)
        self.scope.setCurrentIndex(1)
        self.scope.setToolTip("应用范围：按原子、残基或链扩展选择")
        selection_form.addWidget(self.selection, 1)
        selection_form.addWidget(self.scope)
        layout.addLayout(selection_form)
        selection_info = QtWidgets.QHBoxLayout()
        self.selection_summary = CompactLabel("未检查选择")
        selection_info.addWidget(self.selection_summary, 1)
        self.inspect_button = button("检查选择", self.inspect_selection, selection_info)
        layout.addLayout(selection_info)
        self.edit_mode = QtWidgets.QComboBox()
        self.edit_mode.addItems(["设计条件", "结构操作"])
        layout.addWidget(self.edit_mode)
        self.edit_stack = QtWidgets.QStackedWidget()
        self.conditions = ConditionEditor()
        self.operations = OperationEditor()
        self.edit_stack.addWidget(self.conditions)
        self.edit_stack.addWidget(self.operations)
        self.edit_mode.currentIndexChanged.connect(self.edit_stack.setCurrentIndex)
        layout.addWidget(self.edit_stack)
        self.apply_button = primary(button("应用条件", self.apply_edit, layout))
        self.edit_mode.currentIndexChanged.connect(
            lambda index: self.apply_button.setText("应用条件" if index == 0 else "应用操作"))
        row = QtWidgets.QHBoxLayout()
        button("撤销", lambda: self.edit_operation("undo", {}), row)
        button("验证输入", self.validate_input, row)
        self.edit_menu_button = menu_button("更多", row)
        self.edit_menu_button.menu().addAction("恢复服务端显示", self.reload_view)
        layout.addLayout(row)
        self.annotation_tree = QtWidgets.QTreeWidget()
        self.annotation_tree.setHeaderLabels(["已设置条件", "数量"])
        self.annotation_tree.setToolTip("点击条件或子项，在结构中定位")
        self.annotation_tree.setMinimumHeight(110)
        self.annotation_tree.itemClicked.connect(self.select_annotation)
        layout.addWidget(self.annotation_tree)
        button("设置生成参数 →", lambda: self.tabs.setCurrentIndex(2), layout)
        layout.addStretch()

    def _build_generate(self):
        layout = self._page("3 生成")
        self.generate_summary = CompactLabel("未打开输入")
        layout.addWidget(self.generate_summary)
        form = QtWidgets.QFormLayout()
        self.backend = QtWidgets.QComboBox()
        self.backend.activated.connect(self._remember_backend)
        self.thinking = QtWidgets.QComboBox()
        for label, value in (("低", "low"), ("中", "medium"), ("高", "high")):
            self.thinking.addItem(label, value)
        self.thinking.setCurrentIndex(1)
        self.samples = QtWidgets.QSpinBox()
        self.samples.setRange(1, 8)
        self.seed = QtWidgets.QLineEdit("42")
        form.addRow("计算后端", self.backend)
        form.addRow("Thinking", self.thinking)
        form.addRow("设计数量", self.samples)
        layout.addLayout(form)
        self.save_trajectory = QtWidgets.QCheckBox("保存轨迹")
        self.save_trajectory.setToolTip("保留全原子 x1 预测帧；增加解码时间与存储占用")
        layout.addWidget(self.save_trajectory)
        advanced = QtWidgets.QWidget()
        sampling = QtWidgets.QFormLayout(advanced)
        sampling.addRow("Seed", self.seed)
        self.backbone_k = QtWidgets.QDoubleSpinBox()
        self.backbone_k.setRange(1, 20)
        self.backbone_k.setDecimals(1)
        self.backbone_k.setSingleStep(0.1)
        self.latent_k = QtWidgets.QDoubleSpinBox()
        self.latent_k.setRange(1, 6)
        self.latent_k.setDecimals(1)
        self.latent_k.setSingleStep(0.1)
        self.partial = QtWidgets.QCheckBox("Partial diffusion")
        self.partial_percent = QtWidgets.QDoubleSpinBox()
        self.partial_percent.setRange(1, 100)
        self.partial_percent.setValue(70)
        self.partial_percent.setSuffix(" %")
        self.partial_percent.setEnabled(False)
        self.partial.toggled.connect(self.partial_percent.setEnabled)
        sampling.addRow("Backbone K", self.backbone_k)
        sampling.addRow("Latent K", self.latent_k)
        sampling.addRow(self.partial)
        sampling.addRow("扩散轨迹比例", self.partial_percent)
        self.trajectory_stride = QtWidgets.QSpinBox()
        self.trajectory_stride.setRange(1, 1000)
        self.trajectory_stride.setValue(10)
        self.trajectory_stride.setEnabled(False)
        self.save_trajectory.toggled.connect(self.trajectory_stride.setEnabled)
        sampling.addRow("轨迹保存间隔（步）", self.trajectory_stride)
        self.sampling_toggle = collapsible("高级采样", advanced, layout)
        self.generate_button = primary(button("验证并生成", self.generate, layout))
        self.generate_button.setToolTip("提交时固定输入修订和后端。\n输入改变后，旧任务结果不会覆盖当前结构。")
        layout.addStretch()

    def _build_results(self):
        layout = self._page("4 结果")
        self.result_list = QtWidgets.QListWidget()
        self.result_list.itemActivated.connect(self.open_result)
        self.result_list.itemClicked.connect(self.open_result)
        layout.addWidget(self.result_list)
        row = QtWidgets.QHBoxLayout()
        self.next_round_button = primary(button("下一轮 →", self.next_round, row))
        self.trajectory_button = button("轨迹", self.open_trajectory, row)
        self.result_menu_button = menu_button("更多", row)
        menu = self.result_menu_button.menu()
        menu.addAction("打开所选结果", self.open_selected_result)
        menu.addAction("与输入叠合比较", self.compare_result)
        menu.addSeparator()
        menu.addAction("导出当前带注释 CIF…", self.export_current)
        menu.addAction("导出所选批次 ZIP…", self.export_batch)
        menu.addAction("恢复服务端显示", self.reload_view)
        layout.addLayout(row)
        self.report = QtWidgets.QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setPlaceholderText("验证信息、生成警告及分析结果")
        self.report.setMinimumHeight(110)
        self.report_toggle = collapsible("详情 · 0", self.report, layout)

    def _set_report(self, text, count=0, error=False):
        self.report.setPlainText(text)
        self.report_toggle.setText(("错误" if error else "详情") + " · %d" % count)
        self.report_toggle.setToolTip(text)
        self.report_toggle.setChecked(error)
        self.tabs.setTabText(3, "4 结果" + (" · %d" % count if count else ""))

    def _issues_report(self, issues, success):
        errors = any(str(issue.get("severity", "")).lower() in ("error", "fatal") for issue in issues)
        self._set_report("\n".join("[%s] %s" % (issue.get("severity", ""), issue.get("message", ""))
                                   for issue in issues) or success, len(issues), errors)
        if errors:
            self.tabs.setCurrentIndex(3)
        return errors

    def _set_ready(self):
        connected = self.api is not None
        has_input = connected and self.current is not None
        busy = self._busy or bool(self._applying_result)
        self.object_combo.setEnabled(connected and not busy)
        self.connect_button.setEnabled(not busy and not self.job_active)
        self.tabs.setEnabled(connected and not busy)
        self.tools_button.setEnabled(has_input and not busy)
        self.generate_button.setEnabled(has_input and not busy and not self.job_active)
        self.apply_button.setEnabled(has_input and not busy)
        self.cancel_button.setEnabled(self.job_active and not busy)
        self.recover_button.setEnabled(connected and not busy)
        self.task_toggle.setText("任务 · 运行中" if self.job_active else "任务")
        if self.job_active and not getattr(self, "_task_was_active", False):
            self.task_toggle.setChecked(True)
        self._task_was_active = self.job_active
        self.progress.setVisible(self._busy or self.job_active)
        self.progress.setRange(0, 0 if self._busy or self.job_active else 1)

    def _error(self, error):
        self.last_error = error
        self.status.setText(str(error))
        self.status.set_error()
        self._set_report(str(error), 1, error=True)
        self.tabs.setCurrentIndex(3)

    def request(self, call, done, message="", failed=None, background=False):
        if (self._polling if background else self._busy):
            return False
        if background:
            self._polling = True
        else:
            self._busy = True
        self._request_id += 1
        key = self._request_id
        self._callbacks[key] = (done, failed, background)
        if message:
            self.status.setText(message)
        self._set_ready()

        def execute():
            try:
                result, error = call(), None
            except Exception as caught:
                result, error = None, caught
            self.finished.emit(key, result, error)
        threading.Thread(target=execute, daemon=True, name="rfd4-api").start()
        return True

    def _finish_request(self, key, result, error):
        done, failed, background = self._callbacks.pop(key)
        # PyMOL may process nested Qt events while replacing a display object.
        # The network request has ended, but the scientific view is not yet
        # ready for another user action or a playback timer.
        self._applying_result += 1
        if background:
            self._polling = False
        else:
            self._busy = False
        try:
            if error is not None:
                (failed or self._error)(error)
            else:
                done(result)
        except Exception as caught:
            self._error(caught)
        finally:
            self._applying_result -= 1
            self._set_ready()

    def _remember_backend(self):
        if self.api:
            self.settings.setValue("backend:" + self.api.base_url, self.backend.currentData())

    def _connection_failed(self, error):
        self.last_error = str(error)
        self.connection_label.setText("● 未连接")
        self.connection_label.setToolTip(str(error))
        self.status.setText("连接失败 · 打开连接设置重试")
        self.status.setToolTip(str(error))

    def _restore_connection(self):
        url = str(self.settings.value("url", ""))
        if not url or self.api or self._busy or not self.settings.value("auto_connect", True, type=bool):
            return
        try:
            token = os.environ.get("RFD4_ACCESS_TOKEN") or load_token(url)
            self.connect_to(url, token, automatic=True)
        except Exception as error:
            self._connection_failed(error)

    def connection_dialog(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("连接 RFD4 Studio")
        layout = QtWidgets.QFormLayout(dialog)
        url = QtWidgets.QLineEdit(self.api.base_url if self.api else
                                 str(self.settings.value("url", "http://127.0.0.1:17862")))
        try:
            stored_token = load_token(url.text())
        except Exception:
            stored_token = ""
        token = QtWidgets.QLineEdit(self.api.token if self.api else
                                   os.environ.get("RFD4_ACCESS_TOKEN") or stored_token)
        token.setEchoMode(QtWidgets.QLineEdit.Password)
        url.textEdited.connect(lambda: token.clear())
        layout.addRow("服务根地址", url)
        layout.addRow("访问令牌", token)
        remember = QtWidgets.QCheckBox("记住登录")
        remember.setChecked(self.settings.value("auto_connect", True, type=bool))
        remember.setToolTip("打开插件自动连接。Windows 使用用户加密凭据；Linux 使用权限 0600 的用户文件。")
        layout.addRow(remember)
        url.setToolTip("HTTPS 服务根地址；SSH 转发可使用 http://127.0.0.1:端口")
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self.connect_to(url.text(), token.text(), remember=remember.isChecked())

    def connect_to(self, url, token="", remember=None, automatic=False):
        try:
            api = ApiClient(url, token)
        except Exception as error:
            self._error(error)
            return

        def connected(caps):
            changed = self.api is not None and self.api.base_url != api.base_url
            if changed:
                self.current = None
                self.sessions.clear()
                self.object_combo.clear()
                self.result_list.clear()
                self.batches.clear()
                self.bridge = DisplayBridge()
            self.api = api
            self.capabilities = caps
            self.settings.setValue("url", api.base_url)
            if remember is not None:
                # Save only credentials accepted by the intended endpoint.
                save_token(api.base_url, api.token if remember else "")
                self.settings.setValue("auto_connect", remember)
            self.job_id.setText(str(self.settings.value("job:" + api.base_url, "")))
            title = str(caps.get("title", "RFD4")).splitlines()[0]
            mode = "回放 · 不运行模型" if caps.get("mode") == "replay" else "已连接"
            self.connection_label.setText("● " + mode)
            self.connection_label.setToolTip("%s\n%s\n%s" % (title, mode, api.base_url))
            self.example_combo.clear()
            for example in caps.get("examples", []):
                self.example_combo.addItem(example.get("label", example["id"]), example["id"])
            self.backend.clear()
            for value in caps.get("backend_ids", ["default"]):
                self.backend.addItem(value, value)
            preferred = self.settings.value("backend:" + api.base_url, caps.get("default_backend", "default"))
            self.backend.setCurrentIndex(max(0, self.backend.findData(preferred)))
            defaults = caps.get("k_defaults", [16.0, 4.0])
            self.backbone_k.setValue(defaults[0])
            self.latent_k.setValue(defaults[1])
            self.conditions.configure(caps.get("conditions", {}))
            self.operations.configure(caps)
            self.refresh_objects()
            self.last_error = None
            self.status.setText("已连接")
            self.status.setToolTip("")
        self.connection_label.setText("● 连接中")
        self.request(api.capabilities, connected, "正在连接服务…",
                     failed=self._connection_failed if automatic else None)

    def parse_options(self):
        return {"preset": self.parse_preset.currentText(), "assembly": self.assembly.text().strip() or "1",
                "model": None if self.model.value() < 0 else self.model.value(),
                "remove_waters": self.remove_waters.isChecked(),
                "hydrogen_policy": self.hydrogen.currentData()}

    def _create_and_view(self, payload, label=None, ready=None):
        if not self.api:
            return
        api = self.api

        def create():
            result = api.create_session(**payload)
            return api.view(result["session_id"])

        def opened(view):
            self._activate_view(view, label)
            self.partial.setChecked(False)
            if ready:
                ready(view)
            self.status.setText("输入已打开，可在 PyMOL 中选择原子或残基。")
        self.request(create, opened, "正在解析并打开结构…")

    def import_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "导入结构", "", "结构 (*.cif *.mmcif)")
        if path:
            try:
                file = Path(path)
                if file.stat().st_size > 32 * 1024 * 1024:
                    raise ValueError("输入超过 32 MiB。")
                self._create_and_view({"cif": file.read_text(encoding="utf-8"), "name": file.name,
                                       "parse_options": self.parse_options()})
            except Exception as error:
                self._error(error)

    def import_example(self):
        example = self.example_combo.currentData()
        if example:
            self._create_and_view({"example_id": example}, self.example_combo.currentText())

    def refresh_objects(self):
        current = self.native_object.currentText()
        self.native_object.clear()
        self.native_object.addItems(cmd.get_object_list("all"))
        if current:
            self.native_object.setCurrentText(current)

    def import_object(self):
        obj = self.native_object.currentText()
        if obj not in cmd.get_object_list("all"):
            self._error(ValueError("请刷新并选择一个分子对象。"))
            return
        try:
            cif = cmd.get_str("cif", "model " + obj, state=1)
            self._create_and_view({"cif": cif, "name": obj + ".cif", "parse_options": self.parse_options()})
        except Exception as error:
            self._error(error)

    def _activate_view(self, view, label=None):
        sid = view["session_id"]
        obj = self.bridge.load(view)
        previous = self.sessions.get(sid, {})
        old_label = previous.get("label")
        self.sessions[sid] = {"view": view, "label": label or old_label or view.get("name", obj)}
        # Retain display lineage when an edit refreshes this same session.
        if "lineage" in previous:
            self.sessions[sid]["lineage"] = previous["lineage"]
        if self.job_active and self.job_data:
            if sid != self.job_data["session_id"] or view["revision"] != self.job_data["revision"]:
                self.accept_results = False
        self.current = sid
        self.bridge.activate(sid)
        self.object_combo.blockSignals(True)
        self.object_combo.clear()
        for key, record in self.sessions.items():
            self.object_combo.addItem(record["label"], key)
        self.object_combo.setCurrentIndex(self.object_combo.findData(sid))
        self.object_combo.blockSignals(False)
        structure = view["structure"]
        self.conditions.set_structure(structure)
        self.operations.set_structure(structure)
        self.operations.set_sessions([(key, record["label"]) for key, record in self.sessions.items() if key != sid])
        summary = "%s · %s 原子 · 修订 %s\n链：%s" % (
            self.sessions[sid]["label"], structure.get("atom_count", len(view["atom_ids"])),
            view["revision"], " / ".join(structure.get("chain_ids", [])))
        short_summary = "%s 原子 · %s 链 · r%s" % (
            structure.get("atom_count", len(view["atom_ids"])), len(structure.get("chain_ids", [])),
            view["revision"])
        for label in (self.input_summary, self.generate_summary):
            label.setText(short_summary)
            label.setToolTip(summary)
        self.annotation_tree.clear()
        for entry in view.get("annotations", []):
            item = QtWidgets.QTreeWidgetItem([entry.get("label", entry["name"]),
                                            str(entry.get("count", "")) + " " + entry.get("unit", "")])
            self.annotation_tree.addTopLevelItem(item)
            all_indices = set()
            for value in entry.get("values", []):
                indices = value.get("atom_indices", [])
                all_indices.update(indices)
                child = QtWidgets.QTreeWidgetItem([str(value.get("label", "")), str(value.get("count", len(indices)))])
                child.setData(0, QtCore.Qt.UserRole, indices)
                item.addChild(child)
            item.setData(0, QtCore.Qt.UserRole, sorted(all_indices))
        self.annotation_tree.resizeColumnToContents(0)
        issues = structure.get("validation", [])
        self._issues_report(issues, "输入交互验证通过。")
        self._set_ready()

    def _choose_session(self, index):
        sid = self.object_combo.itemData(index)
        if sid and sid != self.current:
            self.open_session(sid)

    def open_session(self, sid):
        self.request(lambda: self.api.view(sid), self._activate_view, "正在打开所选结构…")

    def reload_view(self):
        if self.current:
            self.open_session(self.current)

    def _selection_indices(self):
        if not self.current:
            raise ValueError("请先打开输入。")
        return self.bridge.selected_indices(self.current, self.selection.text().strip())

    def inspect_selection(self):
        try:
            indices = self._selection_indices()
            self.selection_summary.setText("%d 原子 · 按%s应用" % (len(indices), self.scope.currentText()))
        except Exception as error:
            self._error(error)

    def select_annotation(self, item, column=0):
        try:
            indices = item.data(0, QtCore.Qt.UserRole)
            if indices is not None and self.current:
                self.selection.setText(self.bridge.select_indices(self.current, indices))
                self.inspect_selection()
        except Exception as error:
            self._error(error)

    def apply_edit(self):
        try:
            if not self.current:
                raise ValueError("请先打开输入。")
            # No-selection operations must work even when the `sele` name is absent.
            self.bridge.assert_current(self.current)
            expression = self.selection.text().strip()
            if expression == "sele" and "sele" not in cmd.get_names("selections"):
                indices = []
            else:
                indices = self.bridge.selected_indices(self.current, expression)
            view = self.sessions[self.current]["view"]
            editor = self.conditions if self.edit_mode.currentIndex() == 0 else self.operations
            operation, payload = editor.action(indices, self.scope.currentData(), view["revision"])
            self.edit_operation(operation, payload)
        except Exception as error:
            self._error(error)

    def edit_operation(self, operation, payload):
        if not self.current:
            self._error(ValueError("请先打开输入。"))
            return
        try:
            self.bridge.assert_current(self.current)
        except Exception as error:
            self._error(error)
            return
        sid = self.current
        revision = self.sessions[sid]["view"]["revision"]
        api = self.api
        scope = self.scope.currentData()

        def edit():
            resolved_payload = dict(payload)
            if operation in ("delete", "charges", "chains"):
                resolved = api.request("POST", "/api/sessions/%s/selections/resolve" % sid,
                                       {"atom_indices": payload["atom_indices"], "scope": scope})
                resolved_payload["atom_indices"] = resolved["atom_indices"]
            result = api.edit(sid, revision, operation, resolved_payload)
            return api.view(result["session_id"])

        def edited(view):
            self._activate_view(view)
            self.status.setText("修改已保存到输入修订 %s。" % view["revision"])

        def failed(error):
            detail = getattr(error, "detail", None)
            if isinstance(detail, dict) and detail.get("code") == "target_discard_confirmation_required":
                answer = QtWidgets.QMessageBox.question(
                    self, "此修改会清除已有目标", str(error) + "\n是否继续？",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
                if answer == QtWidgets.QMessageBox.Yes:
                    confirmed = dict(payload, allow_target_discard=True, expected_revision=detail["revision"])
                    self.edit_operation(operation, confirmed)
                    return
            self._error(error)
        self.request(edit, edited, "正在保存修改…", failed)

    def validate_input(self):
        if self.current:
            sid = self.current

            def validated(result):
                issues = result.get("issues", []) if isinstance(result, dict) else result
                errors = self._issues_report(issues, "完整输入验证通过。")
                self.status.setText("验证错误 · %d 项" % len(issues) if errors else "验证完成 · %d 项提示" % len(issues))
                if errors:
                    self.status.set_error()
            self.request(lambda: self.api.request("POST", "/api/sessions/%s/validate?full=true" % sid, {}),
                         validated, "正在完整验证输入…")

    def generation_options(self):
        try:
            seed = int(self.seed.text())
        except ValueError:
            raise ValueError("Seed 必须是 0 到 4294967295 的整数。") from None
        if not 0 <= seed < 2**32:
            raise ValueError("Seed 必须是 0 到 4294967295 的整数。")
        result = {"thinking": self.thinking.currentData(), "samples": self.samples.value(), "seed": seed,
                  "backbone_k": self.backbone_k.value(), "latent_k": self.latent_k.value(),
                  "partial_diffusion": self.partial.isChecked(),
                  "partial_diffusion_percent": self.partial_percent.value(),
                  "trajectory_stride": self.trajectory_stride.value() if self.save_trajectory.isChecked() else 0}
        backend = self.backend.currentData()
        if backend and backend != "default":
            result["backend"] = backend
        return result

    def generate(self):
        if not self.current or self.job_active or self._busy:
            return
        try:
            self.bridge.assert_current(self.current)
            view = self.sessions[self.current]["view"]
            payload = {"job_id": uuid.uuid4().hex, "session_id": self.current,
                       "revision": view["revision"], "options": self.generation_options()}
        except Exception as error:
            self._error(error)
            return
        self.job_data = payload
        self.job_api = self.api
        self.job_active = True
        self.accept_results = True
        self.job_id.setText(payload["job_id"])
        self.settings.setValue("job:" + self.api.base_url, payload["job_id"])
        self.settings.sync()

        def failed(error):
            # A validation response proves no submission. Network errors do not.
            if isinstance(error, ApiError) and error.status in (400, 401, 403, 404, 409, 422, 429):
                self.job_active = False
            self._error(error)
        self.request(lambda: self.job_api.submit(payload), self._handle_job, "正在验证并提交…", failed)

    def _handle_job(self, result):
        if not self.job_data or result.get("job_id") != self.job_data["job_id"]:
            raise ValueError("任务身份不匹配，已停止自动处理。")
        state = result.get("status")
        if state == "complete" and "designs" not in result:
            self.job_active = True
            self.timer.start()
            self.status.setText("任务完成，正在取回结构…")
            return
        if state in ("queued", "running"):
            self.job_active = True
            self.timer.start()
            self.status.setText("任务已接收 · %s · 等待资源或生成中。" % result.get("backend", "default"))
            return
        self.timer.stop()
        self.job_active = False
        if state == "complete":
            if not self.accept_results:
                self.status.setText("旧输入任务已完成，结果已丢弃。")
                return
            designs = result.get("designs", [])
            if not designs or not all(isinstance(cif, str) and cif.strip() for cif in designs):
                raise ValueError("完成响应缺少有效结构。")
            job = result["job_id"]
            if job in self.batches:
                self.status.setText("该批结果已在结果列表中。")
                return
            self.batches[job] = designs
            input_sid = result.get("session_id", self.job_data.get("session_id"))
            server_round = (result.get("lineage") or {}).get("round")
            local_lineage = self.sessions.get(input_sid, {}).get("lineage", {})
            round_number = server_round if server_round is not None else local_lineage.get("round", 1)
            for number, cif in enumerate(designs, 1):
                label = ("第 %s 轮 · 设计 %d" % (round_number, number)
                         if server_round is not None or input_sid in self.sessions else
                         "批次 %d · 设计 %d" % (len(self.batches), number))
                item = QtWidgets.QListWidgetItem(label)
                item.setToolTip("%s\n任务 %s\n输入 %s" % (label, job, input_sid))
                item.setData(QtCore.Qt.UserRole, {"cif": cif, "label": label, "job_id": job,
                                                  "candidate_index": number - 1,
                                                  "input_sid": input_sid, "session_id": None})
                self.result_list.addItem(item)
            warnings = result.get("warnings", [])
            self._set_report("\n".join(str(w) for w in warnings) or "生成完成，无新增警告。", len(warnings))
            if self.tabs.currentIndex() == 2:
                self.tabs.setCurrentIndex(3)
            self.status.setText("生成完成：%d 个设计。点击结果在 PyMOL 中打开。" % len(designs))
        elif state in ("cancelled", "stale"):
            self.status.setText("任务结果已丢弃。" + (" 已开始的推理会安全退出。" if state == "cancelled" else " 输入已改变。"))
        elif state == "failed":
            self._error(ApiError(result.get("error") or "生成失败，请保留任务 ID 查看后端证据。"))
        else:
            self.job_active = True
            self._error(ApiError("任务状态未知，请保留原任务 ID 并查询。"))

    def poll_job(self):
        if self._busy or self._polling or not self.job_active or not self.job_data:
            return

        def failed(error):
            self.timer.stop()
            self._error(error)
        job_id, api = self.job_data["job_id"], self.job_api
        self.request(lambda: api.job(job_id), self._handle_job, failed=failed, background=True)

    def recover_job(self):
        if self._polling:
            return
        job = self.job_id.text().strip()
        if len(job) != 32 or any(c not in "0123456789abcdef" for c in job):
            self._error(ValueError("请输入完整的 32 位任务 ID。"))
            return
        if self.job_active and self.job_data and job != self.job_data["job_id"]:
            self._error(ValueError("当前仍在跟踪另一任务，请先取消等待。"))
            return
        self.job_api = self.api

        def recovered(result):
            self.job_data = {"job_id": job, "session_id": result["session_id"], "revision": result["revision"]}
            self.accept_results = True
            self._handle_job(result)
        self.request(lambda: self.job_api.job(job), recovered, "正在查询原任务；不会重新提交…")

    def cancel_job(self):
        if self.job_active and self.job_data:
            job = self.job_data["job_id"]
            self.accept_results = False
            self.timer.stop()
            self.request(lambda: self.job_api.cancel(job), self._handle_job, "正在取消等待…")

    def open_selected_result(self):
        if self.result_list.currentItem():
            self.open_result(self.result_list.currentItem())

    def next_round(self):
        item = self.result_list.currentItem()
        if not item or self._busy:
            self._error(ValueError("请先选择作为下一轮起点的 candidate。"))
            return
        record = item.data(QtCore.Qt.UserRole)
        api = self.api
        candidate_index = record["candidate_index"]

        def fork():
            result = api.request(
                "POST", "/api/pymol/jobs/%s/candidates/%d/fork" % (record["job_id"], candidate_index), {})
            return result, api.view(result["session_id"])

        def opened(response):
            result, view = response
            lineage = result.get("lineage", {})
            label = "第%s轮 · 输入" % lineage.get("round", 2)
            self._activate_view(view, label)
            self.sessions[view["session_id"]]["lineage"] = lineage
            self.object_combo.setItemData(
                self.object_combo.findData(view["session_id"]),
                "来源：%s\n任务：%s" % (record["label"], record["job_id"]), QtCore.Qt.ToolTipRole)
            options = result.get("options", {})
            if "thinking" in options:
                self.thinking.setCurrentIndex(max(0, self.thinking.findData(options["thinking"])))
            if "backend" in options:
                self.backend.setCurrentIndex(max(0, self.backend.findData(options["backend"])))
            self.samples.setValue(options.get("samples", self.samples.value()))
            self.seed.setText(str(options.get("seed", self.seed.text())))
            if options.get("backbone_k") is not None:
                self.backbone_k.setValue(options["backbone_k"])
            if options.get("latent_k") is not None:
                self.latent_k.setValue(options["latent_k"])
            self.partial.setChecked(options.get("partial_diffusion", False))
            self.partial_percent.setValue(options.get("partial_diffusion_percent", 70))
            stride = options.get("trajectory_stride", 0)
            self.save_trajectory.setChecked(stride > 0)
            self.trajectory_stride.setValue(stride or 10)
            self.tabs.setCurrentIndex(1)
            self.status.setText("已从 %s 建立独立的下一轮输入。原结果保留；可修改条件后再次生成。"
                                % record["label"])
        self.request(fork, opened, "正在从所选 candidate 建立下一轮输入…")

    def open_trajectory(self):
        item = self.result_list.currentItem()
        if not item:
            self._error(ValueError("请先选择一个 candidate。"))
            return
        from .trajectory import TrajectoryDialog
        previous = getattr(self, "_trajectory", None)
        if previous is not None:
            previous.close()
        self._trajectory = TrajectoryDialog(self, item.data(QtCore.Qt.UserRole))
        self._trajectory.show()
        self._trajectory.load_metadata()

    def open_result(self, item):
        if self._busy:
            return
        record = item.data(QtCore.Qt.UserRole)
        if record.get("session_id") in self.sessions:
            self.open_session(record["session_id"])
            return

        def opened(view):
            record["session_id"] = view["session_id"]
            item.setData(QtCore.Qt.UserRole, record)
        # Display labels may be localized. Keep the service's export filename
        # stable and ASCII, independent of those labels.
        filename = "candidate_%s_%d.cif" % (record["job_id"][:8], record["candidate_index"] + 1)
        self._create_and_view({"cif": record["cif"], "name": filename},
                              record["label"], opened)

    def compare_result(self):
        item = self.result_list.currentItem()
        if not item:
            self._error(ValueError("请先选择并打开一个结果。"))
            return
        record = item.data(QtCore.Qt.UserRole)
        try:
            if not record.get("session_id") or record["input_sid"] not in self.sessions:
                raise ValueError("请先打开该结果及其原始输入，再进行叠合。")
            name, alignment = self.bridge.compare(record["session_id"], record["input_sid"])
            self.status.setText("已创建比较副本 %s；CA 对齐 RMSD %.3f Å（%s 原子）。"
                                % (name, alignment[0], alignment[1]))
        except Exception as error:
            self._error(error)

    def export_current(self):
        if not self.current:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "导出带注释 CIF", "design.cif", "CIF (*.cif)")
        if path:
            sid = self.current

            def saved(cif):
                Path(path).write_text(cif, encoding="utf-8")
                self.status.setText("已保存带完整注释的 CIF。")
            self.request(lambda: self.api.cif(sid), saved, "正在导出原始注释…")

    def export_batch(self):
        item = self.result_list.currentItem()
        if not item:
            self._error(ValueError("请先选择一个结果，以确定导出批次。"))
            return
        record = item.data(QtCore.Qt.UserRole)
        designs = self.batches[record["job_id"]]
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "导出本批结果", "designs.zip", "ZIP (*.zip)")
        if path:
            try:
                with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                    for i, cif in enumerate(designs, 1):
                        archive.writestr("design-%d.cif" % i, cif)
                    archive.writestr("provenance.json", json.dumps(
                        {"job_id": record["job_id"], "input_session": record["input_sid"]}, indent=2))
                self.status.setText("已导出 %d 个带注释设计。" % len(designs))
            except Exception as error:
                self._error(error)

    def open_tools(self):
        from .tools import ViewTools
        if self._tools is None:
            self._tools = ViewTools(self)
        self._tools.show()
        self._tools.raise_()
