"""Per-dataset Review reference and rigid alignment in scene coordinates."""

from collections import Counter
from contextlib import contextmanager
from pathlib import Path
import re
import tempfile
from urllib.request import urlopen
from uuid import uuid4

from pymolplugins.core.math3d import (
    fit_rigid_transform, homogeneous_to_ttt, mat4_multiply, ttt_to_homogeneous,
)
from pymolplugins.core.windows_paths import windows_long_path


_PDB_ID = re.compile(r"[1-9][A-Za-z0-9]{3}")
_DIRECTIONS = ("row_to_reference", "reference_to_row")
_SOURCES = ("row", "file", "pdb")
_MODES = ("auto", "protein", "ligand")
_VIEW_OVERLAY_TRANSPARENCY = 0.65


def validate_reference_track(raw):
    """Return a normalized JSON object. An empty object turns the track off."""
    if not isinstance(raw, dict):
        raise ValueError("reference_track must be a JSON object")
    if not raw:
        return {}
    fields = {
        "source": "", "value": "", "selection": "", "row_selection": "",
        "direction": "row_to_reference", "mode": "auto",
    }
    if set(raw) - fields.keys():
        raise ValueError("Unknown reference track field")
    for name in fields:
        value = raw.get(name, fields[name])
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError(f"Invalid reference track {name}")
        fields[name] = value.strip()
    source, value = fields["source"], fields["value"]
    if source not in _SOURCES or not value:
        raise ValueError("Choose a CSV row, structure file, or PDB ID")
    if fields["direction"] not in _DIRECTIONS or fields["mode"] not in _MODES:
        raise ValueError("Invalid reference alignment direction or mode")
    if source == "pdb":
        if not _PDB_ID.fullmatch(value):
            raise ValueError("Enter a four-character PDB ID")
        fields["value"] = value.upper()
    return fields


def parse_pdb_reference(text):
    match = re.fullmatch(r"\s*([1-9][A-Za-z0-9]{3})(?:\s+and\s+(.+?))?\s*", text, re.I)
    if not match:
        raise ValueError("Enter a PDB ID or a selection such as 6FZN and resn 9UY")
    return match[1].upper(), match[2] or ""


def fetch_pdb_reference(pdb_id, cache_dir):
    """Fetch a PDB entry into the Review session cache off the Qt thread."""
    if not _PDB_ID.fullmatch(pdb_id):
        raise ValueError("Invalid PDB ID")
    cache = Path(cache_dir)
    dest = cache / f"reference_{pdb_id.upper()}.cif"
    if not dest.exists():
        with urlopen(f"https://files.rcsb.org/download/{pdb_id.upper()}.cif", timeout=12) as response:
            content = response.read()
        if not content.lstrip().startswith(b"data_"):
            raise ValueError(f"RCSB returned invalid mmCIF for {pdb_id}")
        with tempfile.NamedTemporaryFile(dir=cache, suffix=".cif", delete=False) as handle:
            temporary = Path(handle.name)
        try:
            temporary.write_bytes(content)
            temporary.replace(dest)
        finally:
            temporary.unlink(missing_ok=True)
    return str(dest)


def _scope(name, selection):
    return f"%{name} and ({selection})" if selection else f"%{name}"


def _coordinates(api, selection):
    atoms = api.get_model(selection, 1).atom
    coords = api.get_coords(selection, 1)
    return list(zip(atoms, coords)) if coords is not None else []


def ligand_atom_coordinates(selected):
    """Index unique heavy-atom names and elements in one ligand residue."""
    selected = list(selected)
    residues = {(a.segi, a.chain, a.resi, a.resn) for a, _ in selected}
    if len(residues) != 1:
        raise ValueError("Select exactly one ligand residue on each side")
    names = [(a.name, a.symbol, xyz) for a, xyz in selected]
    counts = Counter((name, element) for name, element, _ in names)
    return {(name, element): xyz for name, element, xyz in names if counts[name, element] == 1}


def ligand_anchor_pairs(rows, refs):
    """Return corresponding coordinates in deterministic name/element order."""
    rows, refs = ligand_atom_coordinates(rows), ligand_atom_coordinates(refs)
    shared = sorted(rows.keys() & refs.keys())
    if len(shared) < 3:
        raise ValueError("Ligand alignment needs at least three shared heavy-atom names and elements")
    return [(rows[name], refs[name]) for name in shared]


@contextmanager
def preserve_alignment_selections(api):
    selections = set(api.get_names("selections"))
    enabled = set(api.get_names("selections", enabled_only=1))
    saved = {}
    try:
        for name in ("_align1", "_align2"):
            if name in selections:
                backup = api.get_unused_name("sa_reference_selection")
                saved[name] = backup
                api.select(backup, f"%{name}", enable=0, quiet=1)
        yield
    finally:
        for name in ("_align1", "_align2"):
            if name in saved:
                api.select(name, f"%{saved[name]}", enable=int(name in enabled), quiet=1)
                api.delete(saved[name])
            elif name not in selections:
                api.delete(name)


def _protein_pairs(api, row, reference, owner, ref_name):
    alignment = api.get_unused_name("sa_reference_alignment")
    try:
        with preserve_alignment_selections(api):
            api.align(row, reference, cycles=0, object=alignment, mobile_state=1,
                      target_state=1, transform=0, quiet=1)
        rows = {a.index: xyz for a, xyz in _coordinates(api, row)}
        refs = {a.index: xyz for a, xyz in _coordinates(api, reference)}
        pairs = []
        for column in api.get_raw_alignment(alignment, active_only=0):
            indices = dict(column)
            if owner in indices and ref_name in indices:
                pairs.append((rows[indices[owner]], refs[indices[ref_name]]))
        if len(pairs) < 3:
            raise ValueError("Protein alignment needs at least three matched Cα atoms")
        return pairs
    finally:
        api.delete(alignment)


def _ttt(api, name):
    value = api.get_object_ttt(name, quiet=1)
    return tuple(value) if value is not None else None


def molecule_identity(api, name, *, create=True):
    """Keep a molecule's lifetime distinct from its reusable scene name."""
    key = "_pp_review_identity"
    token = api.get_property(key, name)
    if token is None and create:
        token = uuid4().hex
        api.set_property(key, token, name)
    return token


class ReferenceTrack:
    """Own the display-only reference and reversible object transforms."""

    def __init__(self):
        self.name = self.key = self.owner = self.delta = None
        self.moved = {}
        self.identities = {}
        self.reference_identity = None
        self.protein = False
        self.view_request = object()

    def loaded(self, api, key):
        return (self.key == key and self.name in api.get_names("objects")
                and molecule_identity(api, self.name) == self.reference_identity)

    def suspend(self, api):
        if self.name:
            from .review_view_presets import clear_review_view

            clear_review_view(api, self.name)
        objects = set(api.get_names("objects"))
        for name, (baseline, applied) in self.moved.items():
            if (name in objects and _ttt(api, name) == applied
                    and (name not in self.identities or molecule_identity(api, name) == self.identities[name])):
                api.set_object_ttt(name, baseline or homogeneous_to_ttt(ttt_to_homogeneous(None)), quiet=1)
        self.moved.clear()
        self.identities.clear()
        self.owner = self.delta = None
        if self.name in objects and molecule_identity(api, self.name) == self.reference_identity:
            api.disable(self.name)

    def clear(self, api):
        self.suspend(api)
        if (self.name in api.get_names("objects")
                and molecule_identity(api, self.name) == self.reference_identity):
            api.delete(self.name)
        self.name = self.key = None
        self.reference_identity = None

    def load(self, api, key, path, selection):
        self.clear(api)
        source = api.get_unused_name("sa_reference_source")
        name = api.get_unused_name("sa_reference_track")
        try:
            api.load(windows_long_path(path), source, quiet=1, zoom=0)
            scope = _scope(source, selection)
            if api.count_atoms(scope, state=1) == 0:
                raise ValueError("The reference selection matches no atoms")
            api.create(name, scope, 1, 1, zoom=0, quiet=1)
            api.disable(name)
            self.name, self.key = name, key
            self.reference_identity = molecule_identity(api, name)
        except Exception:
            api.delete(name)
            raise
        finally:
            api.delete(source)

    def apply(self, api, owner, config, products):
        self.suspend(api)
        ref_name = self.name
        ref = _scope(ref_name, "")
        protein = config["mode"] == "protein" or (
            config["mode"] == "auto" and api.count_atoms(f"{ref} and polymer.protein and name CA")
        )
        row = _scope(owner, config["row_selection"] or ("polymer.protein" if protein else "organic"))
        api.enable(ref_name)
        try:
            if protein:
                ca = " and polymer.protein and name CA"
                pairs = _protein_pairs(api, row + ca, ref + ca, owner, ref_name)
            else:
                pairs = ligand_anchor_pairs(_coordinates(api, f"({row}) and not hydro"),
                                            _coordinates(api, f"({ref}) and not hydro"))
            rows, refs = zip(*pairs)
            move_row = config["direction"] == "row_to_reference"
            fit = fit_rigid_transform(rows, refs) if move_row else fit_rigid_transform(refs, rows)
            self.owner, self.delta = owner, fit.matrix
            self.move_products(api, owner, {owner, *products} if move_row else {ref_name})
            if move_row:
                api.center(ref, state=1, origin=0, animate=0)
            else:
                self.delta = None
            self.protein = protein
            self.restore_view(api)
        except Exception:
            self.suspend(api)
            raise
        unit = "Cα" if protein else "heavy atoms"
        return f"{len(pairs)} {unit} · RMSD {fit.rmsd_after:.3f} Å"

    def restore_view(self, api):
        """Restore the track's standard appearance without touching its alignment."""
        from .review_view_presets import clear_review_view

        if not self.loaded(api, self.key):
            return
        clear_review_view(api, self.name)
        ref = _scope(self.name, "")
        for setting in ("cartoon_color", "cartoon_transparency", "stick_transparency"):
            api.unset(setting, self.name, quiet=1)
        api.hide("everything", ref)
        api.show("cartoon" if self.protein else "sticks", ref + (" and polymer.protein" if self.protein else ""))
        api.color("yelloworange", ref if self.protein else ref + " and elem C")
        if not self.protein:
            api.set("stick_radius", 0.16, ref)
            api.set("stick_transparency", 0.25, ref)

    def style_view_overlay(self, api, view):
        """Draw the reference as context over the candidate's full-strength view."""
        from .review_view_presets import review_view_overlays

        ref = _scope(self.name, "")
        if view in {"overview", "interface"}:
            api.set("cartoon_color", "yelloworange", self.name)
            api.color("yelloworange", f"{ref} and elem C")
        for setting in ("cartoon_transparency", "stick_transparency"):
            api.set(setting, _VIEW_OVERLAY_TRANSPARENCY, self.name)
        for overlay in review_view_overlays(api, self.name):
            if api.get_type(overlay) == "object:cgo":
                api.set("cgo_transparency", _VIEW_OVERLAY_TRANSPARENCY, overlay)
            else:
                api.set("stick_transparency", _VIEW_OVERLAY_TRANSPARENCY, overlay)
                api.set("transparency", max(_VIEW_OVERLAY_TRANSPARENCY,
                                            api.get_setting_float("transparency", overlay)), overlay)

    def move_products(self, api, owner, products):
        if owner != self.owner or self.delta is None:
            return
        for name in set(api.get_names("objects")) & set(products):
            if api.get_type(name) == "object:group":
                continue
            baseline = _ttt(api, name)
            identity = molecule_identity(api, name) if api.get_type(name) == "object:molecule" else None
            if name in self.moved and self.identities.get(name) == identity:
                continue
            matrix = mat4_multiply(self.delta, ttt_to_homogeneous(baseline))
            api.set_object_ttt(name, homogeneous_to_ttt(matrix), quiet=1)
            self.moved[name] = (baseline, _ttt(api, name))
            if identity is not None:
                self.identities[name] = identity
