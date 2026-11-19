"""Render declarative PyMOL skill Bundle views on Annotator-owned candidates."""

from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import lru_cache, partial
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from uuid import uuid4
import base64
import json
import os
import posixpath
import sys
from typing import Callable

from pymolplugins.core.windows_paths import windows_long_path
from pymolplugins.remote.ssh import SshClient, is_ssh_path, parse_ssh_path
from .review_file import bundle_matches_table


SKILL_MODULES = (
    "acceptance_bundle", "acceptance_cavity_runtime", "acceptance_pocket_void",
    "acceptance_recipes", "acceptance_collection",
)


@dataclass(frozen=True)
class BundleViewRequest:
    table_path: str
    config: dict
    row_path: str
    label: str


@dataclass(frozen=True)
class CandidateCavity:
    title: str
    identity: str
    capture: Callable


@contextmanager
def _skill_imports():
    previous = {name: sys.modules.get(name) for name in SKILL_MODULES}
    search_path = sys.path[:]
    try:
        yield
    finally:
        sys.path[:] = search_path
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


@lru_cache(maxsize=1)
def _load_skill():
    from pymol import cmd

    root = Path(__file__).with_name("_bundle_skill")
    modules = {}
    extend = cmd.extend
    with _skill_imports():
        # The skill is also a standalone console tool. Keep its commands and
        # navigation out of Annotator's session when importing it as a renderer.
        cmd.extend = lambda *args, **kwargs: None
        try:
            for name in SKILL_MODULES:
                spec = spec_from_file_location(name, root / f"{name}.py")
                module = module_from_spec(spec)
                modules[name] = sys.modules[name] = module
                spec.loader.exec_module(module)
        finally:
            cmd.extend = extend
    return modules


def _relative_asset(value, key):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Bundle membrane requires {key}.")
    value = value.strip().replace("\\", "/")
    relative = posixpath.normpath(value)
    if PureWindowsPath(value).drive or relative.startswith("/") or relative in {".", ".."} or relative.startswith("../"):
        raise ValueError(f"Bundle {key} must stay inside the score table folder.")
    return relative


def _read_sidecars(table_path, paths):
    if is_ssh_path(table_path):
        remote = parse_ssh_path(table_path)
        parent = posixpath.dirname(remote.path)
        values = SshClient(remote).read_small_files([posixpath.join(parent, name) for name in paths])
        for path, value in zip(paths, values):
            if "content" not in value:
                raise ValueError(f"Bundle sidecar unavailable: {path}")
        return [base64.b64decode(value["content"]) for value in values]
    parent = Path(os.path.abspath(table_path)).parent
    assets = [parent / path for path in paths]
    try:
        resolved = parent.resolve()
        if any(not asset.resolve().is_relative_to(resolved) for asset in assets):
            raise ValueError("Bundle sidecars must stay inside the score table folder.")
    except OSError as error:
        # Some Windows mounted volumes can open files but cannot return a final NT path.
        if getattr(error, "winerror", None) != 1005:
            raise
    return [Path(windows_long_path(asset)).read_bytes() for asset in assets]


def _read_membrane_assets(request):
    config = request.config
    membrane = config.get("membrane")
    if config.get("view") != "ligand_pocket" or not membrane:
        return None
    if not isinstance(membrane, dict):
        raise ValueError("Bundle membrane must be a JSON object.")
    paths = [_relative_asset(membrane.get(key), key) for key in ("reference_path", "surface_path")]
    reference, surface = _read_sidecars(request.table_path, paths)
    annotation = json.loads(surface.decode("utf-8-sig")).get(request.row_path)
    if not isinstance(annotation, dict) or not annotation.get("surface_residues"):
        raise ValueError(f"Membrane surface annotation is missing for {request.row_path}.")
    return Path(paths[0]).name, reference, annotation


def _prepare_bundle_assets(request):
    if request.config.get("view_sidecar") == "bundle_view.json":
        raw, = _read_sidecars(request.table_path, ["bundle_view.json"])
        config = json.loads(raw.decode("utf-8-sig"))
        if not bundle_matches_table(config, request.table_path) or config.get("path_column", "structure_path") != request.config["path_column"]:
            raise ValueError("Bundle view sidecar names a different score table or structure column.")
        request = replace(request, config=config)
    return request, _read_membrane_assets(request)


def _ligand_renderer(modules, request, root, assets, focus):
    config = dict(request.config)
    column = config.get("path_column", "structure_path")
    if assets is not None:
        name, reference, annotation = assets
        reference_path = root / "reference" / name
        reference_path.parent.mkdir()
        reference_path.write_bytes(reference)
        view_annotation = {key: value for key, value in annotation.items() if key != "cavity_centers"}
        (root / "surface.json").write_text(json.dumps({request.row_path: view_annotation}), encoding="utf-8")
        config["membrane"] = {
            **config["membrane"],
            "reference_path": reference_path.relative_to(root).as_posix(),
            "surface_path": "surface.json",
        }
    with _skill_imports():
        sys.modules.update(modules)
        renderer = modules["acceptance_collection"]._ligand_view(
            config, root, column, step_scene=focus, calculate_cavity=False
        )
    return renderer, {column: request.row_path}


def _draw_bundle(api, owner, prepared):
    request, assets = prepared
    modules = _load_skill()
    view = request.config.get("view", "cartoon")
    if view not in ("ligand_pocket", "cartoon"):
        raise ValueError(f"Unsupported Bundle view: {view!r}")
    token = uuid4().hex
    identity = f"{owner}_{token}"
    focus, backup = f"_sa_bundle_focus_{token}", f"_sa_bundle_backup_{token}"
    selections = set(api.get_names("selections"))
    coordinates = [api.get_coordset(owner, state) for state in range(1, api.count_states(owner) + 1)]
    transform = api.get_object_ttt(owner)
    api.scene(backup, "store")
    try:
        with TemporaryDirectory(prefix="pymol-bundle-view-") as directory:
            if view == "ligand_pocket":
                render, row = _ligand_renderer(modules, request, Path(directory), assets, focus)
            else:
                render, row = modules["acceptance_collection"]._cartoon_view, {}
            render(identity, {"model": owner}, row)
            if view == "ligand_pocket":
                legend = api.get_scene_message(focus).splitlines()[1:]
                title = next(iter(request.label.splitlines()), "")
                # PyMOL's native scene message wizard requires ASCII. Unicode
                # labels remain available in the Annotator score table.
                if not title or not title.isascii():
                    title = Path(request.row_path).stem.encode("ascii", "replace").decode("ascii")
                message = "\n".join([title, *legend])
                api.set_scene_message(focus, message)
                api.scene(focus, "recall", animate=0)
    except Exception:
        for state, coords in enumerate(coordinates, 1):
            api.load_coordset(coords, owner, state)
        if transform is not None:
            api.set_object_ttt(owner, transform)
        api.scene(backup, "recall")
        raise
    finally:
        for name in set(api.get_names("selections")) - selections:
            api.delete(name)
        for name in (focus, backup):
            if name in api.get_scene_list():
                api.scene(name, "delete")
    enabled = modules["acceptance_recipes"]._truthy(request.config.get("show_cavity", True), True)
    if view == "ligand_pocket" and enabled:
        protein = f"%{owner} and ({request.config['protein_selection']})"
        ligand = f"%{owner} and ({request.config['ligand_selection']})"
        return CandidateCavity("Pocket cavity", identity, lambda renderer: renderer.capture_cavity(api, protein, ligand))


def prepare_bundle_view(api, owner, request):
    return owner, _draw_bundle, partial(_prepare_bundle_assets, request), partial(_draw_bundle, api, owner)


def prepare_candidate_cavity(api, view):
    modules = _load_skill()
    snapshot = view.capture(modules["acceptance_pocket_void"])
    return partial(modules["acceptance_cavity_runtime"].calculate_cavity, snapshot), partial(
        draw_candidate_cavity, api, view
    )


def draw_candidate_cavity(api, view, payload):
    result, cached = payload
    cavity = _load_skill()["acceptance_pocket_void"]
    cavity.draw_cavity(view.identity, result, api=api)
    recess = sum(region["volume"] for region in result["recesses"])
    enclosed = sum(region["volume"] for region in result["enclosed"])
    status = f"{view.title} · {recess:.1f} Å³ recess · {enclosed:.1f} Å³ enclosed"
    tooltip = (
        f"{status}\nCyan: reached by a 1.4 Å probe from bulk, shielded from the 4.0 Å probe.\n"
        "Violet: the water-sized probe fits, with no path to bulk in this model.\n"
        "Volumes count probe-center grid cells in the bound geometry (0.5 Å grid).\n"
        + ("Read from cache." if cached else "Calculated for this structure.")
    )
    return status, tooltip
