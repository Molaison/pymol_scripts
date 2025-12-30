from pymol import cmd


MACARON_COLORS = {
    "color1": [176, 163, 209],  # Purple
    "color2": [139, 208, 213],  # Cyan
    "color3": [168, 224, 238],  # Light blue
    "color4": [197, 225, 163],  # Light green
    "color5": [255, 227, 139],  # Yellow
    "color6": [245, 163, 125],  # Orange
    "color7": [232, 141, 178],  # Pink
}


def apply_macaron_style(selection="polymer", ray=1):
    """
    Apply a "macaron" (pastel) color palette and ray-trace-friendly settings.

    Args:
        selection (str): Target selection for coloring (default: "polymer").
        ray (int|bool): Whether to run ray tracing after applying (default: 1).
    """
    for name, rgb in MACARON_COLORS.items():
        cmd.set_color(name, rgb)

    palette = " ".join(MACARON_COLORS.keys())
    cmd.spectrum("count", palette, selection)

    cmd.set("specular", 0)
    cmd.set("reflect", 0)
    cmd.set("ambient", 1)
    cmd.set("depth_cue", 0)
    cmd.set("ray_trace_mode", 1)
    cmd.set("ray_trace_depth_factor", 1)
    cmd.set("ray_trace_disco_factor", 1)
    cmd.set("ray_trace_gain", 0.005)
    cmd.set("ray_shadow", 0)
    cmd.set("antialias", 2)

    if int(ray):
        cmd.ray()


cmd.extend("macaron_style", apply_macaron_style)

