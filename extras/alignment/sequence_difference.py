
from collections import Counter
from dataclasses import dataclass
import re
import traceback
from typing import Dict, List, Optional, Tuple

from pymol import cmd

try:
    from pymol.Qt import QtCore, QtWidgets
except Exception:
    QtCore = None
    QtWidgets = None


DEFAULT_PALETTE = ["red", "blue", "green", "orange", "purple"]
RESI_PATTERN = re.compile(r"(-?\d+)([A-Za-z]*)")

THREE_TO_ONE = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "PHE": "F",
    "PRO": "P",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
    "ASX": "B",
    "GLX": "Z",
    "SEC": "U",
    "PYL": "O",
    "MSE": "M",
    "XLE": "L",
    "AIB": "A",
    "UNK": "X",
}


@dataclass
class ResidueInfo:
    code: str
    selector: str
    sort_key: Tuple[str, int, str]


@dataclass
class ObjectStat:
    name: str
    mismatches: int
    missing: int
    details: List[str]


@dataclass
class ReferenceInfo:
    label: str
    codes: Dict[Tuple[str, str], str]
    selectors: Dict[Tuple[str, str], Optional[str]]
    mode: str
    object_name: Optional[str] = None


def highlight_seq_diff(
    max_objects: int = 5,
    reference_mode: str = "consensus",
    reference_index: int = 1,
    palette: Optional[str] = None,
    match_color: str = "grey80",
    gap_color: str = "orange",
    paint_reference: bool = True,
    quiet: bool = False,
):
    """
    Highlight sequence differences for enabled objects in the PyMOL sequence viewer.

    Args:
        max_objects: Maximum number of enabled objects to compare.
        reference_mode: Either "consensus" or "object".
        reference_index: 1-based index of the reference object when using "object".
        palette: Optional comma-separated list of colors for mismatches.
        match_color: Color used for residues that match the reference.
        gap_color: Color applied to reference residues highlighted because of gaps.
        paint_reference: Whether to recolor the reference object at differing sites.
        quiet: Suppress summary output when True.
    """
    try:
        stats, reference = _run_highlight_seq_diff(
            max_objects,
            reference_mode,
            reference_index,
            palette,
            match_color,
            gap_color,
            paint_reference,
        )
        if stats and not quiet:
            _print_summary(stats, reference)
    except Exception as exc:
        print(f"Error highlighting sequence differences: {exc}")
        traceback.print_exc()


def _run_highlight_seq_diff(
    max_objects,
    reference_mode,
    reference_index,
    palette,
    match_color,
    gap_color,
    paint_reference,
):
    objects = _enabled_objects(max_objects)
    if not objects:
        print("No enabled objects detected.")
        return None, None

    residue_maps = _collect_residue_maps(objects)
    objects = list(residue_maps)
    if len(objects) < 2:
        print("Need at least two polymer objects with CA atoms.")
        return None, None

    position_keys = _collect_position_keys(residue_maps)
    if not position_keys:
        print("No comparable residues found across the selected objects.")
        return None, None

    reference = _build_reference(
        reference_mode, reference_index, objects, residue_maps, position_keys
    )
    palette_colors = _prepare_palette(palette, len(objects))
    palette_colors = [
        _ensure_color(
            color,
            DEFAULT_PALETTE[idx % len(DEFAULT_PALETTE)],
            f"user_{idx}",
        )
        for idx, color in enumerate(palette_colors)
    ]
    match_color = _ensure_color(match_color, "grey80", "match")
    gap_color = _ensure_color(gap_color, "orange", "gap")

    cmd.set("seq_view", 1)
    stats = _color_objects(
        objects,
        residue_maps,
        position_keys,
        reference,
        palette_colors,
        match_color,
        gap_color,
        paint_reference,
    )
    return stats, reference


def _enabled_objects(max_objects) -> List[str]:
    names = cmd.get_names("objects", enabled_only=True)
    if not names:
        return []
    limit = _resolve_max_objects(max_objects)
    if len(names) > limit:
        print(f"Limiting to first {limit} enabled objects: {names[:limit]}")
    return names[:limit]


def _resolve_max_objects(value) -> int:
    if value in (None, "", "None"):
        return 5
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 5
    return max(1, parsed)


def _collect_residue_maps(objects: List[str]) -> Dict[str, Dict[Tuple[str, str], ResidueInfo]]:
    residue_maps: Dict[str, Dict[Tuple[str, str], ResidueInfo]] = {}
    for name in objects:
        residues = _build_residue_lookup(name)
        if residues:
            residue_maps[name] = residues
        else:
            print(f"Skipping '{name}': no polymer CA residues found.")
    return residue_maps


def _build_residue_lookup(object_name: str) -> Dict[Tuple[str, str], ResidueInfo]:
    model = cmd.get_model(f"{object_name} and polymer.protein and name CA")
    residues: Dict[Tuple[str, str], ResidueInfo] = {}
    for atom in model.atom:
        chain = (atom.chain or "").strip()
        resi = atom.resi.strip()
        if not resi:
            continue
        key = (chain or "_", resi)
        if key in residues:
            continue
        selector = _compose_selector(object_name, chain, resi)
        sort_key = _sort_key(chain, resi)
        residues[key] = ResidueInfo(_three_to_one(atom.resn), selector, sort_key)
    return residues


def _compose_selector(obj: str, chain: str, resi: str) -> str:
    chain_clause = "" if not chain else f" and chain {chain}"
    return f"{obj} and polymer.protein and resi {resi}{chain_clause}"


def _three_to_one(resn: str) -> str:
    return THREE_TO_ONE.get(resn.upper(), "X")


def _sort_key(chain: str, resi: str) -> Tuple[str, int, str]:
    match = RESI_PATTERN.match(resi)
    if match:
        number = int(match.group(1))
        suffix = match.group(2)
    else:
        number = 0
        suffix = resi
    chain_key = chain if chain else "_"
    return (chain_key, number, suffix)


def _collect_position_keys(
    residue_maps: Dict[str, Dict[Tuple[str, str], ResidueInfo]]
) -> List[Tuple[str, str]]:
    ordering: Dict[Tuple[str, str], Tuple[str, int, str]] = {}
    for residues in residue_maps.values():
        for key, info in residues.items():
            ordering.setdefault(key, info.sort_key)
    return sorted(ordering, key=lambda item: ordering[item])


def _build_reference(
    reference_mode: str,
    reference_index: int,
    objects: List[str],
    residue_maps: Dict[str, Dict[Tuple[str, str], ResidueInfo]],
    position_keys: List[Tuple[str, str]],
) -> ReferenceInfo:
    mode = (reference_mode or "consensus").strip().lower()
    if mode not in {"consensus", "object"}:
        print(f"Unknown reference_mode '{reference_mode}', falling back to consensus.")
        mode = "consensus"

    if mode == "object":
        idx = _clamp_index(reference_index, len(objects))
        ref_obj = objects[idx - 1]
        residues = residue_maps[ref_obj]
        codes: Dict[Tuple[str, str], str] = {}
        selectors: Dict[Tuple[str, str], Optional[str]] = {}
        for key in position_keys:
            info = residues.get(key)
            codes[key] = info.code if info else "-"
            selectors[key] = info.selector if info else None
        label = f"{ref_obj} (#{idx})"
        return ReferenceInfo(label, codes, selectors, "object", ref_obj)

    codes = {}
    for key in position_keys:
        letters = [residue_maps[obj][key].code for obj in objects if key in residue_maps[obj]]
        if letters:
            best = Counter(letters).most_common()
            codes[key] = sorted(best, key=lambda item: (-item[1], item[0]))[0][0]
        else:
            codes[key] = "-"
    return ReferenceInfo("consensus", codes, {}, "consensus", None)


def _clamp_index(value, size: int) -> int:
    try:
        idx = int(value)
    except (TypeError, ValueError):
        idx = 1
    idx = max(1, idx)
    return min(idx, size)


def _prepare_palette(palette, count: int) -> List[str]:
    if isinstance(palette, str) and palette.strip():
        colors = [item.strip() for item in palette.split(",") if item.strip()]
    elif isinstance(palette, (list, tuple)):
        colors = [str(item).strip() for item in palette if str(item).strip()]
    else:
        colors = []
    if not colors:
        colors = list(DEFAULT_PALETTE)
    while len(colors) < count:
        colors.extend(DEFAULT_PALETTE)
    return colors[:count]


def _ensure_color(color: Optional[str], fallback: str, tag: str) -> str:
    candidate = (color or "").strip()
    if candidate and _color_exists(candidate):
        return candidate

    defined = _maybe_define_hex_color(candidate, tag)
    if defined:
        return defined

    fallback = (fallback or "").strip() or "blue"
    if _color_exists(fallback):
        if candidate:
            print(f"Color '{candidate}' not recognized; using '{fallback}'.")
        return fallback

    defined_fallback = _maybe_define_hex_color(fallback, f"{tag}_fallback")
    if defined_fallback:
        if candidate:
            print(f"Color '{candidate}' not recognized; using '{fallback}'.")
        return defined_fallback

    print(f"Color '{candidate}' not recognized; defaulting to 'blue'.")
    return "blue"


def _color_exists(name: str) -> bool:
    if not name:
        return False
    try:
        return cmd.get_color_index(name) >= 0
    except Exception:
        return False


def _maybe_define_hex_color(value: Optional[str], tag: str) -> Optional[str]:
    if not value or not value.startswith("#") or len(value) != 7:
        return None
    try:
        rgb = [int(value[i : i + 2], 16) / 255.0 for i in (1, 3, 5)]
    except ValueError:
        return None
    color_name = f"seqdiff_{tag}"
    cmd.set_color(color_name, rgb)
    return color_name


def _color_objects(
    objects: List[str],
    residue_maps: Dict[str, Dict[Tuple[str, str], ResidueInfo]],
    position_keys: List[Tuple[str, str]],
    reference: ReferenceInfo,
    palette: List[str],
    match_color: str,
    gap_color: str,
    paint_reference: bool,
) -> List[ObjectStat]:
    for obj in objects:
        cmd.color(match_color, f"{obj} and polymer.protein")

    painted_reference = set()
    stats: List[ObjectStat] = []

    for color, obj in zip(palette, objects):
        residues = residue_maps[obj]
        mismatches = 0
        missing = 0
        details: List[str] = []

        for key in position_keys:
            ref_code = reference.codes.get(key)
            info = residues.get(key)
            obj_code = info.code if info else "-"
            if ref_code is None or obj_code == ref_code:
                continue

            mismatches += 1
            if info:
                cmd.color(color, info.selector)
            else:
                missing += 1

            if (
                paint_reference
                and reference.selectors.get(key)
                and key not in painted_reference
            ):
                cmd.color(gap_color, reference.selectors[key])
                painted_reference.add(key)

            details.append(_format_detail(key, obj_code, ref_code))

        stats.append(ObjectStat(obj, mismatches, missing, details))

    return stats


def _format_detail(key: Tuple[str, str], obj_code: str, ref_code: Optional[str]) -> str:
    chain, resi = key
    chain_label = chain if chain != "_" else "-"
    ref_label = ref_code if ref_code else "-"
    return f"{chain_label}:{resi} {obj_code}->{ref_label}"


def _print_summary(stats: List[ObjectStat], reference: ReferenceInfo) -> None:
    print(f"Reference: {reference.label} ({reference.mode})")
    for stat in stats:
        base = (
            f"{stat.name}: {stat.mismatches} mismatches"
            f", {stat.missing} gaps vs reference"
        )
        if stat.details:
            preview = ", ".join(stat.details[:8])
            print(f"{base} | {preview}")
        else:
            print(base)


def __init_plugin__(app=None):
    try:
        from pymol.plugins import addmenuitemqt
    except Exception:
        return
    addmenuitemqt("Seq Diff (run)", lambda: highlight_seq_diff())
    if QtWidgets:
        addmenuitemqt("Seq Diff Reference Picker", show_seqdiff_dialog)


cmd.extend("highlight_seq_diff", highlight_seq_diff)

_seqdiff_dialog = None

if QtWidgets:

    class SeqDiffDialog(QtWidgets.QDialog):
        """Small helper dialog to run highlight_seq_diff with a chosen reference."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.object_names: List[str] = []
            self.setWindowTitle("Sequence Difference Highlighter")
            self.setMinimumWidth(360)
            self._build_ui()
            self.refresh_objects()

        def _build_ui(self):
            layout = QtWidgets.QVBoxLayout(self)

            info = QtWidgets.QLabel(
                "Enabled objects (top N). Double-click to use as reference."
            )
            info.setWordWrap(True)
            layout.addWidget(info)

            self.max_spin = QtWidgets.QSpinBox()
            self.max_spin.setRange(1, 50)
            self.max_spin.setValue(5)
            self.max_spin.valueChanged.connect(self.refresh_objects)

            top_row = QtWidgets.QHBoxLayout()
            top_row.addWidget(QtWidgets.QLabel("Max objects:"))
            top_row.addWidget(self.max_spin)
            top_row.addStretch()
            refresh_btn = QtWidgets.QPushButton("Refresh")
            refresh_btn.clicked.connect(self.refresh_objects)
            top_row.addWidget(refresh_btn)
            layout.addLayout(top_row)

            self.object_list = QtWidgets.QListWidget()
            self.object_list.itemDoubleClicked.connect(self.run_for_selected)
            layout.addWidget(self.object_list)

            palette_row = QtWidgets.QHBoxLayout()
            palette_row.addWidget(QtWidgets.QLabel("Palette:"))
            self.palette_edit = QtWidgets.QLineEdit("")
            self.palette_edit.setPlaceholderText("e.g. red,blue,#ffaa00")
            palette_row.addWidget(self.palette_edit)
            layout.addLayout(palette_row)

            color_row = QtWidgets.QHBoxLayout()
            self.match_edit = QtWidgets.QLineEdit("grey80")
            self.gap_edit = QtWidgets.QLineEdit("orange")
            color_row.addWidget(QtWidgets.QLabel("Match color:"))
            color_row.addWidget(self.match_edit)
            color_row.addWidget(QtWidgets.QLabel("Gap color:"))
            color_row.addWidget(self.gap_edit)
            layout.addLayout(color_row)

            self.paint_checkbox = QtWidgets.QCheckBox("Color reference gaps")
            self.paint_checkbox.setChecked(True)
            layout.addWidget(self.paint_checkbox)

            button_row = QtWidgets.QHBoxLayout()
            consensus_btn = QtWidgets.QPushButton("Consensus Reference")
            consensus_btn.clicked.connect(self.run_consensus)
            button_row.addWidget(consensus_btn)

            ref_btn = QtWidgets.QPushButton("Use Selected Object")
            ref_btn.clicked.connect(self.run_for_selected)
            button_row.addWidget(ref_btn)

            button_row.addStretch()
            layout.addLayout(button_row)

        def refresh_objects(self):
            max_objects = self.max_spin.value()
            names = _enabled_objects(max_objects)
            self.object_names = names
            self.object_list.clear()
            for idx, name in enumerate(names, start=1):
                self.object_list.addItem(f"{idx}. {name}")
            if names:
                self.object_list.setCurrentRow(0)
            else:
                empty = QtWidgets.QListWidgetItem("(No enabled objects)")
                if QtCore is not None:
                    empty.setFlags(QtCore.Qt.NoItemFlags)
                self.object_list.addItem(empty)

        def run_consensus(self):
            self._trigger_highlight("consensus", None)

        def run_for_selected(self):
            row = self.object_list.currentRow()
            if row < 0 or row >= len(self.object_names):
                print("Select a reference object first.")
                return
            self._trigger_highlight("object", row + 1)

        def _trigger_highlight(self, mode, ref_index):
            palette_value = self.palette_edit.text().strip()
            palette_arg = palette_value if palette_value else None
            highlight_seq_diff(
                max_objects=self.max_spin.value(),
                reference_mode=mode,
                reference_index=ref_index if ref_index is not None else 1,
                palette=palette_arg,
                match_color=self.match_edit.text().strip() or "grey80",
                gap_color=self.gap_edit.text().strip() or "orange",
                paint_reference=self.paint_checkbox.isChecked(),
            )


def show_seqdiff_dialog():
    """Open the reference picker dialog."""
    global _seqdiff_dialog
    if QtWidgets is None:
        print("PyMOL Qt bindings not available; cannot open dialog.")
        return None
    if _seqdiff_dialog is None:
        _seqdiff_dialog = SeqDiffDialog()
    _seqdiff_dialog.show()
    _seqdiff_dialog.raise_()
    _seqdiff_dialog.activateWindow()
    return _seqdiff_dialog


cmd.extend("show_seqdiff_dialog", show_seqdiff_dialog)
