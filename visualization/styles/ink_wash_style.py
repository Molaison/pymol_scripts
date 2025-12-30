from pymol import cmd


def apply_ink_wash_style(ray_size=2000):
    """
    Apply an "ink wash" black/white high-contrast ray-trace preset.

    Args:
        ray_size (int): Output size for `ray` (square, default: 2000).
    """
    cmd.set("opaque_background", 1)
    cmd.bg_color("white")

    cmd.set("transparency", 0.8)
    cmd.set("specular", 0)
    cmd.set("ambient", 1)
    cmd.set("ray_trace_mode", 1)
    cmd.set("ray_trace_gain", 1)
    cmd.set("cartoon_highlight_color", "gray70")
    cmd.set("ray_trace_depth_factor", 1)
    cmd.set("ray_trace_disco_factor", 0.05)

    if ray_size and int(ray_size) > 0:
        cmd.ray(int(ray_size), int(ray_size))


cmd.extend("ink_wash_style", apply_ink_wash_style)

