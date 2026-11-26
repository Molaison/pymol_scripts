"""Reversible residue coloring, explicit selections and portable exports."""

from pymol import cmd

from collections import defaultdict
import csv
from pathlib import Path
import re

from ._coretosurf import COLORS, Residue, format_rosetta
from pymolplugins.residues.analysis import quoted


_colors = {}
_selections = set()
MODES = ("layers", "rsa", "hydropathy", "exposed", "contacts")


def residue_expression(records, indices=False):
    parts = []
    for record in records:
        model = "model " + quoted(record["object"])
        if indices:
            tail = "index " + "+".join(map(str, record["indices"]))
        else:
            number = record["resi"]
            number = "\\" + number if number.startswith("-") else number
            tail = "segi %s and chain %s and resi %s" % (
                quoted(record["segi"]), quoted(record["chain"]), number)
        parts.append("(%s and %s)" % (model, tail))
    return " or ".join(parts) or "none"


def _atom_identity(atom):
    return (atom.segi, atom.chain, atom.resi, atom.resn, atom.name, atom.alt, atom.id)


def _read_colors(obj, indices):
    saved = {}
    atom_colors = {}
    cmd.iterate("model %s and elem C" % quoted(obj), "colors[index] = color",
                space={"colors": atom_colors})
    for atom in cmd.get_model("model " + quoted(obj)).atom:
        if atom.index not in indices or atom.symbol != "C":
            continue
        saved[atom.index] = _atom_identity(atom), atom_colors[atom.index]
    return saved


def capture_colors(report):
    """Capture exactly the carbon atoms a residue color operation affects."""
    indices = defaultdict(set)
    for record in report["residues"]:
        indices[record["object"]].update(record["indices"])
    return {obj: _read_colors(obj, atoms) for obj, atoms in indices.items()}


def _remember(obj, indices):
    saved = _colors.setdefault(obj, {})
    for index, current in _read_colors(obj, indices).items():
        old = saved.get(index)
        if old is None or old[0] != current[0]:
            saved[index] = current


def _gradient(value, low, high, name):
    fraction = max(0.0, min(1.0, (value - low) / (high - low)))
    step = round(fraction * 32)
    color = "_pp_residue_%s_%d" % (name, step)
    existing = cmd.get_color_index(color)
    if existing >= 0:
        return existing
    t = step / 32.0
    # Blue -> near-white -> orange, shared by continuous numeric metrics.
    start, middle, end = (0.15, 0.4, 0.85), (0.94, 0.94, 0.94), (0.92, 0.35, 0.08)
    a, b, mix = (start, middle, 2 * t) if t <= 0.5 else (middle, end, 2 * t - 1)
    cmd.set_color(color, [x + (y - x) * mix for x, y in zip(a, b)])
    return cmd.get_color_index(color)


def color_report(report, mode, exposure=25.0, hydrophobicity=0.0, remember=True):
    by_object = defaultdict(dict)
    palette = {}
    maximum = max((r["contacts"] or 0 for r in report["residues"]), default=1) or 1
    for record in report["residues"]:
        if mode == "layers":
            color = cmd.get_color_index(COLORS.get(record["layer"], "gray70"))
        elif mode == "exposed":
            exposed = (record["rsa"] is not None and record["rsa"] >= exposure and
                       record["hydropathy"] is not None and record["hydropathy"] > hydrophobicity)
            color = cmd.get_color_index("orange" if exposed else "gray80")
        else:
            field, low, high = {"rsa": ("rsa", 0, 100),
                                "hydropathy": ("hydropathy", -4.5, 4.5),
                                "contacts": ("contacts", 0, maximum)}[mode]
            value = record[field]
            if value not in palette:
                palette[value] = (cmd.get_color_index("gray70") if value is None else
                                  _gradient(value, low, high, mode))
            color = palette[value]
        by_object[record["object"]].update(dict.fromkeys(record["indices"], color))
    for obj, colors in by_object.items():
        if remember:
            _remember(obj, colors)
        cmd.alter("model %s and elem C" % quoted(obj),
                  "color=colors.get(index, color)", space={"colors": colors})
        cmd.recolor("model " + quoted(obj))


def restore_colors(snapshot):
    """Restore surviving atoms from one View or command color snapshot."""
    restored = 0
    for obj, saved in snapshot.items():
        if obj not in cmd.get_names("objects"):
            continue
        colors = {a.index: saved[a.index][1]
                  for a in cmd.get_model("model " + quoted(obj)).atom
                  if a.index in saved and saved[a.index][0] == _atom_identity(a)}
        cmd.alter("model " + quoted(obj), "color=colors.get(index, color)",
                  space={"colors": colors})
        cmd.recolor("model " + quoted(obj))
        restored += len(colors)
    return restored


def clear():
    """Restore command colors and remove only selections owned here."""
    restored = restore_colors(_colors)
    for name in _selections:
        cmd.delete(name)
    _colors.clear()
    _selections.clear()
    return dict(restored_atoms=restored)


def create_selections(report, prefix="pp_residues"):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", prefix):
        raise ValueError("prefix must be a valid PyMOL name")
    layers = ("core", "boundary", "surface", "unclassified")
    names = [prefix + "_" + layer for layer in layers]
    collisions = set(names).intersection(cmd.get_names("all")) - _selections
    if collisions:
        raise ValueError("Selection names already in use: " + ", ".join(sorted(collisions)))
    for layer, name in zip(layers, names):
        records = [r for r in report["residues"] if r["layer"] == layer]
        cmd.select(name, residue_expression(records, indices=True), enable=0)
        _selections.add(name)
    return names


def format_layers(report, fmt="pymol", prefix="pp_residues"):
    """Export object-qualified PyMOL selections or per-object Rosetta resnums."""
    lines, warnings = [], []
    for layer in ("core", "boundary", "surface", "unclassified"):
        records = [r for r in report["residues"] if r["layer"] == layer]
        if fmt == "pymol":
            lines.append("select %s_%s, %s" % (prefix, layer, residue_expression(records)))
        elif fmt == "rosetta":
            by_object = defaultdict(list)
            for record in records:
                by_object[record["object"]].append(record)
            for obj, rows in by_object.items():
                segments = defaultdict(set)
                for row in report["residues"]:
                    if row["object"] != obj:
                        continue
                    segments[row["chain"], row["resi"]].add(row["segi"])
                unambiguous = [r for r in rows if not r.get("rosetta_ambiguous") and
                               len(segments[r["chain"], r["resi"]]) == 1]
                ambiguous = len(rows) - len(unambiguous)
                if ambiguous:
                    warnings.append("%s: %d residues omitted: duplicate chain/resi in different segments." % (obj, ambiguous))
                residues = [Residue(r["segi"], r["chain"], r["resi"], r["resn"], r["sasa"])
                            for r in unambiguous]
                text, skipped = format_rosetta(residues)
                lines.append("%s / %s: %s" % (obj, layer, text))
                if skipped:
                    warnings.append("%s / %s omitted: %s" % (obj, layer, ", ".join(skipped)))
        else:
            raise ValueError("format must be pymol or rosetta")
    return "\n".join(lines), warnings


def write_csv(report, path, pairs=False):
    """Write metrics or a sparse C-alpha contact map to a CSV file."""
    path = Path(path).expanduser()
    fields = ("object", "segi", "chain", "resi", "resn", "sasa", "rsa",
              "layer", "hydropathy", "contacts")
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        if not pairs:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(report["residues"])
        else:
            writer = csv.writer(stream)
            writer.writerow(["object", "segi_1", "chain_1", "resi_1", "segi_2", "chain_2", "resi_2", "ca_distance"])
            for pair in report["pairs"]:
                a, b = report["residues"][pair["i"]], report["residues"][pair["j"]]
                writer.writerow([a["object"], a["segi"], a["chain"], a["resi"],
                                 b["segi"], b["chain"], b["resi"], pair["distance"]])
    return str(path.resolve())
