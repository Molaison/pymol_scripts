from pymol import cmd


def publication_style():
    """
    Set the PyMOL visualization style to a publication-friendly format.
    """
    cmd.set("ray_trace_mode", 1)  # 'on' in PyMOL is 1 in Python
    cmd.set("ray_trace_gain", 0.05)
    cmd.set("ray_trace_depth_factor", 1)
    cmd.set("ray_trace_disco_factor", 1)
    cmd.set("specular", 0)
    cmd.set("ambient", 0.8)
    cmd.set("cartoon_side_chain_helper", 1)  # 'on' in PyMOL is 1 in Python
    cmd.set("valence", 0)  # 'off' in PyMOL is 0 in Python
    cmd.set("ray_shadow", 0)  # 'off' in PyMOL is 0 in Python
    cmd.set("reflect", 0.5)
    cmd.set("stick_radius", 0.35)


cmd.extend("pbs", publication_style)
