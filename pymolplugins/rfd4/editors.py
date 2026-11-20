"""Native Qt request editors; no network, AtomWorks or PyMOL selection access.

``action()`` returns a session-relative POST route and its JSON body. Selection
indices are server indices supplied by the caller, never PyMOL atom IDs. The
caller owns selection expansion for unscoped structure operations and all 409
confirmation handling; these widgets never authorize target loss.
"""

import math
import sys

from pymol.Qt import QtCore, QtWidgets

from pymolplugins.host.ui_icons import set_tool_icon
from .ui import CompactLabel, collapsible


_LABELS = {
    "sequence": "序列约束", "coordinate": "坐标约束", "index": "取消索引约束",
    "hotspot": "热点", "index_group": "索引分组", "coordinate_group": "坐标分组",
    "chain_centroid": "链质心", "centroid": "链质心", "cath": "CATH 折叠类别",
    "rasa": "相对溶剂可及面积", "salt_bridge": "盐桥", "metal_coordination": "金属配位",
    "secondary_structure": "二级结构", "nucleic_acid_secondary_structure": "核酸二级结构",
    "internal_coordinate": "内部坐标", "Sequence probability": "序列概率",
    "hbond": "氢键", "pistack": "π–π 堆积", "pication": "阳离子–π 相互作用",
    "saltbridge": "盐桥", "hydrophobic": "疏水相互作用", "metal_coord": "金属配位",
    "na_ss_state": "核酸二级结构", "cath_label": "CATH 折叠类别", "cath_fold": "CATH 折叠类别",
}
_CONDITION_CATEGORIES = (
    ("常用", ("sequence", "coordinate", "index", "hotspot")),
    ("相互作用", ("rasa", "hbond", "pistack", "pication", "saltbridge", "hydrophobic", "metal_coord",
                   "salt_bridge", "metal_coordination")),
    ("二级结构与折叠", ("secondary_structure", "na_ss_state", "cath_label",
                       "nucleic_acid_secondary_structure", "cath", "cath_fold")),
    ("分组与空间", ("index_group", "coordinate_group", "chain_centroid", "centroid")),
)
# Accepted mmCIF strings in ChainTypeInfo.STRING_TO_ENUM, passed unchanged by
# StudioDocument.insert_segment() to insert_expandable_segment().
_SEGMENT_CHAIN_TYPES = (
    ("蛋白质（L 型）", "polypeptide(L)"),
    ("DNA", "polydeoxyribonucleotide"),
    ("RNA", "polyribonucleotide"),
)
_UNITS = {"atoms": "原子", "residues": "残基", "chains": "链", "groups": "组", "pairs": "对"}
_ENUM_LABELS = {
    "NULL": "未设置", "NONE": "无", "HELIX": "螺旋", "SHEET": "折叠片",
    "STRAND": "链状折叠", "LOOP": "环", "COIL": "无规卷曲", "UNKNOWN": "未知",
}
_BOND_LABELS = {
    "SINGLE": "单键", "DOUBLE": "双键", "TRIPLE": "三键",
    "AROMATIC_SINGLE": "芳香单键", "AROMATIC_DOUBLE": "芳香双键",
    "AROMATIC": "芳香键", "COORDINATION": "配位键", "ANY": "任意键",
}


def _label(key, spec):
    return _LABELS.get(key, spec.get("label", key))


def _combo(items, parent=None):
    widget = QtWidgets.QComboBox(parent)
    for label, value in items:
        widget.addItem(str(label), value)
    return widget


def _enabled_combo():
    return _combo([("启用 / 设置", True), ("清除（恢复默认）", False)])


def _number(spec=None, integer=False, default=None):
    spec = spec or {}
    widget = QtWidgets.QSpinBox() if integer else QtWidgets.QDoubleSpinBox()
    if not integer:
        widget.setDecimals(int(spec.get("decimals", 4)))
    lower = spec.get("minimum", -2147483647 if integer else -sys.float_info.max)
    upper = spec.get("maximum", 2147483647 if integer else sys.float_info.max)
    widget.setRange(int(lower) if integer else float(lower), int(upper) if integer else float(upper))
    widget.setSingleStep(int(spec.get("step", 1)) if integer else float(spec.get("step", 0.1)))
    value = spec.get("default") if default is None else default
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        widget.setValue(int(value) if integer else float(value))
    else:
        widget.setValue(max(lower, min(0, upper)))
    widget.setKeyboardTracking(False)
    return widget


def _numeric_value(widget, label):
    # Do not silently accept the previous value when the user typed invalid text.
    if not widget.hasAcceptableInput():
        raise ValueError("{}请输入范围内的有效数字。".format(label))
    value = widget.value()
    if not math.isfinite(value):
        raise ValueError("{}必须是有限数字。".format(label))
    return value


def _indices(values, required=True, exact=None):
    if values is None:
        values = []
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
        raise ValueError("当前选择已失效，请重新同步结构并选择原子。")
    result = list(dict.fromkeys(values))
    if exact is not None and len(result) != exact:
        raise ValueError("此操作需要恰好选择 {} 个不同原子。".format(exact))
    if required and not result:
        raise ValueError("请先选择需要编辑的原子。")
    return result


def _revision(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("结构版本无效，请重新同步结构。")
    return value


def _scoped(values, scope, revision, required=True):
    if scope not in ("atom", "residue", "chain"):
        raise ValueError("选择范围必须是原子、残基或链。")
    return {"atom_indices": _indices(values, required), "scope": scope,
            "expected_revision": _revision(revision)}


def _display(value):
    if isinstance(value, (list, tuple)):
        return "[{}]".format(", ".join(_display(item) for item in value))
    if isinstance(value, bool):
        return "是" if value else "否"
    if value is None:
        return "未设置"
    return str(value)


class _FormEditor(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._fields = {}
        self._form_widget = None

    def _new_form(self):
        if self._form_widget is not None:
            self._layout.removeWidget(self._form_widget)
            self._form_widget.hide()
            self._form_widget.deleteLater()
        self._fields = {}
        self._form_widget = QtWidgets.QWidget(self)
        self._help_button = None
        self._form = QtWidgets.QFormLayout(self._form_widget)
        self._form.setContentsMargins(0, 0, 0, 0)
        self._form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        self._form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        self._layout.addWidget(self._form_widget)

    def _add(self, key, label, widget):
        self._fields[key] = widget
        self._form.addRow(label, widget)
        return widget

    def _note(self, text):
        if self._help_button is None:
            self._help_button = QtWidgets.QToolButton()
            set_tool_icon(self._help_button, "info")
            self._help_button.setAccessibleName("编辑说明")
            self._form.addRow(self._help_button)
            help_button = self._help_button
            help_button.clicked.connect(lambda: QtWidgets.QMessageBox.information(
                self, "编辑说明", help_button.toolTip()))
        previous = self._help_button.toolTip()
        self._help_button.setToolTip((previous + "\n\n" if previous else "") + text)
        return self._help_button


class ConditionEditor(_FormEditor):
    """Render one schema category/condition and return conditions/groups/centroids.

    ``configure`` accepts the server's ``schema['conditions']`` mapping.
    ``set_structure`` accepts ``StudioDocument.summary()`` unchanged, including
    ``annotations``, ``condition_counts`` and ``condition_editors``.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._conditions = {}
        self._summary = {}
        self._categories = {}
        selector = QtWidgets.QFormLayout()
        self.category_combo = QtWidgets.QComboBox()
        self.option_combo = QtWidgets.QComboBox()
        self._option_label = QtWidgets.QLabel("当前条件")
        selector.addRow("条件类别", self.category_combo)
        selector.addRow(self._option_label, self.option_combo)
        self._layout.addLayout(selector)
        self.category_combo.currentIndexChanged.connect(self._category_changed)
        self.option_combo.currentIndexChanged.connect(self._render)
        self._render()

    def configure(self, conditions: dict):
        previous = self._key()
        self._conditions = dict(conditions or {})
        self._categories = {}
        # Keep schema-defined editor groups together, including all probability
        # tokens. Known conditions use task-oriented categories; new schema keys
        # remain editable under Other rather than disappearing.
        category_for_key = {key: ("category", label)
                            for label, keys in _CONDITION_CATEGORIES for key in keys}
        keys = [key for _, members in _CONDITION_CATEGORIES for key in members if key in self._conditions]
        keys += [key for key in self._conditions if key not in keys]
        for key in keys:
            spec = self._conditions[key]
            if spec.get("visible") is False:
                continue
            group = spec.get("editor_group")
            category = ("group", group) if group else category_for_key.get(key, ("category", "其他条件"))
            self._categories.setdefault(category, []).append(key)
        order = [("category", label) for label, _ in _CONDITION_CATEGORIES]
        order.append(("group", "Sequence probability"))
        order += [category for category in self._categories if category not in order and category != ("category", "其他条件")]
        order.append(("category", "其他条件"))
        self._categories = {category: self._categories[category] for category in order if category in self._categories}
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        selected = 0
        for category, members in self._categories.items():
            label = _LABELS.get(category[1], category[1])
            self.category_combo.addItem(label, category)
            if previous in members:
                selected = self.category_combo.count() - 1
        self.category_combo.setCurrentIndex(selected)
        self.category_combo.blockSignals(False)
        self._category_changed()
        index = self.option_combo.findData(previous)
        if index >= 0:
            self.option_combo.setCurrentIndex(index)

    def set_structure(self, summary: dict):
        self._summary = dict(summary or {})
        self._render()

    def _key(self):
        return self.option_combo.currentData()

    def _category_changed(self, *_):
        category = self.category_combo.currentData()
        members = self._categories.get(category, [])
        self.option_combo.blockSignals(True)
        self.option_combo.clear()
        for key in members:
            spec = self._conditions[key]
            self.option_combo.addItem(str(spec.get("option_label") or _label(key, spec)), key)
        self.option_combo.blockSignals(False)
        self.option_combo.setVisible(bool(members))
        self._option_label.setVisible(bool(members))
        self._render()

    def _state(self, field, key):
        return self._summary.get("condition_editors", {}).get(field, {}).get(key, [])

    def _render(self, *_):
        self._new_form()
        key = self._key()
        if key not in self._conditions:
            self._note("请连接服务器以读取条件定义。")
            return
        spec = self._conditions[key]
        kind = spec.get("kind")
        if spec.get("group_editor"):
            mode = self._add("group_action", "分组操作", _combo([
                ("新建组并加入选择", "new"), ("将选择加入现有组", "add"),
                ("将选择移出分组（恢复默认）", "remove"), ("删除整个组", "delete")]))
            groups = self._state("groups", key)
            self._add("group_id", "现有组", _combo([
                ("组 {} · {} 原子{}".format(group["id"], group["count"],
                 "（不可删除）" if not group.get("can_delete", True) else ""), group["id"])
                for group in groups]))
            mode.currentIndexChanged.connect(self._update_fields)
            self._note("删除整组不需要选择原子；移出分组仅影响当前选择，不按组号筛选。")
        else:
            mode = self._add("enabled", "操作", _enabled_combo())
            if spec.get("invert_mask"):
                mode.setItemText(0, "启用取消索引约束")
                mode.setItemText(1, "清除取消状态（恢复索引约束）")
                self._note("启用表示取消所选位置的索引约束，不是固定索引。")
            if kind == "enum":
                choices = spec.get("values", {})
                widget = self._add("value", "取值", _combo([
                    (_ENUM_LABELS.get(name, name), value) for name, value in choices.items()]))
                index = widget.findData(spec.get("default"))
                if index >= 0:
                    widget.setCurrentIndex(index)
            elif kind in ("scalar", "integer", "pair"):
                self._add("value", "距离（Å）" if kind == "pair" else "取值",
                          _number(spec, integer=kind == "integer"))
            elif kind == "text":
                text = QtWidgets.QLineEdit()
                if spec.get("default") is not None:
                    text.setText(str(spec["default"]))
                if key in ("cath", "cath_fold", "cath_label"):
                    text.setPlaceholderText("例如 1.x.x 或 2.60.40")
                self._add("value", "文本", text)
            elif kind == "vector3":
                if spec.get("level") == "chain":
                    chains = self._state("centroids", key)
                    self._add("chain", "目标链", _combo([(entry["chain"], entry["chain"]) for entry in chains]))
                vector_widget = QtWidgets.QWidget()
                row = QtWidgets.QHBoxLayout(vector_widget)
                row.setContentsMargins(0, 0, 0, 0)
                vector = []
                default = spec.get("default")
                for axis in range(3):
                    axis_spec = dict(spec)
                    axis_spec.pop("default", None)
                    widget = _number(axis_spec, default=default[axis] if isinstance(default, (list, tuple)) else 0)
                    widget.setPrefix("XYZ"[axis] + " ")
                    row.addWidget(widget)
                    vector.append(widget)
                self._fields["vector"] = vector
                self._form.addRow("坐标（Å）", vector_widget)
                if "chain" in self._fields:
                    self._fields["chain"].currentIndexChanged.connect(self._load_centroid)
                    self._load_centroid()
            elif kind not in ("mask", "operation"):
                self._note("此服务器条件类型尚不可编辑：{}".format(kind))
            if kind == "operation":
                self._note("内部坐标通过专用结构操作接口应用。")
            mode.currentIndexChanged.connect(self._update_fields)
        level = {"atom": "原子", "residue": "残基", "chain": "链"}.get(spec.get("level"), spec.get("level", ""))
        self._note("条件层级：{}；清除恢复服务器默认值：{}。".format(level, _display(spec.get("default"))))
        self._update_fields()
        self._add_overview(key, spec)

    def _update_fields(self, *_):
        if "group_action" in self._fields:
            self._fields["group_id"].setEnabled(self._fields["group_action"].currentData() in ("add", "delete"))
        elif "enabled" in self._fields:
            enabled = self._fields["enabled"].currentData()
            if "value" in self._fields:
                self._fields["value"].setEnabled(enabled)
            for widget in self._fields.get("vector", []):
                widget.setEnabled(enabled)

    def _load_centroid(self, *_):
        chain = self._fields["chain"].currentData()
        for entry in self._state("centroids", self._key()):
            if entry["chain"] == chain:
                value = entry.get("target") or entry.get("current")
                for axis, widget in enumerate(self._fields["vector"]):
                    widget.setValue(value[axis] if value is not None else 0)
                break

    def _add_overview(self, key, spec):
        lines = []
        entries = [entry for entry in self._summary.get("annotations", []) if entry.get("name") == key]
        for entry in entries:
            lines.append("已设置：{} {}".format(entry.get("count", 0), _UNITS.get(entry.get("unit"), entry.get("unit", ""))))
            for item in entry.get("values", []):
                value = item.get("value", "已启用")
                if spec.get("kind") == "mask" and spec.get("invert_mask"):
                    value = "已取消索引约束"
                lines.append("{} · {} 原子".format(_display(value), item.get("count", 0)))
        if not entries:
            count = self._summary.get("condition_counts", {}).get(key, 0)
            lines.append("已设置数量：{}".format(count) if count else "当前没有非默认条件值。")
        for group in self._state("groups", key):
            lines.append("组 {}：{} 原子{}".format(group["id"], group["count"], "（不可删除）" if not group.get("can_delete", True) else ""))
        for entry in self._state("centroids", key):
            lines.append("链 {}：当前质心 {}；目标 {}".format(entry["chain"], _display(entry.get("current")), _display(entry.get("target"))))
        overview = QtWidgets.QPlainTextEdit()
        overview.setReadOnly(True)
        overview.setPlainText("\n".join(lines))
        overview.setMaximumHeight(78)
        # Keep the original overview widget available to GUI automation.
        self._fields["overview"] = overview
        details = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(details)
        layout.setContentsMargins(0, 0, 0, 0)
        summary = CompactLabel(lines[0] if entries else "已设置 %s" % self._summary.get("condition_counts", {}).get(key, 0))
        layout.addWidget(summary)
        self.overview_toggle = collapsible("当前状态详情", overview, layout)
        self._form.addRow(details)

    def action(self, atom_indices: list[int], scope: str, revision: int) -> tuple[str, dict]:
        key = self._key()
        if key not in self._conditions:
            raise ValueError("请先连接服务器并选择条件。")
        spec = self._conditions[key]
        kind = spec.get("kind")
        if spec.get("group_editor"):
            action = self._fields["group_action"].currentData()
            payload = _scoped(atom_indices, scope, revision, required=action != "delete")
            payload.update(condition=key, action=action)
            if action in ("add", "delete"):
                group_id = self._fields["group_id"].currentData()
                group = next((item for item in self._state("groups", key) if item["id"] == group_id), None)
                if group is None:
                    raise ValueError("请先选择一个现有分组。")
                if action == "delete" and not group.get("can_delete", True):
                    raise ValueError("默认未分配组不能删除。")
                payload["group_id"] = group_id
            return "groups", payload
        enabled = self._fields["enabled"].currentData()
        value = None
        if enabled:
            widget = self._fields.get("value")
            if kind == "enum":
                value = widget.currentData()
                if value is None:
                    raise ValueError("服务器未提供可用的条件取值。")
            elif kind in ("scalar", "integer", "pair"):
                value = _numeric_value(widget, "条件值")
                if kind == "pair" and value <= 0:
                    raise ValueError("距离必须大于 0。")
            elif kind == "text":
                value = widget.text()
            elif kind == "vector3":
                value = [_numeric_value(widget, "XYZ"[axis] + " 坐标") for axis, widget in enumerate(self._fields["vector"])]
        if kind == "vector3" and spec.get("level") == "chain":
            chain = self._fields["chain"].currentData()
            if chain is None:
                raise ValueError("请先载入结构并选择目标链。")
            return "centroids", {"condition": key, "chain": chain, "value": value,
                                 "expected_revision": _revision(revision)}
        payload = _scoped(atom_indices, scope, revision)
        if kind == "operation":
            if key not in ("internal_coordinate", "internal_coords"):
                raise ValueError("此条件需要服务器专用操作，不能作为普通条件提交。")
            payload["enabled"] = enabled
            return "internal-coords", payload
        if kind not in ("mask", "enum", "scalar", "integer", "text", "vector3", "pair"):
            raise ValueError("此服务器条件类型尚不可编辑。")
        if kind == "pair":
            _indices(atom_indices, exact=2)
            # Pair selection must not expand into whole residues or chains.
            payload["scope"] = "atom"
        payload.update(condition=key, enabled=enabled, value=value)
        return "conditions", payload


class OperationEditor(_FormEditor):
    """One structure-operation selector and its current request form.

    ``capabilities`` is the server schema (notably ``bond_types``).
    ``set_sessions`` takes available *other* sessions as ``(id, label)`` pairs;
    the caller excludes the active session, whose ID is not in a summary.
    """

    _OPERATIONS = (
        ("插入可变长度片段", "segments"), ("展开片段", "segments/realize"),
        ("折叠片段", "segments/collapse"), ("删除所选原子", "delete"),
        ("重编号残基", "reset-residue-ids"), ("撤销", "undo"),
        ("重置全部条件", "conditions/reset"), ("调整电荷", "charges"),
        ("质子化", "protonate"), ("分配链", "chains"), ("编辑键", "bonds"),
        ("从 CCD 推断键", "bonds/infer-ccd"), ("距离约束", "distance-restraints"),
        ("内部坐标约束", "internal-coords"), ("清除内部坐标目标", "internal-coords/clear-targets"),
        ("合并会话结构", "combine"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._capabilities = {}
        self._summary = {}
        self._sessions = []
        self.operation_combo = _combo(self._OPERATIONS)
        selector = QtWidgets.QFormLayout()
        selector.addRow("结构操作", self.operation_combo)
        self._layout.addLayout(selector)
        self.operation_combo.currentIndexChanged.connect(self._render)
        self._render()

    def configure(self, capabilities: dict):
        self._capabilities = dict(capabilities or {})
        self._render()

    def set_structure(self, summary: dict):
        self._summary = dict(summary or {})
        if self.operation_combo.currentData() == "undo":
            self._render()

    def set_sessions(self, sessions: list[tuple[str, str]]):
        self._sessions = list(sessions or [])
        if self.operation_combo.currentData() == "combine":
            selected = {item.data(QtCore.Qt.UserRole) for item in self._fields["sessions"].selectedItems()}
            self._render()
            for index in range(self._fields["sessions"].count()):
                item = self._fields["sessions"].item(index)
                item.setSelected(item.data(QtCore.Qt.UserRole) in selected)

    def _render(self, *_):
        self._new_form()
        operation = self.operation_combo.currentData()
        if operation == "segments":
            self._add("minimum", "最小残基数", _number({"minimum": 1, "maximum": 10000}, True, 1))
            self._add("maximum", "最大残基数", _number({"minimum": 1, "maximum": 10000}, True, 1))
            mode = self._add("new_chain", "插入位置", _combo([("所选原子所在残基", False), ("新建链（不需要选择）", True)]))
            self._add("before", "相对位置", _combo([("残基前", True), ("残基后", False)]))
            chain = QtWidgets.QLineEdit()
            chain.setPlaceholderText("留空由服务器分配")
            self._add("chain_id", "新链名称", chain)
            self._add("chain_type", "新链类型", _combo(_SEGMENT_CHAIN_TYPES))
            self._note("原位置插入需要恰好选择一个原子；服务器按该原子所在残基确定插前或插后。")
            mode.currentIndexChanged.connect(self._update_fields)
        elif operation == "segments/realize":
            seed = QtWidgets.QLineEdit()
            seed.setPlaceholderText("留空使用随机种子；填写非负整数可复现")
            self._add("seed", "随机种子（可选）", seed)
        elif operation == "delete":
            self._add("close_residue_gaps", "残基编号", _combo([("保留编号空缺", False), ("删除后闭合编号空缺", True)]))
        elif operation == "charges":
            self._add("charge_mode", "电荷操作", _combo([("设置绝对值", "value"), ("增加 / 减少", "delta")]))
            self._add("charge", "电荷", _number({"minimum": -10, "maximum": 10}, True, 0))
        elif operation == "protonate":
            self._add("ph", "pH", _number({"minimum": 0, "maximum": 14, "decimals": 2}, default=7.4))
            self._note("此操作对整个结构质子化，不受当前选择范围限制。")
        elif operation == "chains":
            chain = QtWidgets.QLineEdit()
            chain.setPlaceholderText("1–8 个字符")
            self._add("chain_id", "目标链名称", chain)
        elif operation == "bonds":
            mode = self._add("action", "操作", _combo([("添加 / 更新键", "add"), ("删除键", "remove")]))
            types = self._capabilities.get("bond_types", list(_BOND_LABELS))
            self._add("bond_type", "键类型", _combo([(_BOND_LABELS.get(name, name), name) for name in types]))
            self._note("需要恰好选择两个不同原子；不扩展到残基或链。")
            mode.currentIndexChanged.connect(self._update_fields)
        elif operation == "distance-restraints":
            mode = self._add("action", "操作", _combo([("设置距离约束", "set"), ("删除距离约束", "remove")]))
            self._add("distance", "距离（Å）", _number({"minimum": 0.0001, "maximum": 1000, "decimals": 4}, default=3.0))
            self._note("需要恰好选择两个不同原子；不扩展到残基或链。")
            mode.currentIndexChanged.connect(self._update_fields)
        elif operation == "internal-coords":
            self._add("enabled", "操作", _enabled_combo())
            self._note("使用当前选择与范围；如会丢弃已有目标，由主面板统一确认。")
        elif operation == "internal-coords/clear-targets":
            self._note("清除所选范围内保存的内部坐标目标。")
        elif operation == "combine":
            sessions = QtWidgets.QListWidget()
            sessions.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
            sessions.setMaximumHeight(200)
            for session_id, label in self._sessions:
                item = QtWidgets.QListWidgetItem(str(label))
                item.setData(QtCore.Qt.UserRole, session_id)
                item.setToolTip(session_id)
                sessions.addItem(item)
            self._add("sessions", "其他会话（可多选）", sessions)
            self._note("将所选会话结构合并到当前结构；最多选择 16 个其他会话。")
        else:
            notes = {
                "segments/collapse": "折叠整个结构中已展开的可变长度片段。",
                "reset-residue-ids": "重新编号整个结构的残基。",
                "conditions/reset": "将整个结构的所有条件恢复到服务器默认值。",
                "bonds/infer-ccd": "使用 CCD 为整个结构推断键。",
                "undo": "撤销当前结构上一步编辑；可撤销步数：{}。".format(self._summary.get("undo_depth", 0)),
            }
            self._note(notes.get(operation, "请选择结构操作。"))
        if operation in ("delete", "charges", "chains"):
            self._note("按主面板选择范围处理所选原子。")
        self._update_fields()

    def _update_fields(self, *_):
        operation = self.operation_combo.currentData()
        if operation == "segments":
            new_chain = self._fields["new_chain"].currentData()
            self._fields["before"].setEnabled(not new_chain)
            self._fields["chain_id"].setEnabled(new_chain)
            for key in ("chain_id", "chain_type"):
                widget = self._fields[key]
                widget.setVisible(new_chain)
                self._form.labelForField(widget).setVisible(new_chain)
        elif operation == "bonds":
            self._fields["bond_type"].setEnabled(self._fields["action"].currentData() == "add")
        elif operation == "distance-restraints":
            self._fields["distance"].setEnabled(self._fields["action"].currentData() == "set")

    def action(self, atom_indices: list[int], scope: str, revision: int) -> tuple[str, dict]:
        operation = self.operation_combo.currentData()
        fields = self._fields
        if operation == "segments":
            minimum = _numeric_value(fields["minimum"], "最小残基数")
            maximum = _numeric_value(fields["maximum"], "最大残基数")
            if maximum < minimum:
                raise ValueError("最大残基数不能小于最小残基数。")
            new_chain = fields["new_chain"].currentData()
            payload = {"minimum": minimum, "maximum": maximum, "new_chain": new_chain,
                       "before": fields["before"].currentData()}
            if new_chain:
                chain_id = fields["chain_id"].text().strip()
                # The document's insertion method is stricter than its API model.
                if len(chain_id) > 4:
                    raise ValueError("新链名称最多允许 4 个字符。")
                payload["chain_id"] = chain_id or None
                payload["chain_type"] = fields["chain_type"].currentData()
            else:
                payload["atom_index"] = _indices(atom_indices, exact=1)[0]
            return operation, payload
        if operation == "segments/realize":
            text = fields["seed"].text().strip()
            seed = None
            if text:
                try:
                    seed = int(text)
                except ValueError:
                    raise ValueError("随机种子必须是非负整数，或留空。") from None
                if seed < 0:
                    raise ValueError("随机种子不能为负数。")
            return operation, {"seed": seed}
        if operation == "delete":
            return operation, {"atom_indices": _indices(atom_indices),
                               "close_residue_gaps": fields["close_residue_gaps"].currentData()}
        if operation == "charges":
            return operation, {"atom_indices": _indices(atom_indices),
                               fields["charge_mode"].currentData(): _numeric_value(fields["charge"], "电荷")}
        if operation == "protonate":
            return operation, {"ph": _numeric_value(fields["ph"], "pH")}
        if operation == "chains":
            chain_id = fields["chain_id"].text().strip()
            if not chain_id or len(chain_id) > 8:
                raise ValueError("目标链名称必须包含 1–8 个字符。")
            return operation, {"atom_indices": _indices(atom_indices), "chain_id": chain_id}
        if operation in ("bonds", "distance-restraints"):
            atoms = _indices(atom_indices, exact=2)
            action = fields["action"].currentData()
            payload = {"atom1": atoms[0], "atom2": atoms[1], "action": action}
            if operation == "bonds" and action == "add":
                bond_type = fields["bond_type"].currentData()
                if not bond_type:
                    raise ValueError("请先选择服务器支持的键类型。")
                payload["bond_type"] = bond_type
            elif operation == "distance-restraints" and action == "set":
                payload["distance"] = _numeric_value(fields["distance"], "距离")
            return operation, payload
        if operation in ("internal-coords", "internal-coords/clear-targets"):
            payload = _scoped(atom_indices, scope, revision)
            payload["enabled"] = fields["enabled"].currentData() if operation == "internal-coords" else True
            return operation, payload
        if operation == "combine":
            session_ids = list(dict.fromkeys(item.data(QtCore.Qt.UserRole) for item in fields["sessions"].selectedItems()))
            if not session_ids or len(session_ids) > 16:
                raise ValueError("请选择 1–16 个其他会话进行合并。")
            return operation, {"session_ids": session_ids}
        if operation == "conditions/reset":
            return operation, {"expected_revision": _revision(revision)}
        if operation in ("segments/collapse", "reset-residue-ids", "undo", "bonds/infer-ccd"):
            return operation, {}
        raise ValueError("请选择有效的结构操作。")
