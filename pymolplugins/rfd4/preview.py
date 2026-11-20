"""Read-only display of committed Studio conditions in PyMOL."""

from dataclasses import dataclass
from math import isfinite, pi, sin
from uuid import uuid4

from pymol import cmd
from pymol.cgo import COLOR, CYLINDER, SPHERE

from pymolplugins.core.pymol_display import display_updates


_COLORS = {
    "HELIX": (0.62, 0.36, 0.82),
    "PAIR": (0.61, 0.36, 0.78),
    "PAIRED": (0.61, 0.36, 0.78),
    "UNPAIRED": (0.07, 0.60, 0.65),
    "SHEET": (0.87, 0.54, 0.17),
    "STRAND": (0.87, 0.54, 0.17),
    "LOOP": (0.07, 0.60, 0.65),
    "COIL": (0.07, 0.60, 0.65),
    "sequence": (0.26, 0.55, 0.76),
    "coordinate": (0.27, 0.56, 0.37),
    "hotspot": (0.89, 0.33, 0.22),
    "hbond": (0.04, 0.55, 0.63),
    "pistack": (0.52, 0.43, 0.74),
    "pication": (0.52, 0.43, 0.74),
    "saltbridge": (0.82, 0.28, 0.44),
    "hydrophobic": (0.81, 0.54, 0.22),
    "metal_coord": (0.34, 0.60, 0.37),
    "segment": (0.36, 0.62, 0.76),
    "restraints": (0.82, 0.28, 0.44),
}
_FALLBACK = (0.23, 0.47, 0.68)
# Draw exact atom targets over broad membership when both affect one atom.
_ATOM_PRIORITY = {"coordinate": 1, "restraints": 1, "hotspot": 2}
_SECONDARY_STRUCTURE = {"secondary_structure", "na_ss_state"}
_OVERVIEW_LAYERS = _SECONDARY_STRUCTURE | {"hotspot", "segment", "restraints"}
_SPHERE_SCALES = {"coordinate_group": 0.18, "coordinate": 0.24, "hotspot": 0.40}
_STATES = {"HELIX": "螺旋", "SHEET": "折叠片", "STRAND": "链状折叠", "LOOP": "环",
           "COIL": "无规卷曲", "PAIR": "配对", "PAIRED": "配对", "UNPAIRED": "未配对"}
_UNITS = {"atoms": " 原子", "residues": " 残基", "chains": " 链", "groups": " 组", "pairs": " 对"}


@dataclass(frozen=True)
class Layer:
    key: str
    label: str
    value: str
    indices: tuple = ()
    color: tuple = _FALLBACK
    segment: tuple = ()
    position: tuple = ()
    pair: tuple = ()
    atom_level: bool = False


def _indices(item, size):
    return tuple(dict.fromkeys(i for i in item.get("atom_indices", ())
                               if type(i) is int and 0 <= i < size))


def _value(value):
    if isinstance(value, bool):
        return "已启用" if value else "已取消"
    if isinstance(value, (tuple, list)):
        return ", ".join(map(str, value))
    return str(value)


def _vector(value):
    if (isinstance(value, (list, tuple)) and len(value) == 3
            and all(type(n) in (int, float) and isfinite(n) for n in value)):
        return tuple(float(n) for n in value)
    return ()


def layers_for_view(view, specs=None):
    """Keep server offsets, values and segment topology tied to one view revision."""
    specs = specs or {}
    structure = view["structure"]
    size = len(view["atom_ids"])
    layers = []
    editors = structure.get("condition_editors", {})
    centroids = {key: [entry for entry in rows if _vector(entry.get("target"))]
                 for key, rows in editors.get("centroids", {}).items()}
    seen = {key for key, rows in centroids.items() if rows}
    entries = [*view.get("annotations", ()), *structure.get("condition_details", ())]
    for entry in entries:
        key = entry["name"]
        if key in seen:
            continue
        seen.add(key)
        spec = specs.get(key, {})
        values = entry.get("values") or [{}]
        for item in values:
            raw = item.get("value")
            name = entry.get("label", key)
            choices = spec.get("values") or {}
            token = next((str(choice).upper() for choice, value in choices.items() if raw == value), str(raw).upper())
            if key == "na_ss_state" and token == "LOOP":
                token = "UNPAIRED"
            if spec.get("kind") == "mask" or isinstance(raw, bool):
                text = "已取消索引" if spec.get("invert_mask") else "已应用"
            else:
                text = _STATES.get(token) or item.get("label") or (_value(raw) if raw is not None else "")
            count = item.get("count", entry.get("count", 0))
            indices = _indices(item, size)
            unit = "atoms" if "atom_indices" in item else entry.get("unit")
            amount = f"{count}{_UNITS.get(unit, '')}"
            if not indices and count:
                amount += " · 无定位信息"
            color = _COLORS.get(token, _COLORS.get(key, _FALLBACK))
            atom_level = entry.get("level", spec.get("level")) == "atom"
            layers.append(Layer(key, name, f"{text} · {amount}".strip(" ·"), indices, color, atom_level=atom_level))

    chains = structure.get("sequence", ())
    for chain in chains:
        items = chain.get("items", ())
        anchors = [_indices(item, size) if item.get("kind") == "residue" else () for item in items]
        for index, item in enumerate(items):
            if item.get("kind") != "segment":
                continue
            before = next((x for x in reversed(anchors[:index]) if x), ())
            after = next((x for x in anchors[index + 1:] if x), ())
            minimum, maximum = item["minimum"], item["maximum"]
            realized = item.get("realized_length")
            count = min(24, (minimum + maximum + 1) // 2)
            indices = _indices(item, size)
            layer = Layer(
                "segment", f"{chain['chain_id']} · 可变片段",
                f"{minimum}–{maximum} 残基 · " + (f"已展开 {realized}" if realized else f"示意 {count} 点"),
                indices, _COLORS["segment"],
                () if realized else (before, after, count),
            )
            layers.append(layer)
    for key, rows in centroids.items():
        for entry in rows:
            target = _vector(entry["target"])
            layers.append(Layer(key, f"{entry['chain']} · 质心目标", _value(target),
                                color=(0.51, 0.29, 0.72), position=target,
                                pair=(_vector(entry.get("current")), target)))
    for entry in editors.get("restraints", ()):
        indices = _indices(entry, size)
        if not indices:
            indices = _indices({"atom_indices": [entry.get("atom1"), entry.get("atom2")]}, size)
        text = entry.get("label") or _value(entry.get("target", entry.get("distance", "")))
        layers.append(Layer("restraints", "距离约束", text, indices, _COLORS["restraints"]))
    for key, count in structure.get("condition_counts", {}).items():
        if count and key not in seen:
            layers.append(Layer(key, key, f"{count} · 服务端未提供定位"))
    return tuple(layers)


def _anchor(atoms, indices):
    candidates = [atoms[i] for i in indices if i in atoms]
    if not candidates:
        return ()
    return tuple(next((atom for atom in candidates if atom.name == "CA"), candidates[0]).coord)


def _segment_points(atoms, segment, origin):
    before, after, count = segment
    left, right = _anchor(atoms, before), _anchor(atoms, after)
    if not left and not right:
        left, right = origin, (origin[0] + 3.0 + count, origin[1], origin[2])
    elif not left:
        left = (right[0] - 3.0 - count, right[1], right[2])
    elif not right:
        right = (left[0] + 3.0 + count, left[1], left[2])
    coordinates = []
    for step in range(1, count + 1):
        fraction = step / (count + 1)
        base = [a + (b - a) * fraction for a, b in zip(left, right)]
        base[1] += min(8.0, 2.0 + 0.75 * count) * sin(pi * fraction)
        coordinates.append(tuple(base))
    return coordinates


def _line(start, end, color, radius):
    return [CYLINDER, *start, *end, radius, *color, *color]


class ConditionPreview:
    def __init__(self, bridge):
        self.bridge = bridge
        self.copy = self.source = self.marker = ""
        self.was_enabled = False
        self.sid = None
        self.last_draw = None
        self.atoms = {}
        self.origin = (0.0, 0.0, 0.0)

    def clear(self):
        if not self.copy and not self.source:
            return
        names = cmd.get_names("objects")
        selection = self.bridge.selection_name
        selected = []
        if self.copy in names and selection in cmd.get_names("selections"):
            selected = [int(atom.id) for atom in cmd.get_model(f"%{selection} and %{self.copy}", state=1).atom]
        with display_updates(cmd):
            self.bridge.preview_object = None
            for name in (self.marker, self.copy):
                if name:
                    cmd.delete(name)
            if self.source in names:
                if self.was_enabled:
                    cmd.enable(self.source)
                if selected:
                    cmd.select_list(selection, self.source, selected, mode="id", quiet=1)
        self.source = self.copy = self.marker = ""
        self.sid = None
        self.last_draw = None
        self.atoms = {}

    def _open(self, sid, view):
        source = self.bridge.assert_current(sid)
        self.source = source
        self.was_enabled = source in cmd.get_names("objects", enabled_only=1)
        self.copy = f"rfd4_preview_{uuid4().hex[:8]}"
        cmd.create(self.copy, f"%{source}", source_state=1, target_state=1, zoom=0, quiet=1)
        atoms = {int(atom.id): atom for atom in cmd.get_model(f"%{self.copy}", state=1).atom}
        if set(atoms) != set(view["atom_ids"]) or len(atoms) != len(view["atom_ids"]):
            raise ValueError("预览副本无法匹配当前结构的原子。")
        self.atoms = {i: atoms[atom_id] for i, atom_id in enumerate(view["atom_ids"])}
        placeholders = set(view.get("placeholder_ids", ()))
        base = [atom.coord for atom in self.atoms.values() if int(atom.id) not in placeholders]
        if base:
            self.origin = (max(coord[0] for coord in base) + 4.0,
                           sum(coord[1] for coord in base) / len(base),
                           sum(coord[2] for coord in base) / len(base))
        self.sid = sid
        self.bridge.preview_object = sid, self.copy
        cmd.disable(source)

    def draw(self, sid, view, selected, all_layers):
        if self.sid != sid or self.copy not in cmd.get_names("objects"):
            self.clear()
            with display_updates(cmd):
                self._open(sid, view)
        signature = (sid, view["revision"], selected)
        if self.last_draw == signature:
            return
        scope = f"%{self.copy}"
        # Broad conditioning is inspection detail. The overview keeps the
        # backbone neutral so explicit targets and the insertions stay visible.
        active = (tuple(layer for layer in all_layers if layer.key in _OVERVIEW_LAYERS or layer.position)
                  if selected is None else selected)
        secondary = tuple(layer for layer in all_layers if layer.key in _SECONDARY_STRUCTURE)
        placeholders = set(view.get("placeholder_ids", ()))
        select = cmd.get_unused_name("rfd4_preview_atoms")
        geometry = []
        with display_updates(cmd):
            if self.marker:
                cmd.delete(self.marker)
                self.marker = ""
            cmd.hide("everything", scope)
            cmd.show("cartoon", scope)
            cmd.show("sticks", f"({scope}) and not polymer")
            cmd.color("gray70", scope)
            cmd.set("cartoon_color", "gray70", scope)
            try:
                for layer in sorted(active, key=lambda layer: _ATOM_PRIORITY.get(layer.key, 0)):
                    self._draw_layer(layer, select, placeholders, geometry)
                segments = (layer for layer in all_layers if layer.segment)
                for index, layer in enumerate(segments):
                    color = next((state.color for state in secondary
                                  if set(state.indices).intersection(layer.indices)), layer.color)
                    self._draw_segment(layer, color, index, geometry)
            finally:
                cmd.delete(select)
            if geometry:
                self.marker = f"rfd4_markers_{uuid4().hex[:8]}"
                cmd.load_cgo(geometry, self.marker, zoom=0)
        self.last_draw = signature

    def _draw_layer(self, layer, selection, placeholders, geometry):
        if layer.position:
            geometry.extend([COLOR, *layer.color, SPHERE, *layer.position, 0.70])
            if layer.pair and layer.pair[0]:
                geometry.extend(_line(*layer.pair, layer.color, 0.12))
        ids = [int(self.atoms[i].id) for i in layer.indices if i in self.atoms
               and int(self.atoms[i].id) not in placeholders]
        if not ids:
            return
        cmd.select_list(selection, self.copy, ids, mode="id", quiet=1)
        color = "0x" + "".join(f"{round(channel * 255):02x}" for channel in layer.color)
        if layer.key in _SECONDARY_STRUCTURE:
            cmd.set("cartoon_color", color, selection)
            cmd.show("cartoon", selection)
            return
        cmd.show("sticks", selection)
        cmd.set("stick_radius", 0.22, selection)
        cmd.color(color, selection)
        if layer.atom_level or layer.key in ("coordinate", "hotspot", "restraints"):
            cmd.show("spheres", selection)
            cmd.set("sphere_color", color, selection)
            cmd.set("sphere_scale", _SPHERE_SCALES.get(layer.key, 0.30), selection)
        if layer.key == "restraints" and len(ids) == 2:
            p, q = [self.atoms[i].coord for i in layer.indices if int(self.atoms[i].id) in ids]
            geometry.extend(_line(p, q, layer.color, 0.12))

    def _draw_segment(self, layer, color, index, geometry):
        left, right, _ = layer.segment
        x, y, z = self.origin
        points = _segment_points(self.atoms, layer.segment, (x, y + 5.0 * index, z))
        for point in points:
            geometry.extend([COLOR, *color, SPHERE, *point, 0.39])
        for start, end in zip([_anchor(self.atoms, left), *points], [*points, _anchor(self.atoms, right)]):
            if start and end:
                geometry.extend(_line(start, end, color, 0.10))

