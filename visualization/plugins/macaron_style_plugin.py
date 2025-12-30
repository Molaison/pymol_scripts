from pymol import cmd
from pymol.Qt import QtWidgets
import traceback

# 全局变量初始化
_previous_settings = {}


def apply_custom_style(selection="polymer", color_scheme="default"):
    """
    Apply custom color spectrum and rendering settings in PyMOL.
    Stores previous settings for reversal.

    Args:
        selection (str): PyMOL selection to apply coloring to (default: "polymer")
        color_scheme (str): Color scheme to use ("default", "warm", "cool")
    """
    global _previous_settings
    _previous_settings = {}

    try:
        # 定义不同的配色方案
        color_schemes = {
            "default": {
                "color1": (176, 163, 209),
                "color2": (139, 208, 213),
                "color3": (168, 224, 238),
                "color4": (197, 225, 163),
                "color5": (255, 227, 139),
                "color6": (245, 163, 125),
                "color7": (232, 141, 178),
            },
            "warm": {
                "color1": (255, 245, 157),
                "color2": (255, 223, 146),
                "color3": (255, 196, 127),
                "color4": (255, 159, 128),
                "color5": (245, 130, 148),
                "color6": (220, 122, 174),
                "color7": (188, 128, 189),
            },
            "cool": {
                "color1": (158, 202, 225),
                "color2": (107, 174, 214),
                "color3": (66, 146, 198),
                "color4": (33, 113, 181),
                "color5": (8, 81, 156),
                "color6": (8, 48, 107),
                "color7": (3, 19, 43),
            },
        }

        colors = color_schemes.get(color_scheme, color_schemes["default"])

        # 保存颜色（RGB 元组）
        _previous_settings["colors"] = {}
        for name in colors:
            try:
                _previous_settings["colors"][name] = cmd.get_color_tuple(name)
            except:
                _previous_settings["colors"][name] = None
            cmd.set_color(name, [c / 255.0 for c in colors[name]])  # PyMOL 颜色是 0-1

        # 保存设置
        settings_to_change = {
            "specular": 0,
            "reflect": 0,
            "ambient": 1,
            "depth_cue": 0,
            "ray_trace_mode": 1,
            "ray_trace_depth_factor": 1,
            "ray_trace_disco_factor": 1,
            "ray_trace_gain": 0.005,
            "ray_shadow": "off",
            "antialias": 2,
        }
        _previous_settings["settings"] = {}
        for key in settings_to_change:
            try:
                _previous_settings["settings"][key] = cmd.get_setting_text(key)
                cmd.set(key, settings_to_change[key])
            except Exception as e:
                print(f"Warning: Could not set {key}: {e}")

        # 光谱上色
        color_list = " ".join(colors.keys())
        cmd.spectrum("count", color_list, selection)

        print(f"Custom style applied to selection: {selection}")
        print(f"Color scheme: {color_scheme}")

    except Exception as e:
        print(f"Error applying custom style: {e}")
        traceback.print_exc()


def revert_custom_style():
    """
    Revert to the settings stored before apply_custom_style() was called.
    """
    global _previous_settings
    if not _previous_settings:
        print("No previous settings stored. Cannot revert.")
        return

    try:
        # 恢复颜色
        if "colors" in _previous_settings:
            for name, rgb in _previous_settings["colors"].items():
                if rgb is not None:
                    cmd.set_color(name, rgb)

        # 恢复设置
        if "settings" in _previous_settings:
            for key, value in _previous_settings["settings"].items():
                try:
                    cmd.set(key, value)
                except Exception as e:
                    print(f"Warning: Could not restore setting {key}: {e}")

        _previous_settings = {}
        print("Settings reverted successfully.")

    except Exception as e:
        print(f"Error reverting settings: {e}")
        traceback.print_exc()


def render_high_quality():
    """
    Apply high-quality rendering settings and ray trace the current view.
    """
    try:
        cmd.set("ray_trace_mode", 1)
        cmd.set("antialias", 2)
        cmd.set("ray_shadows", "off")
        cmd.ray()
        print("High-quality rendering applied.")
    except Exception as e:
        print(f"Error during rendering: {e}")


def quick_style_preset(preset="publication"):
    """
    Apply quick style presets for different purposes.

    Args:
        preset (str): Style preset ("publication", "presentation", "analysis")
    """
    presets = {
        "publication": {
            "bg_color": "white",
            "cartoon_color": "gray70",
            "stick_radius": 0.2,
            "sphere_scale": 0.25,
        },
        "presentation": {
            "bg_color": "black",
            "cartoon_color": "cyan",
            "stick_radius": 0.3,
            "sphere_scale": 0.3,
        },
        "analysis": {
            "bg_color": "white",
            "cartoon_color": "salmon",
            "stick_radius": 0.15,
            "sphere_scale": 0.2,
        },
    }

    if preset in presets:
        settings = presets[preset]
        try:
            cmd.bg_color(settings["bg_color"])
            cmd.color(settings["cartoon_color"], "polymer")
            cmd.set("stick_radius", settings["stick_radius"])
            cmd.set("sphere_scale", settings["sphere_scale"])
            print(f"Applied {preset} preset.")
        except Exception as e:
            print(f"Error applying preset {preset}: {e}")
    else:
        print(f"Unknown preset: {preset}")


class MacaronStyleDialog(QtWidgets.QDialog):
    """GUI dialog for the Macaron style plugin."""

    def __init__(self, parent=None):
        super(MacaronStyleDialog, self).__init__(parent)
        self.setWindowTitle("Macaron Style Plugin")
        self.setFixedSize(300, 200)
        self.init_ui()

    def init_ui(self):
        layout = QtWidgets.QVBoxLayout()

        # Color scheme selection
        layout.addWidget(QtWidgets.QLabel("Color Scheme:"))
        self.color_combo = QtWidgets.QComboBox()
        self.color_combo.addItems(["default", "warm", "cool"])
        layout.addWidget(self.color_combo)

        # Selection input
        layout.addWidget(QtWidgets.QLabel("Selection:"))
        self.selection_edit = QtWidgets.QLineEdit("polymer")
        layout.addWidget(self.selection_edit)

        # Buttons
        button_layout = QtWidgets.QHBoxLayout()

        apply_btn = QtWidgets.QPushButton("Apply Style")
        apply_btn.clicked.connect(self.apply_style)
        button_layout.addWidget(apply_btn)

        revert_btn = QtWidgets.QPushButton("Revert")
        revert_btn.clicked.connect(revert_custom_style)
        button_layout.addWidget(revert_btn)

        render_btn = QtWidgets.QPushButton("Render")
        render_btn.clicked.connect(render_high_quality)
        button_layout.addWidget(render_btn)

        layout.addLayout(button_layout)

        # Preset buttons
        preset_layout = QtWidgets.QHBoxLayout()
        for preset in ["publication", "presentation", "analysis"]:
            btn = QtWidgets.QPushButton(preset.title())
            btn.clicked.connect(lambda checked, p=preset: quick_style_preset(p))
            preset_layout.addWidget(btn)

        layout.addLayout(preset_layout)
        self.setLayout(layout)

    def apply_style(self):
        selection = self.selection_edit.text() or "polymer"
        color_scheme = self.color_combo.currentText()
        apply_custom_style(selection, color_scheme)


def show_macaron_dialog():
    """Show the Macaron style dialog."""
    dialog = MacaronStyleDialog()
    dialog.show()
    return dialog


# 注册插件命令
cmd.extend("apply_custom_style", apply_custom_style)
cmd.extend("revert_custom_style", revert_custom_style)
cmd.extend("render_high_quality", render_high_quality)
cmd.extend("quick_style_preset", quick_style_preset)
cmd.extend("show_macaron_dialog", show_macaron_dialog)


# 插件菜单注册
def __init_plugin__(app=None):
    """Initialize the plugin in PyMOL."""
    from pymol.plugins import addmenuitemqt

    addmenuitemqt("Macaron Style", show_macaron_dialog)


# 插件信息
__author__ = "GitHub Copilot"
__version__ = "1.0"
__description__ = "Custom visualization styles for PyMOL"
