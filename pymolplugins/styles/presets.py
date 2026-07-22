"""Declarative style preset definitions and validation.

This module deliberately has no PyMOL dependency.  Keeping the schema and its
validation pure makes preset mistakes detectable in WSL and in normal Python
CI before a script is loaded by the Windows PyMOL process.
"""

from dataclasses import dataclass, field
import re


SCHEMA_VERSION = 1
VALID_CATEGORIES = frozenset(("lighting", "palette", "scene", "render"))

# PyMOL has many settings, but presets should only use names which have been
# deliberately reviewed.  Extend this allow-list when adding a preset instead
# of silently accepting a typo which PyMOL may otherwise report only at load
# time.
KNOWN_SETTINGS = frozenset(
    (
        "ambient",
        "antialias",
        "bg_rgb",
        "cartoon_highlight_color",
        "cartoon_side_chain_helper",
        "depth_cue",
        "line_smooth",
        "opaque_background",
        "power",
        "ray_shadow",
        "ray_trace_color",
        "ray_trace_depth_factor",
        "ray_trace_disco_factor",
        "ray_trace_gain",
        "ray_trace_mode",
        "reflect",
        "specular",
        "sphere_scale",
        "stick_radius",
        "transparency",
        "valence",
    )
)

VALID_REPRESENTATIONS = frozenset(
    (
        "cartoon",
        "dots",
        "ellipsoids",
        "everything",
        "labels",
        "lines",
        "mesh",
        "nb_spheres",
        "nonbonded",
        "ribbon",
        "spheres",
        "sticks",
        "surface",
    )
)

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class StylePreset:
    """A versioned, declarative PyMOL style preset."""

    name: str
    description: str
    category: str
    aliases: tuple = ()
    colors: dict = field(default_factory=dict)
    global_settings: dict = field(default_factory=dict)
    selection_settings: dict = field(default_factory=dict)
    color_operations: tuple = ()
    representation_operations: tuple = ()
    render_defaults: dict = field(default_factory=dict)
    compatibility: tuple = ("PyMOL 2.x",)
    reversible_fields: tuple = (
        "global_settings",
        "selection_settings",
        "background",
        "atom_colors",
        "representations",
    )
    schema_version: int = SCHEMA_VERSION

    def as_dict(self):
        """Return a JSON-friendly description of this preset."""
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "aliases": list(self.aliases),
            "description": self.description,
            "category": self.category,
            "colors": {name: list(rgb) for name, rgb in self.colors.items()},
            "global_settings": dict(self.global_settings),
            "selection_settings": dict(self.selection_settings),
            "color_operations": [dict(item) for item in self.color_operations],
            "representation_operations": [
                dict(item) for item in self.representation_operations
            ],
            "render_defaults": dict(self.render_defaults),
            "compatibility": list(self.compatibility),
            "reversible_fields": list(self.reversible_fields),
        }


def _rgb255(red, green, blue):
    """Convert an authoring-time 8-bit triplet to the canonical 0..1 form."""
    return tuple(channel / 255.0 for channel in (red, green, blue))


MACARON_DEFAULT = {
    "pp_macaron_1": _rgb255(176, 163, 209),
    "pp_macaron_2": _rgb255(139, 208, 213),
    "pp_macaron_3": _rgb255(168, 224, 238),
    "pp_macaron_4": _rgb255(197, 225, 163),
    "pp_macaron_5": _rgb255(255, 227, 139),
    "pp_macaron_6": _rgb255(245, 163, 125),
    "pp_macaron_7": _rgb255(232, 141, 178),
}

MACARON_WARM = {
    "pp_macaron_warm_1": _rgb255(255, 245, 157),
    "pp_macaron_warm_2": _rgb255(255, 223, 146),
    "pp_macaron_warm_3": _rgb255(255, 196, 127),
    "pp_macaron_warm_4": _rgb255(255, 159, 128),
    "pp_macaron_warm_5": _rgb255(245, 130, 148),
    "pp_macaron_warm_6": _rgb255(220, 122, 174),
    "pp_macaron_warm_7": _rgb255(188, 128, 189),
}

MACARON_COOL = {
    "pp_macaron_cool_1": _rgb255(158, 202, 225),
    "pp_macaron_cool_2": _rgb255(107, 174, 214),
    "pp_macaron_cool_3": _rgb255(66, 146, 198),
    "pp_macaron_cool_4": _rgb255(33, 113, 181),
    "pp_macaron_cool_5": _rgb255(8, 81, 156),
    "pp_macaron_cool_6": _rgb255(8, 48, 107),
    "pp_macaron_cool_7": _rgb255(3, 19, 43),
}

PROFESSIONAL_COLORS = {
    "pp_professional_blue": _rgb255(60, 120, 180),
    "pp_professional_red": _rgb255(180, 60, 60),
    "pp_professional_green": _rgb255(60, 180, 120),
}

RFD2_SAM_COLORS = {
    "pp_rfd2_nitrogen": _rgb255(2, 118, 253),
    "pp_rfd2_gray": _rgb255(51, 51, 51),
    "pp_rfd2_black": _rgb255(34, 34, 34),
    "pp_rfd2_yellow": _rgb255(250, 199, 44),
    "pp_rfd2_teal": _rgb255(41, 176, 193),
    "pp_rfd2_green": _rgb255(170, 195, 47),
    "pp_rfd2_pink": _rgb255(236, 114, 164),
    "pp_rfd2_blue": _rgb255(68, 153, 231),
    "pp_rfd2_light_gray": _rgb255(220, 220, 220),
    "pp_rfd2_red": _rgb255(228, 74, 62),
    "pp_rfd2_light_green": _rgb255(101, 179, 124),
}

RFD2_PAPER_COLORS = {
    "pp_rfd2_paper_teal": (0.310, 0.725, 0.686),
    "pp_rfd2_paper_navajo": _rgb255(255, 224, 172),
    "pp_rfd2_paper_melon": _rgb255(255, 198, 178),
    "pp_rfd2_paper_pink": _rgb255(255, 172, 183),
    "pp_rfd2_paper_purple": _rgb255(213, 154, 181),
    "pp_rfd2_paper_lightblue": _rgb255(149, 150, 198),
    "pp_rfd2_paper_blue": _rgb255(102, 134, 197),
    "pp_rfd2_paper_darkblue": _rgb255(75, 95, 170),
}


SOFT_MACARON_SETTINGS = {
    "specular": 0,
    "reflect": 0,
    "ambient": 1,
    "depth_cue": 0,
    "ray_trace_mode": 1,
    "ray_trace_depth_factor": 1,
    "ray_trace_disco_factor": 1,
    "ray_trace_gain": 0.005,
    "ray_shadow": 0,
    "antialias": 2,
}


def _spectrum(colors):
    return (
        {
            "action": "spectrum",
            "expression": "count",
            "palette": tuple(colors),
            "selection": "{selection}",
            "byres": 0,
        },
    )


PRESETS = (
    StylePreset(
        name="publication_light",
        aliases=("publication", "pbs"),
        description="Clean, shadow-free lighting for publication figures.",
        category="lighting",
        global_settings={
            "ray_trace_mode": 1,
            "ray_trace_gain": 0.05,
            "ray_trace_depth_factor": 1,
            "ray_trace_disco_factor": 1,
            "specular": 0,
            "ambient": 0.8,
            "cartoon_side_chain_helper": 1,
            "valence": 0,
            "ray_shadow": 0,
            "reflect": 0.5,
            "stick_radius": 0.35,
            "antialias": 2,
        },
        render_defaults={"width": 1600, "height": 1200},
    ),
    StylePreset(
        name="minimal_light",
        aliases=("minimal",),
        description="Subtle lighting on a white background.",
        category="lighting",
        global_settings={
            "specular": 0.2,
            "ambient": 0.6,
            "reflect": 0.1,
            "ray_trace_mode": 1,
            "ray_trace_gain": 0.02,
            "ray_shadow": 0,
            "cartoon_side_chain_helper": 1,
            "stick_radius": 0.3,
            "antialias": 2,
            "bg_rgb": (1.0, 1.0, 1.0),
        },
        render_defaults={"width": 1600, "height": 1200},
    ),
    StylePreset(
        name="macaron_default",
        aliases=("macaron",),
        description="Pastel macaron spectrum with soft, shadow-free lighting.",
        category="scene",
        colors=MACARON_DEFAULT,
        global_settings=SOFT_MACARON_SETTINGS,
        color_operations=_spectrum(MACARON_DEFAULT),
        render_defaults={"width": 1600, "height": 1200},
    ),
    StylePreset(
        name="macaron_warm",
        aliases=("warm_macaron",),
        description="Warm macaron spectrum with soft, shadow-free lighting.",
        category="scene",
        colors=MACARON_WARM,
        global_settings=SOFT_MACARON_SETTINGS,
        color_operations=_spectrum(MACARON_WARM),
        render_defaults={"width": 1600, "height": 1200},
    ),
    StylePreset(
        name="macaron_cool",
        aliases=("cool_macaron",),
        description="Cool macaron spectrum with soft, shadow-free lighting.",
        category="scene",
        colors=MACARON_COOL,
        global_settings=SOFT_MACARON_SETTINGS,
        color_operations=_spectrum(MACARON_COOL),
        render_defaults={"width": 1600, "height": 1200},
    ),
    StylePreset(
        name="ink_wash",
        aliases=("watercolor", "watercolour"),
        description="High-key ink-wash lighting on an opaque white background.",
        category="scene",
        global_settings={
            "opaque_background": 1,
            "bg_rgb": (1.0, 1.0, 1.0),
            "transparency": 0.8,
            "specular": 0,
            "ambient": 1,
            "ray_trace_mode": 1,
            "ray_trace_gain": 1,
            "cartoon_highlight_color": "gray70",
            "ray_trace_depth_factor": 1,
            "ray_trace_disco_factor": 0.05,
        },
        render_defaults={"width": 2000, "height": 2000},
    ),
    StylePreset(
        name="high_contrast_outline",
        aliases=("cartoon", "high_contrast"),
        description="Flat, high-contrast cartoon lighting with smooth outlines.",
        category="lighting",
        global_settings={
            "specular": 0,
            "ambient": 0.8,
            "ray_trace_mode": 1,
            "ray_trace_depth_factor": 1,
            "ray_trace_disco_factor": 0.05,
            "antialias": 2,
            "line_smooth": 1,
            "ray_shadow": 0,
            "valence": 0,
        },
        render_defaults={"width": 2000, "height": 2000},
    ),
    StylePreset(
        name="rfd2_lighting",
        aliases=("rfd2", "rf_diffusion_lighting"),
        description="RFdiffusion2 inspection lighting.",
        category="lighting",
        global_settings={
            "specular": 0,
            "ray_shadow": 0,
            "valence": 0,
            "antialias": 2,
            "ray_trace_mode": 1,
            "ray_trace_disco_factor": 1,
            "ray_trace_gain": 0.1,
            "power": 0.2,
            "ambient": 0.4,
            "ray_trace_color": "gray30",
        },
    ),
    StylePreset(
        name="rfd2_paper_palette",
        aliases=("paper_rainbow",),
        description="RFdiffusion2 paper-inspired seven-color spectrum.",
        category="palette",
        colors=RFD2_PAPER_COLORS,
        color_operations=_spectrum(
            (
                "pp_rfd2_paper_navajo",
                "pp_rfd2_paper_melon",
                "pp_rfd2_paper_pink",
                "pp_rfd2_paper_purple",
                "pp_rfd2_paper_lightblue",
                "pp_rfd2_paper_blue",
                "pp_rfd2_paper_darkblue",
            )
        ),
    ),
    StylePreset(
        name="rfd2_sam_palette",
        aliases=("rfd2_rainbow",),
        description="RFdiffusion2 Sam palette spectrum.",
        category="palette",
        colors=RFD2_SAM_COLORS,
        color_operations=_spectrum(
            (
                "pp_rfd2_blue",
                "pp_rfd2_teal",
                "pp_rfd2_green",
                "pp_rfd2_yellow",
                "pp_rfd2_red",
            )
        ),
    ),
    StylePreset(
        name="professional_secondary_structure",
        aliases=("protein_colors",),
        description="Blue helices, red sheets, and green loops.",
        category="palette",
        colors=PROFESSIONAL_COLORS,
        color_operations=(
            {
                "action": "color",
                "color": "pp_professional_blue",
                "selection": "({selection}) and ss h",
            },
            {
                "action": "color",
                "color": "pp_professional_red",
                "selection": "({selection}) and ss s",
            },
            {
                "action": "color",
                "color": "pp_professional_green",
                "selection": "({selection}) and ss l",
            },
        ),
    ),
)


def _validate_name(value, label):
    if not isinstance(value, str) or not _NAME_RE.match(value):
        raise ValueError("Invalid %s %r; use lowercase snake_case" % (label, value))


def _validate_rgb(name, rgb):
    if not name.startswith("pp_"):
        raise ValueError("Custom color %r must use the pp_ prefix" % name)
    if not isinstance(rgb, (tuple, list)) or len(rgb) != 3:
        raise ValueError("Color %r must contain exactly three channels" % name)
    if any(not isinstance(value, (int, float)) or value < 0 or value > 1 for value in rgb):
        raise ValueError("Color %r channels must all be in the 0..1 range" % name)


def _validate_settings(preset, known_settings):
    for field_name in ("global_settings", "selection_settings"):
        settings = getattr(preset, field_name)
        if not isinstance(settings, dict):
            raise ValueError("%s.%s must be a dict" % (preset.name, field_name))
        unknown = set(settings).difference(known_settings)
        if unknown:
            raise ValueError(
                "%s uses unknown PyMOL setting(s): %s"
                % (preset.name, ", ".join(sorted(unknown)))
            )
    if "bg_rgb" in preset.global_settings:
        _validate_rgb("pp_background", preset.global_settings["bg_rgb"])


def _validate_color_operations(preset):
    for operation in preset.color_operations:
        action = operation.get("action")
        if action not in ("color", "spectrum"):
            raise ValueError("%s has invalid color action %r" % (preset.name, action))
        if "selection" not in operation:
            raise ValueError("%s color operation lacks selection" % preset.name)
        if action == "color" and operation.get("color") not in preset.colors:
            raise ValueError("%s color operation references an undeclared color" % preset.name)
        if action == "spectrum":
            palette = operation.get("palette", ())
            if not palette or any(name not in preset.colors for name in palette):
                raise ValueError("%s spectrum references an undeclared color" % preset.name)


def _validate_representation_operations(preset):
    for operation in preset.representation_operations:
        if operation.get("action") not in ("show", "hide"):
            raise ValueError("%s has an invalid representation action" % preset.name)
        if operation.get("representation") not in VALID_REPRESENTATIONS:
            raise ValueError("%s uses an unknown representation" % preset.name)
        if "selection" not in operation:
            raise ValueError("%s representation operation lacks selection" % preset.name)


def _validate_render_defaults(preset):
    allowed = {"width", "height", "filename"}
    unknown = set(preset.render_defaults).difference(allowed)
    if unknown:
        raise ValueError("%s has unknown render default(s): %s" % (preset.name, unknown))
    for key in ("width", "height"):
        if key in preset.render_defaults and int(preset.render_defaults[key]) <= 0:
            raise ValueError("%s render %s must be positive" % (preset.name, key))


def validate_presets(presets, known_settings=KNOWN_SETTINGS):
    """Validate presets and return canonical and alias lookup dictionaries."""
    canonical = {}
    lookup = {}
    for preset in presets:
        if preset.schema_version != SCHEMA_VERSION:
            raise ValueError("Unsupported schema version for %s" % preset.name)
        _validate_name(preset.name, "preset name")
        if preset.category not in VALID_CATEGORIES:
            raise ValueError("Invalid category %r for %s" % (preset.category, preset.name))
        if preset.name in lookup:
            raise ValueError("Duplicate canonical name or alias %r" % preset.name)
        canonical[preset.name] = preset
        lookup[preset.name] = preset
        for alias in preset.aliases:
            _validate_name(alias, "alias")
            if alias in lookup:
                raise ValueError("Duplicate canonical name or alias %r" % alias)
            lookup[alias] = preset
        for name, rgb in preset.colors.items():
            _validate_name(name, "color name")
            _validate_rgb(name, rgb)
        _validate_settings(preset, known_settings)
        _validate_color_operations(preset)
        _validate_representation_operations(preset)
        _validate_render_defaults(preset)
    return canonical, lookup


# Fail fast even when this pure module is imported outside PyMOL.
CANONICAL_PRESETS, PRESET_LOOKUP = validate_presets(PRESETS)
