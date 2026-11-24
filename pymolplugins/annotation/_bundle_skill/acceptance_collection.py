"""Load one identity bundle at a time from a project-relative TSV/CSV.

Use this for a large review corpus when a curated ``.pse`` answers the main
question but the reviewer still needs deterministic access to every item.
Collection manifests bind semantic roles to path columns; task-local callbacks
may style those roles without placing arbitrary PyMOL commands in the manifest.
"""

from __future__ import annotations

import csv
import inspect
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional

from pymol import CmdException, cmd


_SCRIPT_DIR = Path(inspect.currentframe().f_code.co_filename).absolute().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

ViewCallback = Callable[[str, Mapping[str, str], Mapping[str, str]], Optional[bool]]

_rows: List[Dict[str, str]] = []
_root: Optional[Path] = None
_id_column = "id"
_role_columns: Dict[str, str] = {}
_view_name = "cartoon"
_index = -1
_loaded_names: List[str] = []
_loaded_scenes: List[str] = []
_summary_columns: Optional[List[str]] = None
_views: Dict[str, ViewCallback] = {}


def _safe_name(value: str, default: str = "item") -> str:
    text = re.sub(r"[^0-9A-Za-z_]+", "_", str(value or default)).strip("_")
    if not text:
        text = default
    if text[0].isdigit():
        text = f"item_{text}"
    return text


def _parse_role_columns(value: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for token in str(value or "").split(","):
        token = token.strip()
        if not token:
            continue
        if "=" not in token:
            raise CmdException("role_columns must use role=path_column pairs")
        role, column = (part.strip() for part in token.split("=", 1))
        if not role or not column:
            raise CmdException("role_columns must use non-empty role=path_column pairs")
        result[_safe_name(role, "role")] = column
    if not result:
        raise CmdException("at least one role=path_column pair is required")
    return result


def _read_rows(manifest: Path) -> List[Dict[str, str]]:
    delimiter = "\t" if manifest.suffix.lower() in {".tsv", ".tab"} else ","
    with manifest.open(newline="", encoding="utf-8") as handle:
        rows = [dict(row) for row in csv.DictReader(handle, delimiter=delimiter)]
    if not rows:
        raise CmdException(f"collection manifest has no rows: {manifest}")
    return rows


def _resolve_input(raw_path: str) -> Path:
    if _root is None:
        raise CmdException("collection is not open")
    return _relative_file(_root, raw_path)


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError as error:
        if getattr(error, "winerror", None) != 1005:
            raise
        return Path(os.path.abspath(path))


def _relative_file(root: Path, raw_path: str) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        raise CmdException("collection path is empty")
    path = Path(text)
    if path.is_absolute():
        raise CmdException(f"collection paths must be relative to the declared root: {path}")
    source = root / path
    try:
        _resolved(source).relative_to(_resolved(root))
    except ValueError as exc:
        raise CmdException(f"collection path escapes the declared root: {path}") from exc
    if not source.is_file():
        raise CmdException(f"collection file is missing: {source}")
    return source


def _cartoon_view(identity: str, objects: Mapping[str, str], row: Mapping[str, str]) -> None:
    del identity, row
    for object_name in objects.values():
        cmd.hide("everything", object_name)
        cmd.show("cartoon", f"{object_name} and polymer.protein")
        try:
            cmd.util.cbc(object_name)
        except Exception:
            pass


def acc_register_collection_view(name: str, callback: ViewCallback) -> str:
    """Register a stable role-aware view callback for the current PyMOL process."""

    view_name = _safe_name(name, "view")
    if not callable(callback):
        raise TypeError("collection view callback must be callable")
    _views[view_name] = callback
    return view_name


def _clear_loaded() -> None:
    global _loaded_names, _loaded_scenes
    for name in _loaded_scenes:
        cmd.scene(name, "clear")
    _loaded_scenes = []
    for name in reversed(_loaded_names):
        try:
            cmd.delete(name)
        except Exception:
            pass
    _loaded_names = []


def _load_index(index: int) -> str:
    global _index, _loaded_names, _loaded_scenes
    if not _rows:
        raise CmdException("collection is not open")
    if index < 0 or index >= len(_rows):
        raise CmdException(f"collection index must be between 1 and {len(_rows)}")

    row = _rows[index]
    identity = str(row.get(_id_column, "")).strip()
    if not identity:
        raise CmdException(f"row {index + 1} has no {_id_column!r} value")

    _clear_loaded()
    _index = -1
    safe_identity = _safe_name(identity)
    objects: Dict[str, str] = {}
    original_names = set(cmd.get_names("all"))
    original_scenes = set(cmd.get_scene_list())
    try:
        for role, column in _role_columns.items():
            if column not in row:
                raise CmdException(f"collection column is missing: {column}")
            source = _resolve_input(row[column])
            object_name = cmd.get_unused_name(f"acc_live__{safe_identity}__{role}", alwaysnumber=0)
            cmd.load(str(source), object_name)
            if int(cmd.count_atoms(object_name)) == 0:
                raise CmdException(f"loaded collection object has no atoms: {source}")
            objects[role] = object_name
            _loaded_names.append(object_name)

        group_name = cmd.get_unused_name(f"acc_live__{safe_identity}__bundle", alwaysnumber=0)
        cmd.group(group_name, " ".join(objects.values()), "add")
        _loaded_names.append(group_name)
        camera_set = _views[_view_name](identity, objects, row)
        for object_name in objects.values():
            cmd.enable(object_name)
        if not camera_set:
            cmd.orient(" or ".join(objects.values()))
            cmd.zoom(" or ".join(objects.values()), 5.0)
    except Exception:
        _loaded_names = [name for name in cmd.get_names("all") if name not in original_names]
        _loaded_scenes = [name for name in cmd.get_scene_list() if name not in original_scenes]
        _clear_loaded()
        raise

    _loaded_names = [name for name in cmd.get_names("all") if name not in original_names]
    _loaded_scenes = [name for name in cmd.get_scene_list() if name not in original_scenes]
    _index = index
    print(f"[collection] {index + 1}/{len(_rows)} id={identity} roles={','.join(objects)}")
    if _summary_columns is not None:
        for key in _summary_columns:
            if row.get(key):
                print(f"  {key}: {row[key]}")
    return identity


def acc_collection_open(
    manifest: str,
    role_columns: str,
    id_column: str = "id",
    root: str = "",
    view: str = "cartoon",
    initial=1,
) -> str:
    """Open a collection and load its first requested identity bundle."""

    global _rows, _root, _id_column, _role_columns, _view_name, _summary_columns
    manifest_path = _resolved(Path(str(manifest)).expanduser())
    if not manifest_path.is_file():
        raise CmdException(f"collection manifest is missing: {manifest_path}")
    declared_root = _resolved(Path(str(root)).expanduser()) if str(root).strip() else manifest_path.parent
    if not declared_root.is_dir():
        raise CmdException(f"collection root is missing: {declared_root}")

    rows = _read_rows(manifest_path)
    identity_column = str(id_column or "id").strip()
    roles = _parse_role_columns(role_columns)
    if identity_column not in rows[0]:
        raise CmdException(f"collection id column is missing: {identity_column}")
    missing_columns = [column for column in roles.values() if column not in rows[0]]
    if missing_columns:
        raise CmdException(f"collection path columns are missing: {', '.join(missing_columns)}")
    identities = [str(row.get(identity_column, "")).strip() for row in rows]
    if any(not identity for identity in identities):
        raise CmdException(f"collection contains an empty {identity_column!r} value")
    if len(set(identities)) != len(identities):
        raise CmdException(f"collection {identity_column!r} values must be unique")

    view_name = _safe_name(view, "cartoon")
    if view_name not in _views:
        raise CmdException(f"unknown collection view: {view_name}")
    _rows = rows
    _root = declared_root
    _id_column = identity_column
    _role_columns = roles
    _view_name = view_name
    _summary_columns = None
    return _load_index(int(initial) - 1)


def _ligand_view(config, root, path_column, *, store_scene=True, step_scene="", calculate_cavity=True,
                 membrane_renderer=None):
    from acceptance_bundle import asset_name
    from acceptance_recipes import ligand_pocket_review

    protein_suffix = config.get("protein_selection")
    ligand_suffix = config.get("ligand_selection")
    if not protein_suffix or not ligand_suffix:
        raise CmdException("ligand_pocket requires explicit protein_selection and ligand_selection.")
    bond = config.get("covalent_link")
    if bond is not None and (not isinstance(bond, list) or len(bond) != 2 or not all(bond)):
        raise CmdException("covalent_link requires exactly two role-scoped atom selections.")
    membrane = config.get("membrane")
    if membrane:
        ref_path = _relative_file(root, membrane.get("reference_path"))
        surface_path = _relative_file(root, membrane.get("surface_path"))
        annotations = json.loads(surface_path.read_text(encoding="utf-8"))
        ref_suffix = membrane.get("reference_ca")
        if not ref_suffix:
            raise CmdException("membrane requires a reference_ca selection in its verified z frame.")

    def draw(identity, objects, row):
        model = objects["model"]
        protein = f"{model} and ({protein_suffix})"
        ligand = f"{model} and ({ligand_suffix})"
        link = (f"({protein}) and ({bond[0]})", f"({ligand}) and ({bond[1]})") if bond else None
        membrane_options = None
        cavity_centers = None
        if membrane:
            annotation = annotations.get(row[path_column])
            if not annotation or not annotation.get("surface_residues"):
                raise CmdException(f"Membrane surface annotation is missing for {identity}.")
            reference = cmd.get_unused_name("_acc_bundle_reference")
            model_ca = f"({protein}) and name CA"
            ref_ca = f"{reference} and ({ref_suffix}) and name CA"
            try:
                if ref_path.suffix.lower() == ".pdb":
                    cmd.read_pdbstr(ref_path.read_bytes(), reference, zoom=0)
                else:
                    cmd.load(str(ref_path), reference, zoom=0)
                model_ids = [(atom.chain, atom.resi) for atom in cmd.get_model(model_ca).atom]
                ref_ids = [(atom.chain, atom.resi) for atom in cmd.get_model(ref_ca).atom]
                if len(model_ids) < 3 or model_ids != ref_ids or len(set(model_ids)) != len(model_ids):
                    raise CmdException(f"Membrane C-alpha correspondence failed for {identity}.")
                rmsd = cmd.pair_fit(model_ca, ref_ca, quiet=1)
                print(f"[bundle] {identity} membrane fit {len(model_ids)} C-alpha, RMSD {rmsd:.3f} A")
            finally:
                cmd.delete(reference)
            membrane_options = {
                "surface_residues": annotation["surface_residues"],
                "biased_residues": annotation.get("biased_residues", []),
                "bounds": membrane.get("bounds"),
                "core_bounds": membrane.get("core_bounds", ()),
                "side_labels": membrane.get("side_labels", ()),
            }
            cavity_centers = annotation.get("cavity_centers")
        if cavity_centers is None and not calculate_cavity:
            cavity_centers = []
        result = ligand_pocket_review(
            identity=identity, protein_selection=protein, ligand_selection=ligand,
            anchor_residues=config.get("anchor_residues", ""), covalent_link=link,
            membrane=membrane_options, show_cavity=config.get("show_cavity", True),
            cavity_centers=cavity_centers, overview_scene="",
            focus_scene=(step_scene or asset_name(identity, "live_focus")) if store_scene else "",
            bind_keys=False, membrane_renderer=membrane_renderer,
        )
        return result if membrane_renderer is not None else True

    return draw


def acc_bundle_step(model: str, structure_path: str) -> None:
    """Style the current Structure Annotator object using its bundle sidecars."""
    source = Path(structure_path).absolute()
    root = source.parent.parent
    config = json.loads((root / "bundle_view.json").read_text(encoding="utf-8"))
    view = config.get("view")
    if view == "cartoon":
        _cartoon_view(model, {"model": model}, {})
        cmd.orient(model)
        cmd.zoom(model, 5.0)
        return
    if view != "ligand_pocket":
        raise CmdException(f"Unsupported bundle step view: {view!r}.")
    path_column = config.get("path_column", "structure_path")
    row = {path_column: source.relative_to(root).as_posix()}
    draw = _ligand_view(config, root, path_column, store_scene=False)
    selections = set(cmd.get_names("selections"))
    try:
        draw(model, {"model": model}, row)
    finally:
        for name in set(cmd.get_names("selections")) - selections:
            cmd.delete(name)


def acc_bundle_open(review_json: str, initial=1) -> str:
    """Open a scored bundle with a registered view and one live structure."""
    global _summary_columns
    config_path = _resolved(Path(str(review_json)).expanduser())
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("mode") != "bundle":
        raise CmdException("The view manifest must declare mode='bundle'.")
    root = config_path.parent
    manifest = _relative_file(root, config.get("scores_file", "sum_scores.csv"))
    path_column = config.get("path_column", "structure_path")
    view = config.get("view", "cartoon")
    if view == "ligand_pocket":
        view = acc_register_collection_view("bundle_ligand_pocket", _ligand_view(config, root, path_column))
    columns = list(config.get("display_columns", []))
    label_column = config.get("label_column")
    if label_column:
        columns.insert(0, label_column)
    rows = _read_rows(manifest)
    missing = [column for column in columns if column not in rows[0]]
    if missing:
        raise CmdException(f"Bundle display columns are missing: {', '.join(missing)}")
    identity = acc_collection_open(
        str(manifest), f"model={path_column}", id_column=config.get("id_column", path_column),
        root=str(root), view=view, initial=initial,
    )
    _summary_columns = columns
    for key in columns:
        if _rows[_index].get(key):
            print(f"  {key}: {_rows[_index][key]}")
    acc_collection_bind_keys()
    return identity


def acc_collection_goto(index=1) -> str:
    return _load_index(int(index) - 1)


def acc_collection_next(step=1) -> str:
    if not _rows:
        raise CmdException("collection is not open")
    return _load_index((_index + int(step)) % len(_rows))


def acc_collection_prev() -> str:
    return acc_collection_next(step=-1)


def acc_collection_info() -> Dict[str, str]:
    if not _rows or _index < 0:
        raise CmdException("collection is not open")
    row = dict(_rows[_index])
    print(f"[collection] {_index + 1}/{len(_rows)}")
    columns = _summary_columns if _summary_columns is not None else [
        key for key in row if key not in _role_columns.values()
    ]
    for key in columns:
        if row.get(key):
            print(f"  {key}: {row[key]}")
    return row


def acc_collection_bind_keys(next_key: str = "right", prev_key: str = "left") -> None:
    cmd.set_key(str(next_key), acc_collection_next)
    cmd.set_key(str(prev_key), acc_collection_prev)


def acc_collection_close() -> None:
    global _rows, _root, _index, _summary_columns
    _clear_loaded()
    _rows = []
    _root = None
    _index = -1
    _summary_columns = None


_views["cartoon"] = _cartoon_view

if __name__ != "<run_path>":
    cmd.extend("acc_collection_open", acc_collection_open)
    cmd.extend("acc_bundle_open", acc_bundle_open)
    cmd.extend("acc_collection_goto", acc_collection_goto)
    cmd.extend("acc_collection_next", acc_collection_next)
    cmd.extend("acc_collection_prev", acc_collection_prev)
    cmd.extend("acc_collection_info", acc_collection_info)
    cmd.extend("acc_collection_bind_keys", acc_collection_bind_keys)
    cmd.extend("acc_collection_close", acc_collection_close)
