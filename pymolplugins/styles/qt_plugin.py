"""Qt compatibility UI backed entirely by the canonical style registry.

The dialog owns no PyMOL style state.  Apply pushes a registry snapshot,
Revert pops that snapshot stack, and Render is the only action which starts a
ray trace.
"""

from pymol import cmd
from pymol.Qt import QtWidgets

from pymolplugins.styles import registry as _registry

_MACARON_PRESETS = {
    "default": "macaron_default",
    "warm": "macaron_warm",
    "cool": "macaron_cool",
}

# Historical button labels are retained, but each now resolves to a reviewed
# canonical preset instead of carrying a second dictionary of PyMOL settings.
_QUICK_PRESETS = {
    "publication": "publication_light",
    "presentation": "macaron_default",
    "analysis": "minimal_light",
}


def apply_custom_style(selection="polymer", color_scheme="default"):
    """Apply a canonical Macaron preset without rendering."""

    scheme = str(color_scheme or "default").strip().lower()
    try:
        preset = _MACARON_PRESETS[scheme]
    except KeyError:
        raise ValueError("color_scheme must be default, warm, or cool")
    return _registry.pp_style_apply(preset, selection, snapshot=1, render=0)


def revert_custom_style(snapshot_id=""):
    """Revert the latest canonical style snapshot."""

    return _registry.pp_style_revert(snapshot_id)


def render_high_quality(width=1600, height=1200, filename=""):
    """Explicitly ray trace the current scene through the registry."""

    return _registry.pp_style_render(width, height, filename)


def quick_style_preset(
    preset="publication", selection="all", snapshot=1, render=0
):
    """Apply a historical quick label or any canonical preset name."""

    requested = str(preset or "publication").strip().lower()
    canonical = _QUICK_PRESETS.get(requested, requested)
    return _registry.pp_style_apply(
        canonical, selection, snapshot=snapshot, render=render
    )


class MacaronStyleDialog(QtWidgets.QDialog):
    """Small Qt front end for canonical Macaron style commands."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Macaron Style Plugin")
        self.setMinimumWidth(320)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        self.color_combo = QtWidgets.QComboBox()
        self.color_combo.addItems(["default", "warm", "cool"])
        form.addRow("Color scheme:", self.color_combo)

        self.selection_edit = QtWidgets.QLineEdit("polymer")
        form.addRow("Selection:", self.selection_edit)
        layout.addLayout(form)

        actions = QtWidgets.QHBoxLayout()
        apply_button = QtWidgets.QPushButton("Apply")
        apply_button.clicked.connect(lambda checked=False: self.apply_style())
        actions.addWidget(apply_button)

        revert_button = QtWidgets.QPushButton("Revert")
        revert_button.clicked.connect(
            lambda checked=False: revert_custom_style()
        )
        actions.addWidget(revert_button)

        render_button = QtWidgets.QPushButton("Render")
        render_button.clicked.connect(
            lambda checked=False: render_high_quality()
        )
        actions.addWidget(render_button)
        layout.addLayout(actions)

        quick = QtWidgets.QHBoxLayout()
        for label in ("publication", "presentation", "analysis"):
            button = QtWidgets.QPushButton(label.title())
            button.clicked.connect(
                lambda checked=False, value=label: self.apply_quick_style(value)
            )
            quick.addWidget(button)
        layout.addLayout(quick)

    def apply_style(self):
        selection = self.selection_edit.text().strip() or "polymer"
        return apply_custom_style(selection, self.color_combo.currentText())

    def apply_quick_style(self, preset):
        selection = self.selection_edit.text().strip() or "all"
        return quick_style_preset(preset, selection)


_DIALOG = None


def show_macaron_dialog():
    """Show and retain the Macaron style dialog."""

    global _DIALOG
    if _DIALOG is None:
        _DIALOG = MacaronStyleDialog()
    _DIALOG.show()
    _DIALOG.raise_()
    _DIALOG.activateWindow()
    return _DIALOG


cmd.extend("apply_custom_style", apply_custom_style)
cmd.extend("revert_custom_style", revert_custom_style)
cmd.extend("render_high_quality", render_high_quality)
cmd.extend("quick_style_preset", quick_style_preset)
cmd.extend("show_macaron_dialog", show_macaron_dialog)


def __init_plugin__(app=None):
    """Register the compatibility dialog in PyMOL's plugin menu."""

    from pymol.plugins import addmenuitemqt

    addmenuitemqt("Macaron Style", show_macaron_dialog)


__author__ = "PymolPlugins contributors"
__version__ = "2.0"
__description__ = "Canonical style registry Qt adapter"
