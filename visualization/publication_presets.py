"""
PyMOL Publication-Quality Visualization Presets

A collection of visualization styles optimized for publication-quality figures.
Includes color schemes, rendering settings, and style presets commonly used
in structural biology publications.
"""

from pymol import cmd


# Color definitions for different themes
MACARON_COLORS = {
    'color1': [176, 163, 209],  # Purple
    'color2': [139, 208, 213],  # Cyan
    'color3': [168, 224, 238],  # Light blue
    'color4': [197, 225, 163],  # Light green
    'color5': [255, 227, 139],  # Yellow
    'color6': [245, 163, 125],  # Orange
    'color7': [232, 141, 178],  # Pink
}

PROFESSIONAL_COLORS = {
    'blue_protein': [60, 120, 180],
    'red_protein': [180, 60, 60],
    'green_protein': [60, 180, 120],
    'gray_surface': [160, 160, 160],
    'gold_ligand': [255, 215, 0],
}


def _setup_colors():
    """Register custom colors in PyMOL."""
    # Macaron palette
    for name, rgb in MACARON_COLORS.items():
        cmd.set_color(name, rgb)
    
    # Professional palette
    for name, rgb in PROFESSIONAL_COLORS.items():
        cmd.set_color(name, rgb)


def publication_style():
    """
    Standard publication-quality rendering settings.
    Optimized for high-quality figures with clean appearance.
    """
    cmd.set("ray_trace_mode", 1)
    cmd.set("ray_trace_gain", 0.05)
    cmd.set("ray_trace_depth_factor", 1)
    cmd.set("ray_trace_disco_factor", 1)
    cmd.set("specular", 0)
    cmd.set("ambient", 0.8)
    cmd.set("cartoon_side_chain_helper", 1)
    cmd.set("valence", 0)
    cmd.set("ray_shadow", 0)
    cmd.set("reflect", 0.5)
    cmd.set("stick_radius", 0.35)
    cmd.set("antialias", 2)
    print("Applied publication style settings")


def macaron_style():
    """
    Instagram-style macaron color scheme with soft rendering.
    Perfect for presentations and social media.
    """
    _setup_colors()
    
    # Apply gradient coloring
    cmd.spectrum("count", "color1 color2 color3 color4 color5 color6 color7", "polymer")
    
    # Soft rendering settings
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
    print("Applied macaron color scheme")


def cartoon_style():
    """
    Cartoon-like rendering with bold outlines and flat colors.
    Good for educational materials and simplified representations.
    """
    cmd.set("specular", 0)
    cmd.set("ambient", 0.8)
    cmd.set("ray_trace_mode", 1)
    cmd.set("ray_trace_depth_factor", 1)
    cmd.set("ray_trace_disco_factor", 0.05)
    cmd.set("antialias", 2)
    cmd.set("line_smooth", 1)
    cmd.set("ray_shadow", 0)
    cmd.set("valence", 0)
    print("Applied cartoon style settings")


def watercolor_style():
    """
    Watercolor (shuimo) style with high transparency and soft edges.
    Creates artistic, painterly visualizations.
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
    print("Applied watercolor style settings")


def minimal_style():
    """
    Clean, minimal style for professional presentations.
    Emphasizes structure with subtle shading.
    """
    cmd.set("specular", 0.2)
    cmd.set("ambient", 0.6)
    cmd.set("reflect", 0.1)
    cmd.set("ray_trace_mode", 1)
    cmd.set("ray_trace_gain", 0.02)
    cmd.set("ray_shadow", 0)
    cmd.set("cartoon_side_chain_helper", 1)
    cmd.set("stick_radius", 0.3)
    cmd.set("antialias", 2)
    cmd.bg_color("white")
    print("Applied minimal style settings")


def apply_protein_colors(selection="all"):
    """
    Apply professional color scheme to proteins.
    
    Args:
        selection (str): PyMOL selection to color
    """
    _setup_colors()
    cmd.color("blue_protein", f"({selection}) and ss h")  # Helices
    cmd.color("red_protein", f"({selection}) and ss s")   # Sheets
    cmd.color("green_protein", f"({selection}) and ss l") # Loops
    print(f"Applied protein colors to {selection}")


def setup_ligand_view(ligand_selection="resn LIG"):
    """
    Set up optimal view for protein-ligand complexes.
    
    Args:
        ligand_selection (str): Selection for ligand
    """
    _setup_colors()
    
    # Style protein
    cmd.show("cartoon", "polymer")
    cmd.color("gray80", "polymer")
    
    # Style ligand
    cmd.show("sticks", ligand_selection)
    cmd.color("gold_ligand", ligand_selection)
    cmd.set("stick_radius", 0.4, ligand_selection)
    
    # Show binding site residues
    binding_site = f"byres ({ligand_selection} around 4)"
    cmd.show("sticks", f"({binding_site}) and sidechain")
    cmd.color("blue_protein", binding_site)
    
    cmd.zoom(ligand_selection, 8)
    print(f"Set up ligand view for {ligand_selection}")


def render_publication(width=1200, height=1200, dpi=300):
    """
    Render high-quality image with publication settings.
    
    Args:
        width (int): Image width in pixels
        height (int): Image height in pixels  
        dpi (int): Dots per inch for print quality
    """
    publication_style()
    cmd.ray(width, height)
    print(f"Rendered {width}x{height} image at {dpi} DPI")


def quick_preset(style_name="publication"):
    """
    Apply a preset style quickly.
    
    Args:
        style_name (str): Style to apply (publication, macaron, cartoon, watercolor, minimal)
    """
    styles = {
        "publication": publication_style,
        "macaron": macaron_style,
        "cartoon": cartoon_style,
        "watercolor": watercolor_style,
        "minimal": minimal_style,
    }
    
    if style_name in styles:
        styles[style_name]()
    else:
        print(f"Unknown style: {style_name}")
        print(f"Available styles: {list(styles.keys())}")


# Register PyMOL commands
cmd.extend("publication_style", publication_style)
cmd.extend("macaron_style", macaron_style)
cmd.extend("cartoon_style", cartoon_style)
cmd.extend("watercolor_style", watercolor_style)
cmd.extend("minimal_style", minimal_style)
cmd.extend("apply_protein_colors", apply_protein_colors)
cmd.extend("setup_ligand_view", setup_ligand_view)
cmd.extend("render_publication", render_publication)
cmd.extend("quick_preset", quick_preset)

# Convenient aliases
cmd.extend("pbs", publication_style)  # Publication style
cmd.extend("macaron", macaron_style)
cmd.extend("watercolor", watercolor_style)
cmd.extend("minimal", minimal_style)
cmd.extend("preset", quick_preset)

# Initialize colors
_setup_colors()

print("Publication Presets module loaded successfully!")
print("Available styles: publication_style, macaron_style, cartoon_style, watercolor_style, minimal_style")
print("Quick access: pbs, macaron, watercolor, minimal, preset <style_name>")