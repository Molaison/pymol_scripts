"""Render declarative PyMOL skill Bundle views on Annotator-owned candidates."""

from contextlib import contextmanager
from dataclasses import dataclass
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

from pymolplugins.core.windows_paths import windows_long_path
from pymolplugins.remote.ssh import SshClient, is_ssh_path, parse_ssh_path


SKILL_MODULES = ("acceptance_bundle", "acceptance_pocket_void", "acceptance_recipes", "acceptance_collection")


@dataclass(frozen=True)
class BundleViewRequest:
    table_path: str
    config: dict
    row_path: str
    label: str


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


def _read_membrane_assets(request):
    config = request.config
    membrane = config.get("membrane")
    if config.get("view") != "ligand_pocket" or not membrane:
        return None
    if not isinstance(membrane, dict):
        raise ValueError("Bundle membrane must be a JSON object.")
    paths = [_relative_asset(membrane.get(key), key) for key in ("reference_path", "surface_path")]
    if is_ssh_path(request.table_path):
        remote = parse_ssh_path(request.table_path)
        parent = posixpath.dirname(remote.path)
        values = SshClient(remote).read_small_files([posixpath.join(parent, name) for name in paths])
        for path, value in zip(paths, values):
            if "content" not in value:
                raise ValueError(f"Bundle sidecar unavailable: {path}")
        reference, surface = [base64.b64decode(value["content"]) for value in values]
    else:
        parent = Path(os.path.abspath(request.table_path)).parent
        assets = [parent / path for path in paths]
        try:
            resolved = parent.resolve()
            if any(not asset.resolve().is_relative_to(resolved) for asset in assets):
                raise ValueError("Bundle sidecars must stay inside the score table folder.")
        except OSError as error:
            # Some Windows mounted volumes can open files but cannot return a
            # final NT path. The relative paths have already been checked above.
            if getattr(error, "winerror", None) != 1005:
                raise
        reference, surface = [Path(windows_long_path(asset)).read_bytes() for asset in assets]
    annotation = json.loads(surface.decode("utf-8-sig")).get(request.row_path)
    if not isinstance(annotation, dict) or not annotation.get("surface_residues"):
        raise ValueError(f"Membrane surface annotation is missing for {request.row_path}.")
    return Path(paths[0]).name, reference, annotation


def _ligand_renderer(modules, request, root, assets, focus):
    config = dict(request.config)
    column = config.get("path_column", "structure_path")
    if assets is not None:
        name, reference, annotation = assets
        reference_path = root / "reference" / name
        reference_path.parent.mkdir()
        reference_path.write_bytes(reference)
        (root / "surface.json").write_text(
            json.dumps({request.row_path: annotation}), encoding="utf-8"
        )
        config["membrane"] = {
            **config["membrane"],
            "reference_path": reference_path.relative_to(root).as_posix(),
            "surface_path": "surface.json",
        }
    with _skill_imports():
        sys.modules.update(modules)
        renderer = modules["acceptance_collection"]._ligand_view(config, root, column, step_scene=focus)
    return renderer, {column: request.row_path}


def _draw_bundle(api, owner, request, assets):
    modules = _load_skill()
    view = request.config.get("view", "cartoon")
    if view not in ("ligand_pocket", "cartoon"):
        raise ValueError(f"Unsupported Bundle view: {view!r}")
    token = uuid4().hex
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
            render(f"{owner}_{token}", {"model": owner}, row)
            if view == "ligand_pocket":
                legend = api.get_scene_message(focus).splitlines()[1:]
                title = request.label.splitlines()[0] if request.label else Path(request.row_path).stem
                message = "\n".join([title, *legend]).replace("–", "-")
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


def prepare_bundle_view(api, owner, request):
    return owner, _draw_bundle, partial(_read_membrane_assets, request), partial(_draw_bundle, api, owner, request)
