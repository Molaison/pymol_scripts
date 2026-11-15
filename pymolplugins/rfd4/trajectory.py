"""Variable-topology trajectory viewing and evidence-backed condition tables."""
from collections import OrderedDict
from pathlib import Path
import uuid

from pymol import cmd
from pymol.Qt import QtCore, QtWidgets

from .bridge import DisplayBridge
from .ui import CompactLabel, button, menu_button, primary

STATUS = {"satisfied": "满足", "unsatisfied": "未满足", "unknown": "无法判断", "unsupported": "不支持"}
COLORS = {"satisfied": "#d9eedf", "unsatisfied": "#ffe1dd", "unknown": "#fff0cc", "unsupported": "#e8e8ed"}
CONDITIONS = {"hbond": "氢键", "hydrophobic": "疏水", "pistack": "π 堆积",
              "pication": "π 阳离子", "saltbridge": "盐桥", "metal-coord": "金属配位"}
ROLES = {"DONOR": "供体", "ACCEPTOR": "受体", "BOTH": "供体与受体"}
REASONS = {
    "correct_role_pair_detected": "检测到符合目标角色的配对",
    "no_correct_role_pair_in_complete_qualified_search_scope": "在完整、可判断的范围内未检测到配对",
    "frame_atom_missing": "此帧缺少目标原子",
    "frame_atom_identity_not_unique": "目标原子对应关系不唯一",
    "residue_or_element_changed": "此帧残基或元素改变，不能代替原目标",
    "frame_target_coordinates_nonfinite": "目标原子坐标缺失",
    "missing_required_heavy_atoms": "搜索范围缺少必要重原子",
    "nonstandard_chemistry_in_search_scope": "非标准化学环境不足以支持否定判断",
    "hydrogen_placement_incomplete_or_skipped": "氢原子放置不完整",
    "donor_hydrogen_not_established_at_target": "目标供体未建立可靠的氢原子",
    "unindexed_target_has_no_native_per_step_correspondence": "未索引目标缺少原生逐帧对应关系",
    "condition_or_requested_role_has_no_implemented_local_evaluator": "此条件或角色尚无局部评估方法",
    "hbond_BOTH_is_not_supported_by_the_current_model": "当前模型不支持 BOTH 氢键角色",
    "local_required_heavy_atoms_missing": "配对残基缺少必要重原子，检测结果不足以确认氢键",
    "local_hydrogen_placement_incomplete": "配对原子的氢原子放置不完整",
    "local_hbond_chemistry_not_established": "无法确认配对原子的局部化学环境",
}


def condition_label(condition):
    name = condition.get("condition", "")
    role = condition.get("requested_role", "")
    label = CONDITIONS.get(name, name)
    return label + (" · " + ROLES[role] if role in ROLES else "")


def reason_label(reason):
    for key, label in REASONS.items():
        if reason.startswith(key):
            return label
    return reason


def atom_label(atom):
    if not isinstance(atom, dict):
        return str(atom or "")
    chain = atom.get("chain_iid", atom.get("chain", ""))
    residue = atom.get("res_id", atom.get("resi", ""))
    name = atom.get("atom_name", atom.get("name", ""))
    return "%s / %s %s / %s" % (chain, atom.get("res_name", ""), residue, name)


class TrajectoryDialog(QtWidgets.QDialog):
    def __init__(self, panel, candidate):
        super().__init__(panel)
        self.panel = panel
        self.api = panel.api
        self.candidate = candidate
        self.base = "/api/pymol/jobs/%s/candidates/%d/trajectory" % (
            candidate["job_id"], candidate["candidate_index"])
        self.bridge = DisplayBridge()
        self.metadata = {}
        self.views = OrderedDict()
        self.reports = {}
        self.current_report = None
        self.shown_frame = None
        self.desired_frame = -1
        self.loading = False
        self.last_error = None
        self.evaluation_queue = []
        self.closed = False
        self.measurements = []
        enabled = set(cmd.get_names("objects", enabled_only=1))
        self.visibility = {obj: obj in enabled for obj in panel.bridge.objects.values()}
        self.play_timer = QtCore.QTimer(self)
        self.play_timer.setInterval(500)
        self.play_timer.timeout.connect(self.advance)
        self.setWindowTitle("轨迹与局部条件 · " + candidate["label"])
        self.resize(760, 560)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)
        header = QtWidgets.QHBoxLayout()
        self.explanation = CompactLabel("x1 · 保存帧")
        self.explanation.setToolTip(
            "x1 是各步的干净结构预测，不是分子动力学轨迹；每帧可能有不同的序列与原子。\n"
            "最终结果单独展示；帧编号对应原始保存帧。")
        header.addWidget(self.explanation, 1)
        self.settings_button = button("检测设置", lambda: None, header)
        self.settings_button.setCheckable(True)
        self.more_button = menu_button("更多", header)
        self.fit_action = self.more_button.menu().addAction("自动适配视野")
        self.fit_action.setCheckable(True)
        self.fit_action.setChecked(True)
        self.fit_action.setToolTip("切换帧时居中并适配大小；不改变结构坐标")
        self.export_action = self.more_button.menu().addAction("保存原始轨迹 CIF…", self.export_trajectory)
        root.addLayout(header)
        row = QtWidgets.QHBoxLayout()
        self.play = button("播放", self.toggle_play, row)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setMinimum(0)
        self.slider.valueChanged.connect(self.choose_frame)
        row.addWidget(self.slider, 1)
        self.frame_label = QtWidgets.QLabel("读取中")
        row.addWidget(self.frame_label)
        self.final_button = button("最终结果", lambda: self.slider.setValue(self.slider.maximum()), row)
        root.addLayout(row)
        settings = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(settings)
        self.hbond_mode = QtWidgets.QComboBox()
        self.hbond_mode.addItem("AtomWorks rosetta", "rosetta")
        self.hbond_mode.addItem("AtomWorks plip", "plip")
        self.interchain = QtWidgets.QCheckBox("仅检查链间相互作用")
        self.interchain.setChecked(True)
        self.hbond_mode.currentIndexChanged.connect(self.clear_assessment)
        self.interchain.toggled.connect(self.clear_assessment)
        form.addRow("检测规则", self.hbond_mode)
        form.addRow(self.interchain)
        self.settings_button.setToolTip("完整氢键规则，不按单一距离判断。\n"
                                       "原子身份或化学环境不完整：无法判断；未实现条件：不支持。")
        settings.hide()
        self.settings_button.toggled.connect(settings.setVisible)
        root.addWidget(settings)
        actions = QtWidgets.QHBoxLayout()
        self.condition_selector = QtWidgets.QComboBox()
        self.condition_selector.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.condition_selector.setMinimumContentsLength(12)
        self.condition_selector.setToolTip("待检查的原始目标范围")
        self.condition_selector.currentIndexChanged.connect(self.clear_assessment)
        actions.addWidget(self.condition_selector, 1)
        self.evaluate_button = primary(button("检查当前帧", self.evaluate_current, actions))
        self.evaluate_all_button = button("检查整条", self.evaluate_all, actions)
        self.stop_button = button("停止", self.stop_evaluation, actions)
        self.stop_button.setToolTip("停止后续帧检查，保留已有结果")
        root.addLayout(actions)
        self.summary = CompactLabel("尚未检查")
        self.summary.setToolTip("选择目标范围后检查当前帧或整条轨迹。满足率保留全部原始目标作为分母。")
        root.addWidget(self.summary)
        self.timeline = QtWidgets.QTableWidget(0, 5)
        self.timeline.setHorizontalHeaderLabels(["保存帧", "满足 / 目标", "可判断", "无法判断", "不支持"])
        self.timeline.setToolTip("满足 / 目标：分母包含无法判断与不支持的原始目标。\n点击行切换帧。")
        self.timeline.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.timeline.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.timeline.cellClicked.connect(self.timeline_selected)
        self.timeline.verticalHeader().hide()
        self.timeline.verticalHeader().setMinimumSectionSize(26)
        self.timeline.verticalHeader().setDefaultSectionSize(26)
        self.timeline.verticalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        self.timeline.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.timeline.setFixedHeight(185)
        root.addWidget(self.timeline)
        self.conditions = QtWidgets.QTableWidget(0, 4)
        self.conditions.setHorizontalHeaderLabels(["条件", "目标原子", "状态", "配对 · 距离"])
        self.conditions.setToolTip("点击行定位目标与配对；悬停查看化学判据与完整依据。")
        self.conditions.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.conditions.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.conditions.cellClicked.connect(self.condition_selected)
        self.conditions.horizontalHeader().setStretchLastSection(True)
        self.conditions.verticalHeader().hide()
        self.conditions.setWordWrap(False)
        self.conditions.setAlternatingRowColors(True)
        for col, width in enumerate((105, 175, 80)):
            self.conditions.setColumnWidth(col, width)
        root.addWidget(self.conditions, 1)
        self.status = CompactLabel()
        root.addWidget(self.status)

    def _request(self, call, done, message):
        if self.closed or self.loading or self.panel._busy:
            return False
        self.loading = True
        self.last_error = None
        self.status.setText(message)

        def completed(result):
            self.loading = False
            if not self.closed:
                done(result)

        def failed(error):
            self.loading = False
            self.last_error = error
            self.play_timer.stop()
            self.play.setText("播放")
            self.evaluation_queue.clear()
            self.status.setText(str(error))
            self.status.set_error()
        return self.panel.request(call, completed, failed=failed)

    def load_metadata(self):
        def loaded(result):
            self.metadata = result
            count = result.get("frame_count", 0)
            self.condition_selector.blockSignals(True)
            self.condition_selector.clear()
            conditions = result.get("conditions", [])
            hbonds = [c["condition_id"] for c in conditions if c.get("condition") == "hbond"]
            if hbonds:
                self.condition_selector.addItem("所有氢键目标", hbonds)
            self.condition_selector.addItem("全部原始条件", None)
            for condition in conditions:
                self.condition_selector.addItem(
                    "%s · %s" % (condition_label(condition), atom_label(condition.get("original_atom"))),
                    [condition["condition_id"]])
            self.condition_selector.blockSignals(False)
            self.slider.blockSignals(True)
            self.slider.setMaximum(count)
            self.slider.setValue(0)
            self.slider.blockSignals(False)
            self.play.setEnabled(count > 1)
            self.evaluate_all_button.setEnabled(count > 0)
            self.timeline.setRowCount(count + 1)
            for row in range(count + 1):
                self.timeline.setItem(row, 0, QtWidgets.QTableWidgetItem(
                    "最终结果" if row == count else str(row + 1)))
            warnings = result.get("warnings", [])
            if result.get("status") != "available":
                self.explanation.setText("无保存帧 · 仅最终结果")
                self.explanation.setToolTip("此任务未保留轨迹（%s）；仍可检查最终结果。" % result.get("reason", ""))
            elif warnings:
                explanation = self.explanation.toolTip()
                self.explanation.setText("x1 · 保存帧 · %d 项提示" % len(warnings))
                self.explanation.setToolTip(explanation + "\n" + "\n".join(map(str, warnings)))
            self.choose_frame(0)
        self._request(lambda: self.api.request("GET", self.base), loaded, "正在读取真实轨迹与原始条件…")

    def _frame_at(self, slider_value):
        return -1 if slider_value == self.metadata.get("frame_count", 0) else slider_value

    def choose_frame(self, position):
        self.desired_frame = self._frame_at(position)
        self._load_desired()

    def _load_desired(self):
        if self.closed or not self.metadata or self.loading:
            return
        frame = self.desired_frame
        if frame in self.views:
            self._show(frame, self.views[frame])
            return

        def loaded(view):
            self.views[frame] = view
            self.views.move_to_end(frame)
            while len(self.views) > 8:
                self.views.popitem(last=False)
            if frame == self.desired_frame:
                self._show(frame, view)
            else:
                self._load_desired()
        self._request(lambda: self.api.request("GET", self.base + "/frames/%d" % frame),
                      loaded, "正在读取帧…" if frame >= 0 else "正在读取最终 candidate…")

    def _show(self, frame, view):
        self.clear_measurements()
        obj = self.bridge.load(view)
        for name in self.panel.bridge.objects.values():
            if name in cmd.get_names("objects"):
                cmd.disable(name)
        cmd.enable(obj)
        if self.fit_action.isChecked():
            cmd.zoom("model " + obj, animate=0)
        self.shown_frame = frame
        self.current_view = view
        self.frame_label.setText("最终" if frame == -1 else
                                 "%d / %d" % (frame + 1, self.metadata["frame_count"]))
        self.conditions.setRowCount(0)
        self.current_report = None
        key = self._key(frame)
        if key in self.reports:
            self._show_report(self.reports[key])
        else:
            self.summary.setText("当前帧 · 未检查")
        self.status.setText("最终结果" if frame == -1 else "x1 · 保存帧")

    def _key(self, frame):
        selected = self.condition_selector.currentData()
        return (frame, tuple(selected or []), self.hbond_mode.currentData(), self.interchain.isChecked())

    def toggle_play(self):
        if self.play_timer.isActive():
            self.play_timer.stop()
            self.play.setText("播放")
        else:
            if self.slider.value() >= self.metadata.get("frame_count", 0) - 1:
                self.slider.setValue(0)
            self.play_timer.start()
            self.play.setText("暂停")

    def advance(self):
        if self.loading or self.panel._busy or self.panel._applying_result:
            return
        if self.slider.value() >= self.metadata.get("frame_count", 0) - 1:
            self.play_timer.stop()
            self.play.setText("播放")
            return
        self.slider.setValue(self.slider.value() + 1)

    def clear_assessment(self, *_):
        self.stop_evaluation()
        self.conditions.setRowCount(0)
        self.current_report = None
        for row in range(self.timeline.rowCount()):
            for col in range(1, 5):
                self.timeline.setItem(row, col, QtWidgets.QTableWidgetItem(""))
        self.summary.setText("范围已改变 · 待检查")

    def evaluate_current(self):
        if self.shown_frame is not None:
            self.evaluation_queue = [self.shown_frame]
            self._evaluate_next()

    def evaluate_all(self):
        self.evaluation_queue = list(range(self.metadata.get("frame_count", 0))) + [-1]
        self._evaluate_next()

    def stop_evaluation(self):
        self.evaluation_queue.clear()

    def _evaluate_next(self):
        if self.closed or not self.evaluation_queue:
            return
        if self.loading or self.panel._busy or self.panel._applying_result:
            QtCore.QTimer.singleShot(100, self._evaluate_next)
            return
        frame = self.evaluation_queue.pop(0)
        key = self._key(frame)
        payload = {"frame_index": frame, "condition_ids": self.condition_selector.currentData(),
                   "hbond_model": self.hbond_mode.currentData(), "inter_chain_only": self.interchain.isChecked()}

        def evaluated(report):
            self.reports[key] = report
            if key == self._key(frame):
                self._timeline_report(frame, report)
                if frame == self.shown_frame:
                    self._show_report(report)
            if self.evaluation_queue:
                QtCore.QTimer.singleShot(0, self._evaluate_next)
            else:
                self.status.setText("检查完成")
                self.status.setToolTip("无法判断和不支持的目标未从原始目标分母中删除。")
                self._load_desired()
        if key in self.reports:
            evaluated(self.reports[key])
        else:
            self._request(lambda: self.api.request("POST", self.base + "/evaluate", payload),
                          evaluated, "正在按原始目标检查%s…" % ("最终 candidate" if frame == -1 else "保存帧 %d" % (frame + 1)))

    def _timeline_report(self, frame, report):
        from pymol.Qt import QtGui
        row = self.metadata.get("frame_count", 0) if frame == -1 else frame
        counts = report["counts"]
        total = report["total_conditions"]
        satisfied = counts.get("satisfied", 0)
        values = ["%s / %s" % (satisfied, total), satisfied + counts.get("unsatisfied", 0),
                  counts.get("unknown", 0), counts.get("unsupported", 0)]
        for col, value in enumerate(values, 1):
            item = QtWidgets.QTableWidgetItem(str(value))
            item.setToolTip("原始目标 %s；满足 %s；未满足 %s；无法判断 %s；不支持 %s" % (
                total, satisfied, counts.get("unsatisfied", 0), counts.get("unknown", 0), counts.get("unsupported", 0)))
            if col == 1:
                state = ("satisfied" if total and satisfied == total else
                         "unsatisfied" if counts.get("unsatisfied", 0) else "unknown")
                item.setBackground(QtGui.QColor(COLORS[state]))
                item.setForeground(QtGui.QColor("#202124"))
            self.timeline.setItem(row, col, item)

    def _show_report(self, report):
        from pymol.Qt import QtGui
        self.current_report = report
        counts = report["counts"]
        known = counts.get("satisfied", 0) + counts.get("unsatisfied", 0)
        self.summary.setText("满足 %s / %s · 可判断 %s · 无法判断 %s · 不支持 %s"
                             % (counts.get("satisfied", 0), report["total_conditions"], known,
                                counts.get("unknown", 0), counts.get("unsupported", 0)))
        self.summary.setToolTip(self.summary.text() + "\n分母为原始目标总数，包含无法判断与不支持。")
        rows = report.get("conditions", [])
        self.conditions.setRowCount(len(rows))
        for row, condition in enumerate(rows):
            evidence = condition.get("evidence", [])
            reason = condition.get("reason", "")
            details = "; ".join(
                    "%s · %.2f Å" % (atom_label(p.get("partner_atom")), p.get("distance", 0))
                    for p in evidence)
            values = [condition_label(condition), atom_label(condition.get("original_atom")),
                      STATUS.get(condition["status"], condition["status"]), details or "—"]
            for col, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(str(value))
                item.setToolTip("%s\n%s\n%s\n%s" % (
                    value, reason_label(reason), reason, details))
                if col == 2:
                    item.setBackground(QtGui.QColor(COLORS.get(condition["status"], "#ffffff")))
                    item.setForeground(QtGui.QColor("#202124"))
                self.conditions.setItem(row, col, item)

    def timeline_selected(self, row, column):
        self.slider.setValue(row)

    def clear_measurements(self):
        for name in self.measurements:
            cmd.delete(name)
        self.measurements.clear()

    def condition_selected(self, row, column):
        if self.current_report is None:
            return
        condition = self.current_report["conditions"][row]
        indices = []
        if condition.get("frame_atom_index") is not None:
            indices.append(condition["frame_atom_index"])
        for pair in condition.get("evidence", []):
            indices.extend([pair["atom1"], pair["atom2"]])
        if not indices:
            self.status.setText(condition.get("reason", "此目标在当前帧无法定位。"))
            return
        sid = self.current_view["session_id"]
        try:
            atom_ids = self.current_view["atom_ids"]
            if any(isinstance(index, bool) or not isinstance(index, int) or
                   not 0 <= index < len(atom_ids) for index in indices):
                raise ValueError("当前帧的目标或配对原子索引超出范围，请重新检查。")
            self.bridge.select_indices(sid, sorted(set(indices)))
            obj = self.bridge.objects[sid]
            cmd.show("sticks", "byres (%s)" % self.bridge.selection_name)
            self.clear_measurements()
            for pair in condition.get("evidence", []):
                name = "rfd4_hbond_" + uuid.uuid4().hex[:8]
                cmd.distance(name, "(model %s) and id %d" % (obj, atom_ids[pair["atom1"]]),
                             "(model %s) and id %d" % (obj, atom_ids[pair["atom2"]]), mode=0)
                self.measurements.append(name)
        except Exception as error:
            self.status.setText(str(error))
            self.status.set_error()

    def export_trajectory(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "保存原始 x1 轨迹", "trajectory-x1.cif", "CIF (*.cif)")
        if path:
            def saved(cif):
                Path(path).write_text(cif, encoding="utf-8")
                self.status.setText("已保存原始多模型 CIF。")
            self._request(lambda: self.api.request("GET", self.base + "/cif", text=True),
                          saved, "正在下载原始轨迹…")

    def closeEvent(self, event):
        self.closed = True
        self.play_timer.stop()
        self.evaluation_queue.clear()
        self.clear_measurements()
        for obj in self.bridge.objects.values():
            cmd.delete(obj)
        cmd.delete(self.bridge.selection_name)
        for obj, visible in self.visibility.items():
            if obj in cmd.get_names("objects"):
                (cmd.enable if visible else cmd.disable)(obj)
        super().closeEvent(event)
