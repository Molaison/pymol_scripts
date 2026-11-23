"""Qt Collections panel, imported only when the panel is requested."""

from pymol import cmd
from pymol.Qt import QtCore, QtGui, QtWidgets
import datetime
import json
import math
from pathlib import Path
import sys
import traceback
from concurrent.futures import CancelledError, ThreadPoolExecutor
from itertools import chain

from pymolplugins.annotation.panel_style import apply_style
from . import core

_dialog = None


def file_field(parent, directory=False, save=False):
    widget = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    edit, button = QtWidgets.QLineEdit(), QtWidgets.QPushButton("浏览…")
    layout.addWidget(edit, 1)
    layout.addWidget(button)

    def browse():
        if directory:
            path = QtWidgets.QFileDialog.getExistingDirectory(parent, "选择目录", edit.text())
        elif save:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(parent, "选择文件", edit.text())
        else:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(parent, "选择文件", edit.text())
        if path:
            edit.setText(path)

    button.clicked.connect(browse)
    return widget, edit


def _form_layout(parent=None):
    form = QtWidgets.QFormLayout(parent)
    form.setFieldGrowthPolicy(QtWidgets.QFormLayout.ExpandingFieldsGrow)
    form.setFormAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
    form.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
    form.setHorizontalSpacing(10)
    form.setVerticalSpacing(6)
    return form


class ScoreTable(QtWidgets.QTableWidget):
    def __init__(self):
        super().__init__(0, 5)
        self.setHorizontalHeaderLabels(["采用 / 打分列", "层数", "最低值（可空）", "最高值（可空）", "数值示例"])
        self.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.verticalHeader().setDefaultSectionSize(26)
        self.setMinimumHeight(190)

    def populate(self, rows):
        self.setRowCount(0)
        if not rows:
            raise ValueError("打分表为空。")
        columns = [name for name in dict.fromkeys(chain.from_iterable(rows))
                   if isinstance(name, str) and name]
        for name in columns:
            values = []
            for row in rows:
                raw = row.get(name)
                if raw is None or raw == "":
                    continue
                if isinstance(raw, str):
                    prefix = raw.lstrip()
                    if not prefix or (prefix[0] not in "+-." and not prefix[0].isdecimal()):
                        continue
                try:
                    value = float(raw)
                except (ValueError, TypeError, OverflowError):
                    continue
                if math.isfinite(value):
                    values.append(value)
                    if len(values) == 100:
                        break
            if not values:
                continue
            self.add_metric(name, f"{min(values):.4g} … {max(values):.4g}")
        return columns

    def add_metric(self, name, example):
        index = self.rowCount()
        self.insertRow(index)
        item = QtWidgets.QTableWidgetItem(name)
        item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
        item.setCheckState(QtCore.Qt.Unchecked)
        self.setItem(index, 0, item)
        bins = QtWidgets.QSpinBox()
        bins.setRange(1, 20)
        bins.setValue(3)
        self.setCellWidget(index, 1, bins)
        self.setItem(index, 2, QtWidgets.QTableWidgetItem(""))
        self.setItem(index, 3, QtWidgets.QTableWidgetItem(""))
        sample = QtWidgets.QTableWidgetItem(example)
        sample.setFlags(sample.flags() & ~QtCore.Qt.ItemIsEditable)
        self.setItem(index, 4, sample)
        return index

    def specifications(self):
        result = []
        for row in range(self.rowCount()):
            if self.item(row, 0).checkState() != QtCore.Qt.Checked:
                continue
            spec = {"name": self.item(row, 0).text(), "bins": self.cellWidget(row, 1).value()}
            for column, key in ((2, "min"), (3, "max")):
                spec[key] = self.item(row, column).text().strip() or None
            spec["min"], spec["max"] = core.metric_bounds(spec)
            result.append(spec)
        if not result:
            raise ValueError("请勾选至少一个关注的打分列。")
        return result


class SamplingForm(QtWidgets.QGroupBox):
    def __init__(self):
        super().__init__("数据与采样")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        form = _form_layout()
        score_widget, self.scores = file_field(self)
        root_widget, self.root = file_field(self, directory=True)
        output_widget, self.output = file_field(self, directory=True)
        self.path_column = QtWidgets.QComboBox()
        self.load_button = QtWidgets.QPushButton("读取打分列")
        self.scores.setToolTip("CSV、TSV、JSON 行列表或 Rosetta 打分文件")
        form.addRow("打分文件", score_widget)
        form.addRow("", self.load_button)
        form.addRow("结构路径列", self.path_column)
        self.root.setToolTip("打分表中的相对结构路径从此目录解析")
        self.root.setPlaceholderText("新建时留空使用打分文件所在目录")
        form.addRow("结构根目录", root_widget)
        form.addRow("保存目录", output_widget)
        layout.addLayout(form)
        self.table = ScoreTable()
        layout.addWidget(self.table, 1)
        help_text = QtWidgets.QLabel("先用最低 / 最高值限定展示范围，再覆盖所选分数的联合分位层。\n"
                                    "相同分数不拆层；逐层轮询，所有已选样本之间执行结构去重。")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        options = _form_layout()
        self.count, self.seed = QtWidgets.QSpinBox(), QtWidgets.QSpinBox()
        self.count.setRange(1, 10000)
        self.count.setValue(24)
        self.seed.setRange(0, 2147483647)
        self.diversity = QtWidgets.QComboBox()
        self.diversity.addItem("binder 形状（去除旋转和平移）", "shape")
        self.diversity.addItem("binder 结合姿态（先按 target 配准）", "pose")
        self.diversity_selection = QtWidgets.QLineEdit()
        self.diversity_selection.setPlaceholderText("留空使用 binder；可填 PyMOL selection")
        self.rmsd, self.coverage = QtWidgets.QDoubleSpinBox(), QtWidgets.QDoubleSpinBox()
        self.rmsd.setRange(0.01, 100)
        self.rmsd.setValue(2)
        self.rmsd.setSuffix(" Å")
        self.coverage.setRange(0.1, 1)
        self.coverage.setSingleStep(0.05)
        self.coverage.setValue(0.8)
        self.allow_unverified = QtWidgets.QCheckBox("允许纳入无法验证差异的样本（会明确标记）")
        for label, widget in (("样本数", self.count), ("随机种子", self.seed),
                              ("结构去重", self.diversity), ("多样性选择", self.diversity_selection),
                              ("最小结构差异", self.rmsd), ("CA 对应覆盖率", self.coverage)):
            options.addRow(label, widget)
        options.addRow(self.allow_unverified)
        layout.addLayout(options)

    def load_columns(self):
        rows = core.read_scores(self.scores.text().strip())
        columns = self.table.populate(rows)
        self.path_column.clear()
        self.path_column.addItems(columns)
        for preferred in ("path", "structure_path", "file", "filename", "description", "id"):
            index = self.path_column.findText(preferred)
            if index >= 0:
                self.path_column.setCurrentIndex(index)
                break
        if not self.output.text():
            score_parent = Path(self.scores.text().strip()).resolve().parent
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.output.setText(str(score_parent / "collections" / f"collection_{stamp}"))

    def configuration(self):
        return {"scores": str(Path(self.scores.text().strip()).resolve()),
                "root": str(Path(self.root.text().strip()).resolve()) if self.root.text().strip() else "",
                "path_column": self.path_column.currentText(), "metrics": self.table.specifications(),
                "count": self.count.value(), "seed": self.seed.value(), "rmsd": self.rmsd.value(),
                "coverage": self.coverage.value(), "diversity": self.diversity.currentData(),
                "diversity_selection": self.diversity_selection.text().strip(),
                "allow_unverified": self.allow_unverified.isChecked()}

    def restore_config(self, config, directory):
        self.scores.setText(config["scores"])
        self.root.setText(config.get("root", ""))
        self.output.setText(str(directory))
        self.path_column.clear()
        self.path_column.addItem(config.get("path_column", "path"))
        self.table.setRowCount(0)
        for spec in config["metrics"]:
            row = self.table.add_metric(spec["name"], "已保存的配置")
            self.table.item(row, 0).setCheckState(QtCore.Qt.Checked)
            self.table.cellWidget(row, 1).setValue(spec.get("bins", 3))
            for column, key in ((2, "min"), (3, "max")):
                value = spec.get(key)
                self.table.item(row, column).setText("" if value is None else str(value))
        for name, default in (("count", 24), ("seed", 0), ("rmsd", 2), ("coverage", 0.8)):
            getattr(self, name).setValue(config.get(name, default))
        self.diversity.setCurrentIndex(self.diversity.findData(config.get("diversity", "shape")))
        self.diversity_selection.setText(config.get("diversity_selection", ""))
        self.allow_unverified.setChecked(config.get("allow_unverified", False))


class RenderForm(QtWidgets.QGroupBox):
    def __init__(self):
        super().__init__("朝向与导出")
        form = _form_layout(self)
        form.setContentsMargins(10, 10, 10, 10)
        self.target, self.binder = QtWidgets.QLineEdit("chain A"), QtWidgets.QLineEdit("chain B")
        self.orientation = QtWidgets.QComboBox()
        for label, value in (("统一 target", "target"), ("统一 binder", "binder"),
                              ("各结构 PCA 主轴", "pca"), ("各结构对称轴", "symmetry")):
            self.orientation.addItem(label, value)
        reference_widget, self.reference = file_field(self)
        self.reference.setPlaceholderText("留空以第一个入选样本为参考")
        self._restored_reference = ""
        self.frame_selection = QtWidgets.QLineEdit("polymer.protein")
        self.axis = QtWidgets.QLineEdit()
        self.axis.setPlaceholderText("如 0,0,1；留空根据至少三条环状链估计")
        self.workers = QtWidgets.QSpinBox()
        self.workers.setRange(1, 64)
        self.workers.setValue(2)
        self.width, self.height = QtWidgets.QSpinBox(), QtWidgets.QSpinBox()
        for widget, value in ((self.width, 1600), (self.height, 1200)):
            widget.setRange(64, 8192)
            widget.setValue(value)
        python_widget, self.python = file_field(self)
        self.python.setText(core.worker_python())
        for label, widget in (("Target 选择", self.target), ("Binder 选择", self.binder),
                              ("统一朝向", self.orientation), ("参考结构", reference_widget),
                              ("PCA / 对称轴选择", self.frame_selection), ("对称轴向量", self.axis),
                              ("并行 PyMOL 进程", self.workers), ("导出宽度", self.width),
                              ("导出高度", self.height), ("渲染 Python", python_widget)):
            form.addRow(label, widget)
        note = QtWidgets.QLabel("target / binder 模式整体配准复合物，共用参考朝向。\n"
                               "PCA 主轴横向、对称轴纵向；所有图片共用正交相机和缩放。\n"
                               "后台预览与导出均使用 ray renderer，不打开窗口。")
        note.setWordWrap(True)
        form.addRow(note)
        form.addRow(QtWidgets.QLabel("转动样本后可「保存当前朝向」；预览与导出按宽高比例自动取景。"))

    def configuration(self):
        text = self.axis.text().strip()
        axis = [float(value.strip()) for value in text.split(",")] if text else None
        if axis is not None and len(axis) != 3:
            raise ValueError("对称轴请输入三个数值，例如 0,0,1。")
        reference = self.reference.text().strip()
        if reference and reference != self._restored_reference:
            reference = str(Path(reference).resolve())
        return {"target": self.target.text().strip(), "binder": self.binder.text().strip(),
                "orientation": self.orientation.currentData(), "reference": reference,
                "frame_selection": self.frame_selection.text().strip(), "symmetry_axis": axis}

    def restore_config(self, config):
        values = core.orientation_config(config)
        self._restored_reference = values["reference"] or ""
        for name in ("target", "binder", "reference", "frame_selection"):
            getattr(self, name).setText(values[name] or "")
        self.orientation.setCurrentIndex(self.orientation.findData(values["orientation"]))
        axis = values["symmetry_axis"]
        self.axis.setText(",".join(map(str, axis)) if axis else "")
        for name in ("width", "height", "workers"):
            if name in config:
                getattr(self, name).setValue(config[name])

class CollectionTask(QtCore.QObject):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.future, self.control, self.callback = None, None, None
        self.close_pending = False
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.poll)

    def start(self, operation, callback, *, recover_on_failure=True):
        if self.future is not None:
            raise ValueError("已有任务正在执行。")
        self.control, self.callback = core.TaskControl(), callback
        self.recover_on_failure = recover_on_failure
        self.panel.set_busy(True)
        self.future = self.executor.submit(operation, self.control)
        self.timer.start(100)

    def cancel(self):
        if self.future is not None:
            self.control.cancelled.set()
            self.panel.status.setText("正在取消，等待所有后台进程退出…")

    def poll(self):
        phase, completed, total = self.control.snapshot()
        if not self.control.cancelled.is_set():
            self.panel.status.setText(f"{phase}：{completed} / {total}" if total else f"{phase}…")
        self.panel.progress.setRange(0, total)
        self.panel.progress.setValue(completed)
        if not self.future.done():
            return
        future, callback = self.future, self.callback
        self.future = None
        self.timer.stop()
        self.panel.set_busy(False)
        try:
            callback(future.result())
        except CancelledError:
            if self.recover_on_failure:
                self.panel.recover_current()
                self.panel.status.setText("已取消，后台进程已退出。已保存的结果保留，可重试未完成项。")
            else:
                self.panel.status.setText("已取消展示页核对。")
        except Exception as error:
            if self.recover_on_failure:
                self.panel.recover_current()
            self.panel.show_error(error)
        if self.close_pending:
            self.close_pending = False
            self.panel.close()


class CollectionActions:
    def __init__(self, panel):
        self.panel = panel

    def rendering_options(self):
        rendering = self.panel.rendering
        return {"workers": rendering.workers.value(), "width": rendering.width.value(),
                "height": rendering.height.value(), "python": rendering.python.text().strip()}

    def apply_pending(self, path, options, configuration, control, reapply=False):
        manifest = core.read_json(path)
        if reapply or core.orientation_config(manifest["config"]) != configuration:
            core.revise_external(path, {"action": "orient", "config": configuration},
                                 options["python"], control)
        with core.edit_collection(path) as manifest:
            if core.orientation_config(manifest["config"]) != configuration:
                raise ValueError("Collection 朝向已变化，请刷新设置后重试。")
            manifest["config"].update({key: options[key] for key in ("workers", "width", "height")})
        return manifest

    def render(self, path, options, control, preview=True, retry=False):
        control.check()
        width, height = options["width"], options["height"]
        if preview:
            width, height = core.preview_size(width, height)
        return core.export_collection(path, options["workers"], width, height, python=options["python"],
                                      preview=preview, control=control, retry_failed=retry)

    def generate(self):
        panel = self.panel
        configuration = panel.sampling.configuration()
        configuration.update(panel.rendering.configuration())
        options = self.rendering_options()
        configuration.update({key: options[key] for key in ("workers", "width", "height")})
        text = panel.sampling.output.text().strip()
        if not text:
            raise ValueError("请填写 collection 保存目录。")
        path = Path(text).resolve() / "01_collection.json"
        if path.exists():
            raise ValueError("此目录已有 collection。请使用「应用朝向并刷新预览」或选择新目录。")
        panel.pending_path = path
        def work(control):
            core.analyze_external(configuration, path.parent, options["python"], control)
            result = self.render(path, options, control)
            return path, result
        panel.task.start(work, panel.finished)

    def refresh(self, exporting=False, retry=False, reapply=False):
        panel = self.panel
        panel.require_manifest()
        path, options = panel.manifest_path, self.rendering_options()
        configuration = panel.rendering.configuration()
        panel.pending_path = path
        def work(control):
            self.apply_pending(path, options, configuration, control, reapply=reapply)
            preview_result = self.render(path, options, control, retry=exporting or retry)
            if exporting and not control.cancelled.is_set():
                result = self.render(path, options, control, preview=False, retry=retry)
            else:
                result = preview_result
            return path, result
        panel.task.start(work, panel.finished)

    def edit(self, action):
        panel = self.panel
        panel.require_manifest()
        indices = sorted(panel.samples.row(item) for item in panel.samples.selectedItems())
        if not indices:
            raise ValueError("请先选中样本。")
        if action == "replace" and len(indices) != 1:
            raise ValueError("替换时请选择一个样本。")
        path, options = panel.manifest_path, self.rendering_options()
        panel.pending_path = path
        selected = [panel.manifest["samples"][index] for index in indices]
        request = {"action": action, "selected_samples": [core.sample_identity(row) for row in selected]}
        if action == "pin":
            request["pinned"] = not all(row.get("pinned", False) for row in selected)
        elif action == "replace" and core.relative_source_fields(panel.manifest["config"]):
            request["source_paths"] = {"scores": panel.sampling.scores.text().strip(),
                                       "root": panel.sampling.root.text().strip()}

        def work(control):
            core.revise_external(path, request, options["python"], control)
            result = self.render(path, options, control, retry=True)
            return path, result
        panel.task.start(work, panel.finished)

    def capture(self):
        panel = self.panel
        panel.require_manifest()
        if panel.previous_scene is None:
            raise ValueError("请先双击样本，在统一坐标系中调整朝向。")
        if core.orientation_config(panel.manifest["config"]) != panel.rendering.configuration():
            raise ValueError("请先应用表单中的朝向修改，再手动转动样本。")
        path, options = panel.manifest_path, self.rendering_options()
        with core.edit_collection(path) as manifest:
            if core.view_frame(manifest) != core.view_frame(panel.manifest):
                raise ValueError("样本或朝向已变化，请刷新预览后重新保存。")
            manifest["view"] = list(cmd.get_view())
            manifest["config"].update({key: options[key] for key in ("workers", "width", "height")})
        panel.manifest = manifest
        panel.pending_path = path

        def work(control):
            return path, self.render(path, options, control)

        panel.task.start(work, panel.finished)


class CollectionPreview:
    def __init__(self, panel):
        self.panel = panel
        self.object_name = None

    def show_sample(self, index):
        panel = self.panel
        panel.require_manifest()
        if not 0 <= index < len(panel.manifest["samples"]):
            raise ValueError("样本编号超出范围。")
        manifest = panel.manifest
        row = manifest["samples"][index]
        enabled = cmd.get_names("all", enabled_only=1)
        view = cmd.get_view()
        staged = cmd.get_unused_name("collections_preview")
        width, height = cmd.get_viewport()
        target_view = core.collection_view(manifest, max(width, 64), max(height, 64))
        try:
            core.load_structure(cmd, row["path"], staged)
            expected = row.get("coordinate_signature")
            sort_for_validation = (
                expected is not None and cmd.get_setting_boolean("retain_order", staged)
                and core.coordinate_signature(cmd.get_coords(f"model {staged}", 1)) != expected
            )
            if sort_for_validation:
                cmd.set("retain_order", 0, staged)
            core.apply_pose(cmd, staged, row["matrix"],
                            expected_coordinates=expected, bounds=manifest["bounds"])
            if sort_for_validation:
                cmd.set("retain_order", 1, staged)
            cmd.set("state", 1, staged)
            cmd.set("all_states", 0, staged)
            core.style_structure(cmd, staged, manifest["config"])
        except Exception:
            cmd.delete(staged)
            cmd.set_view(view)
            raise

        if panel.previous_scene is None:
            panel.previous_scene = (enabled, view)
        if self.object_name is not None and self.object_name != staged:
            cmd.delete(self.object_name)
        self.object_name = staged
        cmd.disable("all")
        cmd.enable(staged)
        cmd.set_view(target_view)
        panel.status.setText(f"预览 {index + 1}: {row['id']}。保存当前朝向后会自动刷新缩略图。")

    def restore(self):
        panel = self.panel
        if panel.previous_scene is None:
            return
        enabled, view = panel.previous_scene
        cmd.delete(self.object_name)
        cmd.disable("all")
        existing = set(cmd.get_names("all"))
        for name in enabled:
            if name in existing:
                cmd.enable(name)
        cmd.set_view(view)
        panel.previous_scene, self.object_name = None, None

    def populate(self):
        panel = self.panel
        panel.samples.clear()
        directory = panel.manifest_path.parent
        size = core.preview_size(panel.rendering.width.value(), panel.rendering.height.value())
        images = core.current_images(panel.manifest, directory / "previews", *size)
        for index, row in enumerate(panel.manifest["samples"]):
            scores = "  ·  ".join(f"{key}: {value:.4g}" for key, value in row["scores"].items())
            labels = ("已固定 · " if row.get("pinned") else "")
            labels += ("差异未验证 · " if row.get("diversity_status") == "unverified"
                       or row.get("incomparable_with_previous", 0) else "")
            labels += "" if index in images else "预览待刷新 · "
            item = QtWidgets.QListWidgetItem(f"{index + 1:02d}  {labels}{row['id']}\n{scores}\n"
                                            f"分层：{' / '.join(str(value + 1) for value in row['stratum'])}")
            if index in images:
                item.setIcon(QtGui.QIcon(QtGui.QPixmap(str(directory / "previews" / row["image"]))))
            item.setToolTip(row["path"])
            panel.samples.addItem(item)


class CollectionDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        apply_style(self, "collectionsWorkspace")
        self.setWindowTitle("Structure Collections")
        self.setMinimumWidth(920)
        if sys.platform == "darwin":
            self.setAttribute(QtCore.Qt.WA_MacSmallSize)
        handle = parent.windowHandle() if parent is not None else None
        screen = handle.screen() if handle is not None else QtWidgets.QApplication.primaryScreen()
        available = screen.availableGeometry().size() - QtCore.QSize(32, 48)
        self.resize(QtCore.QSize(1200, 850).boundedTo(available))
        self.manifest_path, self.manifest, self.previous_scene, self.pending_path = None, None, None, None
        self.task, self.actions, self.scene = CollectionTask(self), CollectionActions(self), CollectionPreview(self)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.buttons = []
        self.build_content(layout)
        self.cancel_button = QtWidgets.QPushButton("取消任务")
        self.cancel_button.clicked.connect(self.task.cancel)
        self.cancel_button.setEnabled(False)
        self.status = QtWidgets.QLabel("请选择打分文件并读取列；勾选关注的指标。")
        self.status.setWordWrap(True)
        self.progress = QtWidgets.QProgressBar()
        self.progress.hide()
        layout.addWidget(self.status)
        layout.addWidget(self.progress)
        layout.addWidget(self.cancel_button)
        self.sampling.load_button.clicked.connect(lambda: self.guarded(self.sampling.load_columns))
        self.samples.itemDoubleClicked.connect(lambda item: self.guarded(lambda: self.preview(self.samples.row(item))))

    def build_content(self, layout):
        self.body = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.settings_scroll = QtWidgets.QScrollArea()
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        settings = QtWidgets.QWidget()
        settings_layout = QtWidgets.QVBoxLayout(settings)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(6)
        self.sampling, self.rendering = SamplingForm(), RenderForm()
        settings_layout.addWidget(self.sampling)
        self.add_buttons(settings_layout, (("生成 Collection", self.actions.generate),
                                           ("打开 Collection", self.open_manifest)))
        settings_layout.addWidget(self.rendering)
        self.add_buttons(settings_layout, (("应用朝向并刷新预览", lambda: self.actions.refresh(reapply=True)),
                                           ("保存当前朝向", self.actions.capture)))
        self.add_buttons(settings_layout, (("批量导出 PNG", lambda: self.actions.refresh(exporting=True)),
                                           ("重试失败 / 未完成项", lambda: self.actions.refresh(exporting=True, retry=True))))
        settings_layout.addStretch()
        self.settings_scroll.setWidget(settings)
        sample_box = QtWidgets.QGroupBox("样本预览")
        sample_layout = QtWidgets.QVBoxLayout(sample_box)
        sample_layout.setContentsMargins(10, 10, 10, 10)
        sample_layout.setSpacing(6)
        modifier = "Command" if sys.platform == "darwin" else "Ctrl"
        note = QtWidgets.QLabel(f"双击载入 PyMOL；{modifier} / Shift 多选。")
        note.setWordWrap(True)
        sample_layout.addWidget(note)
        self.samples = QtWidgets.QListWidget()
        self.samples.setIconSize(QtCore.QSize(180, 135))
        self.samples.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        sample_layout.addWidget(self.samples)
        self.add_buttons(sample_layout, (("固定 / 解除固定", lambda: self.actions.edit("pin")),
                                        ("删除", lambda: self.actions.edit("remove")),
                                        ("替换", lambda: self.actions.edit("replace"))))
        self.add_buttons(sample_layout, (("恢复原场景", self.restore_scene), ("打开展示页", self.open_gallery)))
        self.body.addWidget(self.settings_scroll)
        self.body.addWidget(sample_box)
        self.body.setChildrenCollapsible(False)
        self.body.setSizes([650, 550])
        layout.addWidget(self.body, 1)

    def add_buttons(self, layout, items):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        for label, callback in items:
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(lambda checked=False, action=callback: self.guarded(action))
            row.addWidget(button)
            self.buttons.append(button)
        layout.addLayout(row)

    def show_error(self, error):
        traceback.print_exception(type(error), error, error.__traceback__)
        self.status.setText(str(error))
        QtWidgets.QMessageBox.warning(self, "Collections", str(error))

    def guarded(self, operation):
        try:
            operation()
        except Exception as error:
            self.show_error(error)

    def set_busy(self, busy):
        self.body.setEnabled(not busy)
        for button in self.buttons:
            button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.progress.setVisible(busy)
        if busy:
            self.status.setText("准备任务…")
            self.progress.setRange(0, 0)

    def finished(self, result):
        path, export = result
        self.load_manifest(path)
        manifest = self.manifest
        succeeded = sum(row["ok"] for row in export["results"])
        action = "已取消，后台进程已退出" if export.get("cancelled") else "已完成"
        hint = " " + core.STALE_POSE if any(row.get("error") == core.STALE_POSE for row in export["results"]) else ""
        self.status.setText(f"{action}。样本 {len(manifest['samples'])} / {manifest['config']['count']}；"
                            f"无效 {len(manifest['invalid_structures'])}；"
                            f"未验证而暂不纳入 {len(manifest.get('unverified_candidates', []))}；"
                            f"图像成功 {succeeded} / {len(export['results'])}，复用 {export.get('reused', 0)}。{hint}")

    def open_manifest(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "打开 Collection", "", "Collection (*.json)")
        if path:
            self.load_manifest(path)

    def load_manifest(self, path):
        if self.task.future is not None:
            raise ValueError("请等待当前任务完成，或先取消任务。")
        manifest = core.read_json(path)
        if manifest.get("version") != 1 or "samples" not in manifest:
            raise ValueError("不是可用的 Collections manifest。")
        self.restore_scene()
        self.manifest_path, self.manifest = Path(path).resolve(), manifest
        self.sampling.restore_config(manifest["config"], self.manifest_path.parent)
        self.rendering.restore_config(manifest["config"])
        self.scene.populate()
        self.status.setToolTip(str(self.manifest_path))
        self.status.setText(f"已打开 {len(manifest['samples'])} 个样本，并恢复采样与朝向设置。")

    def recover_current(self):
        path = self.pending_path or self.manifest_path
        if path and path.exists():
            self.load_manifest(path)

    def require_manifest(self):
        if self.manifest is None:
            raise ValueError("请先生成或打开 Collection。")
        if self.task.future is not None:
            raise ValueError("请等待当前任务完成，或先取消任务。")

    def preview(self, index):
        self.scene.show_sample(index)

    def capture_view(self):
        self.guarded(self.actions.capture)

    def restore_scene(self):
        self.scene.restore()

    def export(self):
        self.guarded(lambda: self.actions.refresh(exporting=True))

    def open_gallery(self):
        self.require_manifest()
        path = self.manifest_path
        self.task.start(lambda control: core.gallery_page(path, control), self._open_gallery_page,
                        recover_on_failure=False)

    def _open_gallery_page(self, page):
        self.task.control.check()
        if not QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(page))):
            raise RuntimeError(f"无法打开展示页：{page}")
        self.status.setText("已打开当前展示页。")

    def closeEvent(self, event):
        if self.task.future is not None:
            self.task.close_pending = True
            self.task.cancel()
            event.ignore()
            return
        self.restore_scene()
        super().closeEvent(event)


def collections_panel():
    """Open the score-stratified Collections panel."""
    global _dialog
    if _dialog is None:
        from pymol.gui import get_qtwindow

        _dialog = CollectionDialog(parent=get_qtwindow())
    _dialog.show()
    _dialog.raise_()
    return _dialog


def collections_open(manifest):
    """Open a saved collection.

    Args:
        manifest: Path to 01_collection.json.
    """
    panel = collections_panel()
    panel.guarded(lambda: panel.load_manifest(manifest))


def collections_preview(index=1):
    """Preview one sample using its saved pose and the shared camera.

    Args:
        index: One-based index in the current collection.
    """
    panel = collections_panel()
    panel.guarded(lambda: panel.preview(int(index) - 1))


def collections_capture_view():
    """Save the current PyMOL view as the common camera for the collection."""
    collections_panel().capture_view()
